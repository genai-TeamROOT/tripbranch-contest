from datetime import UTC, datetime

import pytest

from app.agent_context.assembler import (
    ContextAssemblyInput,
    assemble_agent_context_response,
)
from app.agent_context.schemas import AgentContextRequest
from app.domain.models import (
    HolidayEntry,
    HolidayResult,
    PlaceDetails,
)
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


def _request(*, with_location: bool = True) -> AgentContextRequest:
    return AgentContextRequest(
        request_id="request-1",
        intent="RECOMMEND",
        conditions={
            "current_location": "경복궁" if with_location else None,
            "search_center": None,
        },
    )


def _metadata(
    source: ProviderSource,
    status: ProviderStatus = ProviderStatus.SUCCESS,
) -> ProviderMetadata:
    return ProviderMetadata(
        source=source,
        status=status,
        retrieved_at=RETRIEVED_AT,
    )


def _error(
    code: str,
    *,
    cause: str,
    retryable: bool = False,
) -> ToolError:
    return ToolError(
        code=code,
        message=f"{code} 오류",
        cause=cause,
        retryable=retryable,
    )


def _location(
    status: ToolStatus = ToolStatus.SUCCESS,
    *,
    cause: str | None = None,
) -> ResolveLocationResult:
    return ResolveLocationResult(
        status=status,
        location=(
            ResolvedLocation(
                requested_query="경복궁",
                provider_query="경복궁",
                resolved_name="경복궁",
                latitude=37.5796,
                longitude=126.977,
                resolution_method=ResolutionMethod.DIRECT,
                confidence=ResolutionConfidence.APPROXIMATE,
            )
            if status is ToolStatus.SUCCESS
            else None
        ),
        error=(
            _error(status.value, cause=cause or status.value)
            if status is not ToolStatus.SUCCESS
            else None
        ),
        provider_metadata=(_metadata(ProviderSource.FAKE_GEOCODING),),
    )


def _weather(
    status: ToolStatus = ToolStatus.SUCCESS,
) -> WeatherForecastToolResult:
    return WeatherForecastToolResult(
        status=status,
        forecast=(
            SelectedWeatherForecast(
                latitude=37.5796,
                longitude=126.977,
                grid_x=60,
                grid_y=127,
                requested_visit_at=RETRIEVED_AT,
                forecast_for=RETRIEVED_AT,
                sky_code="1",
                precipitation_type="0",
                data_type="forecast",
                observed_at=None,
                retrieved_at=RETRIEVED_AT,
                timezone="Asia/Seoul",
                timezone_assumed=False,
                selection_method=ForecastSelectionMethod.NEAREST,
            )
            if status is ToolStatus.SUCCESS
            else None
        ),
        error=(
            _error("unavailable", cause="timeout", retryable=True)
            if status is ToolStatus.UNAVAILABLE
            else None
        ),
        provider_metadata=(
            _metadata(ProviderSource.FAKE_WEATHER),
        ),
    )


def _places(
    status: ToolStatus = ToolStatus.SUCCESS,
) -> NearbyPlaceDetailsResult:
    candidate = PlaceCandidate(
        place_id="126508",
        content_type_id="12",
        name="경복궁",
        category="attraction",
        latitude=37.5796,
        longitude=126.977,
        raw_source="fake",
    )
    details = PlaceDetails(
        content_id="126508",
        content_type_id="12",
        title="경복궁",
        address=None,
        overview="상세",
        homepage=None,
        telephone=None,
        operating_hours=None,
        rest_date=None,
        raw_common={},
        raw_intro={},
        provider="fake",
    )
    places = ()
    if status in {ToolStatus.SUCCESS, ToolStatus.PARTIAL}:
        places = (
            EnrichedPlace(
                candidate=candidate,
                details=details if status is ToolStatus.SUCCESS else None,
                detail_status=(
                    DetailStatus.SUCCESS
                    if status is ToolStatus.SUCCESS
                    else DetailStatus.UNAVAILABLE
                ),
            ),
        )
    return NearbyPlaceDetailsResult(
        places=places,
        status=status,
        source="fake",
        retrieved_at=RETRIEVED_AT,
        elapsed_ms=1,
        error=(
            _error("unavailable", cause="timeout", retryable=True)
            if status is ToolStatus.UNAVAILABLE
            else None
        ),
        warnings=("partial_data",) if status is ToolStatus.PARTIAL else (),
        provider_metadata=(_metadata(ProviderSource.FAKE_PLACE),),
    )


def _holidays(
    status: ToolStatus = ToolStatus.SUCCESS,
) -> HolidayToolResult:
    entries = (
        (
            HolidayEntry(
                date="2026-07-17",
                name="제헌절",
                kind="01",
                sequence=1,
                is_holiday=True,
                raw_data={},
            ),
        )
        if status is ToolStatus.SUCCESS
        else ()
    )
    return HolidayToolResult(
        status=status,
        holidays=(
            HolidayResult(
                year=2026,
                month=7,
                entries=entries,
                provider="fake",
            )
            if status in {ToolStatus.SUCCESS, ToolStatus.NO_DATA}
            else None
        ),
        error=(
            _error("unavailable", cause="timeout", retryable=True)
            if status is ToolStatus.UNAVAILABLE
            else None
        ),
        provider_metadata=(
            _metadata(
                ProviderSource.FAKE_HOLIDAY,
                (
                    ProviderStatus.NO_DATA
                    if status is ToolStatus.NO_DATA
                    else ProviderStatus.SUCCESS
                ),
            ),
        ),
    )


