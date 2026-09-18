"""서울시 시영·공영주차장 실시간 API(GetParkingInfo) Provider.

도시데이터 ``PRK_STTS``와 달리 이 API는 주차장 코드·현재 주차 대수·갱신 시각을
구 단위로 안정적으로 제공한다. 좌표는 제공하지 않으므로 위치 정렬은 별도 카탈로그가
맡는다.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import httpx

from app.domain.models import MunicipalParkingStatus
from app.errors import ProviderTimeoutError, ProviderUnavailableError
from app.providers.contracts import ProviderResult, ProviderSource, ProviderStatus, provider_result

logger = logging.getLogger(__name__)

_BASE_URL = "http://openapi.seoul.go.kr:8088"
_SERVICE = "GetParkingInfo"


def _text(value: object | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _int(value: object | None) -> int | None:
    try:
        # 실제 API는 정수처럼 보이는 수를 725.0 같은 JSON number/문자열로도 보낸다.
        # ``int("725.0")``은 실패하므로 float을 거쳐 정수화한다.
        return int(float(str(value))) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _rows(payload: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    service = payload.get(_SERVICE)
    if not isinstance(service, Mapping):
        return ()
    raw = service.get("row")
    return tuple(item for item in raw if isinstance(item, Mapping)) if isinstance(raw, list) else ()


def map_municipal_parking_response(
    payload: Mapping[str, object], *, requested_district: str
) -> tuple[MunicipalParkingStatus, ...]:
    """GetParkingInfo 행을 공영주차장 실시간 계약으로 정규화한다."""

    return tuple(
        MunicipalParkingStatus(
            code=_text(row.get("PKLT_CD")) or f"{requested_district}:{index}",
            name=_text(row.get("PKLT_NM")) or "공영주차장",
            address=_text(row.get("ADDR")),
            district=_text(row.get("GU_NM")) or requested_district,
            capacity=_int(row.get("TPKCT")),
            current_parked_count=_int(row.get("NOW_PRK_VHCL_CNT")),
            observed_at=_text(row.get("NOW_PRK_VHCL_UPDT_TM")),
            paid=(
                True
                if _text(row.get("PAY_YN")) == "Y"
                else False if _text(row.get("PAY_YN")) == "N" else None
            ),
            # API 명세: 1이면 실시간 데이터 연계가 있고 현재 주차 대수가 20분 이내
            # 갱신된 상태다. 값이 비어 있으면 실시간 수치로 표시하지 않는다.
            is_live=_text(row.get("PRK_STTS_YN")) == "1",
        )
        for index, row in enumerate(_rows(payload), start=1)
    )


class FakeMunicipalParkingProvider:
    async def get_district_parking(
        self, district: str
    ) -> ProviderResult[tuple[MunicipalParkingStatus, ...]]:
        normalized = district.strip() or "종로구"
        return provider_result(
            (
                MunicipalParkingStatus(
                    code="FAKE-JONGNO-1",
                    name="테스트 종로 공영주차장",
                    address="서울특별시 종로구 사직로 161",
                    district=normalized,
                    capacity=120,
                    current_parked_count=76,
                    observed_at="2026-08-27 11:24:53",
                    paid=True,
                    is_live=True,
                ),
                MunicipalParkingStatus(
                    code="FAKE-JONGNO-2",
                    name="테스트 구청 공영주차장",
                    address="서울특별시 종로구 종로1길 1",
                    district=normalized,
                    capacity=60,
                    current_parked_count=None,
                    observed_at=None,
                    paid=True,
                    is_live=False,
                ),
            ),
            source=ProviderSource.FAKE_MUNICIPAL_PARKING,
        )


class RealMunicipalParkingProvider:
    """공영주차장 구 단위 최신 주차 대수 Provider."""

    def __init__(
        self, api_key: str, client: httpx.AsyncClient, timeout_seconds: float = 10.0
    ) -> None:
        self._api_key = api_key
        self._client = client
        self._timeout_seconds = timeout_seconds

    async def get_district_parking(
        self, district: str
    ) -> ProviderResult[tuple[MunicipalParkingStatus, ...]]:
        query = district.strip()
        if not query:
            raise ValueError("district가 필요합니다.")
        url = f"{_BASE_URL}/{self._api_key}/json/{_SERVICE}/1/1000/{quote(query, safe='')}"
        try:
            response = await self._client.get(url, timeout=self._timeout_seconds)
            response.raise_for_status()
            payload: Any = response.json()
        except httpx.TimeoutException:
            raise ProviderTimeoutError("서울시 공영주차장") from None
        except (httpx.HTTPError, ValueError) as exc:
            logger.error("서울시 공영주차장 호출 실패 (%s, district=%s)", type(exc).__name__, query)
            raise ProviderUnavailableError("서울시 공영주차장") from None
        if not isinstance(payload, Mapping):
            raise ProviderUnavailableError(
                "서울시 공영주차장", detail="응답 형식이 올바르지 않습니다."
            )
        lots = map_municipal_parking_response(payload, requested_district=query)
        return provider_result(
            lots,
            source=ProviderSource.SEOUL_MUNICIPAL_PARKING,
            status=ProviderStatus.SUCCESS if lots else ProviderStatus.NO_DATA,
        )


__all__ = [
    "FakeMunicipalParkingProvider",
    "RealMunicipalParkingProvider",
    "map_municipal_parking_response",
]
