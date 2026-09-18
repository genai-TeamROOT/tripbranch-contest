"""추천 결과를 조립하는 도메인 서비스.

역할: C Tool로 위치·날씨·장소를 조회해 RecommendationContext를 조립하고, D의
주력 진입점(run_recommendation_pipeline_from_context())에 위임한다. D 코드는
C Tool을 직접 호출하지 않는다([TECH-02]).
입력: 해석된 조건(InterpretedConditions), 이미 노출된 place_id 목록.
출력: RecommendationResponse 모델.
호출 시점: /api/recommendations 라우터가 get_recommendations()를 호출한다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from app.agent_context.mappers import (
    map_location_context,
    map_places_context,
    map_weather_context,
)
from app.agent_context.schemas import RecommendationContext
from app.config import settings
from app.errors import AppError
from app.observability.api_usage import create_external_client
from app.providers.protocols import GeocodingProvider, PlaceProvider, WeatherProvider
from app.schemas import InterpretedConditions, RecommendationResponse
from app.services.recommendation_pipeline import run_recommendation_pipeline_from_context
from app.tools.contracts import ToolError, ToolStatus
from app.tools.nearby_place_details import (
    NearbyPlaceDetailsQuery,
    NearbyPlaceDetailsTool,
)
from app.tools.resolve_location import ResolveLocationQuery, ResolveLocationTool
from app.tools.weather_forecast import GetWeatherForecastTool, WeatherForecastQuery

_KST = ZoneInfo("Asia/Seoul")


async def build_recommendations(
    conditions: InterpretedConditions,
    shown_place_ids: list[str],
    geocoding_provider: GeocodingProvider,
    place_provider: PlaceProvider,
    weather_provider: WeatherProvider,
) -> RecommendationResponse:
    """Fake/Real과 무관하게 동일한 Tool 조회→Context 조립→D 위임 흐름을 실행한다."""
    location_result = await ResolveLocationTool(geocoding_provider).execute(
        ResolveLocationQuery(conditions.location_query)
    )
    if location_result.status is not ToolStatus.SUCCESS or location_result.location is None:
        raise _location_error(location_result.status, location_result.error)

    location = location_result.location
    visit_at = datetime.now(_KST)
    excluded_place_ids = frozenset(shown_place_ids)

    weather_result, places_result = await asyncio.gather(
        GetWeatherForecastTool(weather_provider).execute(
            WeatherForecastQuery(
                location.latitude,
                location.longitude,
                visit_at=visit_at,
            )
        ),
        NearbyPlaceDetailsTool(place_provider, place_provider).execute(
            NearbyPlaceDetailsQuery(
                latitude=location.latitude,
                longitude=location.longitude,
                search_radius_km=conditions.search_radius_km,
                limit=settings.recommendation_candidate_limit,
                preferred_categories=tuple(conditions.preferred_categories),
                excluded_place_ids=excluded_place_ids,
            )
        ),
    )

    context = RecommendationContext(
        location=map_location_context(location_result),
        weather=map_weather_context(weather_result),
        places=map_places_context(places_result),
    )
    return await run_recommendation_pipeline_from_context(
        context,
        visit_at=visit_at,
        search_radius_km=conditions.search_radius_km,
        shown_place_ids=excluded_place_ids,
        recommendation_limit=settings.recommendation_result_limit,
    )


def _location_error(status: ToolStatus, error: ToolError | None) -> AppError:
    return AppError(
        code=error.code if error else "unavailable",
        message=error.message if error else "위치 검색 서비스를 사용할 수 없습니다.",
        status_code={
            ToolStatus.NO_DATA: 404,
            ToolStatus.UNSUPPORTED: 422,
            ToolStatus.UNAVAILABLE: 502,
        }.get(status, 502),
        retryable=error.retryable if error else False,
        details=error.details if error else None,
    )


async def get_recommendations(
    conditions: InterpretedConditions, shown_place_ids: list[str]
) -> RecommendationResponse:
    """라우터가 호출하는 Fake/Real 공통 추천 파이프라인 진입점."""
    from app.providers.factory import (
        get_geocoding_provider,
        get_place_provider,
        get_weather_provider,
    )

    async with create_external_client() as client:
        return await build_recommendations(
            conditions,
            shown_place_ids,
            geocoding_provider=get_geocoding_provider(client),
            place_provider=get_place_provider(client),
            weather_provider=get_weather_provider(client),
        )
