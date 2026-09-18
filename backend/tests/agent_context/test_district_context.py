"""구 이름으로 들어온 요청이 실제로 구 단위 경로를 타는지 검증한다(D-119).

`test_district_selection.py`가 고르는 규칙 자체를 못 박는다면, 이쪽은 **그 규칙이
실제 요청에서 돌기는 하는지**를 본다. 배선이 빠져 있어도 선택 로직 테스트는 그대로
통과하기 때문이다.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.agent_context.schemas import AgentContextRequest, UserConditions
from app.agent_context.service import ContextService, ContextTools
from app.providers.concentration import FakeConcentrationProvider
from app.providers.geocoding import FakeGeocodingProvider
from app.providers.holiday import FakeHolidayProvider
from app.providers.stub import (
    FakeDistrictPlaceSearchProvider,
    FakePlaceProvider,
    FakeWeatherProvider,
)
from app.repositories.fake_places import FakePlaceLocationRepository
from app.tools.concentration import GetConcentrationTool
from app.tools.holiday import GetHolidaysTool
from app.tools.nearby_place_details import NearbyPlaceDetailsTool
from app.tools.resolve_location import ResolveLocationTool
from app.tools.weather_forecast import GetWeatherForecastTool

KST = ZoneInfo("Asia/Seoul")


def _service(*, with_district_provider: bool = True) -> ContextService:
    place_provider = FakePlaceProvider()
    return ContextService(
        ContextTools(
            location=ResolveLocationTool(
                FakeGeocodingProvider(),
                place_repository=FakePlaceLocationRepository(),
            ),
            places=NearbyPlaceDetailsTool(
                place_provider,
                place_provider,
                district_search_provider=(
                    FakeDistrictPlaceSearchProvider() if with_district_provider else None
                ),
            ),
            weather=GetWeatherForecastTool(FakeWeatherProvider()),
            holidays=GetHolidaysTool(FakeHolidayProvider()),
            concentration=GetConcentrationTool(FakeConcentrationProvider()),
        ),
        candidate_limit=30,
        clock=lambda: datetime.now(KST),
    )


def _request(
    search_center: str,
    *,
    place_types: list[str] | None = None,
    place_tags: list[str] | None = None,
) -> AgentContextRequest:
    return AgentContextRequest(
        request_id="request-1",
        intent="RECOMMEND",
        conditions=UserConditions(
            search_center=search_center,
            place_types=place_types or [],
            place_tags=place_tags or [],
        ),
    )


@pytest.mark.asyncio
async def test_구_이름이면_구_단위로_모았다는_사실을_D에게_넘긴다() -> None:
    response = await _service().fetch_context(_request("강남구"))

    assert response.context is not None
    assert response.context.district_scope is not None
    assert response.context.district_scope.district_code == "680"
    assert response.context.district_scope.district_name == "강남구"


@pytest.mark.asyncio
async def test_구_이름이_아니면_구_단위로_가지_않는다() -> None:
    """"경복궁"은 지금까지처럼 그 좌표 둘레를 반경으로 본다."""
    response = await _service().fetch_context(_request("경복궁"))

    assert response.context is not None
    assert response.context.district_scope is None


@pytest.mark.asyncio
async def test_짧은_권역명도_구로_받는다() -> None:
    """사용자는 "강남구"라고 다 붙여 말하지 않는다."""
    response = await _service().fetch_context(_request("강남"))

    assert response.context is not None
    assert response.context.district_scope is not None
    assert response.context.district_scope.district_name == "강남구"


@pytest.mark.asyncio
async def test_선택_규칙이_실제로_후보를_움직인다() -> None:
    """**이 테스트가 조용한 fake를 막는다.**

    Fake 구는 쇼핑이 90곳으로 압도적인데(실제 강남구가 713/1,100이다), 선택 규칙이
    안 돌면 그 비율이 결과에 그대로 나온다. 쇼핑이 6곳으로 눌려 있다는 것은 분류
    몫과 쇼핑 절대 상한이 실제로 걸렸다는 뜻이다.
    """
    response = await _service().fetch_context(_request("강남구"))

    assert response.context is not None
    assert response.context.places is not None
    places = response.context.places.data or []
    counts = Counter(place.category for place in places)

    assert counts["shopping"] == 6
    # 원본 비율대로였다면 쇼핑이 절반을 넘는다.
    assert counts["shopping"] < len(places) / 2
    # 관광지·문화시설이 몫만큼 살아 있다.
    assert counts["attraction"] > 0
    assert counts["cultural_facility"] > 0


@pytest.mark.asyncio
async def test_구_단위_provider가_없으면_반경_검색으로_돌아가지_않는다() -> None:
    """조용히 되돌아가면 "강남구"가 대표점 주변 결과를 내면서 아무도 모른다."""
    response = await _service(with_district_provider=False).fetch_context(_request("강남구"))

    assert response.status in {"unavailable", "no_data"}


@pytest.mark.asyncio
async def test_분류를_말한_구_요청은_그_분류만_나온다() -> None:
    """"강남구 맛집"에 관광지를 섞어 넣으면 요청을 무시하는 것이 된다."""
    response = await _service().fetch_context(
        _request("강남구", place_types=["restaurant"])
    )

    assert response.context is not None
    assert response.context.places is not None
    places = response.context.places.data or []

    assert places, "분류 조건이 붙으면 후보가 통째로 사라진다"
    assert {place.category for place in places} == {"restaurant"}


@pytest.mark.asyncio
async def test_분류를_말하면_소진율_상한을_걸지_않는다() -> None:
    """지목한 분류는 다 보여준다.

    Fake 구의 레포츠는 3곳뿐이라, 조건 없는 요청에 걸리는 규칙이 여기에도 걸리면
    수가 줄어든다 — 몫 2에 잘리거나 소진율 60%(= 1곳)에 잘린다. 사용자가 직접
    지목한 분류는 그 둘 다 적용하지 않는다는 것을 이 수로 못 박는다.
    """
    response = await _service().fetch_context(
        _request("강남구", place_types=["leisure"])
    )

    assert response.context is not None
    assert response.context.places is not None
    places = response.context.places.data or []

    assert len(places) == 3
    assert {place.category for place in places} == {"leisure"}
