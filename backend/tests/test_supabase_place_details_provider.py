"""Supabase 상세조회 Provider와 Tool 배치 경로 계약 테스트."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.models import PlaceDetails, StoredPlaceDetail
from app.domain.operating_hours import OperatingAvailability
from app.providers.contracts import (
    ProviderSource,
    ProviderStatus,
    provider_result,
)
from app.providers.supabase_place_details import SupabasePlaceDetailsProvider
from app.repositories.supabase_places import SupabaseRepositoryError
from app.schemas import PlaceCandidate
from app.tools.nearby_place_details import (
    DetailStatus,
    NearbyPlaceDetailsQuery,
    NearbyPlaceDetailsTool,
    ToolStatus,
)

_FETCHED_AT = datetime(2026, 7, 20, 3, 0, tzinfo=UTC)


def _row(
    content_id: str,
    *,
    operating_hours_raw: str | None = "09:00~18:00",
    rest_date_raw: str | None = "매주 월요일",
    content_type_id: str = "12",
    detail_fetched_at: datetime | None = _FETCHED_AT,
) -> StoredPlaceDetail:
    return StoredPlaceDetail(
        content_id=content_id,
        content_type_id=content_type_id,
        title=f"장소 {content_id}",
        address="서울특별시 종로구",
        operating_hours_raw=operating_hours_raw,
        rest_date_raw=rest_date_raw,
        detail_fetch_status="success",
        detail_fetched_at=detail_fetched_at,
        source_modified_at=None,
    )


class FakeRepository:
    """is_active=true 행만 담고 있는 저장소 스텁."""

    def __init__(
        self,
        rows: dict[str, StoredPlaceDetail],
        error: Exception | None = None,
    ) -> None:
        self.rows = rows
        self.error = error
        self.calls: list[list[str]] = []

    async def get_active_place_details(
        self, content_ids
    ) -> dict[str, StoredPlaceDetail]:
        self.calls.append(list(content_ids))
        if self.error is not None:
            raise self.error
        return {
            content_id: self.rows[content_id]
            for content_id in content_ids
            if content_id in self.rows
        }


def _candidate(index: int, content_type_id: str | None = "12") -> PlaceCandidate:
    return PlaceCandidate(
        place_id=f"place-{index}",
        content_type_id=content_type_id,
        name=f"장소 {index}",
        category="attraction",
        latitude=37.5 + index / 1000,
        longitude=127.0,
        raw_source="test",
    )


class SearchProvider:
    def __init__(self, candidates: list[PlaceCandidate]) -> None:
        self.candidates = candidates

    async def search_places(
        self,
        latitude: float,
        longitude: float,
        preferred_categories: list[str],
        search_radius_km: float,
        region_code: str | None = None,
        district_code: str | None = None,
        category_filter=None,
        limit: int = 10,
    ):
        return provider_result(
            self.candidates[:limit],
            source=ProviderSource.TOUR_API_PLACE,
        )


class SingleDetailsProvider:
    """다건 계약을 구현하지 않는 기존 Provider(TourAPI/Fake) 대역."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get_details(self, content_id: str, content_type_id: str):
        self.calls.append(content_id)
        return provider_result(
            PlaceDetails(
                content_id=content_id,
                content_type_id=content_type_id,
                title=f"장소 {content_id}",
                address=None,
                overview="상세정보",
                homepage=None,
                telephone=None,
                operating_hours="09:00~18:00",
                rest_date=None,
                raw_common={},
                raw_intro={},
                provider="test",
            ),
            source=ProviderSource.TOUR_API_PLACE,
        )


def _query(limit: int = 3) -> NearbyPlaceDetailsQuery:
    return NearbyPlaceDetailsQuery(
        latitude=37.5,
        longitude=127.0,
        limit=limit,
    )


@pytest.mark.asyncio
async def test_batch_lookup_returns_all_requested_places() -> None:
    repository = FakeRepository({"a": _row("a"), "b": _row("b")})
    provider = SupabasePlaceDetailsProvider(repository)

    result = await provider.get_details_batch(["a", "b"])

    assert set(result.data) == {"a", "b"}
    assert result.metadata.status is ProviderStatus.SUCCESS
    assert result.metadata.source is ProviderSource.SUPABASE_PLACES
    # 후보가 여러 건이어도 저장소 조회는 한 번만 일어난다.
    assert repository.calls == [["a", "b"]]


@pytest.mark.asyncio
async def test_batch_lookup_marks_partial_when_some_content_ids_missing() -> None:
    repository = FakeRepository({"a": _row("a")})
    provider = SupabasePlaceDetailsProvider(repository)

    result = await provider.get_details_batch(["a", "missing"])

    assert set(result.data) == {"a"}
    assert result.metadata.status is ProviderStatus.PARTIAL


@pytest.mark.asyncio
async def test_inactive_place_is_excluded_as_missing() -> None:
    # 저장소는 is_active=true만 반환하므로 비활성 장소는 조회 결과에 없다.
    repository = FakeRepository({"active": _row("active")})
    provider = SupabasePlaceDetailsProvider(repository)

    result = await provider.get_details_batch(["active", "inactive"])

    assert "inactive" not in result.data
    assert result.metadata.status is ProviderStatus.PARTIAL


@pytest.mark.asyncio
async def test_null_operating_hours_yields_unknown_schedule() -> None:
    repository = FakeRepository(
        {"a": _row("a", operating_hours_raw=None, rest_date_raw=None)}
    )
    provider = SupabasePlaceDetailsProvider(repository)

    result = await provider.get_details_batch(["a"])

    details = result.data["a"]
    # 운영정보가 없어도 장소 데이터 자체는 반환하고, 운영시간만 unknown으로 남긴다.
    assert details.title == "장소 a"
    assert details.operating_hours is None
    assert details.operating_schedule is not None
    assert details.operating_schedule.availability is OperatingAvailability.UNKNOWN


