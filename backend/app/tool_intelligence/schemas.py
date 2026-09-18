"""A Runtime이 사용하는 Tool Intelligence 요청·응답 Pydantic 계약."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from app.place_search_policy import (
    DEFAULT_PLACE_SEARCH_RADIUS_KM,
    MAX_PLACE_SEARCH_RADIUS_KM,
)
from app.providers.contracts import ProviderSource, ProviderStatus
from app.recommendation_limits import (
    DEFAULT_RECOMMENDATION_CANDIDATE_LIMIT,
    MAX_RECOMMENDATION_CANDIDATE_LIMIT,
    MIN_RECOMMENDATION_LIMIT,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolType(StrEnum):
    RESOLVE_LOCATION = "resolve_location"
    SEARCH_NEARBY_PLACES = "search_nearby_places"
    GET_PLACE_DETAILS = "get_place_details"
    GET_WEATHER_FORECAST = "get_weather_forecast"
    GET_CONCENTRATION = "get_concentration"
    GET_HOLIDAYS = "get_holidays"


class ResponseStatus(StrEnum):
    SUCCESS = "success"
    NO_DATA = "no_data"
    PARTIAL = "partial"
    INVALID_INPUT = "invalid_input"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"
    INTERNAL_ERROR = "internal_error"


class Coordinates(StrictModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class ResolveLocationParameters(StrictModel):
    location_query: str = Field(min_length=1, max_length=200)

    @field_validator("location_query")
    @classmethod
    def validate_location_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("location_query는 비어 있을 수 없습니다.")
        return normalized


class SearchNearbyPlacesParameters(StrictModel):
    location: Coordinates
    radius_km: float = Field(
        default=DEFAULT_PLACE_SEARCH_RADIUS_KM,
        gt=0,
        le=MAX_PLACE_SEARCH_RADIUS_KM,
    )
    limit: int = Field(
        default=DEFAULT_RECOMMENDATION_CANDIDATE_LIMIT,
        ge=MIN_RECOMMENDATION_LIMIT,
        le=MAX_RECOMMENDATION_CANDIDATE_LIMIT,
    )
    place_types: list[str] = Field(default_factory=list)
    place_tags: list[str] = Field(default_factory=list)
    excluded_place_ids: list[str] = Field(default_factory=list)


class GetPlaceDetailsParameters(StrictModel):
    place_id: str = Field(min_length=1)
    place_type: str | None = None


class GetWeatherForecastParameters(StrictModel):
    location: Coordinates
    visit_at: datetime | None = None


class GetConcentrationParameters(StrictModel):
    area_code: str = Field(min_length=1)
    district_code: str = Field(min_length=1)
    place_name: str | None = None


class GetHolidaysParameters(StrictModel):
    year: int = Field(ge=1, le=9999)
    month: int | None = Field(default=None, ge=1, le=12)


class ResolveLocationRequest(StrictModel):
    request_id: str = Field(min_length=1)
    tool_type: Literal[ToolType.RESOLVE_LOCATION]
    parameters: ResolveLocationParameters


class SearchNearbyPlacesRequest(StrictModel):
    request_id: str = Field(min_length=1)
    tool_type: Literal[ToolType.SEARCH_NEARBY_PLACES]
    parameters: SearchNearbyPlacesParameters


class GetPlaceDetailsRequest(StrictModel):
    request_id: str = Field(min_length=1)
    tool_type: Literal[ToolType.GET_PLACE_DETAILS]
    parameters: GetPlaceDetailsParameters


class GetWeatherForecastRequest(StrictModel):
    request_id: str = Field(min_length=1)
    tool_type: Literal[ToolType.GET_WEATHER_FORECAST]
    parameters: GetWeatherForecastParameters


class GetConcentrationRequest(StrictModel):
    request_id: str = Field(min_length=1)
    tool_type: Literal[ToolType.GET_CONCENTRATION]
    parameters: GetConcentrationParameters


class GetHolidaysRequest(StrictModel):
    request_id: str = Field(min_length=1)
    tool_type: Literal[ToolType.GET_HOLIDAYS]
    parameters: GetHolidaysParameters


ToolRequest = Annotated[
    ResolveLocationRequest
    | SearchNearbyPlacesRequest
    | GetPlaceDetailsRequest
    | GetWeatherForecastRequest
    | GetConcentrationRequest
    | GetHolidaysRequest,
    Field(discriminator="tool_type"),
]
_TOOL_REQUEST_ADAPTER = TypeAdapter(ToolRequest)


def validate_tool_request(payload: object) -> ToolRequest:
    """A가 전달한 JSON 호환 payload를 Tool별 요청 모델로 검증한다."""

    return _TOOL_REQUEST_ADAPTER.validate_python(payload)


class ProviderMetadataData(StrictModel):
    source: ProviderSource
    status: ProviderStatus
    retrieved_at: datetime

    @field_validator("retrieved_at")
    @classmethod
    def validate_retrieved_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("retrieved_at은 timezone-aware datetime이어야 합니다.")
        return value


class ToolErrorData(StrictModel):
    code: str
    message: str
    cause: str | None = None
    retryable: bool
    details: dict[str, str] = Field(default_factory=dict)


class ToolWarningData(StrictModel):
    code: str
    message: str


T = TypeVar("T")


class ToolResponse(StrictModel, Generic[T]):
    request_id: str
    tool_type: ToolType
    status: ResponseStatus
    data: T | None
    error: ToolErrorData | None
    warnings: list[ToolWarningData] = Field(default_factory=list)
    provider_metadata: list[ProviderMetadataData] = Field(default_factory=list)


class ResolvedLocationData(StrictModel):
    requested_query: str
    resolved_name: str
    location: Coordinates
    resolution_method: str
    confidence: str
    retrieved_at: datetime


class NormalizedTimeRangeData(StrictModel):
    start: str
    end: str
    crosses_midnight: bool


class OperatingHoursData(StrictModel):
    raw_operating_hours: str | None
    raw_rest_date: str | None
    cleaned_operating_hours: str | None
    cleaned_rest_date: str | None
    availability: str
    parse_status: str
    time_ranges: list[NormalizedTimeRangeData]
    assumption_reason: str | None
    warnings: list[str]


class PlaceData(StrictModel):
    place_id: str
    name: str
    category: str
    location: Coordinates
    address: str | None
    distance_km: float
    operating_hours: OperatingHoursData | None
    detail_status: str


class NearbyPlacesData(StrictModel):
    places: list[PlaceData]
    count: int
    search_center: Coordinates
    radius_km: float
    elapsed_ms: float
    retrieved_at: datetime


class WeatherForecastData(StrictModel):
    location: Coordinates
    grid_x: int
    grid_y: int
    sky_code: str | None
    precipitation_type: str | None
    data_type: Literal["forecast"]
    requested_visit_at: datetime
    forecast_for: datetime
    observed_at: None
    retrieved_at: datetime
    timezone: str
    timezone_assumed: bool
    selection_method: str


class ConcentrationForecastData(StrictModel):
    place_name: str
    forecast_date: str | None
    concentration_rate: float | None


class ConcentrationData(StrictModel):
    area_code: str
    district_code: str
    requested_place_name: str | None
    forecasts: list[ConcentrationForecastData]
    retrieved_at: datetime


class HolidayData(StrictModel):
    date: str
    name: str
    kind: str | None
    sequence: int | None


class HolidaysData(StrictModel):
    year: int
    month: int | None
    holidays: list[HolidayData]
    retrieved_at: datetime
