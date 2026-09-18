"""한국관광공사 관광지 집중률 예측 Provider의 Stub/Real 구현."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from app.domain.models import ConcentrationForecast, ConcentrationResult
from app.errors import ProviderTimeoutError, ProviderUnavailableError
from app.providers.contracts import (
    ProviderResult,
    ProviderSource,
    ProviderStatus,
    provider_result,
)
from app.providers.upstream_errors import upstream_error_detail

logger = logging.getLogger(__name__)

_CONCENTRATION_URL = (
    "https://apis.data.go.kr/B551011/TatsCnctrRateService/tatsCnctrRatedList"
)
_PLACE_NAME_KEYS = ("tAtsNm", "tatsNm", "touristAttractionName")
_FORECAST_DATE_KEYS = ("fcastYmd", "forecastYmd", "baseYmd", "forecastDate", "ymd")
_CONCENTRATION_RATE_KEYS = ("cnctrRate", "concentrationRate", "congestionRate", "rate")
_KST = ZoneInfo("Asia/Seoul")


def _first_value(item: Mapping[str, object], keys: tuple[str, ...]) -> object | None:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return None


def _to_float(value: object | None) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).rstrip("%"))
    except (TypeError, ValueError):
        return None


def map_concentration_item(
    item: Mapping[str, object], requested_place_name: str | None
) -> ConcentrationForecast:
    """원본 필드 차이를 공통 예측 모델로 흡수한다."""
    place_name = _first_value(item, _PLACE_NAME_KEYS) or requested_place_name or "unknown"
    forecast_date = _first_value(item, _FORECAST_DATE_KEYS)
    rate = _first_value(item, _CONCENTRATION_RATE_KEYS)
    return ConcentrationForecast(
        place_name=str(place_name),
        forecast_date=str(forecast_date) if forecast_date is not None else None,
        concentration_rate=_to_float(rate),
        raw_data=dict(item),
    )


def map_concentration_response(
    payload: Mapping[str, object],
    *,
    area_code: str,
    district_code: str,
    requested_place_name: str | None,
) -> ConcentrationResult:
    response = payload.get("response")
    body = response.get("body") if isinstance(response, Mapping) else None
    items = body.get("items") if isinstance(body, Mapping) else None
    raw_items = items.get("item", []) if isinstance(items, Mapping) else []
    if isinstance(raw_items, Mapping):
        raw_items = [raw_items]
    if not isinstance(raw_items, list):
        raw_items = []

    forecasts = tuple(
        map_concentration_item(item, requested_place_name)
        for item in raw_items
        if isinstance(item, Mapping)
    )
    return ConcentrationResult(
        area_code=area_code,
        district_code=district_code,
        requested_place_name=requested_place_name,
        forecasts=forecasts,
        provider="tour_api_concentration",
    )


class FakeConcentrationProvider:
    """종로구 경복궁의 재현 가능한 집중률 예측을 반환한다."""

    async def get_forecast(
        self,
        area_code: str,
        district_code: str,
        place_name: str | None = None,
    ) -> ProviderResult[ConcentrationResult]:
        resolved_name = place_name or "경복궁"
        today = datetime.now(_KST).date()
        forecasts = tuple(
            ConcentrationForecast(
                place_name=resolved_name,
                forecast_date=forecast_date.strftime("%Y%m%d"),
                concentration_rate=rate,
                raw_data={
                    "tAtsNm": resolved_name,
                    "fcastYmd": forecast_date.strftime("%Y%m%d"),
                    "cnctrRate": rate,
                },
            )
            for forecast_date, rate in (
                (today - timedelta(days=1), 42.0),
                (today, 58.0),
                (today + timedelta(days=1), 76.0),
            )
        )
        result = ConcentrationResult(
            area_code=area_code,
            district_code=district_code,
            requested_place_name=place_name,
            forecasts=forecasts,
            provider="fake_concentration",
        )
        return provider_result(
            result,
            source=ProviderSource.FAKE_CONCENTRATION,
            status=ProviderStatus.SUCCESS if forecasts else ProviderStatus.NO_DATA,
        )


class RealConcentrationProvider:
    """TatsCnctrRateService를 사용하는 실제 집중률 Provider."""

    def __init__(
        self,
        api_key: str,
        client: httpx.AsyncClient,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._api_key = api_key
        self._client = client
        self._timeout_seconds = timeout_seconds

    async def get_forecast(
        self,
        area_code: str,
        district_code: str,
        place_name: str | None = None,
    ) -> ProviderResult[ConcentrationResult]:
        params = {
            "pageNo": "1",
            "numOfRows": "100",
            "MobileOS": "ETC",
            "MobileApp": "TripBranch",
            "areaCd": area_code,
            "signguCd": district_code,
            "_type": "json",
        }
        if place_name:
            params["tAtsNm"] = place_name
        request_params = {"serviceKey": self._api_key, **params}

        try:
            response = await self._client.get(
                _CONCENTRATION_URL,
                params=request_params,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException:
            # httpx 원인 예외에는 ServiceKey가 포함된 요청 정보가 남을 수 있다.
            request_params.clear()
            response = None
            logger.error("TourAPI Concentration 호출 타임아웃 (area=%s)", area_code)
            raise ProviderTimeoutError("TourAPI Concentration") from None
        except httpx.HTTPStatusError as exc:
            # 4xx는 인증·쿼터 거절이라 본문 errMsg 없이는 원인을 못 가린다.
            detail = f"HTTP {exc.response.status_code}, {upstream_error_detail(exc.response)}"
            request_params.clear()
            response = None
            logger.error(
                "TourAPI Concentration 호출 실패 (%s, area=%s)", detail, area_code
            )
            raise ProviderUnavailableError(
                "TourAPI Concentration", detail=detail
            ) from None
        except (httpx.HTTPError, ValueError) as exc:
            request_params.clear()
            response = None
            logger.error(
                "TourAPI Concentration 호출 실패 (%s, area=%s)",
                type(exc).__name__,
                area_code,
            )
            raise ProviderUnavailableError("TourAPI Concentration") from None

        response_node = payload.get("response", {})
        header = response_node.get("header", {}) if isinstance(response_node, dict) else {}
        result_code = str(header.get("resultCode", ""))
        if result_code not in {"", "00", "0000"}:
            # HTTP 200으로 내려오는 오류(쿼터 초과 등)는 이 분기로 온다.
            logger.error(
                "TourAPI Concentration 응답 오류 (resultCode=%s, resultMsg=%s, area=%s)",
                result_code,
                header.get("resultMsg", ""),
                area_code,
            )
            raise ProviderUnavailableError(
                "TourAPI Concentration",
                detail=f"{result_code}: {header.get('resultMsg', '')}",
            )

        result = map_concentration_response(
            payload,
            area_code=area_code,
            district_code=district_code,
            requested_place_name=place_name,
        )
        return provider_result(
            result,
            source=ProviderSource.TOUR_API_CONCENTRATION,
            status=(
                ProviderStatus.SUCCESS
                if result.forecasts
                else ProviderStatus.NO_DATA
            ),
        )
