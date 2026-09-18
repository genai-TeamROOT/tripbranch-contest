"""카카오맵 대중교통 경로 Provider 회귀 테스트.

실 API 실측(2026-08-20, 경복궁 기준 7개 구간)에서 확인한 응답 성질을 고정한다.
"""

from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from app.domain.travel_route import (
    GeoCoordinate,
    RouteDestination,
    RouteSource,
    RouteStatus,
    TravelMode,
)
from app.providers.contracts import ProviderSource, ProviderStatus
from app.providers.kakao_transit_route import (
    KAKAO_MAP_TRANSIT_ROUTE_URL,
    FakeTransitRouteProvider,
    RealKakaoTransitRouteProvider,
)


def _destinations() -> tuple[RouteDestination, ...]:
    return (
        RouteDestination("first", GeoCoordinate(37.571, 126.981)),
        RouteDestination("second", GeoCoordinate(37.572, 126.982)),
    )


def _route(total_time: int, total_distance: int) -> dict[str, object]:
    return {
        "properties": {
            "type": "BUS_AND_SUBWAY",
            "totalTime": total_time,
            "totalDistance": total_distance,
            "transfers": 1,
            "fare": {"value": 1550},
        },
        "steps": [],
    }


@pytest.mark.asyncio
async def test_transit_route_calls_single_route_api_for_each_destination() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert str(request.url).startswith(KAKAO_MAP_TRANSIT_ROUTE_URL)
        assert request.headers["Authorization"] == "KakaoAK test-key"
        query = parse_qs(request.url.query.decode())
        assert query["start_x"] == ["126.98"]
        assert query["start_y"] == ["37.57"]
        assert query["input_coord"] == ["WGS84"]
        assert query["output_coord"] == ["WGS84"]
        # 출발 시각 파라미터는 이 API에 없다. 보내는 순간 400이 된다.
        assert "departure_time" not in query
        index = 1 if query["end_x"] == ["126.981"] else 2
        return httpx.Response(
            200,
            json={"status": "OK", "routes": [_route(index * 90, index * 100)]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await RealKakaoTransitRouteProvider("test-key", client).get_routes(
            GeoCoordinate(37.57, 126.98), _destinations()
        )

    assert len(requests) == 2
    assert [route.place_id for route in result.data.routes] == ["first", "second"]
    assert [route.distance_m for route in result.data.routes] == [100, 200]
    assert [route.duration_seconds for route in result.data.routes] == [90, 180]
    assert all(route.mode is TravelMode.TRANSIT for route in result.data.routes)
    assert all(route.source is RouteSource.KAKAO_TRANSIT for route in result.data.routes)
    assert result.metadata.source is ProviderSource.KAKAO_TRANSIT_ROUTE
    assert result.metadata.status is ProviderStatus.SUCCESS


@pytest.mark.asyncio
async def test_transit_route_picks_the_fastest_route_not_the_first() -> None:
    """`routes[]`는 소요시간 순으로 정렬돼 있지 않다.

    실측 배열(경복궁→남산서울타워)이 `[2401, 2398, 2983, 3991, ...]`이었다. 첫
    원소를 쓰면 최적이 아닌 경로가 실측값으로 나간다. 최악 경로는 최소의 2배가
    넘어서(40분 대 81분) 조용히 틀린 값이 된다.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "OK",
                "routes": [
                    _route(2401, 8621),
                    _route(2398, 8624),
                    _route(2983, 9309),
                    _route(4858, 12000),
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await RealKakaoTransitRouteProvider("test-key", client).get_routes(
            GeoCoordinate(37.57, 126.98), (RouteDestination("p1", GeoCoordinate(37.55, 126.98)),)
        )

    route = result.data.routes[0]
    assert route.duration_seconds == 2398
    # 거리도 그 경로의 값이어야 한다 — 최소 시간과 최소 거리가 다른 경로일 수 있다.
    assert route.distance_m == 8624


@pytest.mark.asyncio
async def test_transit_route_treats_equal_points_as_zero_distance() -> None:
    """출발=도착은 HTTP 200 `EQUAL_POINTS`에 `routes: []`로 온다.

    routes가 비어 있다고 no_data로 떨어뜨리면 같은 장소를 "경로 없음"으로 답한다.
    카카오 도보의 SAME_POINT와 같은 결과여야 한다.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "EQUAL_POINTS", "routes": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await RealKakaoTransitRouteProvider("test-key", client).get_routes(
            GeoCoordinate(37.57, 126.98), (RouteDestination("p1", GeoCoordinate(37.57, 126.98)),)
        )

    route = result.data.routes[0]
    assert route.status is RouteStatus.SUCCESS
    assert route.distance_m == 0
    assert route.duration_seconds == 0


@pytest.mark.asyncio
async def test_transit_route_reports_no_data_for_short_distance_no_results() -> None:
    """근거리는 `status: "NO_RESULTS"`로 온다 — 실 API 실측(2026-08-20).

    안국역 기준 201m 지점이 이 응답이었다. 279m는 정상 조회됐으므로 경계는
    250m 안팎이다. 걸어서 3분 거리에 버스·지하철 경로가 없는 것이라 장애가
    아니라 값 없음이다.

    "근처" 검색은 후보가 기준점에 붙어 있어 이 응답이 흔하다. `_consistent_routes()`가
    후보 하나만 실측이 없어도 전체를 직선거리로 내리므로(scoring.py), 이 분기가
    UNAVAILABLE로 새면 조회 실패로 잘못 집계된다.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "NO_RESULTS",
                "properties": {"total": 0, "bus": 0, "subway": 0, "busAndSubway": 0},
                "routes": [],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await RealKakaoTransitRouteProvider("test-key", client).get_routes(
            GeoCoordinate(37.5765, 126.9853),
            (RouteDestination("p1", GeoCoordinate(37.5747, 126.9855)),),
        )

    route = result.data.routes[0]
    assert route.status is RouteStatus.NO_DATA
    assert route.error_code == "kakao_status_no_results"
    assert result.metadata.status is ProviderStatus.NO_DATA


@pytest.mark.asyncio
async def test_transit_route_reports_no_data_when_routes_are_empty() -> None:
    """status는 OK인데 경로가 없으면 실패가 아니라 값 없음이다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "OK", "routes": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await RealKakaoTransitRouteProvider("test-key", client).get_routes(
            GeoCoordinate(37.57, 126.98), (RouteDestination("p1", GeoCoordinate(37.55, 126.98)),)
        )

    route = result.data.routes[0]
    assert route.status is RouteStatus.NO_DATA
    assert route.error_code == "kakao_no_transit_route"
    assert result.metadata.status is ProviderStatus.NO_DATA


@pytest.mark.asyncio
async def test_transit_route_skips_routes_with_unusable_properties() -> None:
    """properties가 깨진 경로는 건너뛰고 나머지 중 최소를 고른다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "OK",
                "routes": [
                    {"properties": {"totalTime": "not-a-number", "totalDistance": 100}},
                    {"properties": {"totalDistance": 500}},
                    {"steps": []},
                    _route(1800, 6000),
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await RealKakaoTransitRouteProvider("test-key", client).get_routes(
            GeoCoordinate(37.57, 126.98), (RouteDestination("p1", GeoCoordinate(37.55, 126.98)),)
        )

    route = result.data.routes[0]
    assert route.status is RouteStatus.SUCCESS
    assert route.duration_seconds == 1800


@pytest.mark.asyncio
async def test_transit_route_keeps_partial_status_when_one_destination_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = parse_qs(request.url.query.decode())
        if query["end_x"] == ["126.981"]:
            return httpx.Response(500, json={"errorType": "InternalError"})
        return httpx.Response(200, json={"status": "OK", "routes": [_route(600, 2000)]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await RealKakaoTransitRouteProvider("test-key", client).get_routes(
            GeoCoordinate(37.57, 126.98), _destinations()
        )

    first, second = result.data.routes
    assert first.status is RouteStatus.UNAVAILABLE
    assert first.error_code == "http_500"
    assert second.status is RouteStatus.SUCCESS
    assert result.metadata.status is ProviderStatus.PARTIAL


@pytest.mark.asyncio
async def test_transit_route_rejects_other_travel_modes() -> None:
    """도보 값을 대중교통 실측인 척 내보내지 않게 mode를 거부한다."""
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as (
        client
    ):
        provider = RealKakaoTransitRouteProvider("test-key", client)
        with pytest.raises(ValueError):
            await provider.get_routes(
                GeoCoordinate(37.57, 126.98), _destinations(), mode=TravelMode.WALKING
            )


@pytest.mark.asyncio
async def test_fake_transit_provider_marks_results_as_straight_line_estimates() -> None:
    """fake는 실측이 아니다 — source가 채점에서 걸러지는 값이어야 한다."""
    result = await FakeTransitRouteProvider(transit_speed_mps=5.5).get_routes(
        GeoCoordinate(37.57, 126.98), _destinations()
    )

    assert all(
        route.source is RouteSource.STRAIGHT_LINE_ESTIMATE for route in result.data.routes
    )
    assert all(route.mode is TravelMode.TRANSIT for route in result.data.routes)
    assert result.metadata.source is ProviderSource.FAKE_TRANSIT_ROUTE
