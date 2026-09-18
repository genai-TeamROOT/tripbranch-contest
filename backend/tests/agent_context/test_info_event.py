"""INFO 행사 질의(question_type=event)가 끝까지 도는지 검증한다.

행사 데이터의 실제 특성(장소명과 안 붙는 행사가 다수)을 fake가 그대로 갖고
있어야 근접 매칭 경로가 실제로 실행된다.
"""

from dataclasses import replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.agent_context.info_schemas import (
    EventInfoResult,
    InfoContextRequest,
    InfoContextResponse,
)
from app.agent_context.service import INFO_EVENT_RESULT_LIMIT, ContextService, ContextTools
from app.errors import ProviderUnavailableError
from app.providers.concentration import FakeConcentrationProvider
from app.providers.contracts import (
    ProviderResult,
    ProviderSource,
    ProviderStatus,
    provider_result,
)
from app.providers.festival import FakeFestivalProvider, FestivalEvent
from app.providers.geocoding import FakeGeocodingProvider
from app.providers.holiday import FakeHolidayProvider
from app.providers.seoul_citydata import FakeRealtimeCityDataProvider
from app.providers.stub import FakePlaceProvider, FakeWeatherProvider
from app.repositories.fake_places import FakePlaceLocationRepository
from app.tools.concentration import GetConcentrationTool
from app.tools.festival import GetFestivalsTool
from app.tools.holiday import GetHolidaysTool
from app.tools.nearby_place_details import NearbyPlaceDetailsTool
from app.tools.realtime_citydata import GetRealtimeCityDataTool
from app.tools.resolve_location import ResolveLocationTool
from app.tools.weather_forecast import GetWeatherForecastTool

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 8, 7, 14, 0, tzinfo=KST)
TODAY = NOW.date()

# 경복궁 좌표(FakePlaceLocationRepository와 같은 값).
GYEONGBOKGUNG = (37.5788, 126.9770)


class StubFestivalProvider:
    """행사 목록을 테스트가 직접 정하는 fake."""

    def __init__(
        self,
        events: list[FestivalEvent] | None = None,
        error: Exception | None = None,
        status: ProviderStatus = ProviderStatus.SUCCESS,
    ) -> None:
        self.calls: list[tuple[str, str | None, date]] = []
        self._events = events or []
        self._error = error
        self._status = status

    async def search_festivals(
        self,
        region_code: str,
        district_code: str | None,
        reference_date: date,
        limit: int = 100,
    ) -> ProviderResult[list[FestivalEvent]]:
        self.calls.append((region_code, district_code, reference_date))
        if self._error is not None:
            raise self._error
        return provider_result(
            self._events,
            source=ProviderSource.TOUR_API_FESTIVAL,
            status=self._status,
        )


def _event(
    title: str,
    *,
    start_offset: int = -1,
    end_offset: int = 1,
    latitude: float | None = 37.5718,
    longitude: float | None = 126.9761,
    address: str | None = "서울특별시 종로구 세종대로 175",
    image_url: str | None = None,
) -> FestivalEvent:
    return FestivalEvent(
        content_id=f"id-{title}",
        title=title,
        start_date=TODAY + timedelta(days=start_offset),
        end_date=TODAY + timedelta(days=end_offset),
        address=address,
        latitude=latitude,
        longitude=longitude,
        image_url=image_url,
    )


def _service(
    festival_provider: object | None = None,
    citydata_provider: object | None = None,
) -> ContextService:
    place_provider = FakePlaceProvider()
    return ContextService(
        ContextTools(
            location=ResolveLocationTool(
                FakeGeocodingProvider(),
                place_repository=FakePlaceLocationRepository(),
            ),
            places=NearbyPlaceDetailsTool(place_provider, place_provider),
            weather=GetWeatherForecastTool(FakeWeatherProvider()),
            holidays=GetHolidaysTool(FakeHolidayProvider()),
            concentration=GetConcentrationTool(FakeConcentrationProvider()),
            festivals=(
                GetFestivalsTool(festival_provider)  # type: ignore[arg-type]
                if festival_provider is not None
                else None
            ),
            realtime_citydata=(
                GetRealtimeCityDataTool(citydata_provider)  # type: ignore[arg-type]
                if citydata_provider is not None
                else None
            ),
        ),
        candidate_limit=10,
        clock=lambda: NOW,
    )


