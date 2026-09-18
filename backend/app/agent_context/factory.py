"""C Context Service와 내부 Tool·Provider 의존성을 조립한다."""

from __future__ import annotations

import httpx

from app.agent_context.concentration_proxy import ConcentrationMappingCache
from app.agent_context.enrichment_service import CandidateEnrichmentService
from app.agent_context.service import ContextService, ContextTools
from app.config import settings
from app.providers.factory import (
    get_barrier_free_place_search_provider,
    get_concentration_provider,
    get_district_place_search_provider,
    get_festival_provider,
    get_geocoding_provider,
    get_holiday_provider,
    get_info_place_detail_provider,
    get_local_search_provider,
    get_municipal_parking_catalog_repository,
    get_municipal_parking_provider,
    get_place_details_provider,
    get_place_evidence_provider,
    get_place_location_repository,
    get_place_photo_repository,
    get_place_search_provider,
    get_public_toilet_repository,
    get_realtime_citydata_provider,
    get_realtime_commercial_provider,
    get_recommendation_card_tool,
    get_weather_provider,
)
from app.tools.concentration import GetConcentrationTool
from app.tools.festival import GetFestivalsTool
from app.tools.holiday import GetHolidaysTool
from app.tools.municipal_parking import GetMunicipalParkingTool
from app.tools.nearby_place_details import NearbyPlaceDetailsTool
from app.tools.place_detail import GetPlaceDetailTool
from app.tools.public_toilet import GetPublicToiletTool
from app.tools.realtime_citydata import GetRealtimeCityDataTool
from app.tools.realtime_commercial import GetRealtimeCommercialTool
from app.tools.resolve_location import ResolveLocationTool
from app.tools.weather_forecast import GetWeatherForecastTool


def get_context_provider(client: httpx.AsyncClient) -> ContextService:
    """설정된 Fake/Real Provider로 A–C ContextProvider를 생성한다."""

    return ContextService(
        ContextTools(
            location=ResolveLocationTool(
                get_geocoding_provider(client),
                place_repository=get_place_location_repository(client),
                local_search_provider=get_local_search_provider(client),
            ),
            places=NearbyPlaceDetailsTool(
                search_provider=get_place_search_provider(client),
                details_provider=get_place_details_provider(client),
                district_search_provider=get_district_place_search_provider(client),
                # 무장애 조건이 붙은 요청만 이쪽으로 간다. 조건이 없으면 위의
                # search_provider(TourAPI)가 그대로 후보를 모은다.
                barrier_free_search_provider=get_barrier_free_place_search_provider(client),
            ),
            weather=GetWeatherForecastTool(get_weather_provider(client)),
            holidays=GetHolidaysTool(get_holiday_provider(client)),
            concentration=GetConcentrationTool(get_concentration_provider(client)),
            # 추천 후보 상세 캐시(PLACE_DETAILS_SOURCE)와 별개로 INFO 전용 설정을
            # 따른다 — 이유는 place_detail.py 모듈 docstring 참고.
            place_detail=GetPlaceDetailTool(get_info_place_detail_provider(client)),
            festivals=GetFestivalsTool(get_festival_provider(client)),
            realtime_commercial=GetRealtimeCommercialTool(get_realtime_commercial_provider(client)),
            realtime_citydata=GetRealtimeCityDataTool(get_realtime_citydata_provider(client)),
            municipal_parking=GetMunicipalParkingTool(get_municipal_parking_provider(client)),
            municipal_parking_catalog=get_municipal_parking_catalog_repository(client),
            public_toilets=GetPublicToiletTool(get_public_toilet_repository(client)),
            # COMPARE의 place_id → 장소명 해석. 추천 카드와 같은 Tool을 공유한다.
            cards=get_recommendation_card_tool(client),
            # 상세 카드의 사진 목록. 상세 조회와 다른 테이블이라 저장소를 따로 준다.
            place_photos=get_place_photo_repository(client),
            # 후기로 답하는 INFO 질의 전용. 추천 채점이 쓰는 것과 같은 Provider다 —
            # 같은 임베딩 공간을 두 번 올릴 이유가 없다. 기능 스위치가 꺼져 있거나
            # 문장 인코더가 없으면 None이고, 그때는 기존 상세 조회로 답한다.
            place_evidence=(
                get_place_evidence_provider(client)
                if settings.review_answer_enabled
                else None
            ),
        ),
        candidate_limit=settings.recommendation_candidate_limit,
        concentration_mapping_cache=_concentration_mapping_cache(client),
    )


def _concentration_mapping_cache(
    client: httpx.AsyncClient,
) -> ConcentrationMappingCache | None:
    """Supabase 설정이 없으면 INFO 대체 조회를 건너뛴다(기존 경로 유지)."""
    repository = get_place_location_repository(client)
    return None if repository is None else ConcentrationMappingCache(repository)


def get_candidate_enrichment_service(
    client: httpx.AsyncClient,
) -> CandidateEnrichmentService:
    """설정된 Concentration Provider를 공통 Tool로 감싼 후보 보강 서비스를 생성한다."""

    return CandidateEnrichmentService(
        GetConcentrationTool(get_concentration_provider(client)),
        candidate_limit=settings.recommendation_result_limit,
        # 조회는 검색어로, 대조는 정식 명칭으로 하기 위해 매핑을 함께 넘긴다(D-057).
        mapping_cache=_concentration_mapping_cache(client),
    )


__all__ = ["get_candidate_enrichment_service", "get_context_provider"]