def _assembly(
    *,
    request: AgentContextRequest | None = None,
    location: ResolveLocationResult | None = None,
    weather: WeatherForecastToolResult | None = None,
    places: NearbyPlaceDetailsResult | None = None,
    holidays: HolidayToolResult | None = None,
) -> ContextAssemblyInput:
    return ContextAssemblyInput(
        request=request or _request(),
        location_result=location if location is not None else _location(),
        weather_result=weather if weather is not None else _weather(),
        places_result=places if places is not None else _places(),
        holidays_result=holidays if holidays is not None else _holidays(),
    )


def test_assembles_success_and_rule_versions() -> None:
    response = assemble_agent_context_response(
        _assembly(),
        rule_versions={"operating_hours_normalization": "v1"},
    )

    assert response.status == "success"
    assert response.context is not None
    assert response.metadata.rule_versions == {
        "operating_hours_normalization": "v1"
    }
    assert [item.source for item in response.metadata.provider_metadata] == [
        "fake_geocoding",
        "fake_weather",
        "fake_place",
        "fake_holiday",
    ]


def test_holiday_no_data_keeps_success_status() -> None:
    response = assemble_agent_context_response(
        _assembly(holidays=_holidays(ToolStatus.NO_DATA))
    )

    assert response.status == "success"
    assert response.context is not None
    assert response.context.holidays is not None
    assert response.context.holidays.data == []


@pytest.mark.parametrize(
    ("weather", "holidays", "warning_code"),
    [
        (_weather(ToolStatus.UNAVAILABLE), _holidays(), "weather_missing"),
        (_weather(), _holidays(ToolStatus.UNAVAILABLE), "holiday_missing"),
    ],
)
def test_optional_provider_failure_returns_partial(
    weather: WeatherForecastToolResult,
    holidays: HolidayToolResult,
    warning_code: str,
) -> None:
    response = assemble_agent_context_response(
        _assembly(weather=weather, holidays=holidays)
    )

    assert response.status == "partial"
    assert response.error is None
    assert warning_code in {warning.code for warning in response.warnings}


def test_partial_place_details_keeps_candidates() -> None:
    response = assemble_agent_context_response(
        _assembly(places=_places(ToolStatus.PARTIAL))
    )

    assert response.status == "partial"
    assert response.context is not None
    assert response.context.places is not None
    assert response.context.places.data
    assert response.context.places.data[0].place_id == "126508"


def test_empty_places_returns_no_data() -> None:
    response = assemble_agent_context_response(
        _assembly(places=_places(ToolStatus.NO_DATA))
    )

    assert response.status == "no_data"
    assert response.context is not None
    assert response.context.places is not None
    assert response.context.places.data == []


def test_place_provider_failure_returns_unavailable_with_location_context() -> None:
    response = assemble_agent_context_response(
        _assembly(places=_places(ToolStatus.UNAVAILABLE))
    )

    assert response.status == "unavailable"
    assert response.context is not None
    assert response.context.location is not None
    assert response.context.places is not None
    assert response.context.places.status == "unavailable"
    assert response.error is not None


def test_missing_location_result_requests_clarification() -> None:
    assembly = _assembly(request=_request(with_location=False))
    assembly = ContextAssemblyInput(
        request=assembly.request,
        location_result=None,
    )

    response = assemble_agent_context_response(assembly)

    assert response.status == "needs_clarification"
    assert response.clarification is not None
    assert response.clarification.code == "location_required"
    assert response.error is None


def test_ambiguous_location_requests_clarification() -> None:
    response = assemble_agent_context_response(
        _assembly(
            location=_location(
                ToolStatus.NO_DATA,
                cause="ambiguous_location",
            )
        )
    )

    assert response.status == "needs_clarification"
    assert response.clarification is not None
    assert response.clarification.code == "location_ambiguous"
    assert response.clarification.missing_fields == []


def test_location_outside_region_is_unsupported_and_keeps_metadata() -> None:
    response = assemble_agent_context_response(
        _assembly(
            location=_location(
                ToolStatus.UNSUPPORTED,
                cause="outside_supported_region",
            )
        )
    )

    assert response.status == "unsupported"
    assert response.context is None
    assert response.error is not None
    assert response.metadata.provider_metadata[0].source == "fake_geocoding"


def test_location_provider_failure_is_unavailable() -> None:
    response = assemble_agent_context_response(
        _assembly(
            location=_location(
                ToolStatus.UNAVAILABLE,
                cause="timeout",
            )
        )
    )

    assert response.status == "unavailable"
    assert response.context is not None
    assert response.error is not None


def test_missing_optional_results_are_partial_and_warnings_are_unique() -> None:
    response = assemble_agent_context_response(
        ContextAssemblyInput(
            request=_request(),
            location_result=_location(),
            places_result=_places(ToolStatus.PARTIAL),
        )
    )

    assert response.status == "partial"
    assert [warning.code for warning in response.warnings] == [
        "partial_data",
        "weather_missing",
        "holiday_missing",
    ]


def test_missing_places_result_is_unavailable() -> None:
    response = assemble_agent_context_response(
        ContextAssemblyInput(
            request=_request(),
            location_result=_location(),
            weather_result=_weather(),
            places_result=None,
            holidays_result=_holidays(),
        )
    )

    assert response.status == "unavailable"
    assert response.error is not None
    assert response.error.code == "places_not_collected"
