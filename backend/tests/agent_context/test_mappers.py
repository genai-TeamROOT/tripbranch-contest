from datetime import UTC, datetime

import pytest

from app.agent_context.mappers import (
    map_holidays_context,
    map_location_context,
    map_places_context,
    map_weather_context,
)
from app.agent_context.schemas import AgentContextResponse, RecommendationContext
from app.domain.models import (
    HolidayEntry,
    HolidayResult,
    PlaceDetails,
)
from app.domain.operating_hours import normalize_operating_schedule
from app.providers.contracts import (
    ProviderMetadata,
    ProviderSource,
    ProviderStatus,
)
from app.schemas import PlaceCandidate
from app.tools.contracts import ToolError, ToolStatus
from app.tools.holiday import HolidayToolResult
from app.tools.nearby_place_details import (
    DetailStatus,
    EnrichedPlace,
    NearbyPlaceDetailsResult,
)
from app.tools.resolve_location import (
    ResolutionConfidence,
    ResolutionMethod,
    ResolvedLocation,
    ResolveLocationResult,
)
from app.tools.weather_forecast import (
    ForecastSelectionMethod,
    SelectedWeatherForecast,
    WeatherForecastToolResult,
)

RETRIEVED_AT = datetime(2026, 7, 24, 6, tzinfo=UTC)


def _metadata(
    source: ProviderSource,
    status: ProviderStatus = ProviderStatus.SUCCESS,
) -> ProviderMetadata:
    return ProviderMetadata(
        source=source,
        status=status,
        retrieved_at=RETRIEVED_AT,
    )


def _tool_error(code: str = "unavailable") -> ToolError:
    return ToolError(
        code=code,
        message="외부 정보를 가져오지 못했습니다.",
        cause="timeout",
        retryable=True,
    )


def test_maps_location_success_and_provider_metadata() -> None:
    result = ResolveLocationResult(
        status=ToolStatus.SUCCESS,
        location=ResolvedLocation(
            requested_query="경복궁",
            provider_query="서울특별시 종로구 사직로 161",
            resolved_name="경복궁",
            latitude=37.5796,
            longitude=126.977,
            resolution_method=ResolutionMethod.ALIAS,
            confidence=ResolutionConfidence.EXACT,
        ),
        error=None,
        warnings=("fallback_used",),
        provider_metadata=(_metadata(ProviderSource.NAVER_GEOCODING),),
    )

    context = map_location_context(result)

    assert context.status == "success"
    assert context.data is not None
    assert context.data.location.latitude == 37.5796
    assert context.data.address is None
    assert context.warnings[0].code == "fallback_used"
    assert context.provider_metadata[0].source == "naver_geocoding"
    assert context.provider_metadata[0].retrieved_at == RETRIEVED_AT


@pytest.mark.parametrize(
    ("status", "cause"),
    [
        (ToolStatus.UNSUPPORTED, "outside_supported_region"),
        (ToolStatus.UNAVAILABLE, "timeout"),
    ],
)
def test_maps_blocked_location_with_error(
    status: ToolStatus,
    cause: str,
) -> None:
    result = ResolveLocationResult(
        status=status,
        location=None,
        error=ToolError(
            code=status.value,
            message="위치를 사용할 수 없습니다.",
            cause=cause,
            retryable=status is ToolStatus.UNAVAILABLE,
        ),
    )

    context = map_location_context(result)

    assert context.status == status.value
    assert context.data is None
    assert context.error is not None
    assert context.error.retryable is (status is ToolStatus.UNAVAILABLE)


def test_maps_location_no_data_as_empty_single_value() -> None:
    context = map_location_context(
        ResolveLocationResult(
            status=ToolStatus.NO_DATA,
            location=None,
            error=ToolError(
                code="no_data",
                message="위치를 찾지 못했습니다.",
                cause="ambiguous_location",
                retryable=False,
            ),
        )
    )

    assert context.status == "no_data"
    assert context.data is None
    assert context.error is None


