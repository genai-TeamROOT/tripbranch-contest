from __future__ import annotations

import pytest

from app.domain.travel_route import (
    GeoCoordinate,
    RouteDestination,
    RouteSource,
    RouteStatus,
    TravelMode,
)
from app.providers.contracts import ProviderSource, ProviderStatus
from app.providers.walking_route import FakeWalkingRouteProvider


@pytest.mark.asyncio
async def test_fake_walking_route_estimates_distance_and_duration() -> None:
    result = await FakeWalkingRouteProvider(walking_speed_mps=1.0).get_routes(
        GeoCoordinate(latitude=37.0, longitude=127.0),
        (
            RouteDestination(
                place_id="north",
                coordinate=GeoCoordinate(latitude=38.0, longitude=127.0),
            ),
        ),
    )

    route = result.data.routes[0]
    assert route.distance_m == pytest.approx(111_195, abs=1)
    assert route.duration_seconds == route.distance_m
    assert route.status is RouteStatus.SUCCESS
    assert route.source is RouteSource.STRAIGHT_LINE_ESTIMATE
    assert route.mode is TravelMode.WALKING
    assert result.metadata.source is ProviderSource.FAKE_WALKING_ROUTE
    assert result.metadata.status is ProviderStatus.SUCCESS


@pytest.mark.asyncio
async def test_fake_walking_route_preserves_destination_order() -> None:
    destinations = tuple(
        RouteDestination(
            place_id=place_id,
            coordinate=GeoCoordinate(latitude=37.0 + offset, longitude=127.0),
        )
        for place_id, offset in (("second", 0.02), ("first", 0.01))
    )

    result = await FakeWalkingRouteProvider(walking_speed_mps=1.2).get_routes(
        GeoCoordinate(latitude=37.0, longitude=127.0), destinations
    )

    assert [route.place_id for route in result.data.routes] == ["second", "first"]


@pytest.mark.asyncio
async def test_fake_walking_route_returns_no_data_for_empty_destinations() -> None:
    result = await FakeWalkingRouteProvider(walking_speed_mps=1.2).get_routes(
        GeoCoordinate(latitude=37.0, longitude=127.0), ()
    )

    assert result.data.routes == ()
    assert result.metadata.status is ProviderStatus.NO_DATA


@pytest.mark.asyncio
async def test_fake_walking_route_supports_same_origin_and_destination() -> None:
    coordinate = GeoCoordinate(latitude=37.0, longitude=127.0)

    result = await FakeWalkingRouteProvider(walking_speed_mps=1.2).get_routes(
        coordinate,
        (RouteDestination(place_id="same", coordinate=coordinate),),
    )

    route = result.data.routes[0]
    assert route.distance_m == 0
    assert route.duration_seconds == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [TravelMode.TRANSIT, TravelMode.DRIVING])
async def test_fake_walking_route_rejects_non_walking_mode(mode: TravelMode) -> None:
    """도보 속도 추정값이 다른 이동수단의 실측인 척 나가지 않는지 확인한다."""
    with pytest.raises(ValueError, match="지원하지 않습니다"):
        await FakeWalkingRouteProvider(walking_speed_mps=1.2).get_routes(
            GeoCoordinate(latitude=37.0, longitude=127.0),
            (
                RouteDestination(
                    place_id="north",
                    coordinate=GeoCoordinate(latitude=38.0, longitude=127.0),
                ),
            ),
            mode=mode,
        )
