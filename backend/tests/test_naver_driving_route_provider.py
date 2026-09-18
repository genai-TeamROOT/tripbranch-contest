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
from app.providers.driving_route import (
    NAVER_DRIVING_ROUTE_OPTION,
    NAVER_DRIVING_ROUTE_URL,
    FakeDrivingRouteProvider,
    RealNaverDrivingRouteProvider,
)

_ORIGIN = GeoCoordinate(37.5788, 126.9770)


def _destinations() -> tuple[RouteDestination, ...]:
    return (
        RouteDestination("first", GeoCoordinate(37.5702, 126.9991)),
        RouteDestination("second", GeoCoordinate(37.5731, 126.9880)),
    )


def _summary_response(distance_m: int, duration_ms: int) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "code": 0,
            "message": "길찾기를 성공하였습니다.",
            "route": {
                NAVER_DRIVING_ROUTE_OPTION: [
                    {"summary": {"distance": distance_m, "duration": duration_ms}}
                ]
            },
        },
    )


def _provider(handler, **kwargs) -> tuple[RealNaverDrivingRouteProvider, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = RealNaverDrivingRouteProvider("key-id", "key-secret", client, **kwargs)
    return provider, client


@pytest.mark.asyncio
async def test_naver_driving_route_calls_directions_for_each_destination() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert str(request.url).startswith(NAVER_DRIVING_ROUTE_URL)
        assert request.headers["x-ncp-apigw-api-key-id"] == "key-id"
        assert request.headers["x-ncp-apigw-api-key"] == "key-secret"
        query = parse_qs(request.url.query.decode())
        # Naver는 경도,위도 순서다 — 뒤집으면 엉뚱한 좌표로 길을 찾는다.
        assert query["start"] == ["126.977,37.5788"]
        assert query["option"] == [NAVER_DRIVING_ROUTE_OPTION]
        index = 1 if query["goal"] == ["126.9991,37.5702"] else 2
        return _summary_response(index * 1000, index * 60_000)

    provider, client = _provider(handler)
    async with client:
        result = await provider.get_routes(_ORIGIN, _destinations())

    assert len(requests) == 2
    assert result.metadata.source is ProviderSource.NAVER_DRIVING_ROUTE
    assert result.metadata.status is ProviderStatus.SUCCESS
    first, second = result.data.routes
    assert first.place_id == "first"
    assert first.source is RouteSource.NAVER_DRIVING
    assert first.mode is TravelMode.DRIVING
    assert (first.distance_m, second.distance_m) == (1000, 2000)


@pytest.mark.asyncio
async def test_naver_driving_route_converts_duration_from_milliseconds() -> None:
    """Naver의 duration은 밀리초다. 초로 바꾸지 않으면 소요시간이 1000배가 된다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _summary_response(2642, 1_332_351)

    provider, client = _provider(handler)
    async with client:
        result = await provider.get_routes(
            _ORIGIN, (RouteDestination("only", GeoCoordinate(37.5702, 126.9991)),)
        )

    route = result.data.routes[0]
    assert route.status is RouteStatus.SUCCESS
    assert route.distance_m == 2642
    assert route.duration_seconds == 1332


@pytest.mark.asyncio
async def test_naver_driving_route_answers_same_point_without_calling_the_api() -> None:
    """출발지와 도착지가 같으면 Naver는 400으로 거절한다 — 호출 전에 0으로 답한다."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _summary_response(0, 0)

    provider, client = _provider(handler)
    async with client:
        result = await provider.get_routes(_ORIGIN, (RouteDestination("same", _ORIGIN),))

    assert calls == 0
    route = result.data.routes[0]
    assert route.status is RouteStatus.SUCCESS
    assert (route.distance_m, route.duration_seconds) == (0, 0)
    assert route.source is RouteSource.NAVER_DRIVING


@pytest.mark.asyncio
async def test_naver_driving_route_treats_unreachable_point_as_no_data() -> None:
    """"도로 주변이 아님"(code=2)은 재시도해도 같은 사실 응답이라 장애로 세지 않는다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "errorCode": 400,
                    "code": 2,
                    "message": "출발지 또는 도착지가 도로 주변이 아닙니다.",
                }
            },
        )

    provider, client = _provider(handler)
    async with client:
        result = await provider.get_routes(
            _ORIGIN, (RouteDestination("island", GeoCoordinate(34.0, 125.0)),)
        )

    route = result.data.routes[0]
    assert route.status is RouteStatus.NO_DATA
    assert route.error_code == "naver_code_2"
    assert result.metadata.status is ProviderStatus.NO_DATA


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "payload"),
    [
        (400, {"error": {"errorCode": 400, "code": 1, "message": "abnormal query"}}),
        (429, {"error": {"errorCode": 429, "message": "Quota Exceeded"}}),
        (500, {"error": {"errorCode": 500, "message": "Internal Server Error"}}),
    ],
)
async def test_naver_driving_route_marks_other_http_failures_unavailable(
    status_code: int, payload: dict[str, object]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload)

    provider, client = _provider(handler)
    async with client:
        result = await provider.get_routes(
            _ORIGIN, (RouteDestination("only", GeoCoordinate(37.5702, 126.9991)),)
        )

    route = result.data.routes[0]
    assert route.status is RouteStatus.UNAVAILABLE
    assert route.error_code == f"http_{status_code}"
    assert route.source is RouteSource.NAVER_DRIVING


@pytest.mark.asyncio
async def test_naver_driving_route_handles_an_empty_error_body() -> None:
    """Directions 미활성 응답은 403에 본문이 비어 있다 — 파싱이 여기서 터지면 안 된다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=b"")

    provider, client = _provider(handler)
    async with client:
        result = await provider.get_routes(
            _ORIGIN, (RouteDestination("only", GeoCoordinate(37.5702, 126.9991)),)
        )

    assert result.data.routes[0].error_code == "http_403"


