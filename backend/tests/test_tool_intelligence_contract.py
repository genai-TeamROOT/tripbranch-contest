from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.domain.models import PlaceDetails
from app.domain.operating_hours import normalize_operating_schedule
from app.place_search_policy import DEFAULT_PLACE_SEARCH_RADIUS_KM
from app.providers.contracts import (
    ProviderMetadata,
    ProviderSource,
    ProviderStatus,
)
from app.recommendation_limits import DEFAULT_RECOMMENDATION_CANDIDATE_LIMIT
from app.schemas import PlaceCandidate
from app.tool_intelligence.mappers import (
    map_nearby_places_response,
    map_resolve_location_response,
    map_weather_response,
)
from app.tool_intelligence.schemas import (
    Coordinates,
    GetWeatherForecastRequest,
    ResolveLocationRequest,
    ResponseStatus,
    SearchNearbyPlacesRequest,
    ToolType,
    validate_tool_request,
)
from app.tools.contracts import ToolStatus
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

FIXED_RETRIEVED_AT = datetime(2026, 7, 24, 6, 0, tzinfo=UTC)


def _metadata(source: ProviderSource) -> ProviderMetadata:
    return ProviderMetadata(
        source=source,
        status=ProviderStatus.SUCCESS,
        retrieved_at=FIXED_RETRIEVED_AT,
    )


def test_tool_request_uses_discriminated_union_and_coordinate_object() -> None:
    request = validate_tool_request(
        {
            "request_id": "request-1",
            "tool_type": "get_weather_forecast",
            "parameters": {
                "location": {
                    "latitude": 37.5796,
                    "longitude": 126.977,
                },
                "visit_at": "2026-07-24T15:00:00+09:00",
            },
        }
    )

    assert isinstance(request, GetWeatherForecastRequest)
    assert request.parameters.location == Coordinates(
        latitude=37.5796,
        longitude=126.977,
    )
    assert request.parameters.visit_at is not None


def test_tool_request_rejects_string_location_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        validate_tool_request(
            {
                "request_id": "request-1",
                "tool_type": "search_nearby_places",
                "parameters": {
                    "location": "37.5796,126.977",
                    "unexpected": True,
                },
            }
        )


def test_resolve_location_response_returns_coordinates_and_retrieved_at() -> None:
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
        provider_metadata=(_metadata(ProviderSource.NAVER_GEOCODING),),
    )

    response = map_resolve_location_response("request-location", result)

    assert response.tool_type is ToolType.RESOLVE_LOCATION
    assert response.status is ResponseStatus.SUCCESS
    assert response.data is not None
    assert response.data.location.latitude == 37.5796
    assert response.data.retrieved_at == FIXED_RETRIEVED_AT
    assert response.provider_metadata[0].retrieved_at == FIXED_RETRIEVED_AT


def test_weather_response_carries_facts_without_judgment() -> None:
    """D-051: 계약에서 3단계 판정(condition)을 걷어냈다.

    `TI-09`(A의 `api_weather`가 C의 good/neutral/bad를 그대로 쓴다)는 무효다.
    C는 기상청 코드만 옮기고, 판정은 사용자 의도를 가진 D가 내린다.
    """
    result = WeatherForecastToolResult(
        status=ToolStatus.SUCCESS,
        forecast=SelectedWeatherForecast(
            latitude=37.5796,
            longitude=126.977,
            grid_x=60,
            grid_y=127,
            requested_visit_at=datetime(2026, 7, 24, 15, 0, tzinfo=UTC),
            forecast_for=datetime(2026, 7, 24, 15, 0, tzinfo=UTC),
            sky_code="1",
            precipitation_type="0",
            data_type="forecast",
            observed_at=None,
            retrieved_at=FIXED_RETRIEVED_AT,
            timezone="Asia/Seoul",
            timezone_assumed=False,
            selection_method=ForecastSelectionMethod.NEAREST,
        ),
        error=None,
        provider_metadata=(_metadata(ProviderSource.KMA_ULTRA_SHORT_FORECAST),),
    )

    response = map_weather_response("request-weather", result)

    assert response.status is ResponseStatus.SUCCESS
    assert response.data is not None
    assert not hasattr(response.data, "condition")
    assert response.data.sky_code == "1"
    assert response.data.precipitation_type == "0"
    assert response.data.retrieved_at == FIXED_RETRIEVED_AT


def test_place_response_contains_raw_and_normalized_operating_hours() -> None:
    schedule = normalize_operating_schedule(
        content_type_id="12",
        operating_hours="09:00~18:00 (입장마감 17:00)",
        rest_date="매주 화요일",
    )
    details = PlaceDetails(
        content_id="126508",
        content_type_id="12",
        title="경복궁",
        address="서울특별시 종로구",
        overview=None,
        homepage=None,
        telephone=None,
        operating_hours="09:00~18:00 (입장마감 17:00)",
        rest_date="매주 화요일",
        raw_common={},
        raw_intro={},
        provider="tour_api",
        operating_schedule=schedule,
    )
    result = NearbyPlaceDetailsResult(
        places=(
            EnrichedPlace(
                candidate=PlaceCandidate(
                    place_id="126508",
                    content_type_id="12",
                    name="경복궁",
                    category="attraction",
                    latitude=37.5796,
                    longitude=126.977,
                    address="서울특별시 종로구",
                    raw_source="tour_api",
                ),
                details=details,
                detail_status=DetailStatus.SUCCESS,
            ),
        ),
        status=ToolStatus.SUCCESS,
        source="nearby_place_details_tool",
        retrieved_at=FIXED_RETRIEVED_AT,
        elapsed_ms=1250.32,
        provider_metadata=(_metadata(ProviderSource.TOUR_API_PLACE),),
    )

    response = map_nearby_places_response(
        "request-places",
        result,
        search_center=Coordinates(latitude=37.5796, longitude=126.977),
        radius_km=2.0,
    )

    assert response.status is ResponseStatus.SUCCESS
    assert response.data is not None
    operating_hours = response.data.places[0].operating_hours
    assert operating_hours is not None
    assert operating_hours.raw_operating_hours == "09:00~18:00 (입장마감 17:00)"
    assert operating_hours.raw_rest_date == "매주 화요일"
    assert operating_hours.time_ranges[0].start == "09:00"
    assert operating_hours.time_ranges[0].end == "18:00"


def test_request_models_keep_request_id_and_tool_type() -> None:
    location_request = ResolveLocationRequest(
        request_id="location-1",
        tool_type=ToolType.RESOLVE_LOCATION,
        parameters={"location_query": "경복궁"},
    )
    places_request = SearchNearbyPlacesRequest(
        request_id="places-1",
        tool_type=ToolType.SEARCH_NEARBY_PLACES,
        parameters={
            "location": {"latitude": 37.5796, "longitude": 126.977},
        },
    )

    assert location_request.request_id == "location-1"
    assert places_request.parameters.radius_km == DEFAULT_PLACE_SEARCH_RADIUS_KM
    # 값 자체가 아니라 "기본값이 그대로 흘러온다"를 확인하는 자리다. 리터럴로 박아두면
    # 후보 수집 정책이 바뀔 때마다 여기가 함께 깨진다(바로 위 radius와 같은 방식).
    assert places_request.parameters.limit == DEFAULT_RECOMMENDATION_CANDIDATE_LIMIT
