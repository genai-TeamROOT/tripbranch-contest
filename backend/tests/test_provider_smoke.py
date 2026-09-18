"""명시적으로 허용했을 때만 실제 외부 API를 호출하는 Provider Smoke Test."""

from __future__ import annotations

import os

import httpx
import pytest

from app.agent_context.schemas import AgentContextRequest
from app.agent_context.schemas import UserConditions as AgentUserConditions
from app.agent_context.service import ContextService, ContextTools
from app.config import Settings
from app.domain.travel_route import (
    GeoCoordinate,
    RouteDestination,
    RouteSource,
    RouteStatus,
)
from app.providers.concentration import RealConcentrationProvider
from app.providers.driving_route import RealNaverDrivingRouteProvider
from app.providers.gemini import RealGeminiProvider
from app.providers.geocoding import RealGeocodingProvider
from app.providers.holiday import RealHolidayProvider
from app.providers.kakao_transit_route import RealKakaoTransitRouteProvider
from app.providers.real_place import RealPlaceProvider
from app.providers.walking_route import RealKakaoWalkingRouteProvider
from app.providers.weather import RealWeatherProvider
from app.schemas import Intent, PlaceType, UserConditions
from app.tools.holiday import GetHolidaysTool
from app.tools.nearby_place_details import NearbyPlaceDetailsTool
from app.tools.resolve_location import (
    ResolutionMethod,
    ResolveLocationQuery,
    ResolveLocationStatus,
    ResolveLocationTool,
)
from app.tools.weather_forecast import (
    GetWeatherForecastTool,
    WeatherForecastQuery,
    WeatherToolStatus,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.smoke,
    pytest.mark.skipif(
        os.getenv("RUN_REAL_PROVIDER_TESTS") != "true",
        reason="RUN_REAL_PROVIDER_TESTS=true일 때만 실제 Provider를 호출합니다.",
    ),
]

settings = Settings()


def _required_value(name: str, value: str) -> str:
    if not value:
        pytest.skip(f"{name} 환경변수가 없습니다.")
    return value


def _tour_api_service_key() -> str:
    return _required_value("TOUR_API_SERVICE_KEY", settings.tour_api_service_key)


def _llm_api_key() -> str:
    return _required_value("LLM_API_KEY", settings.llm_api_key)


async def test_naver_geocoding_real_smoke() -> None:
    async with httpx.AsyncClient() as client:
        provider = RealGeocodingProvider(
            api_key_id=_required_value("NAVER_MAP_CLIENT_ID", settings.naver_map_client_id),
            api_key=_required_value("NAVER_MAP_CLIENT_SECRET", settings.naver_map_client_secret),
            client=client,
        )
        result = await ResolveLocationTool(provider).execute(ResolveLocationQuery("경복궁"))

    assert result.status is ResolveLocationStatus.SUCCESS
    assert result.location is not None
    assert result.location.resolution_method is ResolutionMethod.ALIAS
    assert 37.0 < result.location.latitude < 38.0
    assert 126.0 < result.location.longitude < 128.0
    print(
        f"Naver Geocoding: {result.location.resolved_name} "
        f"({result.location.latitude:.4f}, {result.location.longitude:.4f})"
    )


async def test_kakao_walking_route_real_smoke() -> None:
    """경복궁 → 인사동 도보 경로.

    **확인하려는 것은 소요시간 값이 아니라 `source`다.** 세 이동수단 중 도보만
    fallback으로 `FakeWalkingRouteProvider`가 붙어 있어(`get_travel_route_tool`),
    카카오 호출이 실패해도 직선거리 추정이 조용히 자리를 메운다 — 응답은 정상으로
    나가고 실측만 사라진다. 자동차·대중교통은 fallback이 없어 실패가 NO_DATA로
    드러나지만 도보는 드러나지 않는다.

    실제로 `route_measured_ratio`가 0%로 찍힌 적이 있다(`tools/travel_route.py`의
    관측 주석). Provider를 직접 부르면 그 fallback을 지나지 않으므로, 키나
    게이트웨이 문제가 여기서 그대로 드러난다.
    """
    async with httpx.AsyncClient() as client:
        provider = RealKakaoWalkingRouteProvider(
            api_key=_required_value("KAKAO_MAP_REST_API_KEY", settings.kakao_map_rest_api_key),
            client=client,
        )
        result = await provider.get_routes(
            GeoCoordinate(37.5796, 126.9770),
            (RouteDestination("insadong", GeoCoordinate(37.5744, 126.9856)),),
        )

    route = result.data.routes[0]
    assert route.status is RouteStatus.SUCCESS
    # 추정으로 대체되면 STRAIGHT_LINE_ESTIMATE가 온다. 이 줄이 이 테스트의 목적이다.
    assert route.source is RouteSource.KAKAO_WALKING
    # 직선거리 약 0.85km 구간이라 보행 경로는 그보다 길다.
    assert route.distance_m is not None and 500 < route.distance_m < 5_000
    assert route.duration_seconds is not None and 300 < route.duration_seconds < 5_400
    print(
        f"Kakao Walking: {route.distance_m}m, "
        f"{route.duration_seconds}s ({route.duration_seconds / 60:.1f}분)"
    )


