"""무장애 여행 정보(KorWithService2) Provider (D-077).

`KorService2`(국문 관광정보)와 인증키·공통 파라미터·오류 규약이 같고 서비스 경로만
다르다. 그래서 호출은 `TourApiClient`에 맡기고 이 모듈은 두 가지만 한다 —
어느 장소에 무장애 정보가 있는지 목록으로 받고, 그 장소의 응답을 저장 계약
(`PlaceBarrierFreeDetails`)으로 옮긴다.

목록을 먼저 받는 이유는 호출을 아끼기 위해서다. 무장애 정보가 등록된 장소는 4개 구
2,570건 중 496건(19%)뿐이고, 등록되지 않은 장소에 detailWithTour2를 부르면
`totalCount: 0`이 돌아온다 — 한도만 쓰고 얻는 게 없다. 구별 목록 1회로 대상을
좁히면 종로구 기준 842회가 아니라 182회로 끝난다.
"""

from __future__ import annotations

from collections.abc import Mapping

import httpx

from app.domain.models import PlaceBarrierFreeDetails
from app.errors import ProviderUnavailableError
from app.providers.tour_api_client import (
    TourApiClient,
    first_item,
    response_body,
    response_items,
)

_BASE_URL = "https://apis.data.go.kr/B551011/KorWithService2"
_AREA_BASED_LIST_PATH = "/areaBasedList2"
_DETAIL_WITH_TOUR_PATH = "/detailWithTour2"
_SERVICE_NAME = "TourAPI(무장애)"

# 목록 조회 numOfRows 상한. KorService2와 같은 값이다(2026-08-25 확인).
_MAX_LIST_ROWS = 1000

# 응답 키 → 저장 필드. 이 표가 이 모듈의 전부이므로 순서를 채움률 순으로 둔다
# (2026-08-25 실측, 숙박을 뺀 427건 기준).
_FIELD_BY_RESPONSE_KEY: tuple[tuple[str, str], ...] = (
    ("route", "approach_route_raw"),  # 64.9%
    ("exit", "entrance_access_raw"),  # 62.1%
    ("restroom", "accessible_restroom_raw"),  # 52.2%
    ("parking", "accessible_parking_raw"),  # 47.1%
    ("elevator", "elevator_raw"),  # 42.2%
    ("handicapetc", "disability_etc_raw"),  # 22.2%
    ("braileblock", "braille_block_raw"),  # 19.7% (응답 키의 철자가 이렇다)
    ("wheelchair", "wheelchair_rental_raw"),  # 16.9% — 출입이 아니라 대여다
    ("publictransport", "public_transport_raw"),  # 13.6%
    ("stroller", "stroller_rental_raw"),  # 13.6%
    ("infantsfamilyetc", "infant_family_etc_raw"),  # 13.1%
    ("lactationroom", "nursing_room_raw"),  # 12.4%
    ("brailepromotion", "braille_promotion_raw"),  # 10.5%
    ("audioguide", "audio_guide_raw"),  # 9.6%
    ("helpdog", "guide_dog_raw"),  # 9.1%
)


def _optional_text(item: Mapping[str, object], key: str) -> str | None:
    """원문을 그대로 담되 공백만 있는 값은 비운 것으로 본다.

    `<br/>` 태그나 `_무장애 편의시설` 접미사도 지우지 않는다 — 해석은 소비 측
    몫이고, 여기서 손대면 원문으로 되돌릴 수 없다(places의 `_raw` 컬럼들과 같은
    규칙이다).
    """
    value = item.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _non_negative_int(value: object, field: str) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        raise ProviderUnavailableError(
            _SERVICE_NAME, detail=f"areaBasedList2 response has invalid {field}"
        ) from None
    if parsed < 0:
        raise ProviderUnavailableError(
            _SERVICE_NAME, detail=f"areaBasedList2 response has invalid {field}"
        )
    return parsed


class RealBarrierFreeProvider:
    def __init__(
        self,
        api_key: str,
        client: httpx.AsyncClient,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._api = TourApiClient(
            api_key,
            client,
            base_url=_BASE_URL,
            timeout_seconds=timeout_seconds,
            service_name=_SERVICE_NAME,
        )

    async def list_barrier_free_content_ids(
        self,
        area_code: str,
        district_code: str,
    ) -> dict[str, str]:
        """그 구에서 무장애 정보가 등록된 장소의 content_id → content_type_id.

        유형을 함께 돌려주는 이유는 호출하는 쪽이 유형으로 대상을 거르기 때문이다
        (숙박 제외). 유형을 places에서 다시 읽어오게 하면, 아직 places에 없는 신규
        장소에서 값이 비어 거르지 못한다.
        """
        normalized_area_code = area_code.strip()
        normalized_district_code = district_code.strip()
        if not normalized_area_code or not normalized_district_code:
            raise ValueError("area_code와 district_code가 필요합니다.")

        listed: dict[str, str] = {}
        page_no = 1
        while True:
            payload = await self._api.request_json(
                _AREA_BASED_LIST_PATH,
                {
                    **self._api.base_params(),
                    "lDongRegnCd": normalized_area_code,
                    "lDongSignguCd": normalized_district_code,
                    "arrange": "A",
                    "pageNo": page_no,
                    "numOfRows": _MAX_LIST_ROWS,
                },
            )
            body = response_body(payload)
            total_count = _non_negative_int(body.get("totalCount"), "totalCount")
            items = response_items(payload)
            for item in items:
                content_id = _optional_text(item, "contentid")
                content_type_id = _optional_text(item, "contenttypeid")
                if content_id is None or content_type_id is None:
                    raise ProviderUnavailableError(
                        _SERVICE_NAME,
                        detail="areaBasedList2 item missing contentid/contenttypeid",
                    )
                listed[content_id] = content_type_id
            # 마지막 쪽은 items가 비어 온다. 개수로만 판단하면 중복 id가 섞였을 때
            # 영원히 다음 쪽을 부른다.
            if not items or len(listed) >= total_count:
                return listed
            page_no += 1

    async def get_barrier_free_details(
        self,
        content_id: str,
    ) -> PlaceBarrierFreeDetails | None:
        """무장애 상세 1건. 등록되지 않은 장소면 None이다.

        None과 "값이 전부 빈 결과"는 다르다 — 목록에 있는데도 15개 필드가 모두 빈
        장소가 496건 중 60건이라, 둘을 뭉개면 그 60건을 매번 다시 부르게 된다.
        """
        normalized_content_id = content_id.strip()
        if not normalized_content_id:
            raise ValueError("content_id가 필요합니다.")

        payload = await self._api.request_json(
            _DETAIL_WITH_TOUR_PATH,
            {
                **self._api.base_params(),
                "contentId": normalized_content_id,
            },
        )
        item = first_item(payload)
        if not item:
            return None
        return PlaceBarrierFreeDetails(
            content_id=normalized_content_id,
            **{
                field: _optional_text(item, key)
                for key, field in _FIELD_BY_RESPONSE_KEY
            },
        )


__all__ = ["RealBarrierFreeProvider"]