def test_maps_weather_success_without_inventing_temperature() -> None:
    result = WeatherForecastToolResult(
        status=ToolStatus.SUCCESS,
        forecast=SelectedWeatherForecast(
            latitude=37.5796,
            longitude=126.977,
            grid_x=60,
            grid_y=127,
            requested_visit_at=RETRIEVED_AT,
            forecast_for=RETRIEVED_AT,
            sky_code="3",
            precipitation_type="0",
            data_type="forecast",
            observed_at=None,
            retrieved_at=RETRIEVED_AT,
            timezone="Asia/Seoul",
            timezone_assumed=False,
            selection_method=ForecastSelectionMethod.NEAREST,
        ),
        error=None,
        provider_metadata=(_metadata(ProviderSource.KMA_ULTRA_SHORT_FORECAST),),
    )

    context = map_weather_context(result)

    assert context.status == "success"
    assert context.data is not None
    # D-051: Tool 결과에 condition이 있어도 C는 A–C 계약에 판정을 싣지 않는다.
    # 사실(precipitation/sky/temperature)만 넘기고 판정은 D가 사용자 의도까지
    # 반영해 다시 내린다.
    assert not hasattr(context.data, "condition")
    assert context.data.sky == "cloudy"
    assert context.data.precipitation == "none"
    # Tool 결과에 기온이 없으면 만들어내지 않는다.
    assert context.data.temperature_celsius is None
    assert context.data.forecast_for == RETRIEVED_AT
    # 기상청 코드를 그대로 넘기지 않고 도메인 용어로 옮긴다(D-051 제안).
    assert context.data.precipitation == "none"
    assert context.data.sky == "cloudy"


def test_maps_weather_facts_without_judging_them() -> None:
    """C는 사실만 옮기고 좋다/나쁘다 판정은 하지 않는다.

    비(PTY=1)는 지금 condition=bad로도 오지만, 사용자가 비를 즐기려는 경우
    (weather_intent=ENJOY) 그 판정이 뒤집혀야 한다. 판정 근거가 되는 사실을 D가
    받아야 그 전환이 가능하다.
    """
    result = WeatherForecastToolResult(
        status=ToolStatus.SUCCESS,
        forecast=SelectedWeatherForecast(
            latitude=37.5796,
            longitude=126.977,
            grid_x=60,
            grid_y=127,
            requested_visit_at=RETRIEVED_AT,
            forecast_for=RETRIEVED_AT,
            sky_code="1",
            precipitation_type="1",
            data_type="forecast",
            observed_at=None,
            retrieved_at=RETRIEVED_AT,
            timezone="Asia/Seoul",
            timezone_assumed=False,
            selection_method=ForecastSelectionMethod.NEAREST,
            temperature_celsius=35.0,
        ),
        error=None,
        provider_metadata=(_metadata(ProviderSource.KMA_ULTRA_SHORT_FORECAST),),
    )

    context = map_weather_context(result)

    assert context.data is not None
    assert context.data.precipitation == "rain"
    assert context.data.sky == "clear"
    assert context.data.temperature_celsius == 35.0


def test_maps_unknown_weather_codes_to_none() -> None:
    """기상청이 새 코드를 추가해도 ValidationError로 요청을 깨지 않는다."""
    result = WeatherForecastToolResult(
        status=ToolStatus.SUCCESS,
        forecast=SelectedWeatherForecast(
            latitude=37.5796,
            longitude=126.977,
            grid_x=60,
            grid_y=127,
            requested_visit_at=RETRIEVED_AT,
            forecast_for=RETRIEVED_AT,
            sky_code="9",
            precipitation_type="9",
            data_type="forecast",
            observed_at=None,
            retrieved_at=RETRIEVED_AT,
            timezone="Asia/Seoul",
            timezone_assumed=False,
            selection_method=ForecastSelectionMethod.NEAREST,
        ),
        error=None,
        provider_metadata=(_metadata(ProviderSource.KMA_ULTRA_SHORT_FORECAST),),
    )

    context = map_weather_context(result)

    assert context.data is not None
    assert context.data.precipitation is None
    assert context.data.sky is None


