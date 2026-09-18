"""INFO 근처 공중화장실 경로를 검증한다.

두 진입을 모두 덮는다 — 지명을 말한 경우(지오코딩 좌표 기준)와 "근처에 화장실
있어?"처럼 지명이 없는 경우(기기 GPS 기준). 후자가 이 기능의 핵심 요구였다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.agent_context.info_schemas import (
    InfoContextRequest,
    RealtimeCityInfoResult,
)
from app.agent_context.schemas import Coordinates
from app.agent_context.service import ContextService, ContextTools
from app.domain.models import GeocodeResult, LocalSearchPlace, PublicToilet
from app.providers.contracts import ProviderSource, provider_result
from app.providers.holiday import FakeHolidayProvider
from app.providers.stub import FakePlaceProvider, FakeWeatherProvider
from app.repositories.fake_public_toilet import FakePublicToiletRepository
from app.tools.holiday import GetHolidaysTool
from app.tools.nearby_place_details import NearbyPlaceDetailsTool
from app.tools.public_toilet import GetPublicToiletTool
from app.tools.resolve_location import ResolveLocationTool
from app.tools.weather_forecast import GetWeatherForecastTool

_KST = ZoneInfo("Asia/Seoul")
# 인사동 쌈지길 앞. 실측 조회에 쓴 좌표와 같다.
_INSADONG = (37.57390, 126.98520)

# 실제 데이터에서 가져온 세 곳. 개방시간 표기의 세 갈래를 각각 담는다.
_ALWAYS_OPEN = PublicToilet(
    toilet_id="insadong-maru",
    name="인사동마루 신관 개방화장실",
    address_new="서울특별시 종로구 인사동길 35-4",
    address_old=None,
    latitude=37.57432,
    longitude=126.98563,
    district="종로구",
    tel="02-2148-2383",
    open_type="민간개방|",
    open_hours_raw="상시(24시간)|",
    restroom_status="남자|여자|",
    accessible_status="남자|여자|",
    amenities=None,
    safety_signs=None,
    location_type="근생시설|",
    manager="인사동마루",
)
# 더 가깝지만 낮에만 연다. 새벽에는 뒤로 밀려야 한다.
_DAYTIME_ONLY = PublicToilet(
    toilet_id="ssamziegil",
    name="쌈지길(지하1층)",
    address_new="서울특별시 종로구 인사동길 44",
    address_old=None,
    latitude=37.57392,
    longitude=126.98522,
    district="종로구",
    tel="02-736-0088",
    open_type="민간개방|",
    open_hours_raw="기타|10:30~20:30",
    restroom_status="남자|여자|",
    accessible_status=None,
    amenities=None,
    safety_signs=None,
    location_type="근생시설|",
    manager="쌈지길",
)
# 반지름(1km) 밖. 결과에 들어오면 안 된다 — 여의도 한강공원 쪽 좌표다.
_FAR_AWAY = PublicToilet(
    toilet_id="far-away",
    name="멀리 있는 화장실",
    address_new="서울특별시 영등포구 여의동로 330",
    address_old=None,
    latitude=37.52780,
    longitude=126.93400,
    district="영등포구",
    tel=None,
    open_type="공공개방|",
    open_hours_raw="상시(24시간)|",
    restroom_status="남자|여자|",
    accessible_status=None,
    amenities=None,
    safety_signs=None,
    location_type="공원 및 하천변|",
    manager="서울시",
)


class _FixedGeocodingProvider:
    def __init__(self, *, latitude: float, longitude: float) -> None:
        self._latitude = latitude
        self._longitude = longitude

    async def geocode(self, location_query: str, *, use_alias: bool = True):
        del use_alias
        return provider_result(
            GeocodeResult(
                query=location_query,
                resolved_name=f"서울특별시 종로구 {location_query}",
                latitude=self._latitude,
                longitude=self._longitude,
            ),
            source=ProviderSource.FAKE_GEOCODING,
        )


class _AmbiguousLocalSearchProvider:
    """같은 이름의 후보를 둘 이상 돌려줘 ambiguous_location을 만든다.

    "인사동"처럼 동네 이름은 상호와 겹쳐 후보가 갈리는 일이 흔하다. 화장실 질문에는
    특히 자주 나오는 상황이라 되묻지 않고 대표 좌표로 답해야 한다.
    """

    async def search_places_by_name(self, query: str, *, display: int = 5):
        del display
        latitude, longitude = _INSADONG
        return provider_result(
            (
                LocalSearchPlace(
                    name=f"{query} 1호점",
                    address="서울 종로구 인사동길 12",
                    road_address="서울 종로구 인사동길 12",
                    category="음식점>카페",
                    latitude=latitude,
                    longitude=longitude,
                ),
                LocalSearchPlace(
                    name=f"{query} 2호점",
                    address="서울 종로구 인사동길 20",
                    road_address="서울 종로구 인사동길 20",
                    category="음식점>카페",
                    latitude=latitude + 0.0006,
                    longitude=longitude + 0.0008,
                ),
            ),
            source=ProviderSource.FAKE_LOCAL_SEARCH,
        )


def _service(
    *,
    toilets: tuple[PublicToilet, ...] = (_ALWAYS_OPEN, _DAYTIME_ONLY, _FAR_AWAY),
    with_toilet_tool: bool = True,
    with_ambiguous_local_search: bool = False,
    clock: Callable[[], datetime] | None = None,
) -> ContextService:
    place_provider = FakePlaceProvider()
    latitude, longitude = _INSADONG
    return ContextService(
        ContextTools(
            location=ResolveLocationTool(
                _FixedGeocodingProvider(latitude=latitude, longitude=longitude),
                local_search_provider=(
                    _AmbiguousLocalSearchProvider() if with_ambiguous_local_search else None
                ),
            ),
            places=NearbyPlaceDetailsTool(place_provider, place_provider),
            weather=GetWeatherForecastTool(FakeWeatherProvider()),
            holidays=GetHolidaysTool(FakeHolidayProvider()),
            public_toilets=(
                GetPublicToiletTool(FakePublicToiletRepository(toilets))
                if with_toilet_tool
                else None
            ),
        ),
        candidate_limit=10,
        clock=clock or (lambda: datetime(2026, 9, 5, 2, 7, tzinfo=_KST)),
    )


def _request(
    *, place_name: str | None, origin: Coordinates | None = None
) -> InfoContextRequest:
    return InfoContextRequest(
        request_id="public-toilet",
        place_name=place_name,
        place_context="explicit" if place_name else "from_conversation",
        question_type="public_toilet",
        specific_question="근처에 화장실 있어?",
        origin_coordinates=origin,
    )


@pytest.mark.asyncio
async def test_named_place_returns_two_nearest_toilets() -> None:
    response = await _service().fetch_info_context(_request(place_name="인사동"))

    assert response.status == "success"
    result = response.result
    assert isinstance(result, RealtimeCityInfoResult)
    assert result.question_type == "public_toilet"
    # 급한 질문이라 두 곳만 싣는다.
    assert len(result.fields) == 2
    assert len(result.detail_items) == 2
    # 1km 밖은 빠진다.
    assert "멀리 있는 화장실" not in result.fields


@pytest.mark.asyncio
async def test_open_now_outranks_closer_but_closed_toilet() -> None:
    """새벽 2시 7분 — 더 가까운 쌈지길(10:30~20:30)은 닫혀 있다.

    거리만으로 정렬하면 20m 더 가까운 닫힌 화장실이 1순위가 되는데, 급해서 묻는
    질문에 그 답은 쓸모없다.
    """

    response = await _service().fetch_info_context(_request(place_name="인사동"))

    result = response.result
    assert isinstance(result, RealtimeCityInfoResult)
    titles = [item.title for item in result.detail_items]
    assert titles[0] == "인사동마루 신관 개방화장실"
    assert result.detail_items[0].details["개방 여부"] == "지금 이용 가능"
    assert result.detail_items[1].details["개방 여부"] == "지금은 닫혀 있음"


@pytest.mark.asyncio
async def test_daytime_query_puts_nearest_open_toilet_first() -> None:
    # 같은 데이터, 낮 3시. 이제 쌈지길도 열려 있어 더 가까운 쪽이 1순위다.
    service = _service(clock=lambda: datetime(2026, 9, 5, 15, 0, tzinfo=_KST))

    response = await service.fetch_info_context(_request(place_name="인사동"))

    result = response.result
    assert isinstance(result, RealtimeCityInfoResult)
    assert result.detail_items[0].title == "쌈지길(지하1층)"


@pytest.mark.asyncio
async def test_device_gps_answers_without_asking_for_a_place_name() -> None:
    """"급한데 근처에 화장실 있어?"는 지명 없이도 답해야 한다.

    지명이 없으면 되묻는 게 INFO의 기본 동작인데, 급한 상황에 "어디 근처요?"를
    되묻는 건 답을 안 준 것과 같다.
    """

    latitude, longitude = _INSADONG
    response = await _service().fetch_info_context(
        _request(
            place_name=None,
            origin=Coordinates(latitude=latitude, longitude=longitude),
        )
    )

    assert response.status == "success"
    assert response.clarification is None
    result = response.result
    assert isinstance(result, RealtimeCityInfoResult)
    assert result.resolved_place_name == "현재 위치"
    assert len(result.detail_items) == 2


@pytest.mark.asyncio
async def test_named_place_wins_over_device_gps() -> None:
    """지명을 말했으면 기기 위치가 있어도 그 지명이 기준이다.

    강남에 앉아 "인사동 화장실 어디야?"를 물었을 때 강남 화장실을 인사동이라고
    답하면 안 된다. 실제로 이 순서가 뒤바뀐 채 배포 직전까지 갔다 — GPS 지름길이
    지명 유무를 보지 않아 이름만 인사동으로 붙고 좌표는 GPS를 썼다.
    """

    # 기기 위치는 여의도(먼 곳)로 두고, 지명은 인사동으로 묻는다. 지오코더는
    # 언제나 인사동 좌표를 돌려주므로, 인사동 화장실이 나와야 지명이 이긴 것이다.
    response = await _service().fetch_info_context(
        _request(
            place_name="인사동",
            origin=Coordinates(latitude=37.5278, longitude=126.9340),
        )
    )

    assert response.status == "success"
    result = response.result
    assert isinstance(result, RealtimeCityInfoResult)
    assert result.requested_place_name == "인사동"
    assert result.resolved_place_name != "현재 위치"
    titles = [item.title for item in result.detail_items]
    # 여의도 좌표를 썼다면 반경 안에 인사동 화장실이 없어 이 목록이 비었을 것이다.
    assert "인사동마루 신관 개방화장실" in titles
    assert "멀리 있는 화장실" not in titles


@pytest.mark.asyncio
async def test_ambiguous_place_name_answers_with_the_lead_candidate() -> None:
    """후보가 갈리는 지명은 되묻지 않고 대표 좌표로 답한다.

    "인사동"은 동네 이름이라 상호와 겹쳐 후보가 여럿 잡힌다. 급한 질문에
    "어느 인사동이요?"를 되묻는 건 답을 안 준 것과 같고, 어느 후보든 반경 1km 안
    화장실은 크게 다르지 않다.

    이 경로가 메타데이터를 이중 튜플로 넘겨 500이 나던 회귀도 함께 막는다 —
    같은 버그가 realtime_public_parking·realtime_bus·realtime_commercial에도
    있었다(2026-09-05 실측).
    """

    response = await _service(with_ambiguous_local_search=True).fetch_info_context(
        _request(place_name="인사동")
    )

    assert response.status == "success"
    assert response.clarification is None
    result = response.result
    assert isinstance(result, RealtimeCityInfoResult)
    assert len(result.detail_items) == 2
    # 메타데이터가 평탄한 tuple[ProviderMetadata, ...]로 조립돼야 한다.
    assert response.metadata.provider_metadata
    assert all(item.source for item in response.metadata.provider_metadata)


@pytest.mark.asyncio
async def test_missing_place_and_gps_still_asks_where() -> None:
    # 기준점이 아예 없으면 물어볼 수밖에 없다.
    response = await _service().fetch_info_context(_request(place_name=None))

    assert response.status == "needs_clarification"
    assert response.clarification is not None
    assert response.clarification.missing_fields == ["place_name"]


@pytest.mark.asyncio
async def test_detail_items_carry_coordinates_for_walking_directions() -> None:
    # 프론트가 카드를 눌러 도보 길찾기를 열려면 항목마다 좌표가 있어야 한다.
    response = await _service().fetch_info_context(_request(place_name="인사동"))

    result = response.result
    assert isinstance(result, RealtimeCityInfoResult)
    for item in result.detail_items:
        assert item.latitude is not None
        assert item.longitude is not None
    assert result.source_url is not None


@pytest.mark.asyncio
async def test_no_toilet_in_radius_returns_no_data_not_error() -> None:
    response = await _service(toilets=(_FAR_AWAY,)).fetch_info_context(
        _request(place_name="인사동")
    )

    assert response.status == "no_data"
    result = response.result
    assert isinstance(result, RealtimeCityInfoResult)
    assert result.question_type == "public_toilet"
    assert result.fields == {}


@pytest.mark.asyncio
async def test_missing_tool_is_reported_as_unavailable() -> None:
    response = await _service(with_toilet_tool=False).fetch_info_context(
        _request(place_name="인사동")
    )

    assert response.status == "unavailable"
    assert response.error is not None
    assert response.error.code == "public_toilet_unavailable"
