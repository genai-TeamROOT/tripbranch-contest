"""위치 설정 화면의 장소 검색 라우트 테스트."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.domain.models import LocalSearchPlace
from app.main import app
from app.providers.contracts import ProviderSource, provider_result
from app.routes import place_search as route
from app.tools.contracts import ToolError, ToolStatus
from app.tools.resolve_location import (
    LocationPurpose,
    ResolutionConfidence,
    ResolutionMethod,
    ResolvedLocation,
    ResolveLocationQuery,
    ResolveLocationResult,
)

_URL = "/api/places/search"

# 안국역(종로구). service_area의 서울 경계 안이다.
_SEOUL_LATITUDE = 37.5765389
_SEOUL_LONGITUDE = 126.9856
# 해운대(부산). 지원 지역 밖이다.
_BUSAN_LATITUDE = 35.1587
_BUSAN_LONGITUDE = 129.1604


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class _FakeProvider:
    """검색어와 display를 그대로 기록해 두는 지역 검색 대역."""

    def __init__(self, places: tuple[LocalSearchPlace, ...]) -> None:
        self._places = places
        self.received_query: str | None = None
        self.received_display: int | None = None

    async def search_places_by_name(self, query: str, *, display: int = 5):
        self.received_query = query
        self.received_display = display
        return provider_result(self._places, source=ProviderSource.FAKE_LOCAL_SEARCH)


def _install(monkeypatch, *places: LocalSearchPlace) -> _FakeProvider:
    provider = _FakeProvider(places)
    monkeypatch.setattr(route, "get_local_search_provider", lambda client: provider)
    return provider


def _place(name: str, latitude: float | None, longitude: float | None) -> LocalSearchPlace:
    return LocalSearchPlace(
        name=name,
        address=f"{name} 지번주소",
        road_address=f"{name} 도로명주소",
        category="지하철,전철",
        latitude=latitude,
        longitude=longitude,
    )


def test_seoul_place_is_returned_with_name_and_coordinates(client, monkeypatch) -> None:
    _install(monkeypatch, _place("안국역", _SEOUL_LATITUDE, _SEOUL_LONGITUDE))

    response = client.get(_URL, params={"query": "안국역"})

    assert response.status_code == 200
    body = response.json()
    assert body["outside_service_area_count"] == 0
    assert [place["name"] for place in body["places"]] == ["안국역"]
    assert body["places"][0]["road_address"] == "안국역 도로명주소"
    assert body["places"][0]["latitude"] == pytest.approx(_SEOUL_LATITUDE)


def test_place_outside_seoul_is_dropped_and_counted(client, monkeypatch) -> None:
    """서울 밖은 후보에서 빼되 개수를 남긴다 — 화면이 이유를 말해야 한다."""
    _install(
        monkeypatch,
        _place("서울역", _SEOUL_LATITUDE, _SEOUL_LONGITUDE),
        _place("해운대역", _BUSAN_LATITUDE, _BUSAN_LONGITUDE),
    )

    body = client.get(_URL, params={"query": "역"}).json()

    assert [place["name"] for place in body["places"]] == ["서울역"]
    assert body["outside_service_area_count"] == 1


def test_place_without_coordinates_is_dropped_without_counting(client, monkeypatch) -> None:
    """좌표 없는 후보는 검색 위치로 쓸 수 없지만 서울 밖이라 뺀 것은 아니다."""
    _install(monkeypatch, _place("좌표없는곳", None, None))

    body = client.get(_URL, params={"query": "좌표없는곳"}).json()

    assert body["places"] == []
    assert body["outside_service_area_count"] == 0


def test_query_is_scoped_to_seoul_before_calling_provider(client, monkeypatch) -> None:
    """지역 검색은 전국 5건뿐이라 서울을 붙이지 않으면 지방 결과에 밀린다."""
    provider = _install(monkeypatch, _place("중앙동", _SEOUL_LATITUDE, _SEOUL_LONGITUDE))

    client.get(_URL, params={"query": "중앙동 카페"})

    assert provider.received_query == "서울 중앙동 카페"
    assert provider.received_display == 5


@pytest.mark.parametrize("query", ["서울 중앙동", "종로구 카페", "강남구청역"])
def test_query_already_pointing_at_seoul_is_left_alone(query: str) -> None:
    assert route.seoul_scoped_query(query) == query


class _FakeResolveTool:
    """위치 해석 사다리 대역. 라우터가 무엇을 넘기고 무엇을 읽는지만 본다."""

    received_queries: list[str] = []

    def __init__(self, **_kwargs) -> None:
        pass

    result = ResolveLocationResult(status=ToolStatus.NO_DATA, location=None, error=None)

    async def execute(self, query: ResolveLocationQuery) -> ResolveLocationResult:
        type(self).received_queries.append(query.location_query)
        assert query.purpose is LocationPurpose.SEARCH_CENTER
        return type(self).result


def _install_resolve_tool(monkeypatch, result: ResolveLocationResult) -> type[_FakeResolveTool]:
    tool = type("_ScopedFakeResolveTool", (_FakeResolveTool,), {})
    tool.received_queries = []
    tool.result = result
    monkeypatch.setattr(route, "ResolveLocationTool", tool)
    return tool


def _resolved(name: str, latitude: float, longitude: float) -> ResolveLocationResult:
    return ResolveLocationResult(
        status=ToolStatus.SUCCESS,
        location=ResolvedLocation(
            requested_query=name,
            provider_query=name,
            resolved_name=name,
            latitude=latitude,
            longitude=longitude,
            resolution_method=ResolutionMethod.DIRECT,
            confidence=ResolutionConfidence.EXACT,
            address="서울특별시 종로구 율곡로 62",
        ),
        error=None,
    )


def test_address_falls_back_to_the_location_ladder(client, monkeypatch) -> None:
    """지역 검색은 상호만 찾는다 - 주소는 사다리(ResolveLocationTool)가 푼다."""
    _install(monkeypatch)  # 지역 검색은 0건
    tool = _install_resolve_tool(
        monkeypatch, _resolved("서울특별시 종로구 율곡로 62", _SEOUL_LATITUDE, _SEOUL_LONGITUDE)
    )

    body = client.get(_URL, params={"query": "율곡로 62"}).json()

    assert [place["name"] for place in body["places"]] == ["서울특별시 종로구 율곡로 62"]
    assert body["places"][0]["address"] == "서울특별시 종로구 율곡로 62"
    # 사다리에는 "서울"을 덧붙이지 않은 원래 검색어를 넘긴다.
    assert tool.received_queries == ["율곡로 62"]


def test_ladder_is_not_called_when_local_search_found_places(client, monkeypatch) -> None:
    """상호로 찾았으면 사다리를 부르지 않는다 - 외부 호출을 한 번 더 낼 이유가 없다."""
    _install(monkeypatch, _place("안국역", _SEOUL_LATITUDE, _SEOUL_LONGITUDE))
    tool = _install_resolve_tool(
        monkeypatch, _resolved("안국역", _SEOUL_LATITUDE, _SEOUL_LONGITUDE)
    )

    client.get(_URL, params={"query": "안국역"})

    assert tool.received_queries == []


def test_ladder_result_outside_seoul_is_counted(client, monkeypatch) -> None:
    _install(monkeypatch)
    _install_resolve_tool(
        monkeypatch,
        ResolveLocationResult(
            status=ToolStatus.UNSUPPORTED,
            location=None,
            error=ToolError(
                code="unsupported_region",
                message="지원하지 않는 지역이에요.",
                cause="outside_supported_region",
                retryable=False,
            ),
        ),
    )

    body = client.get(_URL, params={"query": "해운대해수욕장로 264"}).json()

    assert body["places"] == []
    assert body["outside_service_area_count"] == 1


def test_ladder_failure_is_reported_as_a_failure(client, monkeypatch) -> None:
    """외부 조회 실패를 "찾은 곳이 없어요"로 감추지 않는다 - 사용자가 검색어만 고치게 된다."""
    _install(monkeypatch)
    _install_resolve_tool(
        monkeypatch,
        ResolveLocationResult(
            status=ToolStatus.UNAVAILABLE,
            location=None,
            error=ToolError(
                code="provider_unavailable",
                message="지도 서비스를 사용할 수 없어요.",
                cause="upstream_error",
                retryable=True,
            ),
        ),
    )

    response = client.get(_URL, params={"query": "율곡로 62"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "provider_unavailable"


def test_blank_query_is_rejected_before_calling_provider(client, monkeypatch) -> None:
    """공백만 보내면 외부 API를 부르지 않고 끊는다."""
    provider = _install(monkeypatch)

    response = client.get(_URL, params={"query": "  "})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert provider.received_query is None


def test_missing_query_is_rejected(client) -> None:
    assert client.get(_URL).status_code == 422