async def test_naver_driving_route_real_smoke() -> None:
    """경복궁 → 광장시장 자동차 경로. 소요시간 단위(밀리초→초) 환산까지 확인한다."""
    async with httpx.AsyncClient() as client:
        provider = RealNaverDrivingRouteProvider(
            api_key_id=_required_value("NAVER_MAP_CLIENT_ID", settings.naver_map_client_id),
            api_key=_required_value("NAVER_MAP_CLIENT_SECRET", settings.naver_map_client_secret),
            client=client,
        )
        result = await provider.get_routes(
            GeoCoordinate(37.5788, 126.9770),
            (RouteDestination("gwangjang", GeoCoordinate(37.5702, 126.9991)),),
        )

    route = result.data.routes[0]
    assert route.status is RouteStatus.SUCCESS
    assert route.source is RouteSource.NAVER_DRIVING
    assert route.distance_m is not None and 1_000 < route.distance_m < 10_000
    # 밀리초를 그대로 실으면 여기서 걸린다(2.6km가 22분이면 1332초).
    assert route.duration_seconds is not None and 60 < route.duration_seconds < 3_600
    print(
        f"Naver Driving: {route.distance_m}m, "
        f"{route.duration_seconds}s ({route.duration_seconds / 60:.1f}분)"
    )


async def test_kakao_transit_route_real_smoke() -> None:
    """경복궁 → 남산서울타워 대중교통 경로.

    응답의 `routes[]`가 소요시간 순이 아니므로 최소값을 골랐는지까지 본다 —
    첫 원소를 쓰면 여기서 더 큰 값이 나온다.
    """
    async with httpx.AsyncClient() as client:
        provider = RealKakaoTransitRouteProvider(
            api_key=_required_value("KAKAO_MAP_REST_API_KEY", settings.kakao_map_rest_api_key),
            client=client,
        )
        result = await provider.get_routes(
            GeoCoordinate(37.5796, 126.9770),
            (RouteDestination("namsan", GeoCoordinate(37.5512, 126.9882)),),
        )

    route = result.data.routes[0]
    assert route.status is RouteStatus.SUCCESS
    assert route.source is RouteSource.KAKAO_TRANSIT
    assert route.distance_m is not None and 1_000 < route.distance_m < 30_000
    # 3km 구간이라 실측 40분 안팎이다. 배차 대기는 포함되지 않은 값이다.
    assert route.duration_seconds is not None and 300 < route.duration_seconds < 7_200
    print(
        f"Kakao Transit: {route.distance_m}m, "
        f"{route.duration_seconds}s ({route.duration_seconds / 60:.1f}분)"
    )


async def test_kma_weather_real_smoke() -> None:
    async with httpx.AsyncClient() as client:
        provider = RealWeatherProvider(
            api_key=_required_value("WEATHER_API_KEY", settings.weather_api_key),
            client=client,
        )
        result = await GetWeatherForecastTool(provider).execute(
            WeatherForecastQuery(37.5788, 126.9770)
        )

    assert result.status is WeatherToolStatus.SUCCESS
    assert result.forecast is not None
    assert result.forecast.data_type == "forecast"
    assert result.forecast.observed_at is None
    print(
        "KMA Weather: "
        f"sky_code={result.forecast.sky_code}, "
        f"pty={result.forecast.precipitation_type}, "
        f"temperature={result.forecast.temperature_celsius}, "
        f"forecast_for={result.forecast.forecast_for.isoformat()}"
    )


async def test_tour_api_place_real_smoke() -> None:
    async with httpx.AsyncClient() as client:
        provider = RealPlaceProvider(
            api_key=_tour_api_service_key(),
            client=client,
        )
        result = await provider.search_places(
            latitude=37.5788,
            longitude=126.9770,
            preferred_categories=[],
            search_radius_km=1.0,
        )
        result = result.data

    assert result
    assert all(candidate.place_id and candidate.name for candidate in result)
    sample_names = ", ".join(candidate.name for candidate in result[:3])
    print(f"TourAPI Places: count={len(result)}, samples=[{sample_names}]")


