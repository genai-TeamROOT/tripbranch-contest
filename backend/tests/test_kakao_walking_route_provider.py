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
from app.providers.walking_route import (
    KAKAO_MAP_WALKING_ROUTE_URL,
    RealKakaoWalkingRouteProvider,
)


def _destinations() -> tuple[RouteDestination, ...]:
    return (
        RouteDestination("first", GeoCoordinate(37.571, 126.981)),
        RouteDestination("second", GeoCoordinate(37.572, 126.982)),
    )


@pytest.mark.asyncio
async def test_kakao_walking_route_calls_single_route_api_for_each_destination() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert str(request.url).startswith(KAKAO_MAP_WALKING_ROUTE_URL)
        assert request.headers["Authorization"] == "KakaoAK test-key"
        query = parse_qs(request.url.query.decode())
        assert query["start_x"] == ["126.98"]
        assert query["start_y"] == ["37.57"]
        assert query["input_coord"] == ["WGS84"]
        assert query["output_coord"] == ["WGS84"]
        assert query["route_mode"] == ["SHORTEST"]
        index = 1 if query["end_x"] == ["126.981"] else 2
        return httpx.Response(
            200,
            json={
                "status": "OK",
                "route": {
                    "properties": {
                        "totalDistance": index * 100,
                        "totalTime": index * 90,
                    }
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await RealKakaoWalkingRouteProvider("test-key", client).get_routes(
            GeoCoordinate(37.57, 126.98), _destinations()
        )

    assert len(requests) == 2
    assert [route.place_id for route in result.data.routes] == ["first", "second"]
    assert [route.distance_m for route in result.data.routes] == [100, 200]
    assert [route.duration_seconds for route in result.data.routes] == [90, 180]
    assert all(route.source is RouteSource.KAKAO_WALKING for route in result.data.routes)
    assert result.metadata.source is ProviderSource.KAKAO_WALKING_ROUTE
    assert result.metadata.status is ProviderStatus.SUCCESS


@pytest.mark.asyncio
async def test_kakao_walking_route_preserves_per_destination_no_data() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = parse_qs(request.url.query.decode())
        if query["end_x"] == ["126.981"]:
            return httpx.Response(
                200,
                json={
                    "status": "OK",
                    "route": {"properties": {"totalDistance": 100, "totalTime": 90}},
                },
            )
        return httpx.Response(200, json={"status": "ROUTE_RESULT_NOT_FOUND"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await RealKakaoWalkingRouteProvider("test-key", client).get_routes(
            GeoCoordinate(37.57, 126.98), _destinations()
        )

    failed = result.data.routes[1]
    assert failed.place_id == "second"
    assert failed.status is RouteStatus.NO_DATA
    assert failed.error_code == "kakao_status_route_result_not_found"
    assert result.metadata.status is ProviderStatus.PARTIAL


@pytest.mark.parametrize("status_code", [401, 429, 500])
@pytest.mark.asyncio
async def test_kakao_walking_route_isolates_http_failure(status_code: int) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                status_code,
                json={"errorCode": "UpstreamError"},
                request=request,
            )
        )
    ) as client:
        result = await RealKakaoWalkingRouteProvider("test-key", client).get_routes(
            GeoCoordinate(37.57, 126.98), _destinations()
        )

    assert all(route.status is RouteStatus.UNAVAILABLE for route in result.data.routes)
    assert all(route.error_code == f"http_{status_code}" for route in result.data.routes)
    assert result.metadata.status is ProviderStatus.PARTIAL


@pytest.mark.asyncio
async def test_kakao_walking_route_isolates_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await RealKakaoWalkingRouteProvider("test-key", client).get_routes(
            GeoCoordinate(37.57, 126.98), _destinations()
        )

    assert all(route.status is RouteStatus.UNAVAILABLE for route in result.data.routes)
    assert all(route.error_code == "provider_timeout" for route in result.data.routes)
    # 실패 경로도 이동수단 라벨을 잃지 않아야 소비 측이 무엇을 못 구한 건지 안다.
    assert all(route.mode is TravelMode.WALKING for route in result.data.routes)


@pytest.mark.asyncio
async def test_kakao_walking_route_maps_same_point_to_zero_distance() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"status": "SAME_POINT"})
        )
    ) as client:
        result = await RealKakaoWalkingRouteProvider("test-key", client).get_routes(
            GeoCoordinate(37.57, 126.98), (_destinations()[0],)
        )

    route = result.data.routes[0]
    assert route.status is RouteStatus.SUCCESS
    assert route.distance_m == 0
    assert route.duration_seconds == 0


@pytest.mark.asyncio
async def test_kakao_walking_route_marks_malformed_success_as_unavailable() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"status": "OK", "route": {}})
        )
    ) as client:
        result = await RealKakaoWalkingRouteProvider("test-key", client).get_routes(
            GeoCoordinate(37.57, 126.98), (_destinations()[0],)
        )

    assert result.data.routes[0].status is RouteStatus.UNAVAILABLE
    assert result.data.routes[0].error_code == "invalid_response"


@pytest.mark.asyncio
async def test_kakao_walking_route_skips_http_for_empty_destinations() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("빈 목적지 요청은 HTTP를 호출하면 안 됩니다.")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await RealKakaoWalkingRouteProvider("test-key", client).get_routes(
            GeoCoordinate(37.57, 126.98), ()
        )

    assert result.data.routes == ()
    assert result.metadata.status is ProviderStatus.NO_DATA


@pytest.mark.asyncio
async def test_kakao_walking_route_enforces_internal_batch_and_positive_radius() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("유효하지 않은 요청은 HTTP를 호출하면 안 됩니다.")

    destinations = tuple(
        RouteDestination(str(index), GeoCoordinate(37.57, 126.98)) for index in range(101)
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RealKakaoWalkingRouteProvider("test-key", client)
        with pytest.raises(ValueError, match="최대 100개"):
            await provider.get_routes(GeoCoordinate(37.57, 126.98), destinations)
        with pytest.raises(ValueError, match="0보다 커야"):
            await provider.get_routes(GeoCoordinate(37.57, 126.98), _destinations(), radius_m=0)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [TravelMode.TRANSIT, TravelMode.DRIVING])
async def test_kakao_walking_route_rejects_non_walking_mode(mode: TravelMode) -> None:
    """도보 엔드포인트 응답을 다른 이동수단의 실측으로 라벨하지 않는지 확인한다."""

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - 호출되면 실패
        raise AssertionError("지원하지 않는 이동수단에서는 외부 호출이 없어야 한다.")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RealKakaoWalkingRouteProvider("test-key", client)
        with pytest.raises(ValueError, match="지원하지 않습니다"):
            await provider.get_routes(
                GeoCoordinate(37.57, 126.98), _destinations(), mode=mode
            )


@pytest.mark.asyncio
async def test_kakao_walking_route_labels_routes_with_requested_mode() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "OK", "route": {"properties": {"totalDistance": 100, "totalTime": 90}}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await RealKakaoWalkingRouteProvider("test-key", client).get_routes(
            GeoCoordinate(37.57, 126.98), _destinations(), mode=TravelMode.WALKING
        )

    assert [route.mode for route in result.data.routes] == [TravelMode.WALKING] * 2
