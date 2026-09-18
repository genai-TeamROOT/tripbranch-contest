"""A 요청을 현재 C Tool로 dispatch하고 공통 응답으로 변환한다."""

from __future__ import annotations

from dataclasses import dataclass

from app.tool_intelligence.mappers import (
    map_concentration_response,
    map_holidays_response,
    map_nearby_places_response,
    map_resolve_location_response,
    map_weather_response,
    unsupported_place_details_response,
)
from app.tool_intelligence.schemas import (
    GetConcentrationRequest,
    GetHolidaysRequest,
    GetPlaceDetailsRequest,
    GetWeatherForecastRequest,
    ResolveLocationRequest,
    SearchNearbyPlacesRequest,
    ToolRequest,
    ToolResponse,
)
from app.tools.concentration import ConcentrationQuery, GetConcentrationTool
from app.tools.holiday import GetHolidaysTool, HolidayQuery
from app.tools.nearby_place_details import (
    NearbyPlaceDetailsQuery,
    NearbyPlaceDetailsTool,
)
from app.tools.resolve_location import ResolveLocationQuery, ResolveLocationTool
from app.tools.weather_forecast import (
    GetWeatherForecastTool,
    WeatherForecastQuery,
)


@dataclass(frozen=True)
class ToolIntelligenceTools:
    location: ResolveLocationTool
    places: NearbyPlaceDetailsTool
    weather: GetWeatherForecastTool
    concentration: GetConcentrationTool
    holidays: GetHolidaysTool


class ToolIntelligenceService:
    def __init__(self, tools: ToolIntelligenceTools) -> None:
        self._tools = tools

    async def execute(self, request: ToolRequest) -> ToolResponse[object]:
        if isinstance(request, ResolveLocationRequest):
            result = await self._tools.location.execute(
                ResolveLocationQuery(request.parameters.location_query)
            )
            return map_resolve_location_response(request.request_id, result)

        if isinstance(request, SearchNearbyPlacesRequest):
            parameters = request.parameters
            result = await self._tools.places.execute(
                NearbyPlaceDetailsQuery(
                    latitude=parameters.location.latitude,
                    longitude=parameters.location.longitude,
                    search_radius_km=parameters.radius_km,
                    limit=parameters.limit,
                    preferred_categories=tuple(
                        [*parameters.place_types, *parameters.place_tags]
                    ),
                    excluded_place_ids=frozenset(parameters.excluded_place_ids),
                )
            )
            return map_nearby_places_response(
                request.request_id,
                result,
                search_center=parameters.location,
                radius_km=parameters.radius_km,
            )

        if isinstance(request, GetWeatherForecastRequest):
            parameters = request.parameters
            result = await self._tools.weather.execute(
                WeatherForecastQuery(
                    latitude=parameters.location.latitude,
                    longitude=parameters.location.longitude,
                    visit_at=parameters.visit_at,
                )
            )
            return map_weather_response(request.request_id, result)

        if isinstance(request, GetConcentrationRequest):
            parameters = request.parameters
            result = await self._tools.concentration.execute(
                ConcentrationQuery(
                    area_code=parameters.area_code,
                    district_code=parameters.district_code,
                    place_name=parameters.place_name,
                )
            )
            return map_concentration_response(request.request_id, result)

        if isinstance(request, GetHolidaysRequest):
            parameters = request.parameters
            result = await self._tools.holidays.execute(
                HolidayQuery(year=parameters.year, month=parameters.month)
            )
            return map_holidays_response(request.request_id, result)

        if isinstance(request, GetPlaceDetailsRequest):
            return unsupported_place_details_response(request.request_id)

        raise TypeError(f"지원하지 않는 ToolRequest 타입입니다: {type(request)!r}")