async def test_context_service_real_smoke() -> None:
    """실제 Provider 결과가 공통 AgentContextResponse로 조립되는지 확인한다."""

    async with httpx.AsyncClient() as client:
        geocoding = RealGeocodingProvider(
            api_key_id=_required_value("NAVER_MAP_CLIENT_ID", settings.naver_map_client_id),
            api_key=_required_value("NAVER_MAP_CLIENT_SECRET", settings.naver_map_client_secret),
            client=client,
        )
        weather = RealWeatherProvider(
            api_key=_required_value("WEATHER_API_KEY", settings.weather_api_key),
            client=client,
        )
        places = RealPlaceProvider(
            api_key=_tour_api_service_key(),
            client=client,
        )
        holidays = RealHolidayProvider(
            api_key=_tour_api_service_key(),
            client=client,
        )
        service = ContextService(
            ContextTools(
                location=ResolveLocationTool(geocoding),
                weather=GetWeatherForecastTool(weather),
                places=NearbyPlaceDetailsTool(places, places),
                holidays=GetHolidaysTool(holidays),
            ),
            candidate_limit=3,
        )
        response = await service.fetch_context(
            AgentContextRequest(
                request_id="real-context-smoke",
                intent="RECOMMEND",
                conditions=AgentUserConditions(
                    search_center="경복궁",
                    place_types=["restaurant"],
                    place_tags=["카페"],
                ),
            )
        )

    assert response.status in {"success", "partial"}
    context = response.context
    assert context is not None
    location_context = context.location
    assert location_context is not None
    assert location_context.data is not None
    places_context = context.places
    assert places_context is not None
    place_data = places_context.data
    assert place_data
    assert response.metadata.provider_metadata
    assert all(
        metadata.retrieved_at.tzinfo is not None for metadata in response.metadata.provider_metadata
    )
    print(
        "ContextService: "
        f"status={response.status}, "
        f"places={len(place_data)}, "
        f"sources={sorted({item.source for item in response.metadata.provider_metadata})}"
    )


async def test_tour_api_keyword_and_details_real_smoke() -> None:
    async with httpx.AsyncClient() as client:
        provider = RealPlaceProvider(
            api_key=_tour_api_service_key(),
            client=client,
        )
        details = await provider.find_details_by_name(
            "경복궁", region_code="11", district_code="110"
        )
        details = details.data

    assert details.title == "경복궁"
    print(
        f"TourAPI Keyword: content_id={details.content_id}, "
        f"content_type_id={details.content_type_id}, title={details.title}"
    )


async def test_tour_api_concentration_real_smoke() -> None:
    async with httpx.AsyncClient() as client:
        provider = RealConcentrationProvider(
            api_key=_tour_api_service_key(),
            client=client,
        )
        result = await provider.get_forecast("11", "11110", "경복궁")
        result = result.data

    assert result.area_code == "11"
    assert result.district_code == "11110"
    assert result.requested_place_name == "경복궁"
    assert result.forecasts
    assert any(forecast.concentration_rate is not None for forecast in result.forecasts)
    print(f"TourAPI Concentration: forecasts={len(result.forecasts)}")


async def test_gemini_real_smoke() -> None:
    """Gemini 연결 + 구조화 출력 JSON 파싱이 실제로 되는지 확인 (오늘 1순위)."""
    provider = RealGeminiProvider(
        api_key=_llm_api_key(),
        fast_model_names=settings.resolved_llm_fast_models,
        generation_model_names=settings.resolved_llm_generation_models,
    )

    classification = (
        await provider.classify_intent(
            "경복궁 근처 카페 추천해줘",
            has_previous_recommendation=False,
            shown_place_count=0,
        )
    ).data
    assert classification.intent is Intent.RECOMMEND

    output = (await provider.extract_recommend_conditions("경복궁 근처 카페 추천해줘")).data
    assert output.recommend is not None
    assert output.recommend.conditions.search_center == "경복궁"
    print(f"Gemini RECOMMEND: {output.recommend.conditions.model_dump_json()}")


async def test_gemini_modify_reject_all_vs_change_condition_real_smoke() -> None:
    """MODIFY의 REJECT_ALL vs CHANGE_CONDITION 구분이 핵심 검증 포인트."""
    provider = RealGeminiProvider(
        api_key=_llm_api_key(),
        fast_model_names=settings.resolved_llm_fast_models,
        generation_model_names=settings.resolved_llm_generation_models,
    )
    current = UserConditions(
        search_center="경복궁",
        place_types=[PlaceType.RESTAURANT],
    )

    reject_all = (await provider.extract_modify_conditions("다른 곳 보여줘", current)).data
    change_condition = (await provider.extract_modify_conditions("무료인 곳으로", current)).data

    reject_modify = reject_all.modify
    change_modify = change_condition.modify
    assert reject_modify is not None
    assert change_modify is not None
    assert reject_modify.modify_type.value == "REJECT_ALL"
    assert change_modify.modify_type.value == "CHANGE_CONDITION"
    condition_changes = change_modify.condition_changes
    assert condition_changes is not None
    assert condition_changes.budget == "free"


async def test_kasi_holiday_real_smoke() -> None:
    async with httpx.AsyncClient() as client:
        provider = RealHolidayProvider(
            api_key=_tour_api_service_key(),
            client=client,
        )
        result = await provider.get_holidays(2026)
        result = result.data

    assert result.entries
    assert all(entry.date.startswith("2026") for entry in result.entries)
    print(f"KASI Holidays: entries={len(result.entries)}, holidays={len(result.holidays)}")
