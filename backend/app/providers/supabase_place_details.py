"""미리 구축된 Supabase places 테이블에서 장소 상세·운영정보를 읽는 Provider.

역할: 후보별 TourAPI 상세 API 호출을 대체한다. 장소 후보 "검색"은 여전히
TourAPI(RealPlaceProvider)가 담당하고, 이 Provider는 검색으로 얻은 content_id의
상세·운영정보만 저장소에서 조회한다.

운영시간은 적재 배치가 저장한 operating_schedule JSON을 읽는다. 저장된
operating_parser_version이 지금 파서와 다르면 그 값을 믿지 않고 원문
(operating_hours_raw, rest_date_raw)을 다시 정규화한다 —
`resolve_operating_schedule()`이 그 판단을 한다.

**예전에는 늘 원문을 다시 정규화했다.** 파서가 자주 바뀌던 동안 "고치면 재동기화
없이 즉시 반영"이 필요했기 때문인데, 후보 수만큼 정규식이 돌았고 파싱을 LLM으로
옮기면 그 자리에서는 아예 부를 수 없다. 그 걱정은 파서 버전 게이트로 옮겨 담았다.
TourAPI 경로(real_place.py)는 방금 받은 응답이라 저장된 값이 없어 그대로 정규화한다.

입력: content_id 목록.
출력: content_id를 키로 하는 PlaceDetails dict.
호출 시점: PLACE_DETAILS_SOURCE=supabase일 때 NearbyPlaceDetailsTool이 호출한다.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from app.domain.models import PlaceDetails, StoredPlaceDetail
from app.domain.operating_hours import resolve_operating_schedule
from app.providers.contracts import (
    ProviderResult,
    ProviderSource,
    ProviderStatus,
    provider_result,
)
from app.repositories.protocols import PlaceRepository

_PROVIDER_NAME = "supabase_places"


class SupabasePlaceDetailsProvider:
    """PlaceDetailsProvider와 BatchPlaceDetailsProvider를 함께 구현한다."""

    def __init__(self, repository: PlaceRepository) -> None:
        self._repository = repository

    async def get_details_batch(
        self,
        content_ids: list[str],
    ) -> ProviderResult[dict[str, PlaceDetails]]:
        """여러 content_id의 상세정보를 한 번의 DB 조회로 가져온다."""
        if not content_ids:
            return provider_result(
                {},
                source=ProviderSource.SUPABASE_PLACES,
                status=ProviderStatus.NO_DATA,
            )

        # 중복 content_id가 있어도 조회는 한 번만 하도록 정리한다.
        unique_ids = list(dict.fromkeys(content_ids))
        rows = await self._repository.get_active_place_details(unique_ids)

        details = {
            content_id: _to_place_details(row) for content_id, row in rows.items()
        }
        return provider_result(
            details,
            source=ProviderSource.SUPABASE_PLACES,
            status=_batch_status(requested=len(unique_ids), found=len(details)),
            detail_fetched_at=_latest_detail_fetched_at(rows.values()),
        )

    async def get_details(
        self, content_id: str, content_type_id: str
    ) -> ProviderResult[PlaceDetails]:
        """단건 조회. 기존 PlaceDetailsProvider 계약 호환을 위해 유지한다.

        content_type_id는 저장소 행의 값을 우선하므로 조회 조건으로 쓰지 않는다.
        """
        rows = await self._repository.get_active_place_details([content_id])
        row = rows.get(content_id)
        if row is None:
            return provider_result(
                _empty_details(content_id, content_type_id),
                source=ProviderSource.SUPABASE_PLACES,
                status=ProviderStatus.NO_DATA,
            )
        return provider_result(
            _to_place_details(row),
            source=ProviderSource.SUPABASE_PLACES,
            status=ProviderStatus.SUCCESS,
            detail_fetched_at=row.detail_fetched_at,
        )


def _batch_status(*, requested: int, found: int) -> ProviderStatus:
    if found == 0:
        return ProviderStatus.NO_DATA
    if found < requested:
        return ProviderStatus.PARTIAL
    return ProviderStatus.SUCCESS


def _latest_detail_fetched_at(
    rows: Iterable[StoredPlaceDetail],
) -> datetime | None:
    """배치 안에서 가장 최근 동기화 시각을 대표값으로 사용한다.

    행마다 동기화 시각이 다르므로 Provider 단위 metadata로는 대표값만 담는다.
    """
    values = [row.detail_fetched_at for row in rows if row.detail_fetched_at is not None]
    return max(values) if values else None


def _to_place_details(row: StoredPlaceDetail) -> PlaceDetails:
    """저장소 행을 기존 PlaceDetails 계약으로 변환한다."""
    return PlaceDetails(
        content_id=row.content_id,
        content_type_id=row.content_type_id,
        title=row.title,
        address=row.address,
        # overview/homepage는 detailCommon2에서만 오고 동기화 대상이 아니라 저장소에
        # 없다. 두 값이 필요한 경로는 HybridPlaceDetailsProvider가 담당한다.
        overview=None,
        homepage=None,
        telephone=row.info_center_raw,
        operating_hours=row.operating_hours_raw,
        rest_date=row.rest_date_raw,
        # raw_intro는 채우지 않는다. 저장소는 유형별 키를 이미 하나로 눌러 담았기
        # 때문에 원본 키를 복원할 수 없다 — 아래 정규화 필드가 그 값을 대신 나른다.
        raw_common={},
        raw_intro={},
        provider=_PROVIDER_NAME,
        parking=row.parking_info_raw,
        parking_fee=row.parking_fee_raw,
        fee=row.use_fee_raw,
        baby_carriage=row.baby_carriage_raw,
        pet=row.pet_raw,
        credit_card=row.credit_card_raw,
        restroom=row.restroom_raw,
        # firstimage2(작은 썸네일, thumbnail_url)보다 firstimage(원본 크기,
        # first_image_url)를 우선한다 — hybrid_place_details.py와 동일한 이유
        # (2026-08-13). first_image_url이 없는 장소만 thumbnail_url로 대체한다.
        thumbnail_url=row.first_image_url or row.thumbnail_url,
        # 적재 배치가 저장한 파싱 결과를 쓰되, 파서가 그 사이 바뀌었으면 원문을
        # 다시 읽는다. 예전에는 늘 원문을 다시 파싱했다 — 후보 수만큼 정규식이
        # 돌았고, 파싱을 LLM으로 옮기면 그 자리에서는 부를 수 없다.
        operating_schedule=resolve_operating_schedule(
            content_type_id=row.content_type_id,
            operating_hours=row.operating_hours_raw,
            rest_date=row.rest_date_raw,
            stored=row.operating_schedule_raw,
            stored_parser_version=row.operating_parser_version,
        ),
    )


def _empty_details(content_id: str, content_type_id: str) -> PlaceDetails:
    return PlaceDetails(
        content_id=content_id,
        content_type_id=content_type_id,
        title=None,
        address=None,
        overview=None,
        homepage=None,
        telephone=None,
        operating_hours=None,
        rest_date=None,
        raw_common={},
        raw_intro={},
        provider=_PROVIDER_NAME,
    )


__all__ = ["SupabasePlaceDetailsProvider"]