def _request(place_name: str | None = "경복궁") -> InfoContextRequest:
    return InfoContextRequest(
        request_id="request-event-1",
        place_name=place_name,
        place_context="explicit",
        question_type="event",
    )


def _event_result(response: InfoContextResponse) -> EventInfoResult:
    assert isinstance(response.result, EventInfoResult)
    return response.result


class TestOngoingFilter:
    @pytest.mark.asyncio
    async def test_진행_중인_행사만_남긴다(self) -> None:
        provider = StubFestivalProvider(
            [
                _event("진행 중 행사", start_offset=-3, end_offset=3),
                _event("끝난 행사", start_offset=-30, end_offset=-10),
                _event("예정 행사", start_offset=10, end_offset=20),
            ]
        )

        response = await _service(provider).fetch_info_context(_request())

        assert response.status == "success"
        titles = [item.title for item in _event_result(response).events]
        assert titles == ["진행 중 행사"]

    @pytest.mark.asyncio
    async def test_시작일과_종료일_당일도_진행_중이다(self) -> None:
        provider = StubFestivalProvider(
            [
                _event("오늘 시작", start_offset=0, end_offset=5),
                _event("오늘 종료", start_offset=-5, end_offset=0),
            ]
        )

        response = await _service(provider).fetch_info_context(_request())

        assert len(_event_result(response).events) == 2

    @pytest.mark.asyncio
    async def test_진행_중이_없으면_no_data다(self) -> None:
        provider = StubFestivalProvider([_event("끝난 행사", start_offset=-30, end_offset=-10)])

        response = await _service(provider).fetch_info_context(_request())

        assert response.status == "no_data"
        result = _event_result(response)
        assert result.events == []
        # 장소는 찾았으므로 남긴다 — A가 "경복궁 근처에 진행 중인 행사가 없어요"로 안내한다.
        assert result.resolved_place_name == "경복궁"


class TestOrdering:
    @pytest.mark.asyncio
    async def test_직접_매칭을_먼저_보여준다(self) -> None:
        # 제목에 장소명이 든 행사는 더 멀어도 앞에 온다.
        provider = StubFestivalProvider(
            [
                _event("가까운 남의 행사", latitude=37.5789, longitude=126.9771),
                _event("경복궁 별빛야행", latitude=37.5700, longitude=126.9900),
            ]
        )

        response = await _service(provider).fetch_info_context(_request())

        result = _event_result(response)
        assert result.events[0].title == "경복궁 별빛야행"
        assert result.events[0].is_direct_match is True
        assert result.events[1].is_direct_match is False
        assert result.has_direct_match is True

    @pytest.mark.asyncio
    async def test_직접_매칭이_없으면_가까운_순이다(self) -> None:
        provider = StubFestivalProvider(
            [
                _event("먼 행사", latitude=37.6000, longitude=127.0200),
                _event("가까운 행사", latitude=37.5790, longitude=126.9772),
            ]
        )

        response = await _service(provider).fetch_info_context(_request())

        result = _event_result(response)
        assert [item.title for item in result.events] == ["가까운 행사", "먼 행사"]
        assert result.has_direct_match is False
        near, far = result.events
        assert near.distance_km is not None and far.distance_km is not None
        assert near.distance_km < far.distance_km

    @pytest.mark.asyncio
    async def test_좌표가_없어도_목록에서_빠지지_않는다(self) -> None:
        # 목록에서 빼면 "행사가 없다"로 잘못 보인다.
        provider = StubFestivalProvider(
            [
                _event("좌표 없는 행사", latitude=None, longitude=None),
                _event("좌표 있는 행사"),
            ]
        )

        response = await _service(provider).fetch_info_context(_request())

        result = _event_result(response)
        titles = [item.title for item in result.events]
        assert "좌표 없는 행사" in titles
        no_coord = next(i for i in result.events if i.title == "좌표 없는 행사")
        assert no_coord.distance_km is None
        assert titles[-1] == "좌표 없는 행사"  # 거리 아는 것 뒤로

    @pytest.mark.asyncio
    async def test_상한_건수까지만_싣는다(self) -> None:
        provider = StubFestivalProvider(
            [_event(f"행사{index}") for index in range(INFO_EVENT_RESULT_LIMIT + 3)]
        )

        response = await _service(provider).fetch_info_context(_request())

        assert len(_event_result(response).events) == INFO_EVENT_RESULT_LIMIT


