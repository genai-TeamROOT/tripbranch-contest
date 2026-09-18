"""ResolveLocationTool이 실제로 호출한 Provider를 출력하는 수동 Inspection Test."""

from __future__ import annotations

import os

import httpx
import pytest

from app.config import settings
from app.domain.models import GeocodeResult, LocalSearchPlace
from app.providers.contracts import ProviderResult
from app.providers.factory import get_geocoding_provider, get_local_search_provider
from app.providers.protocols import GeocodingProvider, LocalSearchProvider
from app.tools.resolve_location import (
    ResolvedLocation,
    ResolveLocationQuery,
    ResolveLocationTool,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.inspection,
    pytest.mark.skipif(
        os.getenv("RUN_REAL_PROVIDER_INSPECTION") != "true",
        reason="RUN_REAL_PROVIDER_INSPECTION=true일 때만 실제 Provider 호출을 확인합니다.",
    ),
]


class RecordingGeocodingProvider:
    """실제 Geocoding 호출 여부와 입력만 남기는 검사 전용 래퍼."""

    def __init__(self, provider: GeocodingProvider) -> None:
        self._provider = provider
        self.calls: list[dict[str, object]] = []

    async def geocode(
        self, location_query: str, *, use_alias: bool = True
    ) -> ProviderResult[GeocodeResult]:
        self.calls.append({"query": location_query, "use_alias": use_alias})
        return await self._provider.geocode(location_query, use_alias=use_alias)


class RecordingLocalSearchProvider:
    """실제 Local Search 호출 여부와 입력, 그리고 후보 상세를 남기는 검사 전용 래퍼.

    calls에는 호출 입력만 담는다 — 후보 목록까지 섞으면 "몇 번 어떤 입력으로
    불렀는가"를 단언할 수 없다. 후보 상세는 candidates에 따로 모은다.
    """

    def __init__(self, provider: LocalSearchProvider) -> None:
        self._provider = provider
        self.calls: list[dict[str, object]] = []
        self.candidates: list[dict[str, object]] = []

    async def search_places_by_name(
        self, query: str, *, display: int = 5
    ) -> ProviderResult[tuple[LocalSearchPlace, ...]]:
        result = await self._provider.search_places_by_name(query, display=display)
        self.calls.append({"query": query, "display": display})
        self.candidates.extend(
            {
                "name": place.name,
                # 모호성 해소 규칙(지하철역 자동 선택 등)을 세우려면 category가 핵심이다.
                "category": place.category,
                "road_address": place.road_address,
                "address": place.address,
                "has_coordinates": place.latitude is not None
                and place.longitude is not None,
            }
            for place in result.data
        )
        return result


def _require_real_provider(mode: str, variable_name: str) -> None:
    if mode != "real":
        pytest.skip(f"{variable_name}=real 설정이 필요합니다.")


def _print_call_flow(
    *,
    query: str,
    geocoding: RecordingGeocodingProvider,
    local_search: RecordingLocalSearchProvider,
    location: ResolvedLocation | None,
    provider_sources: tuple[str, ...],
    status: str,
    error: dict[str, object] | None,
) -> None:
    print("=== Resolve Location Tool Inspection ===")
    print(f"query: {query}")
    print(f"local_search_calls: {local_search.calls}")
    print(f"local_search_candidates: {len(local_search.candidates)}건")
    for index, candidate in enumerate(local_search.candidates, start=1):
        print(
            f"  {index}. {candidate['name']}"
            f" | category={candidate['category']}"
            f" | coords={'O' if candidate['has_coordinates'] else 'X'}"
            f" | {candidate['road_address'] or candidate['address']}"
        )
    print(f"geocoding_calls: {geocoding.calls}")
    print(f"provider_sources: {provider_sources}")
    print(f"status: {status}")
    print(f"error: {error}")
    print(f"resolution_method: {location.resolution_method if location else None}")
    if location is not None:
        # 후보가 여러 건일 때 실제로 무엇이 선택됐는지 추론 없이 확인한다.
        print(
            f"resolved: {location.resolved_name}"
            f" ({location.latitude}, {location.longitude})"
            f" | {location.address}"
        )


async def test_inspect_place_name_calls_local_search_before_geocoding() -> None:
    """장소명은 Local Search가 성공하면 Geocoding을 호출하지 않는지 확인한다."""
    _require_real_provider(settings.resolved_local_search_provider, "LOCAL_SEARCH_PROVIDER")

    async with httpx.AsyncClient() as client:
        geocoding = RecordingGeocodingProvider(get_geocoding_provider(client))
        local_search = RecordingLocalSearchProvider(get_local_search_provider(client))
        result = await ResolveLocationTool(
            geocoding,
            place_repository=None,
            local_search_provider=local_search,
        ).execute(ResolveLocationQuery(os.getenv("INSPECTION_PLACE_QUERY", "안국역")))

    _print_call_flow(
        query=os.getenv("INSPECTION_PLACE_QUERY", "안국역"),
        geocoding=geocoding,
        local_search=local_search,
        location=result.location,
        provider_sources=tuple(metadata.source.value for metadata in result.provider_metadata),
        status=result.status.value,
        error=(
            {
                "code": result.error.code,
                "cause": result.error.cause,
                "details": result.error.details,
            }
            if result.error is not None
            else None
        ),
    )
    assert local_search.calls == [
        {"query": os.getenv("INSPECTION_PLACE_QUERY", "안국역"), "display": 5}
    ]
    assert geocoding.calls == []


async def test_inspect_address_calls_geocoding_directly() -> None:
    """주소는 Local Search를 건너뛰고 Geocoding만 호출하는지 확인한다."""
    _require_real_provider(settings.resolved_geocoding_provider, "GEOCODING_PROVIDER")
    address = os.getenv("INSPECTION_ADDRESS_QUERY", "서울특별시 종로구 인사동길 44")

    async with httpx.AsyncClient() as client:
        geocoding = RecordingGeocodingProvider(get_geocoding_provider(client))
        local_search = RecordingLocalSearchProvider(get_local_search_provider(client))
        result = await ResolveLocationTool(
            geocoding,
            place_repository=None,
            local_search_provider=local_search,
        ).execute(ResolveLocationQuery(address))

    _print_call_flow(
        query=address,
        geocoding=geocoding,
        local_search=local_search,
        location=result.location,
        provider_sources=tuple(metadata.source.value for metadata in result.provider_metadata),
        status=result.status.value,
        error=(
            {
                "code": result.error.code,
                "cause": result.error.cause,
                "details": result.error.details,
            }
            if result.error is not None
            else None
        ),
    )
    assert local_search.calls == []
    assert geocoding.calls == [{"query": address, "use_alias": False}]