@pytest.mark.asyncio
async def test_raw_text_is_renormalized_into_schedule() -> None:
    repository = FakeRepository({"a": _row("a")})
    provider = SupabasePlaceDetailsProvider(repository)

    result = await provider.get_details_batch(["a"])

    schedule = result.data["a"].operating_schedule
    assert schedule is not None
    # DB의 operating_schedule JSON이 아니라 원문에서 다시 정규화한 결과여야 한다.
    assert schedule.availability is OperatingAvailability.SCHEDULED
    assert schedule.raw_operating_hours == "09:00~18:00"
    assert [rule.weekdays for rule in schedule.closure_rules] == [frozenset({0})]


@pytest.mark.asyncio
async def test_batch_metadata_separates_retrieved_at_and_detail_fetched_at() -> None:
    repository = FakeRepository({"a": _row("a")})
    provider = SupabasePlaceDetailsProvider(repository)

    result = await provider.get_details_batch(["a"])

    assert result.metadata.detail_fetched_at == _FETCHED_AT
    assert result.metadata.retrieved_at > _FETCHED_AT


@pytest.mark.asyncio
async def test_repository_error_propagates_as_app_error() -> None:
    repository = FakeRepository({}, error=SupabaseRepositoryError("HTTP 500"))
    provider = SupabasePlaceDetailsProvider(repository)

    with pytest.raises(SupabaseRepositoryError):
        await provider.get_details_batch(["a"])


@pytest.mark.asyncio
async def test_tool_uses_batch_lookup_and_preserves_candidate_order() -> None:
    candidates = [_candidate(index) for index in range(3)]
    repository = FakeRepository(
        {candidate.place_id: _row(candidate.place_id) for candidate in candidates}
    )
    tool = NearbyPlaceDetailsTool(
        search_provider=SearchProvider(candidates),
        details_provider=SupabasePlaceDetailsProvider(repository),
    )

    result = await tool.execute(_query())

    assert [item.candidate.place_id for item in result.places] == [
        "place-0",
        "place-1",
        "place-2",
    ]
    assert result.status is ToolStatus.SUCCESS
    assert len(repository.calls) == 1


@pytest.mark.asyncio
async def test_tool_returns_partial_when_some_places_missing_in_db() -> None:
    candidates = [_candidate(index) for index in range(3)]
    repository = FakeRepository({"place-0": _row("place-0")})
    tool = NearbyPlaceDetailsTool(
        search_provider=SearchProvider(candidates),
        details_provider=SupabasePlaceDetailsProvider(repository),
    )

    result = await tool.execute(_query())

    assert result.status is ToolStatus.PARTIAL
    statuses = [item.detail_status for item in result.places]
    assert statuses == [
        DetailStatus.SUCCESS,
        DetailStatus.NO_DATA,
        DetailStatus.NO_DATA,
    ]


@pytest.mark.asyncio
async def test_tool_returns_no_data_when_no_place_found_in_db() -> None:
    candidates = [_candidate(index) for index in range(2)]
    tool = NearbyPlaceDetailsTool(
        search_provider=SearchProvider(candidates),
        details_provider=SupabasePlaceDetailsProvider(FakeRepository({})),
    )

    result = await tool.execute(_query())

    assert result.status is ToolStatus.NO_DATA


@pytest.mark.asyncio
async def test_tool_maps_repository_failure_to_unavailable() -> None:
    candidates = [_candidate(index) for index in range(2)]
    repository = FakeRepository({}, error=SupabaseRepositoryError("HTTP 503"))
    tool = NearbyPlaceDetailsTool(
        search_provider=SearchProvider(candidates),
        details_provider=SupabasePlaceDetailsProvider(repository),
    )

    result = await tool.execute(_query())

    assert result.status is ToolStatus.UNAVAILABLE
    assert all(
        item.detail_status is DetailStatus.UNAVAILABLE for item in result.places
    )
    # 요청 시 TourAPI fallback은 하지 않는다.
    assert all(item.details is None for item in result.places)


@pytest.mark.asyncio
async def test_tool_skips_candidate_without_content_type_id() -> None:
    candidates = [_candidate(0), _candidate(1, content_type_id=None)]
    repository = FakeRepository(
        {"place-0": _row("place-0"), "place-1": _row("place-1")}
    )
    tool = NearbyPlaceDetailsTool(
        search_provider=SearchProvider(candidates),
        details_provider=SupabasePlaceDetailsProvider(repository),
    )

    result = await tool.execute(_query())

    assert result.places[1].detail_status is DetailStatus.NO_DATA
    assert result.places[1].error_code == "missing_content_type_id"
    # 원본(TourAPI)에 유형이 없는 장소는 저장소 조회 대상에서도 제외한다.
    assert repository.calls == [["place-0"]]


@pytest.mark.asyncio
async def test_tool_falls_back_to_single_lookup_for_legacy_provider() -> None:
    candidates = [_candidate(index) for index in range(2)]
    details_provider = SingleDetailsProvider()
    tool = NearbyPlaceDetailsTool(
        search_provider=SearchProvider(candidates),
        details_provider=details_provider,
    )

    result = await tool.execute(_query())

    # 다건 계약 미지원 Provider는 기존 단건 병렬 조회 경로를 그대로 탄다.
    assert sorted(details_provider.calls) == ["place-0", "place-1"]
    assert result.status is ToolStatus.SUCCESS