def test_maps_weather_timeout_to_non_blocking_context_error() -> None:
    context = map_weather_context(
        WeatherForecastToolResult(
            status=ToolStatus.UNAVAILABLE,
            forecast=None,
            error=_tool_error(),
        )
    )

    assert context.status == "unavailable"
    assert context.data is None
    assert context.error is not None
    assert context.error.retryable is True


def _place_details() -> PlaceDetails:
    return PlaceDetails(
        content_id="126508",
        content_type_id="12",
        title="경복궁",
        address="서울특별시 종로구",
        overview=None,
        homepage=None,
        telephone=None,
        operating_hours="09:00~18:00",
        rest_date="매주 화요일",
        raw_common={},
        raw_intro={},
        provider="tour_api",
        operating_schedule=normalize_operating_schedule(
            content_type_id="12",
            operating_hours="09:00~18:00",
            rest_date="매주 화요일",
        ),
    )


def _candidate(place_id: str = "126508") -> PlaceCandidate:
    return PlaceCandidate(
        place_id=place_id,
        content_type_id="12",
        lcls_systm1="HS",
        lcls_systm2="HS01",
        lcls_systm3="HS010100",
        name="경복궁",
        category="attraction",
        latitude=37.5796,
        longitude=126.977,
        raw_source="tour_api",
    )


def test_세분류_코드가_C에서_A로_전달된다() -> None:
    """D가 실내외를 판정하려면 대분류만으로는 부족하다."""
    result = NearbyPlaceDetailsResult(
        places=(
            EnrichedPlace(
                candidate=_candidate(),
                details=None,
                detail_status=DetailStatus.NO_DATA,
            ),
        ),
        status=ToolStatus.SUCCESS,
        source="nearby_place_details_tool",
        retrieved_at=RETRIEVED_AT,
        elapsed_ms=10,
        provider_metadata=(_metadata(ProviderSource.TOUR_API_PLACE),),
    )

    context = map_places_context(result)

    assert context.data is not None
    place = context.data[0]
    assert (place.lcls_systm1, place.lcls_systm2, place.lcls_systm3) == (
        "HS",
        "HS01",
        "HS010100",
    )


def test_maps_places_with_raw_and_normalized_operating_information() -> None:
    result = NearbyPlaceDetailsResult(
        places=(
            EnrichedPlace(
                candidate=_candidate(),
                details=_place_details(),
                detail_status=DetailStatus.SUCCESS,
            ),
        ),
        status=ToolStatus.SUCCESS,
        source="nearby_place_details_tool",
        retrieved_at=RETRIEVED_AT,
        elapsed_ms=10,
        provider_metadata=(_metadata(ProviderSource.TOUR_API_PLACE),),
    )

    context = map_places_context(result)

    assert context.status == "success"
    assert context.data is not None
    place = context.data[0]
    assert place.operating_hours_raw == "09:00~18:00"
    assert place.rest_date_raw == "매주 화요일"
    assert place.operating_schedule is not None
    assert place.operating_schedule["time_ranges"] == [
        {
            "open_time": "09:00",
            "close_time": "18:00",
            "crosses_midnight": False,
        }
    ]
    assert place.operating_schedule["rules"] == [
        {
            "months": None,
            "weekdays": None,
            "time_ranges": [
                {
                    "open_time": "09:00",
                    "close_time": "18:00",
                    "crosses_midnight": False,
                }
            ],
        }
    ]
    assert place.operating_schedule["closure_rules"][0]["weekdays"] == ["tuesday"]