class TestQuery:
    @pytest.mark.asyncio
    async def test_법정동_코드로_조회한다(self) -> None:
        # sigunguCode를 쓰면 응답 다수가 필터에서 탈락한다(D-055).
        # 구는 넘기지 않는다 — 서울 전체를 한 번에 받아 지원 구만 남긴다(D-025).
        provider = StubFestivalProvider([_event("행사")])

        await _service(provider).fetch_info_context(_request())

        assert provider.calls == [("11", None, TODAY)]


class TestFailures:
    @pytest.mark.asyncio
    async def test_외부_API_장애는_unavailable이다(self) -> None:
        provider = StubFestivalProvider(error=ProviderUnavailableError("TourAPI"))

        response = await _service(provider).fetch_info_context(_request())

        assert response.status == "unavailable"
        assert response.error is not None
        assert response.error.retryable is True

    @pytest.mark.asyncio
    async def test_Tool이_없으면_unavailable로_알린다(self) -> None:
        response = await _service().fetch_info_context(_request())

        assert response.status == "unavailable"
        assert response.error is not None
        assert response.error.code == "festival_not_configured"

    @pytest.mark.asyncio
    async def test_place_name이_없으면_되묻는다(self) -> None:
        provider = StubFestivalProvider([_event("행사")])

        response = await _service(provider).fetch_info_context(_request(place_name=None))

        assert response.status == "needs_clarification"
        assert provider.calls == []

    @pytest.mark.asyncio
    async def test_행사_되묻기는_전용_코드를_쓴다(self) -> None:
        """"축제 추천해줘"는 장소를 물은 발화가 아니다(TP-237).

        `place_required`를 그대로 쓰면 A가 "어떤 장소에 대해 알고 싶으신가요?"로
        되묻는데, 사용자가 묻지 않은 것을 되묻는 셈이 된다.
        """
        provider = StubFestivalProvider([_event("행사")])

        response = await _service(provider).fetch_info_context(_request(place_name=None))

        assert response.clarification is not None
        assert response.clarification.code == "event_place_required"
        assert response.clarification.missing_fields == ["place_name"]

    @pytest.mark.asyncio
    async def test_realtime_event도_같은_되묻기_코드를_쓴다(self) -> None:
        response = await _service(
            StubFestivalProvider([_event("행사")]), _EmptyEventCityDataProvider()
        ).fetch_info_context(
            InfoContextRequest(
                request_id="request-realtime-event-clarify",
                place_name=None,
                place_context="explicit",
                question_type="realtime_event",
            )
        )

        assert response.clarification is not None
        assert response.clarification.code == "event_place_required"

    @pytest.mark.asyncio
    async def test_행사가_아닌_질의는_기존_코드를_유지한다(self) -> None:
        """되묻기 코드를 늘린 것이 다른 유형까지 바꾸지 않았는지 못 박는다."""
        response = await _service().fetch_info_context(
            InfoContextRequest(
                request_id="request-hours-clarify",
                place_name=None,
                place_context="explicit",
                question_type="operating_hours",
            )
        )

        assert response.clarification is not None
        assert response.clarification.code == "place_required"


