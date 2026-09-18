"""WeatherProvider 계약과 구현체.

계약: 좌표의 현재 날씨를 good/neutral/bad 중 하나로 반환한다. 업스트림(KMA)의
세부 날씨 코드를 이 세 단계로 매핑하는 책임은 구현체가 진다 - 이 셋 이외의
값은 provider 밖으로 노출되지 않는다.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from app.domain.models import WeatherForecastResult, WeatherForecastSlot
from app.errors import AppError
from app.providers.contracts import ProviderResult, ProviderSource, provider_result
from app.providers.kma_grid import latlon_to_grid
from app.providers.upstream_errors import upstream_error_detail

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")
_ULTRA_SRT_FCST_URL = (
    "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtFcst"
)

# PTY(강수형태): 0 없음 외에는 모두 강수 상황.
_PRECIPITATION_PTY_CODES = {"1", "2", "3", "4", "5", "6", "7"}

# SKY(하늘상태): 1 맑음, 3 구름많음, 4 흐림.
_KNOWN_SKY_CODES = {"1", "3", "4"}

# resultCode 03(NODATA_ERROR): 요청은 정상이나 그 격자·발표시각에 자료가 없다.
_NO_DATA_RESULT_CODE = "03"


def resolve_base_date_time(now: datetime) -> tuple[str, str]:
    """주어진 시각 기준으로 조회 가능한 가장 최신 초단기예보 base_date/base_time을 구한다.

    발표는 매시 30분, API 제공은 발표 후 10분 뒤(매시 40분)부터 시작되므로
    45분 이전이면 직전 시각의 30분 발표분을 사용한다.
    """
    base_dt = now if now.minute >= 45 else now - timedelta(hours=1)
    return base_dt.strftime("%Y%m%d"), base_dt.strftime("%H30")


def _optional_float(value: str | None) -> float | None:
    """T1H(기온)는 숫자 문자열로 온다. 결측·비정상 값은 버린다."""
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def has_usable_sky_or_precipitation(sky: str | None, pty: str | None) -> bool:
    """SKY·PTY 중 최소 하나가 알려진 코드인지 — slot을 살릴지 판정한다.

    D-051 전에는 이 판단이 `map_sky_pty_to_condition()`의 예외로만 표현돼 있었다.
    그 함수는 레거시 3단계 판정용이라 사라질 예정이었는데, "판정이 안 된다"와
    "slot을 버린다"가 한 함수에 얽혀 있으면 판정을 걷어낼 때 필터까지 조용히
    같이 사라진다. 미리 별도 함수로 분리해 뒀고, 실제로 그 제거가 이뤄졌다.
    """
    if pty is not None and pty in _PRECIPITATION_PTY_CODES:
        return True
    return sky is not None and sky in _KNOWN_SKY_CODES


def map_items_to_forecast_slots(items: list[dict]) -> tuple[WeatherForecastSlot, ...]:
    """KMA 항목을 예보 대상 시각별 SKY·PTY slot으로 묶는다.

    아래 루프는 `has_usable_sky_or_precipitation()`이 False면(SKY·PTY 둘 다 결측·
    미지의 코드) slot 자체를 버린다. 초단기예보는 예보시각 하나에 카테고리 10종을 한
    묶음으로 주므로 SKY·PTY·T1H가 항상 함께 오고, numOfRows=100이 실제 항목
    수(10×6)보다 커서 페이지가 잘리지도 않는다 — 그래서 지금은 "판정 불가"와
    "데이터 없음"이 같은 말이다.

    예보 범위를 넓히려고 단기예보(getVilageFcst)를 붙이면 이 가정이 깨진다:
    거기선 TMP가 1시간 단위인데 SKY·PTY는 3시간 단위라 기온만 있는 시각이 대부분이다
    (초단기실황 getUltraSrtNcst도 SKY가 아예 없다). D는 기온만으로도 폭염·한파를
    판정하므로(D-051) 그때는 slot을 버리는 대신 sky_code·precipitation_type을
    None으로 두고 temperature_celsius를 살려 보내야 한다.
    """
    grouped: dict[tuple[str, str], dict[str, str]] = {}
    for item in items:
        category = item.get("category")
        forecast_date = item.get("fcstDate")
        forecast_time = item.get("fcstTime")
        value = item.get("fcstValue")
        if (
            category not in {"SKY", "PTY", "T1H"}
            or not forecast_date
            or not forecast_time
            or value is None
        ):
            continue
        grouped.setdefault((str(forecast_date), str(forecast_time)), {})[
            str(category)
        ] = str(value)

    slots: list[WeatherForecastSlot] = []
    for (forecast_date, forecast_time), values in sorted(grouped.items()):
        if not has_usable_sky_or_precipitation(values.get("SKY"), values.get("PTY")):
            continue
        try:
            forecast_for = datetime.strptime(
                f"{forecast_date}{forecast_time}", "%Y%m%d%H%M"
            ).replace(tzinfo=_KST)
        except ValueError:
            continue
        slots.append(
            WeatherForecastSlot(
                forecast_for=forecast_for,
                sky_code=values.get("SKY"),
                precipitation_type=values.get("PTY"),
                temperature_celsius=_optional_float(values.get("T1H")),
            )
        )
    return tuple(slots)


class RealWeatherProvider:
    """KMA 단기예보 조회서비스(getUltraSrtFcst)를 사용하는 실제 구현.

    초단기실황(getUltraSrtNcst)과 달리 SKY(하늘상태)와 PTY(강수형태)를 한 번의
    호출로 함께 제공해서, D가 판정에 쓸 사실을 한 번에 모두 얻을 수 있다(D-051).
    """

    def __init__(
        self,
        api_key: str,
        client: httpx.AsyncClient,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._api_key = api_key
        self._client = client
        self._timeout_seconds = timeout_seconds

    async def get_forecast_slots(
        self, latitude: float, longitude: float
    ) -> ProviderResult[WeatherForecastResult]:
        nx, ny = latlon_to_grid(latitude, longitude)
        base_date, base_time = resolve_base_date_time(datetime.now(_KST))

        params = {
            "pageNo": "1",
            "numOfRows": "100",
            "dataType": "JSON",
            "base_date": base_date,
            "base_time": base_time,
            "nx": str(nx),
            "ny": str(ny),
        }
        request_params = {"serviceKey": self._api_key, **params}

        try:
            response = await self._client.get(
                _ULTRA_SRT_FCST_URL,
                params=request_params,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            # 4xx는 게이트웨이 인증·쿼터 거절이라 본문의 errMsg 없이는 원인을 못 가린다.
            logger.error(
                "KMA 호출 실패 (http_status=%s, %s, grid=%s/%s, base=%s %s)",
                exc.response.status_code,
                upstream_error_detail(exc.response),
                nx,
                ny,
                base_date,
                base_time,
            )
            request_params.clear()
            raise AppError(
                code="weather_unavailable",
                message="날씨 정보를 가져오지 못했습니다.",
                status_code=502,
                retryable=True,
            ) from None
        except (httpx.HTTPError, ValueError) as exc:
            # httpx 원인 예외에는 ServiceKey가 포함된 요청 정보가 남을 수 있다.
            request_params.clear()
            logger.error(
                "KMA 호출 실패 (%s, grid=%s/%s, base=%s %s)",
                type(exc).__name__,
                nx,
                ny,
                base_date,
                base_time,
            )
            raise AppError(
                code="weather_unavailable",
                message="날씨 정보를 가져오지 못했습니다.",
                status_code=502,
                retryable=True,
            ) from None

        header = payload.get("response", {}).get("header", {})
        result_code = header.get("resultCode")
        if result_code != "00":
            logger.error(
                "KMA 응답 오류 (resultCode=%s, resultMsg=%s, grid=%s/%s, base=%s %s)",
                result_code,
                header.get("resultMsg"),
                nx,
                ny,
                base_date,
                base_time,
            )
            # 03은 업스트림이 정상 처리한 끝에 "그 격자·발표시각에 자료가 없다"고
            # 답한 것이라, 서비스키 미등록(30)이나 호출 한도 초과(22)와 성격이
            # 다르다. 즉시 재시도해도 결과가 같으므로 결측으로 구분해 올린다 -
            # 한 코드로 뭉뚱그리면 로그에서 설정 사고와 정상 결측이 안 갈린다.
            if result_code == _NO_DATA_RESULT_CODE:
                raise AppError(
                    code="weather_no_data",
                    message="사용 가능한 날씨 예보가 없습니다.",
                    status_code=502,
                    retryable=False,
                )
            raise AppError(
                code="weather_unavailable",
                message=header.get("resultMsg", "날씨 정보를 가져오지 못했습니다."),
                status_code=502,
                retryable=True,
            )

        try:
            items = payload["response"]["body"]["items"]["item"]
        except (KeyError, TypeError):
            items = []
        slots = map_items_to_forecast_slots(items if isinstance(items, list) else [])
        return provider_result(
            WeatherForecastResult(
                latitude=latitude,
                longitude=longitude,
                grid_x=nx,
                grid_y=ny,
                slots=slots,
                provider="kma_ultra_short_forecast",
            ),
            source=ProviderSource.KMA_ULTRA_SHORT_FORECAST,
        )