def test_maps_partial_places_without_dropping_unverified_candidate() -> None:
    result = NearbyPlaceDetailsResult(
        places=(
            EnrichedPlace(
                candidate=_candidate("place-without-details"),
                details=None,
                detail_status=DetailStatus.UNAVAILABLE,
                error_code="provider_unavailable",
            ),
        ),
        status=ToolStatus.PARTIAL,
        source="nearby_place_details_tool",
        retrieved_at=RETRIEVED_AT,
        elapsed_ms=10,
        warnings=("partial_data",),
        provider_metadata=(_metadata(ProviderSource.TOUR_API_PLACE),),
    )

    context = map_places_context(result)

    assert context.status == "partial"
    assert context.data is not None
    assert context.data[0].place_id == "place-without-details"
    assert context.data[0].operating_schedule is None
    assert context.warnings[0].code == "partial_data"


def test_maps_place_no_data_to_empty_list() -> None:
    context = map_places_context(
        NearbyPlaceDetailsResult(
            places=(),
            status=ToolStatus.NO_DATA,
            source="nearby_place_details_tool",
            retrieved_at=RETRIEVED_AT,
            elapsed_ms=1,
            provider_metadata=(
                _metadata(
                    ProviderSource.TOUR_API_PLACE,
                    ProviderStatus.NO_DATA,
                ),
            ),
        )
    )

    assert context.status == "no_data"
    assert context.data == []
    assert context.provider_metadata[0].status == "no_data"


def test_maps_holidays_and_empty_holiday_result() -> None:
    entry = HolidayEntry(
        date="2026-08-15",
        name="광복절",
        kind="01",
        sequence=1,
        is_holiday=True,
        raw_data={},
    )
    success = map_holidays_context(
        HolidayToolResult(
            status=ToolStatus.SUCCESS,
            holidays=HolidayResult(
                year=2026,
                month=8,
                entries=(entry,),
                provider="fake",
            ),
            error=None,
            provider_metadata=(_metadata(ProviderSource.FAKE_HOLIDAY),),
        )
    )
    no_data = map_holidays_context(
        HolidayToolResult(
            status=ToolStatus.NO_DATA,
            holidays=HolidayResult(
                year=2026,
                month=7,
                entries=(),
                provider="fake",
            ),
            error=None,
            provider_metadata=(_metadata(ProviderSource.FAKE_HOLIDAY, ProviderStatus.NO_DATA),),
        )
    )

    assert success.data is not None
    assert success.data[0].name == "광복절"
    assert no_data.data == []


def test_mapped_context_is_accepted_by_top_level_response_contract() -> None:
    location = map_location_context(
        ResolveLocationResult(
            status=ToolStatus.SUCCESS,
            location=ResolvedLocation(
                requested_query="경복궁",
                provider_query="경복궁",
                resolved_name="경복궁",
                latitude=37.5796,
                longitude=126.977,
                resolution_method=ResolutionMethod.DIRECT,
                confidence=ResolutionConfidence.APPROXIMATE,
            ),
            error=None,
            provider_metadata=(_metadata(ProviderSource.FAKE_GEOCODING),),
        )
    )
    places = map_places_context(
        NearbyPlaceDetailsResult(
            places=(
                EnrichedPlace(
                    candidate=_candidate(),
                    details=_place_details(),
                    detail_status=DetailStatus.SUCCESS,
                ),
            ),
            status=ToolStatus.SUCCESS,
            source="nearby_place_details_tool",
            retrieved_at=RETRIEVED_AT,
            elapsed_ms=1,
            provider_metadata=(_metadata(ProviderSource.FAKE_PLACE),),
        )
    )

    response = AgentContextResponse(
        request_id="request-1",
        intent="RECOMMEND",
        status="success",
        context=RecommendationContext(location=location, places=places),
        metadata={"provider_metadata": [], "rule_versions": {}},
    )

    assert response.context is not None
    assert response.context.places is not None
