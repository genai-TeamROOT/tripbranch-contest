"""C 내부 Tool 결과를 A가 소비하는 공통 ToolResponse로 변환한다."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import TypeVar

from app.domain.operating_hours import OperatingSchedule
from app.place_search_policy import EARTH_RADIUS_KM
from app.providers.contracts import ProviderMetadata
from app.tool_intelligence.schemas import (
    ConcentrationData,
    ConcentrationForecastData,
    Coordinates,
    HolidayData,
    HolidaysData,
    NearbyPlacesData,
    NormalizedTimeRangeData,
    OperatingHoursData,
    PlaceData,
    ProviderMetadataData,
    ResolvedLocationData,
    ResponseStatus,
    ToolErrorData,
    ToolResponse,
    ToolType,
    ToolWarningData,
    WeatherForecastData,
)
from app.tools.concentration import ConcentrationToolResult
from app.tools.contracts import ToolError, ToolStatus
from app.tools.holiday import HolidayToolResult
from app.tools.nearby_place_details import NearbyPlaceDetailsResult
from app.tools.resolve_location import ResolveLocationResult
from app.tools.weather_forecast import WeatherForecastToolResult

T = TypeVar("T")

_WARNING_MESSAGES = {
    "partial_data": "일부 데이터를 확인하지 못했습니다.",
    "stale_data": "최신성이 보장되지 않는 데이터를 사용했습니다.",
    "fallback_used": "대체 조회 방식으로 결과를 찾았습니다.",
}


def map_resolve_location_response(
    request_id: str,
    result: ResolveLocationResult,
) -> ToolResponse[ResolvedLocationData]:
    metadata = _metadata(result.provider_metadata)
    retrieved_at = _retrieved_at(result.provider_metadata)
    data = None
    if result.location is not None and retrieved_at is not None:
        data = ResolvedLocationData(
            requested_query=result.location.requested_query,
            resolved_name=result.location.resolved_name,
            location=Coordinates(
                latitude=result.location.latitude,
                longitude=result.location.longitude,
            ),
            resolution_method=result.location.resolution_method.value,
            confidence=result.location.confidence.value,
            retrieved_at=retrieved_at,
        )
    return _response(
        request_id=request_id,
        tool_type=ToolType.RESOLVE_LOCATION,
        status=result.status,
        data=data,
        error=result.error,
        warnings=result.warnings,
        provider_metadata=metadata,
    )


def map_weather_response(
    request_id: str,
    result: WeatherForecastToolResult,
) -> ToolResponse[WeatherForecastData]:
    data = None
    retrieved_at = _retrieved_at(result.provider_metadata)
    if result.forecast is not None:
        forecast = result.forecast
        data = WeatherForecastData(
            location=Coordinates(
                latitude=forecast.latitude,
                longitude=forecast.longitude,
            ),
            grid_x=forecast.grid_x,
            grid_y=forecast.grid_y,
            sky_code=forecast.sky_code,
            precipitation_type=forecast.precipitation_type,
            data_type="forecast",
            requested_visit_at=forecast.requested_visit_at,
            forecast_for=forecast.forecast_for,
            observed_at=None,
            retrieved_at=retrieved_at or forecast.retrieved_at,
            timezone=forecast.timezone,
            timezone_assumed=forecast.timezone_assumed,
            selection_method=forecast.selection_method.value,
        )
    return _response(
        request_id=request_id,
        tool_type=ToolType.GET_WEATHER_FORECAST,
        status=result.status,
        data=data,
        error=result.error,
        warnings=result.warnings,
        provider_metadata=_metadata(result.provider_metadata),
    )


def map_nearby_places_response(
    request_id: str,
    result: NearbyPlaceDetailsResult,
    *,
    search_center: Coordinates,
    radius_km: float,
) -> ToolResponse[NearbyPlacesData]:
    places = [
        PlaceData(
            place_id=item.candidate.place_id,
            name=item.candidate.name,
            category=item.candidate.category,
            location=Coordinates(
                latitude=item.candidate.latitude,
                longitude=item.candidate.longitude,
            ),
            address=item.candidate.address,
            distance_km=_haversine_km(
                search_center.latitude,
                search_center.longitude,
                item.candidate.latitude,
                item.candidate.longitude,
            ),
            operating_hours=_operating_hours(
                item.details.operating_schedule if item.details else None
            ),
            detail_status=item.detail_status.value,
        )
        for item in result.places
    ]
    data = None
    retrieved_at = _retrieved_at(result.provider_metadata)
    if (
        result.status in {ToolStatus.SUCCESS, ToolStatus.PARTIAL}
        and retrieved_at is not None
    ):
        data = NearbyPlacesData(
            places=places,
            count=len(places),
            search_center=search_center,
            radius_km=radius_km,
            elapsed_ms=round(result.elapsed_ms, 2),
            retrieved_at=retrieved_at,
        )
    return _response(
        request_id=request_id,
        tool_type=ToolType.SEARCH_NEARBY_PLACES,
        status=result.status,
        data=data,
        error=result.error,
        warnings=result.warnings,
        provider_metadata=_metadata(result.provider_metadata),
    )


def map_concentration_response(
    request_id: str,
    result: ConcentrationToolResult,
) -> ToolResponse[ConcentrationData]:
    data = None
    retrieved_at = _retrieved_at(result.provider_metadata)
    if result.concentration is not None and retrieved_at is not None:
        data = ConcentrationData(
            area_code=result.concentration.area_code,
            district_code=result.concentration.district_code,
            requested_place_name=result.concentration.requested_place_name,
            forecasts=[
                ConcentrationForecastData(
                    place_name=item.place_name,
                    forecast_date=item.forecast_date,
                    concentration_rate=item.concentration_rate,
                )
                for item in result.concentration.forecasts
            ],
            retrieved_at=retrieved_at,
        )
    return _response(
        request_id=request_id,
        tool_type=ToolType.GET_CONCENTRATION,
        status=result.status,
        data=data,
        error=result.error,
        warnings=result.warnings,
        provider_metadata=_metadata(result.provider_metadata),
    )


def map_holidays_response(
    request_id: str,
    result: HolidayToolResult,
) -> ToolResponse[HolidaysData]:
    data = None
    retrieved_at = _retrieved_at(result.provider_metadata)
    if result.holidays is not None and retrieved_at is not None:
        data = HolidaysData(
            year=result.holidays.year,
            month=result.holidays.month,
            holidays=[
                HolidayData(
                    date=item.date,
                    name=item.name,
                    kind=item.kind,
                    sequence=item.sequence,
                )
                for item in result.holidays.holidays
            ],
            retrieved_at=retrieved_at,
        )
    return _response(
        request_id=request_id,
        tool_type=ToolType.GET_HOLIDAYS,
        status=result.status,
        data=data,
        error=result.error,
        warnings=result.warnings,
        provider_metadata=_metadata(result.provider_metadata),
    )


def unsupported_place_details_response(
    request_id: str,
) -> ToolResponse[None]:
    return ToolResponse[None](
        request_id=request_id,
        tool_type=ToolType.GET_PLACE_DETAILS,
        status=ResponseStatus.UNSUPPORTED,
        data=None,
        error=ToolErrorData(
            code="unsupported",
            message="place_id만으로 상세조회 유형을 복원하는 기능은 아직 지원하지 않습니다.",
            cause="content_type_mapping_unavailable",
            retryable=False,
        ),
        warnings=[],
        provider_metadata=[],
    )


def _operating_hours(schedule: OperatingSchedule | None) -> OperatingHoursData | None:
    if schedule is None:
        return None
    return OperatingHoursData(
        raw_operating_hours=schedule.raw_operating_hours,
        raw_rest_date=schedule.raw_rest_date,
        cleaned_operating_hours=schedule.cleaned_operating_hours,
        cleaned_rest_date=schedule.cleaned_rest_date,
        availability=schedule.availability.value,
        parse_status=schedule.parse_status.value,
        time_ranges=[
            NormalizedTimeRangeData(
                start=time_range.start.strftime("%H:%M"),
                end=time_range.end.strftime("%H:%M"),
                crosses_midnight=time_range.crosses_midnight,
            )
            for rule in schedule.rules
            for time_range in rule.time_ranges
        ],
        assumption_reason=schedule.assumption_reason,
        warnings=list(schedule.warnings),
    )


def _response(
    *,
    request_id: str,
    tool_type: ToolType,
    status: ToolStatus,
    data: T | None,
    error: ToolError | None,
    warnings: tuple[str, ...],
    provider_metadata: list[ProviderMetadataData],
) -> ToolResponse[T]:
    return ToolResponse[T](
        request_id=request_id,
        tool_type=tool_type,
        status=ResponseStatus(status.value),
        data=data,
        error=_error(error),
        warnings=[
            ToolWarningData(
                code=warning,
                message=_WARNING_MESSAGES.get(warning, warning),
            )
            for warning in warnings
        ],
        provider_metadata=provider_metadata,
    )


def _metadata(
    values: tuple[ProviderMetadata, ...],
) -> list[ProviderMetadataData]:
    return [
        ProviderMetadataData(
            source=value.source,
            status=value.status,
            retrieved_at=value.retrieved_at.astimezone(UTC),
        )
        for value in values
    ]


def _retrieved_at(
    values: tuple[ProviderMetadata, ...],
) -> datetime | None:
    if not values:
        return None
    return max(value.retrieved_at for value in values).astimezone(UTC)


def _error(error: ToolError | None) -> ToolErrorData | None:
    if error is None:
        return None
    return ToolErrorData(
        code=error.code,
        message=error.message,
        cause=error.cause,
        retryable=error.retryable,
        details=error.details,
    )


def _haversine_km(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    latitude_delta = math.radians(latitude_b - latitude_a)
    longitude_delta = math.radians(longitude_b - longitude_a)
    value = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(math.radians(latitude_a))
        * math.cos(math.radians(latitude_b))
        * math.sin(longitude_delta / 2) ** 2
    )
    return round(EARTH_RADIUS_KM * 2 * math.asin(math.sqrt(value)), 3)
