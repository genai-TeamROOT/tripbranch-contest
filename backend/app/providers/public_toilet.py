"""서울시 공중화장실 위치정보 API(mgisToiletPoi) Provider.

공영주차장(GetParkingInfo)과 달리 이 API는 구·좌표 필터 파라미터가 없다 — 실측
확인 결과 경로에 구 이름을 덧붙여도 무시하고 항상 전량(4,447건)을 준다. 그래서
사용자 요청 때마다 부르지 않고, 동기화 스크립트가 전량을 받아 ``public_toilets``
표에 적재한 뒤 조회는 그 표에서 한다. 적재주기가 "비정기(자료 변경 시)"라 실시간성
손실도 없다.

좌표는 원본이 WGS84로 이미 주므로(결측 0건) 주차장처럼 지오코딩할 필요가 없다.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import httpx

from app.domain.models import PublicToilet
from app.errors import ProviderTimeoutError, ProviderUnavailableError
from app.providers.contracts import ProviderResult, ProviderSource, ProviderStatus, provider_result

logger = logging.getLogger(__name__)

_BASE_URL = "http://openapi.seoul.go.kr:8088"
_SERVICE = "mgisToiletPoi"
# 서울 열린데이터광장 공통 상한. 한 번에 이보다 많이 요청하면 오류가 난다.
_PAGE_SIZE = 1000
# 전량이 4,447건인데 자료가 늘어날 수 있어 여유를 둔다. 무한 루프 방지용 상한이라
# 실제 종료 조건은 "받은 행이 페이지 크기보다 적다"다.
_MAX_PAGES = 20


def _text(value: object | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _float(value: object | None) -> float | None:
    try:
        return float(str(value)) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _rows(payload: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    service = payload.get(_SERVICE)
    if not isinstance(service, Mapping):
        return ()
    raw = service.get("row")
    return tuple(item for item in raw if isinstance(item, Mapping)) if isinstance(raw, list) else ()


def _total_count(payload: Mapping[str, object]) -> int | None:
    service = payload.get(_SERVICE)
    if not isinstance(service, Mapping):
        return None
    total = service.get("list_total_count")
    try:
        return int(float(str(total))) if total not in (None, "") else None
    except (TypeError, ValueError):
        return None


def map_public_toilet_response(payload: Mapping[str, object]) -> tuple[PublicToilet, ...]:
    """mgisToiletPoi 행을 공중화장실 계약으로 정규화한다.

    좌표가 없는 행은 버린다 — "근처 화장실"은 거리로만 답할 수 있어서 좌표 없는
    항목은 적재해도 쓸 데가 없다. 실측 전량에는 결측이 없었지만 자료가 갱신되며
    생길 수 있어 방어한다.
    """

    toilets: list[PublicToilet] = []
    for row in _rows(payload):
        latitude = _float(row.get("COORD_Y"))
        longitude = _float(row.get("COORD_X"))
        toilet_id = _text(row.get("OBJECTID"))
        if latitude is None or longitude is None or toilet_id is None:
            continue
        toilets.append(
            PublicToilet(
                # 원본이 283814.0 같은 실수로 주므로 소수점을 떼고 문자열로 쓴다.
                toilet_id=toilet_id.removesuffix(".0"),
                name=_text(row.get("CONTS_NAME")) or "공중화장실",
                address_new=_text(row.get("ADDR_NEW")),
                address_old=_text(row.get("ADDR_OLD")),
                latitude=latitude,
                longitude=longitude,
                district=_text(row.get("GU_NAME")),
                tel=_text(row.get("TEL_NO")),
                open_type=_text(row.get("VALUE_01")),
                open_hours_raw=_text(row.get("VALUE_02")),
                restroom_status=_text(row.get("VALUE_04")),
                accessible_status=_text(row.get("VALUE_05")),
                amenities=_text(row.get("VALUE_06")),
                safety_signs=_text(row.get("VALUE_07")),
                location_type=_text(row.get("VALUE_08")),
                manager=_text(row.get("VALUE_09")),
            )
        )
    return tuple(toilets)


# 개발·테스트용 인사동 주변 3곳. 개방시간 표기의 세 갈래(24시간 / 시각 구간 /
# 해석 불가)를 각각 하나씩 담아 판정 경로를 모두 덮는다. Fake 저장소도 같은
# 데이터를 써서 fake 모드에서 카드 렌더까지 확인할 수 있게 한다.
FAKE_TOILETS: tuple[PublicToilet, ...] = (
    PublicToilet(
        toilet_id="FAKE-INSADONG-1",
        name="인사동마루 신관 개방화장실",
        address_new="서울특별시 종로구 인사동길 35-4",
        address_old="서울특별시 종로구 관훈동 196-10",
        latitude=37.57432,
        longitude=126.98563,
        district="종로구",
        tel="02-2148-2383",
        open_type="민간개방|",
        open_hours_raw="상시(24시간)|",
        restroom_status="남자|여자|",
        accessible_status="남자|여자|",
        amenities="기저귀교환대(남)|기저귀교환대(여)|",
        safety_signs="비상벨(여)|",
        location_type="근생시설|",
        manager="인사동마루",
    ),
    # 개방시간이 시각으로 해석되는 곳. "지금 닫힘" 판정 경로를 덮는다.
    PublicToilet(
        toilet_id="FAKE-INSADONG-2",
        name="쌈지길(지하1층)",
        address_new="서울특별시 종로구 인사동길 44",
        address_old="서울특별시 종로구 관훈동 38",
        latitude=37.57411,
        longitude=126.98527,
        district="종로구",
        tel="02-736-0088",
        open_type="민간개방|",
        open_hours_raw="기타|10:30~20:30",
        restroom_status="남자|여자|",
        accessible_status="남자|여자|",
        amenities=None,
        safety_signs=None,
        location_type="근생시설|",
        manager="쌈지길",
    ),
    # 개방시간을 해석할 수 없는 곳(실측 11%). 원문을 그대로 보여주는 경로를 덮는다.
    PublicToilet(
        toilet_id="FAKE-INSADONG-3",
        name="인사아트프라자 개방화장실",
        address_new="서울특별시 종로구 인사동길 34-1",
        address_old=None,
        latitude=37.57368,
        longitude=126.98498,
        district="종로구",
        tel=None,
        open_type="민간개방|",
        open_hours_raw="정시(영업시작~종료)",
        restroom_status="남자|여자|",
        accessible_status=None,
        amenities=None,
        safety_signs=None,
        location_type="업무시설|",
        manager="㈜인사아트프라자",
    ),
)


class FakePublicToiletProvider:
    async def list_all_toilets(self) -> ProviderResult[tuple[PublicToilet, ...]]:
        return provider_result(FAKE_TOILETS, source=ProviderSource.FAKE_PUBLIC_TOILET)


class RealPublicToiletProvider:
    """서울시 공중화장실 위치정보 전량 조회 Provider."""

    def __init__(
        self, api_key: str, client: httpx.AsyncClient, timeout_seconds: float = 10.0
    ) -> None:
        self._api_key = api_key
        self._client = client
        self._timeout_seconds = timeout_seconds

    async def list_all_toilets(self) -> ProviderResult[tuple[PublicToilet, ...]]:
        toilets: list[PublicToilet] = []
        start = 1
        for _ in range(_MAX_PAGES):
            payload = await self._fetch_page(start, start + _PAGE_SIZE - 1)
            page = map_public_toilet_response(payload)
            toilets.extend(page)
            total = _total_count(payload)
            # 좌표 결측으로 버린 행이 있으면 len(page)가 실제 수신 행보다 적을 수
            # 있으므로, 종료는 API가 알려주는 전체 건수로 판정한다.
            if total is not None and start + _PAGE_SIZE - 1 >= total:
                break
            if not page:
                break
            start += _PAGE_SIZE
        return provider_result(
            tuple(toilets),
            source=ProviderSource.SEOUL_PUBLIC_TOILET,
            status=ProviderStatus.SUCCESS if toilets else ProviderStatus.NO_DATA,
        )

    async def _fetch_page(self, start_index: int, end_index: int) -> Mapping[str, object]:
        url = f"{_BASE_URL}/{self._api_key}/json/{_SERVICE}/{start_index}/{end_index}/"
        try:
            response = await self._client.get(url, timeout=self._timeout_seconds)
            response.raise_for_status()
            payload: Any = response.json()
        except httpx.TimeoutException:
            raise ProviderTimeoutError("서울시 공중화장실") from None
        except (httpx.HTTPError, ValueError) as exc:
            logger.error(
                "서울시 공중화장실 호출 실패 (%s, start=%s)", type(exc).__name__, start_index
            )
            raise ProviderUnavailableError("서울시 공중화장실") from None
        if not isinstance(payload, Mapping):
            raise ProviderUnavailableError(
                "서울시 공중화장실", detail="응답 형식이 올바르지 않습니다."
            )
        return payload


__all__ = [
    "FAKE_TOILETS",
    "FakePublicToiletProvider",
    "RealPublicToiletProvider",
    "map_public_toilet_response",
]
