"""INFO 상세 질의(concentration 외)가 ContextService에서 끝까지 도는지 검증한다."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.agent_context.info_schemas import (
    InfoContextRequest,
    InfoContextResponse,
    InfoQuestionType,
    PlaceInfoResult,
)
from app.agent_context.service import ContextService, ContextTools
from app.domain.models import PlaceDetails
from app.errors import AppError, ProviderUnavailableError
from app.providers.concentration import FakeConcentrationProvider
from app.providers.contracts import (
    ProviderResult,
    ProviderSource,
    ProviderStatus,
    provider_result,
)
from app.providers.geocoding import FakeGeocodingProvider
from app.providers.holiday import FakeHolidayProvider
from app.providers.stub import FakePlaceProvider, FakeWeatherProvider
from app.repositories.fake_places import FakePlaceLocationRepository
from app.tools.concentration import GetConcentrationTool
from app.tools.holiday import GetHolidaysTool
from app.tools.nearby_place_details import NearbyPlaceDetailsTool
from app.tools.place_detail import GetPlaceDetailTool
from app.tools.resolve_location import ResolveLocationTool
from app.tools.weather_forecast import GetWeatherForecastTool

KST = ZoneInfo("Asia/Seoul")


class RecordingPlaceProvider(FakePlaceProvider):
    """find_details_by_name의 인자와 호출 횟수를 기록하는 fake.

    ContextService가 사용자 발화가 아니라 해석된 정식 명칭으로 조회하는지,
    location_info처럼 상세 조회가 필요 없는 질문에서 호출을 아끼는지 본다.
    """

    def __init__(
        self,
        details: PlaceDetails | None = None,
        error: AppError | None = None,
        status: ProviderStatus = ProviderStatus.SUCCESS,
    ) -> None:
        self.requested_names: list[str] = []
        self._details = details
        self._error = error
        self._status = status

    async def find_details_by_name(
        self,
        name: str,
        region_code: str | None = None,
        district_code: str | None = None,
    ) -> ProviderResult[PlaceDetails]:
        self.requested_names.append(name)
        if self._error is not None:
            raise self._error
        assert self._details is not None
        return provider_result(
            self._details,
            source=ProviderSource.TOUR_API_PLACE,
            status=self._status,
        )


def _details(
    *,
    title: str = "경복궁",
    address: str | None = "서울특별시 종로구 사직로 161",
    overview: str | None = None,
    operating_hours: str | None = None,
    rest_date: str | None = None,
    raw_intro: dict[str, object] | None = None,
    parking: str | None = None,
    fee: str | None = None,
) -> PlaceDetails:
    return PlaceDetails(
        content_id="126508",
        content_type_id="14",
        title=title,
        address=address,
        overview=overview,
        homepage=None,
        telephone=None,
        operating_hours=operating_hours,
        rest_date=rest_date,
        raw_common={},
        raw_intro=raw_intro or {},
        provider="tour_api",
        parking=parking,
        fee=fee,
    )


def _service(
    place_provider: RecordingPlaceProvider | None = None,
    *,
    with_place_detail: bool = True,
) -> ContextService:
    search_provider = FakePlaceProvider()
    detail_provider = place_provider or RecordingPlaceProvider(_details())
    return ContextService(
        ContextTools(
            location=ResolveLocationTool(
                FakeGeocodingProvider(),
                place_repository=FakePlaceLocationRepository(),
            ),
            places=NearbyPlaceDetailsTool(search_provider, search_provider),
            weather=GetWeatherForecastTool(FakeWeatherProvider()),
            holidays=GetHolidaysTool(FakeHolidayProvider()),
            concentration=GetConcentrationTool(FakeConcentrationProvider()),
            place_detail=(
                GetPlaceDetailTool(detail_provider) if with_place_detail else None
            ),
        ),
        candidate_limit=10,
        clock=lambda: datetime.now(KST),
    )


def _request(
    question_type: InfoQuestionType,
    *,
    place_name: str | None = "경복궁",
) -> InfoContextRequest:
    return InfoContextRequest(
        request_id="request-info-1",
        place_name=place_name,
        place_context="explicit",
        question_type=question_type,
    )


def _place_result(response: InfoContextResponse) -> PlaceInfoResult:
    """result가 집중률이 아니라 상세 결과 타입인지 확인하고 좁힌다."""
    assert isinstance(response.result, PlaceInfoResult)
    return response.result


class TestOperatingHours:
    @pytest.mark.asyncio
    async def test_운영시간을_상세조회에서_채운다(self) -> None:
        provider = RecordingPlaceProvider(
            _details(operating_hours="09:00~18:00", rest_date="매주 화요일")
        )

        response = await _service(provider).fetch_info_context(
            _request("operating_hours")
        )

        assert response.status == "success"
        result = _place_result(response)
        assert result.question_type == "operating_hours"
        assert result.fields == {
            "operating_hours": "09:00~18:00",
            "rest_date": "매주 화요일",
        }

    @pytest.mark.asyncio
    async def test_해석된_정식_명칭으로_조회한다(self) -> None:
        # 사용자 발화 "경복궁"을 그대로 넘기지 않고 저장소 title로 조회해야
        # provider의 이름 정확 일치 검색에 걸린다.
        provider = RecordingPlaceProvider(_details(operating_hours="09:00~18:00"))

        await _service(provider).fetch_info_context(_request("operating_hours"))

        assert provider.requested_names == ["경복궁"]


class TestFee:
    @pytest.mark.asyncio
    async def test_요금을_정규화_필드에서_뽑는다(self) -> None:
        provider = RecordingPlaceProvider(
            _details(fee="어른 3,000원 / 어린이 1,500원")
        )

        response = await _service(provider).fetch_info_context(_request("fee"))

        assert response.status == "success"
        assert _place_result(response).fields == {
            "fee": "어른 3,000원 / 어린이 1,500원"
        }

    @pytest.mark.asyncio
    async def test_요금_정보가_없으면_no_data이고_장소는_남는다(self) -> None:
        # A가 "경복궁의 요금 정보는 없어요"처럼 장소를 짚어 안내할 수 있어야 한다.
        provider = RecordingPlaceProvider(_details(parking="가능"))

        response = await _service(provider).fetch_info_context(_request("fee"))

        assert response.status == "no_data"
        result = _place_result(response)
        assert result.fields == {}
        assert result.resolved_place_name == "경복궁"


class TestGeneralInfo:
    @pytest.mark.asyncio
    async def test_개요의_HTML을_정리해서_넘긴다(self) -> None:
        provider = RecordingPlaceProvider(
            _details(overview="조선의 법궁<br>경복궁입니다.")
        )

        response = await _service(provider).fetch_info_context(_request("general_info"))

        assert _place_result(response).fields["overview"] == "조선의 법궁 경복궁입니다."


class TestLocationInfo:
    @pytest.mark.asyncio
    async def test_장소_해석_결과만으로_답하고_상세조회를_하지_않는다(self) -> None:
        provider = RecordingPlaceProvider(_details())

        response = await _service(provider).fetch_info_context(
            _request("location_info")
        )

        assert response.status == "success"
        assert _place_result(response).fields == {
            "address": "서울특별시 종로구 사직로 161"
        }
        assert _place_result(response).destination_coordinates is not None
        assert _place_result(response).destination_coordinates.latitude == pytest.approx(37.5788)
        assert _place_result(response).destination_coordinates.longitude == pytest.approx(126.9770)
        assert provider.requested_names == []


class TestEvent:
    @pytest.mark.asyncio
    async def test_event는_행사_경로로_빠진다(self) -> None:
        # 상세 조회 경로가 아니라 별도 행사 경로를 탄다(자세한 검증은
        # test_info_event.py). Tool이 없으면 unavailable로 알린다.
        provider = RecordingPlaceProvider(_details())

        response = await _service(provider).fetch_info_context(_request("event"))

        assert response.status == "unavailable"
        assert response.error is not None
        assert response.error.code == "festival_not_configured"
        assert provider.requested_names == []


class TestFailures:
    @pytest.mark.asyncio
    async def test_장소를_못_찾으면_no_data다(self) -> None:
        provider = RecordingPlaceProvider(
            error=AppError(
                code="place_not_found",
                message="'경복궁' 장소를 정확히 찾을 수 없어요.",
                status_code=404,
            )
        )

        response = await _service(provider).fetch_info_context(_request("fee"))

        assert response.status == "no_data"
        assert _place_result(response).resolved_place_name == "경복궁"

    @pytest.mark.asyncio
    async def test_외부_API_장애는_unavailable로_올린다(self) -> None:
        provider = RecordingPlaceProvider(
            error=ProviderUnavailableError("TourAPI", detail="boom")
        )

        response = await _service(provider).fetch_info_context(_request("fee"))

        assert response.status == "unavailable"
        assert response.error is not None
        assert response.error.retryable is True

    @pytest.mark.asyncio
    async def test_Tool이_없으면_unavailable로_알린다(self) -> None:
        # D-042: 기능이 없는 것을 빈 결과로 위장하지 않는다.
        response = await _service(with_place_detail=False).fetch_info_context(
            _request("fee")
        )

        assert response.status == "unavailable"
        assert response.error is not None
        assert response.error.code == "place_detail_not_configured"

    @pytest.mark.asyncio
    async def test_place_name이_없으면_되묻는다(self) -> None:
        response = await _service().fetch_info_context(
            _request("fee", place_name=None)
        )

        assert response.status == "needs_clarification"
        assert response.clarification is not None
        assert response.clarification.code == "place_required"


class TestMetadata:
    @pytest.mark.asyncio
    async def test_장소_해석과_상세조회_출처를_모두_남긴다(self) -> None:
        provider = RecordingPlaceProvider(_details(raw_intro={"usefee": "무료"}))

        response = await _service(provider).fetch_info_context(_request("fee"))

        sources = [item.source for item in response.metadata.provider_metadata]
        assert ProviderSource.TOUR_API_PLACE.value in sources
        assert len(sources) >= 2


class TestConcentrationUnchanged:
    @pytest.mark.asyncio
    async def test_집중률_경로는_그대로_동작한다(self) -> None:
        # 분기를 넣으면서 기존 경로가 깨지지 않았는지 고정한다.
        response = await _service().fetch_info_context(_request("concentration"))

        assert response.status in ("success", "no_data")
        assert response.result is not None
        assert not isinstance(response.result, PlaceInfoResult)


def test_기본_question_type은_concentration이다() -> None:
    # 기존 호출부(A의 집중률 전용 배선)가 question_type을 안 보내도 동작해야 한다.
    request = InfoContextRequest(
        request_id="request-1",
        place_name="경복궁",
        place_context="explicit",
    )

    assert request.question_type == "concentration"