@pytest.mark.asyncio
async def test_naver_driving_route_isolates_timeout_per_destination() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = parse_qs(request.url.query.decode())
        if query["goal"] == ["126.9991,37.5702"]:
            raise httpx.ReadTimeout("timeout", request=request)
        return _summary_response(1500, 300_000)

    provider, client = _provider(handler)
    async with client:
        result = await provider.get_routes(_ORIGIN, _destinations())

    failed, succeeded = result.data.routes
    assert failed.status is RouteStatus.UNAVAILABLE
    assert failed.error_code == "provider_timeout"
    assert succeeded.status is RouteStatus.SUCCESS
    assert result.metadata.status is ProviderStatus.PARTIAL


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"code": 0, "route": {}},
        {"code": 0, "route": {NAVER_DRIVING_ROUTE_OPTION: []}},
        {"code": 0, "route": {NAVER_DRIVING_ROUTE_OPTION: [{"summary": {"distance": 100}}]}},
        {
            "code": 0,
            "route": {
                NAVER_DRIVING_ROUTE_OPTION: [{"summary": {"distance": -1, "duration": 10}}]
            },
        },
    ],
)
async def test_naver_driving_route_marks_malformed_success_as_unavailable(
    payload: dict[str, object],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    provider, client = _provider(handler)
    async with client:
        result = await provider.get_routes(
            _ORIGIN, (RouteDestination("only", GeoCoordinate(37.5702, 126.9991)),)
        )

    route = result.data.routes[0]
    assert route.status is RouteStatus.UNAVAILABLE
    assert route.error_code == "invalid_response"


@pytest.mark.asyncio
async def test_naver_driving_route_skips_http_for_empty_destinations() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("목적지가 없으면 호출하지 않아야 한다")

    provider, client = _provider(handler)
    async with client:
        result = await provider.get_routes(_ORIGIN, ())

    assert result.data.routes == ()
    assert result.metadata.status is ProviderStatus.NO_DATA


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [TravelMode.WALKING, TravelMode.TRANSIT])
async def test_naver_driving_route_rejects_non_driving_mode(mode: TravelMode) -> None:
    """자동차 값을 다른 이동수단의 실측인 척 내보내지 않는다."""

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("지원하지 않는 mode는 호출 전에 거부해야 한다")

    provider, client = _provider(handler)
    async with client:
        with pytest.raises(ValueError):
            await provider.get_routes(_ORIGIN, _destinations(), mode=mode)

    with pytest.raises(ValueError):
        await FakeDrivingRouteProvider(5.5).get_routes(_ORIGIN, _destinations(), mode=mode)


@pytest.mark.asyncio
async def test_fake_driving_route_estimates_from_straight_line_distance() -> None:
    """fake는 실측이 아님을 source로 드러낸다 — 채점이 이걸 보고 걸러낸다."""
    result = await FakeDrivingRouteProvider(5.0).get_routes(
        _ORIGIN, (RouteDestination("only", GeoCoordinate(37.5702, 126.9991)),)
    )

    route = result.data.routes[0]
    assert route.source is RouteSource.STRAIGHT_LINE_ESTIMATE
    assert route.mode is TravelMode.DRIVING
    assert route.duration_seconds == pytest.approx(route.distance_m / 5.0, abs=1)
    assert result.metadata.source is ProviderSource.FAKE_DRIVING_ROUTE


def test_naver_driving_route_rejects_invalid_construction() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    with pytest.raises(ValueError):
        RealNaverDrivingRouteProvider("", "secret", client)
    with pytest.raises(ValueError):
        RealNaverDrivingRouteProvider("id", "secret", client, max_concurrency=11)
    with pytest.raises(ValueError):
        FakeDrivingRouteProvider(0)