class TestFakeProviderShape:
    """Fake가 실제 데이터 특성을 유지하는지 고정한다.

    실측(2026-08-07) 종로구 진행 중 6건 중 장소명과 이름이 붙는 건 0건이었다.
    Fake가 전부 직접 매칭이면 근접 경로가 한 번도 실행되지 않는다.
    """

    @pytest.mark.asyncio
    async def test_fake는_진행_중과_아닌_것을_모두_갖는다(self) -> None:
        events = (await FakeFestivalProvider().search_festivals("11", "110", TODAY)).data

        ongoing = [item for item in events if item.is_ongoing(TODAY)]
        assert len(ongoing) >= 2
        assert len(ongoing) < len(events)  # 걸러지는 것도 있어야 한다

    @pytest.mark.asyncio
    async def test_fake에_장소명과_안_붙는_행사가_있다(self) -> None:
        response = await _service(FakeFestivalProvider()).fetch_info_context(_request())

        result = _event_result(response)
        assert any(item.is_direct_match for item in result.events)
        assert any(not item.is_direct_match for item in result.events)

    @pytest.mark.asyncio
    async def test_fake_좌표로_거리가_계산된다(self) -> None:
        response = await _service(FakeFestivalProvider()).fetch_info_context(_request())

        distances = [item.distance_km for item in _event_result(response).events]
        assert all(value is not None for value in distances)
        assert any(value > 0 for value in distances if value is not None)


class _EmptyEventCityDataProvider(FakeRealtimeCityDataProvider):
    """서울시 실시간 도시데이터가 행사를 하나도 안 주는 상태."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get_area_citydata(self, area_name_or_code: str):  # type: ignore[no-untyped-def]
        self.calls.append(area_name_or_code)
        result = await super().get_area_citydata(area_name_or_code)
        return provider_result(
            replace(result.data, events=()),
            source=result.metadata.source,
            status=result.metadata.status,
        )


def _realtime_event_request() -> InfoContextRequest:
    return InfoContextRequest(
        request_id="request-realtime-event-1",
        place_name="경복궁",
        place_context="explicit",
        question_type="realtime_event",
    )


class TestRealtimeEventFallback:
    """서울시 실시간이 비면 TourAPI로 한 번 더 본다.

    두 출처가 거의 겹치지 않아서다 — 2026-09-04 실측에서 그날 진행 중인 행사가 서울시
    95건·TourAPI 21건인데 양쪽에 다 있는 것은 3건뿐이었다. 한쪽만 보고 "없다"고 답하면
    다른 쪽에 있는 것을 통째로 놓친다.
    """

    @pytest.mark.asyncio
    async def test_서울시_실시간이_비면_TourAPI_행사를_돌려준다(self) -> None:
        citydata = _EmptyEventCityDataProvider()
        festivals = StubFestivalProvider([_event("한복문화주간")])

        response = await _service(festivals, citydata).fetch_info_context(
            _realtime_event_request()
        )

        assert citydata.calls, "서울시 실시간을 먼저 봐야 한다"
        assert festivals.calls, "비었으면 TourAPI로 넘어가야 한다"
        result = _event_result(response)
        assert response.status == "success"
        assert [item.title for item in result.events] == ["한복문화주간"]

    @pytest.mark.asyncio
    async def test_서울시_실시간에_행사가_있으면_TourAPI를_부르지_않는다(self) -> None:
        festivals = StubFestivalProvider([_event("한복문화주간")])

        response = await _service(festivals, FakeRealtimeCityDataProvider()).fetch_info_context(
            _realtime_event_request()
        )

        assert not festivals.calls, "서울시 실시간으로 답했으면 TourAPI를 부를 이유가 없다"
        assert response.status == "success"
        assert not isinstance(response.result, EventInfoResult)

    @pytest.mark.asyncio
    async def test_양쪽_다_비면_no_data다(self) -> None:
        citydata = _EmptyEventCityDataProvider()
        # 작년에 끝난 행사만 있는 상태 — is_ongoing()이 전부 떨어뜨린다.
        festivals = StubFestivalProvider([_event("작년 축제", start_offset=-400, end_offset=-390)])

        response = await _service(festivals, citydata).fetch_info_context(
            _realtime_event_request()
        )

        assert festivals.calls
        assert response.status == "no_data"
        assert _event_result(response).events == []
