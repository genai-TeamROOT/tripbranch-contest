"""장소 동기화 저장소가 제공해야 하는 비동기 계약."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.models import (
    AccessibilityNeed,
    BarrierFreePlaceRow,
    DistrictPlaceRow,
    PlaceBarrierFreeDetails,
    PlaceCategoryFilter,
    PlaceEvidenceMatch,
    PlaceMoodMatch,
    PlaceMoodProfile,
    PlacePhoto,
    PublicToilet,
    StoredMunicipalParkingLot,
    StoredPlaceDetail,
    StoredPlaceLocation,
    StoredPlaceState,
    TourPlaceRecord,
)


class PlaceLocationRepository(Protocol):
    """장소명으로 저장된 TourAPI 기준 위치를 찾는 읽기 전용 계약."""

    async def find_active_places_by_name(
        self, name: str
    ) -> tuple[StoredPlaceLocation, ...]: ...


class MunicipalParkingCatalogRepository(Protocol):
    """공영주차장 코드와 한 번 보강한 좌표를 연결하는 읽기/동기화 계약."""

    async def find_by_codes(
        self, codes: Sequence[str]
    ) -> dict[str, StoredMunicipalParkingLot]: ...

    async def upsert_lots(self, lots: Sequence[StoredMunicipalParkingLot]) -> None: ...


class PublicToiletRepository(Protocol):
    """적재된 공중화장실 위치를 좌표로 찾는 읽기/동기화 계약."""

    async def find_near(
        self, latitude: float, longitude: float, *, radius_km: float, limit: int
    ) -> tuple[PublicToilet, ...]: ...

    async def upsert_toilets(self, toilets: Sequence[PublicToilet]) -> None: ...


class ConcentrationMappingRepository(Protocol):
    """집중률 매핑이 있는 장소 목록을 읽는 읽기 전용 계약."""

    async def find_concentration_mapped_places(
        self,
    ) -> tuple[StoredPlaceLocation, ...]: ...


class PlaceDetailsReadRepository(Protocol):
    """content_id로 상세 행만 읽는 읽기 전용 계약.

    동기화용 PlaceRepository와 분리한 이유는 소비자가 읽기만 하기 때문이다 —
    카드 조립처럼 조회만 하는 쪽이 동기화 메서드 전부를 구현한 저장소를 요구하면
    fake를 만들 때 쓰지도 않는 메서드를 채워야 한다.
    """

    async def get_active_place_details(
        self,
        content_ids: Sequence[str],
        *,
        include_barrier_free: bool = False,
    ) -> dict[str, StoredPlaceDetail]: ...


class PlaceRepository(Protocol):
    async def create_sync_run(self, area_code: str, district_code: str) -> UUID: ...

    async def try_acquire_sync_lock(
        self,
        area_code: str,
        district_code: str,
        sync_run_id: UUID,
        lock_ttl: str = "2 hours",
    ) -> bool: ...

    async def release_sync_lock(
        self,
        area_code: str,
        district_code: str,
        sync_run_id: UUID,
    ) -> bool: ...

    async def get_region_place_states(
        self,
        area_code: str,
        district_code: str,
    ) -> dict[str, StoredPlaceState]: ...

    async def get_active_place_details(
        self,
        content_ids: Sequence[str],
        *,
        include_barrier_free: bool = False,
    ) -> dict[str, StoredPlaceDetail]: ...

    async def upsert_place_list(
        self,
        places: Sequence[TourPlaceRecord],
        existing_states: Mapping[str, StoredPlaceState],
        sync_run_id: UUID,
        fetched_at: datetime,
    ) -> None: ...

    async def update_operating_details(
        self,
        content_id: str,
        operating_hours_raw: str | None,
        rest_date_raw: str | None,
        operating_schedule: Mapping[str, object] | None,
        parse_status: str,
        parser_version: str,
        fetched_at: datetime,
        parking_info_raw: str | None = None,
        parking_fee_raw: str | None = None,
        use_fee_raw: str | None = None,
        discount_info_raw: str | None = None,
        info_center_raw: str | None = None,
        baby_carriage_raw: str | None = None,
        pet_raw: str | None = None,
        credit_card_raw: str | None = None,
        restroom_raw: str | None = None,
    ) -> None: ...

    async def list_barrier_free_fetched_at(
        self,
        content_ids: Sequence[str],
    ) -> dict[str, datetime]: ...

    async def upsert_barrier_free_details(
        self,
        details: Sequence[PlaceBarrierFreeDetails],
        fetched_at: datetime,
    ) -> None: ...

    async def update_parsed_schedule(
        self,
        content_id: str,
        operating_schedule: Mapping[str, object] | None,
        parse_status: str,
        parser_version: str,
    ) -> None: ...

    async def mark_detail_failed(self, content_id: str, error_code: str) -> None: ...

    async def reactivate_source_missing_places(
        self,
        content_ids: Sequence[str],
    ) -> int: ...

    async def deactivate_unseen_places(
        self,
        area_code: str,
        district_code: str,
        sync_run_id: UUID,
        inactive_at: datetime,
    ) -> int: ...

    async def complete_sync_run(
        self,
        sync_run_id: UUID,
        *,
        status: str,
        api_total_count: int | None,
        processed_count: int,
        success_count: int,
        failed_count: int,
        new_count: int,
        updated_count: int,
        deactivated_count: int,
        detail_attempted_count: int,
        error_summary: Mapping[str, object] | None = None,
        completed_at: datetime,
    ) -> None: ...


class DistrictPlaceRepository(Protocol):
    """구 하나의 활성 장소를 전부 읽는 읽기 전용 계약(D-119).

    다른 후보 수집 계약과 나눈 이유는 **모으는 단위가 다르기** 때문이다. 좌표와
    반경을 받지 않고 구 코드 하나만 받는다 — 구 단위 요청은 기준점이 없다.

    개수 제한도 받지 않는다. 몇 곳을 쓸지 고르는 일은 저장소가 아니라
    `agent_context.district_selection`이 한다. 여기서 앞의 N곳만 잘라 주면 그
    자름이 무슨 순서인지가 결과를 정해버리는데, 구 단위에는 의미 있는 순서가 없다.
    """

    async def list_active_places_in_district(
        self, district_code: str
    ) -> tuple[DistrictPlaceRow, ...]:
        """그 구의 활성 장소 전량. 좌표가 말이 안 되는 행은 빼고 돌려준다."""
        ...


class BarrierFreePlaceSearchRepository(Protocol):
    """무장애 편의를 요구한 요청의 후보를 찾는 읽기 전용 계약.

    PlaceLocationRepository와 계약을 나눈다. 그쪽은 이름으로 한 곳을 찾는
    검색 중심점 해석이고, 이쪽은 좌표 둘레에서 조건을 만족하는 여러 곳을
    거리순으로 모으는 후보 수집이다. 쓰는 테이블도 다르다 — 이 계약만
    place_barrier_free를 읽는다.
    """

    async def search_places_barrier_free(
        self,
        *,
        latitude: float,
        longitude: float,
        radius_km: float,
        needs: Sequence[AccessibilityNeed],
        category_filter: PlaceCategoryFilter | None = None,
        limit: int,
    ) -> tuple[BarrierFreePlaceRow, ...]:
        """조건을 **전부** 만족하는 장소를 반경 안에서 거리순으로 돌려준다.

        `needs`가 비어 있으면 구현체는 ValueError를 던진다. 조건 없는 전체 반경
        검색으로 조용히 바뀌면, 무장애를 요구한 요청이 조건이 빠진 결과를 받고도
        그 사실을 알 수 없다.
        """
        ...


class PlaceEvidenceRepository(Protocol):
    """취향 근거를 벡터 검색으로 찾는 읽기 전용 계약.

    후보를 반드시 좁혀서 부른다 — RPC가 후보 상한(500)을 강제하고, 좁히지 않으면
    40,389행 전체를 훑어 6~9초가 걸린다(2026-08-18 실측).
    """

    async def search_place_evidence(
        self,
        query_embedding: Sequence[float],
        candidate_content_ids: Sequence[str],
        *,
        match_count: int,
        min_similarity: float,
    ) -> tuple[PlaceEvidenceMatch, ...]: ...


class PlaceMoodRepository(Protocol):
    """장소 사진의 분위기 벡터를 읽는 읽기 전용 계약.

    경로가 둘이고 비용이 크게 다르다.

      find_mood_profiles   발화 경로. 미리 계산된 축 점수만 읽는다. 벡터 연산이
                           없고 임베딩 모델도 필요 없다.
      search_place_mood    사진 경로. 올린 사진을 임베딩해 최근접 장소를 찾는다.
                           질의 벡터를 만들려면 SigLIP이 있어야 한다.

    분위기 벡터는 텍스트 임베딩(place_embeddings)과 좌표계가 다르다. 둘 다
    768차원이지만 한쪽은 한국어 문장, 다른 쪽은 사진이 사는 공간이라 섞으면
    계산은 되고 뜻이 없다. 그래서 PlaceEvidenceRepository와 계약을 나눈다.
    """

    async def find_mood_profiles(
        self,
        content_ids: Sequence[str],
    ) -> dict[str, PlaceMoodProfile]: ...

    async def search_place_mood(
        self,
        query_embedding: Sequence[float],
        candidate_content_ids: Sequence[str] | None,
        *,
        match_count: int,
        min_similarity: float,
        latitude: float | None = None,
        longitude: float | None = None,
        radius_km: float | None = None,
        mean_center: bool = False,
        axis_weight: float = 1.0,
    ) -> tuple[PlaceMoodMatch, ...]: ...

    async def find_first_photo_urls(
        self,
        content_ids: Sequence[str],
    ) -> dict[str, str]: ...


class PlacePhotoRepository(Protocol):
    """장소 사진 목록을 읽는 읽기 전용 계약.

    저장소는 PlaceMoodRepository와 같은 테이블(place_image_embeddings)이지만
    계약을 나눈다. 사진을 보여주는 데는 임베딩도 SigLIP도 필요 없고, 분위기
    검색이 꺼져 있어도 상세 화면의 사진은 나와야 한다.
    """

    async def find_place_photos(
        self,
        content_ids: Sequence[str],
    ) -> dict[str, tuple[PlacePhoto, ...]]: ...
