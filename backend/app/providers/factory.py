"""설정에 따라 공통 계약을 만족하는 Stub/Real Provider를 생성한다.

provider 모드 문자열 자체의 유효성은 Settings(app.config)가 Literal로 보장하므로
여기서는 "real" 모드에 필요한 자격증명 유무만 확인한다. 부팅 시점 일괄 검증은
validate_provider_config()가 담당한다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import cast

import httpx

from app.config import Settings, settings
from app.domain.travel_route import TravelMode
from app.errors import AppError
from app.providers.concentration import FakeConcentrationProvider, RealConcentrationProvider
from app.providers.driving_route import (
    FakeDrivingRouteProvider,
    RealNaverDrivingRouteProvider,
)
from app.providers.festival import FakeFestivalProvider, RealFestivalProvider
from app.providers.gemini import RealGeminiProvider
from app.providers.gemini_audio import GeminiAudioTranscriber
from app.providers.gemini_vlm_rerank import GeminiPhotoReranker
from app.providers.geocoding import FakeGeocodingProvider, RealGeocodingProvider
from app.providers.google_place_photos import (
    FakeGooglePlacePhotoProvider,
    GooglePlacePhotoProvider,
)
from app.providers.google_translate import GoogleTranslateProvider
from app.providers.holiday import FakeHolidayProvider, RealHolidayProvider
from app.providers.hybrid_place_details import HybridPlaceDetailsProvider
from app.providers.hybrid_place_photos import HybridPlacePhotoProvider
from app.providers.kakao_transit_route import (
    FakeTransitRouteProvider,
    RealKakaoTransitRouteProvider,
)
from app.providers.local_search import FakeLocalSearchProvider, RealLocalSearchProvider
from app.providers.municipal_parking import (
    FakeMunicipalParkingProvider,
    RealMunicipalParkingProvider,
)
from app.providers.place_evidence import PlaceEvidenceProvider
from app.providers.place_evidence_encoder import get_shared_encoder
from app.providers.place_mood import PlaceMoodProvider
from app.providers.protocols import (
    BarrierFreePlaceSearchProvider,
    ConcentrationProvider,
    DistrictPlaceSearchProvider,
    FestivalProvider,
    GeocodingProvider,
    GooglePlacePhotoProviderProtocol,
    HolidayProvider,
    LLMProvider,
    LocalSearchProvider,
    MunicipalParkingProvider,
    PlaceDetailByNameProvider,
    PlaceDetailsProvider,
    PlaceImageProvider,
    PlaceProvider,
    PlaceSearchProvider,
    PublicToiletProvider,
    RealtimeCityDataProvider,
    RealtimeCommercialProvider,
    TravelRouteProvider,
    WeatherProvider,
)
from app.providers.public_toilet import (
    FAKE_TOILETS,
    FakePublicToiletProvider,
    RealPublicToiletProvider,
)
from app.providers.real_place import RealPlaceProvider
from app.providers.seoul_citydata import (
    FakeRealtimeCityDataProvider,
    FakeRealtimeCommercialProvider,
    RealRealtimeCityDataProvider,
    RealRealtimeCommercialProvider,
)
from app.providers.stub import (
    FakeBarrierFreePlaceSearchProvider,
    FakeDistrictPlaceSearchProvider,
    FakeLLMProvider,
    FakePlaceProvider,
    FakeWeatherProvider,
)
from app.providers.supabase_barrier_free_search import (
    SupabaseBarrierFreePlaceSearchProvider,
)
from app.providers.supabase_district_search import (
    SupabaseDistrictPlaceSearchProvider,
)
from app.providers.supabase_place_details import SupabasePlaceDetailsProvider
from app.providers.walking_route import (
    FakeWalkingRouteProvider,
    RealKakaoWalkingRouteProvider,
)
from app.providers.weather import RealWeatherProvider
from app.repositories.fake_municipal_parking import FakeMunicipalParkingCatalogRepository
from app.repositories.fake_places import (
    FakePlaceDetailsRepository,
    FakePlaceLocationRepository,
    FakePlacePhotoRepository,
)
from app.repositories.fake_public_toilet import FakePublicToiletRepository
from app.repositories.municipal_parking import SupabaseMunicipalParkingRepository
from app.repositories.protocols import PlacePhotoRepository
from app.repositories.public_toilet import SupabasePublicToiletRepository
from app.repositories.supabase_places import SupabasePlaceRepository
from app.tools.closure_extract import ClosureExtractor
from app.tools.recommendation_cards import RecommendationCardTool
from app.tools.travel_route import TravelRouteProviders, TravelRouteTool

logger = logging.getLogger(__name__)


def _require_key(value: str, variable_name: str) -> str:
    if not value:
        raise ValueError(f"{variable_name} 환경변수가 필요합니다.")
    return value


def get_llm_provider() -> LLMProvider:
    if settings.resolved_llm_provider == "fake":
        return FakeLLMProvider()
    return RealGeminiProvider(
        api_key=_require_key(settings.llm_api_key, "LLM_API_KEY"),
        fast_model_names=settings.resolved_llm_fast_models,
        generation_model_names=settings.resolved_llm_generation_models,
        # 상세 카드 추천 이유 전용 티어(PLACE_REASON_MODEL_NAME). 클릭당 별도
        # 호출이라 fast·generation 어느 쪽과도 특성이 달라 스위치를 따로 둔다.
        place_reason_model_names=settings.resolved_place_reason_models,
        # 구간 이동수단 판정 전용(MODE_JUDGE_MODEL_NAME). 비우면 generation과 같다.
        mode_judge_model_names=settings.resolved_mode_judge_models,
        # Tool/DB 호출과 분리된 LLM 전용 타임아웃(설정 없으면 EXTERNAL_API_TIMEOUT_SECONDS로
        # 폴백) — EXTERNAL_API_TIMEOUT_SECONDS를 Gemini 지연 때문에 올리면 TourAPI/Naver/
        # Supabase까지 같은 값을 물려받는 문제가 있어 분리했다(2026-08-11).
        timeout_seconds=settings.resolved_llm_timeout_seconds,
        max_retries=settings.external_api_retry_count,
    )


def get_closure_extractor() -> ClosureExtractor | None:
    """적재 배치가 쓸 휴무 추출기를 만든다. 꺼져 있거나 못 쓰면 None이다.

    None이면 지금까지와 같이 정규식만 쓴다 — 주차가 섞인 문구를 못 읽을 뿐
    적재는 정상이다. 그래서 부팅을 막지 않는다.

    **Fake LLM 모드에서는 만들지 않는다.** 가짜 응답으로 휴무를 채우면 근거
    없는 쉬는 날이 DB에 남는데, 오류가 없어 알아채기 어렵다. 게다가 이건
    화면에 잠깐 뜨고 마는 값이 아니라 **저장되는 값**이라, 끄고 다시 켜도
    남는다(D-042와 같은 성격의 조용한 fake다).
    """
    if not settings.closure_extract_enabled:
        return None
    if settings.resolved_llm_provider != "real":
        logger.warning(
            "CLOSURE_EXTRACT_ENABLED=true인데 LLM이 fake 모드라 휴무 추출을"
            " 끕니다. 주차가 섞인 휴무 문구는 정규식이 못 읽은 채 저장됩니다."
        )
        return None
    if not settings.llm_api_key:
        logger.warning(
            "CLOSURE_EXTRACT_ENABLED=true인데 LLM_API_KEY가 비어 있어 휴무"
            " 추출을 끕니다."
        )
        return None
    provider = get_llm_provider()
    # LLMProvider 규약에는 휴무 추출이 없다 — 발화 이해·카드 생성에 쓰는 계약이라
    # 적재 배치용 메서드를 거기 얹지 않았다. 그래서 실제 구현을 확인하고 넘긴다.
    # 메서드가 없는 것을 넘기면 장소마다 조용히 되돌아가므로, 여기서 걸러 낸다.
    if not isinstance(provider, RealGeminiProvider):
        logger.warning(
            "LLM이 real인데 %s라 휴무 추출을 끕니다.", type(provider).__name__
        )
        return None
    return provider


def get_google_translate_provider(client: httpx.AsyncClient) -> GoogleTranslateProvider:
    """영어 UI 요청에서만 만드는 Cloud Translation Basic(v2) provider."""

    return GoogleTranslateProvider(
        api_key=_require_key(settings.google_translate_api_key, "GOOGLE_TRANSLATE_API_KEY"),
        client=client,
        timeout_seconds=settings.external_api_timeout_seconds,
    )


def get_gemini_audio_transcriber() -> GeminiAudioTranscriber:
    """음성 입력 전사용 Gemini 클라이언트를 만든다.

    Fake LLM 모드에서는 실제 음성을 텍스트로 바꿀 모델이 없으므로, 가짜 문장을
    만들어 채팅으로 보내지 않고 기능 미사용 오류를 명시적으로 반환한다.
    """
    if settings.resolved_llm_provider != "real":
        # 원인은 로그로만 남긴다. 사용자는 "Gemini 실연동 환경"이 무엇인지도, 어떻게
        # 바꾸는지도 알 수 없다 — 화면에는 지금 할 수 있는 일만 말한다(TP-250).
        logger.warning(
            "음성 입력 요청을 거절했습니다: LLM provider가 real이 아닙니다(현재 %s).",
            settings.resolved_llm_provider,
        )
        raise AppError(
            code="voice_input_unavailable",
            message="지금은 음성 입력을 쓸 수 없어요. 텍스트로 입력해주세요.",
            status_code=503,
            retryable=False,
            provider="Gemini",
        )
    return GeminiAudioTranscriber(
        api_key=_require_key(settings.llm_api_key, "LLM_API_KEY"),
        model_name=settings.resolved_gemini_audio_model_name,
        timeout_seconds=settings.resolved_llm_timeout_seconds,
    )


def get_geocoding_provider(client: httpx.AsyncClient) -> GeocodingProvider:
    if settings.resolved_geocoding_provider == "fake":
        return FakeGeocodingProvider()
    return RealGeocodingProvider(
        api_key_id=_require_key(settings.naver_map_client_id, "NAVER_MAP_CLIENT_ID"),
        api_key=_require_key(settings.naver_map_client_secret, "NAVER_MAP_CLIENT_SECRET"),
        client=client,
        timeout_seconds=settings.external_api_timeout_seconds,
    )


def get_local_search_provider(client: httpx.AsyncClient) -> LocalSearchProvider:
    if settings.resolved_local_search_provider == "fake":
        return FakeLocalSearchProvider()
    return RealLocalSearchProvider(
        api_key_id=_require_key(
            settings.naver_local_search_client_id, "NAVER_LOCAL_SEARCH_CLIENT_ID"
        ),
        api_key=_require_key(
            settings.naver_local_search_client_secret,
            "NAVER_LOCAL_SEARCH_CLIENT_SECRET",
        ),
        client=client,
        timeout_seconds=settings.external_api_timeout_seconds,
    )


def get_weather_provider(client: httpx.AsyncClient) -> WeatherProvider:
    if settings.resolved_weather_provider == "fake":
        return FakeWeatherProvider(
            settings.fake_weather_sky_code,
            settings.fake_weather_precipitation_type,
        )
    return RealWeatherProvider(
        api_key=_require_key(settings.weather_api_key, "WEATHER_API_KEY"),
        client=client,
        timeout_seconds=settings.external_api_timeout_seconds,
    )


def get_walking_route_provider(
    client: httpx.AsyncClient, semaphore: asyncio.Semaphore | None = None
) -> TravelRouteProvider:
    """설정에 맞는 도보 경로 Provider를 반환한다."""
    if settings.travel_route_provider == "fake":
        return FakeWalkingRouteProvider(walking_speed_mps=settings.walking_speed_mps)
    return RealKakaoWalkingRouteProvider(
        api_key=_require_key(
            settings.kakao_map_rest_api_key,
            "KAKAO_MAP_REST_API_KEY",
        ),
        client=client,
        timeout_seconds=settings.external_api_timeout_seconds,
        max_concurrency=settings.travel_route_max_concurrency,
        semaphore=semaphore,
    )


def get_driving_route_provider(client: httpx.AsyncClient) -> TravelRouteProvider:
    """설정에 맞는 자동차 경로 Provider를 반환한다."""
    if settings.travel_route_driving_provider == "fake":
        return FakeDrivingRouteProvider(driving_speed_mps=settings.driving_speed_mps)
    return RealNaverDrivingRouteProvider(
        api_key_id=_require_key(settings.naver_map_client_id, "NAVER_MAP_CLIENT_ID"),
        api_key=_require_key(settings.naver_map_client_secret, "NAVER_MAP_CLIENT_SECRET"),
        client=client,
        timeout_seconds=settings.external_api_timeout_seconds,
        max_concurrency=settings.travel_route_max_concurrency,
    )


def get_transit_route_provider(
    client: httpx.AsyncClient, semaphore: asyncio.Semaphore | None = None
) -> TravelRouteProvider:
    """설정에 맞는 대중교통 경로 Provider를 반환한다.

    도보와 같은 카카오 키를 쓴다 — 같은 `Authorization: KakaoAK` 헤더의 다른
    엔드포인트라 키를 하나 더 발급받을 필요가 없다.
    """
    if settings.travel_route_transit_provider == "fake":
        return FakeTransitRouteProvider(transit_speed_mps=settings.transit_speed_mps)
    return RealKakaoTransitRouteProvider(
        api_key=_require_key(
            settings.kakao_map_rest_api_key,
            "KAKAO_MAP_REST_API_KEY",
        ),
        client=client,
        timeout_seconds=settings.external_api_timeout_seconds,
        max_concurrency=settings.travel_route_max_concurrency,
        semaphore=semaphore,
    )


def get_travel_route_tool(client: httpx.AsyncClient) -> TravelRouteTool:
    """이동 경로 Tool을 이동수단별 Provider로 구성한다.

    도보·자동차·대중교통 셋을 등록한다. 미등록 이동수단은 Tool이 호출 없이
    NO_DATA로 답하므로, 등록되지 않은 동안 다른 수단의 값이 대신 나가지 않는다.

    자동차와 대중교통에는 fallback을 두지 않는다. fallback이 내는 직선거리 추정은 source가
    STRAIGHT_LINE_ESTIMATE라 `scoring._applied_travel_route()`가 어차피 걸러내서
    채점에도 문구에도 쓰이지 않는다 — 쓰이지 않을 값을 벤더마다 만들 이유가 없다.
    도보의 fallback은 기존 동작이라 그대로 둔다.
    """
    walking_fallback = (
        FakeWalkingRouteProvider(walking_speed_mps=settings.walking_speed_mps)
        if settings.travel_route_provider == "real"
        else None
    )
    # 도보와 대중교통은 **같은 카카오 키**를 쓰므로 동시 요청 한도를 함께 나눈다.
    # 인스턴스마다 세마포어를 두면 한 후보를 두 수단으로 조회할 때(D-118) 동시
    # 요청이 5+5로 합산돼 카카오가 `API limit has been exceeded.`를 낸다 —
    # 2026-09-02 실측에서 40건 중 대부분이 그렇게 거절됐다. 자동차(네이버)는
    # 벤더가 달라 여기 묶지 않는다.
    kakao_route_semaphore = asyncio.Semaphore(settings.travel_route_max_concurrency)
    return TravelRouteTool(
        {
            TravelMode.WALKING: TravelRouteProviders(
                primary=get_walking_route_provider(client, kakao_route_semaphore),
                fallback=walking_fallback,
            ),
            TravelMode.DRIVING: TravelRouteProviders(
                primary=get_driving_route_provider(client),
                fallback=None,
            ),
            TravelMode.TRANSIT: TravelRouteProviders(
                primary=get_transit_route_provider(client, kakao_route_semaphore),
                fallback=None,
            ),
        }
    )


def get_place_provider(client: httpx.AsyncClient) -> PlaceProvider:
    if settings.resolved_place_provider == "fake":
        return FakePlaceProvider()
    return RealPlaceProvider(
        api_key=_require_key(settings.tour_api_service_key, "TOUR_API_SERVICE_KEY"),
        client=client,
        timeout_seconds=settings.external_api_timeout_seconds,
    )


def get_place_search_provider(client: httpx.AsyncClient) -> PlaceSearchProvider:
    """장소 후보 목록 검색 provider. 상세조회 출처와 무관하게 기존 경로를 유지한다."""
    return get_place_provider(client)


def get_district_place_search_provider(
    client: httpx.AsyncClient,
) -> DistrictPlaceSearchProvider:
    """구 이름으로 들어온 요청의 후보 검색 provider를 준비한다(D-119).

    구 전량 후보는 Supabase에만 있으므로 이 경로는 TourAPI로 대체할 수 없다 —
    locationBasedList2는 좌표와 반경이 필수다. 그래서 실 provider 모드에서 Supabase
    설정이 없으면 **Fake로 내려가지 않고 부팅에서 멈춘다**(D-042). 조용히 Fake로
    바뀌면 "강남구 추천"이 `"테스트 관광지 3"`을 돌려주고 그 사실이 오류 없이
    지나간다.

    구 이름이 아닌 요청은 이 provider를 부르지 않는다 — 후보 수집은 여전히
    `get_place_search_provider()`(TourAPI 반경 검색)가 맡는다.
    """
    if settings.resolved_place_provider == "fake":
        return FakeDistrictPlaceSearchProvider()
    return SupabaseDistrictPlaceSearchProvider(
        SupabasePlaceRepository(
            supabase_url=_require_key(settings.supabase_url, "SUPABASE_URL"),
            secret_key=_require_key(settings.supabase_secret_key, "SUPABASE_SECRET_KEY"),
            client=client,
        )
    )


def get_barrier_free_place_search_provider(
    client: httpx.AsyncClient,
) -> BarrierFreePlaceSearchProvider:
    """무장애 조건이 붙은 요청의 후보 검색 provider를 준비한다.

    무장애 정보는 Supabase에만 있으므로 이 경로는 TourAPI로 대체할 수 없다.
    그래서 실 provider 모드에서 Supabase 설정이 없으면 **Fake로 내려가지 않고
    부팅에서 멈춘다**(D-042). 조용히 Fake로 바뀌면 무장애를 요구한 사용자가
    `"테스트 무장애 카페"`를 추천받고, 그 사실이 오류 없이 지나간다.

    조건이 없는 요청은 이 provider를 부르지 않는다 — 후보 수집은 여전히
    `get_place_search_provider()`(TourAPI)가 맡는다.
    """
    if settings.resolved_place_provider == "fake":
        return FakeBarrierFreePlaceSearchProvider()
    return SupabaseBarrierFreePlaceSearchProvider(
        SupabasePlaceRepository(
            supabase_url=_require_key(settings.supabase_url, "SUPABASE_URL"),
            secret_key=_require_key(settings.supabase_secret_key, "SUPABASE_SECRET_KEY"),
            client=client,
            timeout_seconds=settings.external_api_timeout_seconds,
        )
    )


def get_place_location_repository(
    client: httpx.AsyncClient,
) -> SupabasePlaceRepository | FakePlaceLocationRepository | None:
    """검색 중심점 해석에 사용할 places 저장소를 준비한다.

    Supabase 설정이 없는 개발·테스트 환경은 종로구 대표 장소를 담은 fake 저장소를
    쓴다. 집중률 조회는 매핑된 장소명으로만 나가므로(D-043) 저장소가 없으면 INFO
    혼잡도가 전부 no_data로 떨어져 fake 환경에서 경로 확인이 불가능해진다.
    """
    if (
        settings.resolved_place_provider != "real"
        or not settings.supabase_url.strip()
        or not settings.supabase_secret_key.strip()
    ):
        return FakePlaceLocationRepository()
    return SupabasePlaceRepository(
        supabase_url=settings.supabase_url,
        secret_key=settings.supabase_secret_key,
        client=client,
        timeout_seconds=settings.external_api_timeout_seconds,
    )


def get_place_photo_repository(
    client: httpx.AsyncClient,
) -> PlacePhotoRepository:
    """상세 화면에 보여줄 장소 사진의 출처를 준비한다.

    적재된 장소는 place_image_embeddings에서 읽고, 적재되지 않은 장소만
    detailImage2로 채운다. 순서를 이렇게 두는 이유와 캐시·한도 처리는
    hybrid_place_photos.py 모듈 문서를 본다.

    Supabase 설정이 없으면 fake 저장소를 준다. 없는 상태로 두면 fake 환경에서
    여러 장 경로가 한 번도 실행되지 않는다.
    """
    if (
        settings.resolved_place_provider != "real"
        or not settings.supabase_url.strip()
        or not settings.supabase_secret_key.strip()
    ):
        return FakePlacePhotoRepository()
    return HybridPlacePhotoProvider(
        photo_repository=SupabasePlaceRepository(
            supabase_url=settings.supabase_url,
            secret_key=settings.supabase_secret_key,
            client=client,
            timeout_seconds=settings.external_api_timeout_seconds,
        ),
        # 여기 도달하면 place provider가 실 provider다(위 게이트에서 fake는 빠진다).
        image_provider=cast(PlaceImageProvider, get_place_provider(client)),
        display_limit=settings.place_photo_display_limit,
        cache_ttl_seconds=settings.place_photo_api_cache_ttl_seconds,
    )


def get_place_details_repository(
    client: httpx.AsyncClient,
) -> SupabasePlaceRepository | None:
    """content_id로 상세 행만 읽는 저장소.

    사진 검색이 쓴다 — 사진 유사도로 먼저 줄을 세운 뒤 상위 N곳의 영업시간만
    확인하는 경로라, TourAPI를 거치는 `NearbyPlaceDetailsTool`(후보 20곳 상한)이
    맞지 않는다. 이쪽은 DB만 읽어 상한이 없다.
    """
    if not settings.supabase_url.strip() or not settings.supabase_secret_key.strip():
        return None
    return SupabasePlaceRepository(
        supabase_url=settings.supabase_url,
        secret_key=settings.supabase_secret_key,
        client=client,
        timeout_seconds=settings.external_api_timeout_seconds,
    )


def get_place_details_provider(client: httpx.AsyncClient) -> PlaceDetailsProvider:
    """후보별 상세·운영정보 provider를 PLACE_DETAILS_SOURCE에 따라 고른다.

    supabase 모드는 요청 시 TourAPI fallback을 하지 않는다 — 저장소 장애는
    Tool에서 unavailable로 그대로 노출된다.
    """
    if settings.resolved_place_details_source == "supabase":
        return SupabasePlaceDetailsProvider(
            SupabasePlaceRepository(
                supabase_url=_require_key(settings.supabase_url, "SUPABASE_URL"),
                secret_key=_require_key(settings.supabase_secret_key, "SUPABASE_SECRET_KEY"),
                client=client,
                timeout_seconds=settings.external_api_timeout_seconds,
            )
        )
    return get_place_provider(client)


def get_info_place_detail_provider(
    client: httpx.AsyncClient,
) -> PlaceDetailByNameProvider:
    """INFO 상세 질의(장소 1건)가 쓸 provider를 준비한다.

    places 캐시가 INFO가 답해야 할 값(운영시간·주차·요금·안내처·편의시설)을 전부
    들고 있어 하이브리드 경로만 남긴다 — 설정으로 고르지 않는다. TourAPI 직접
    조회와 답할 수 있는 질문이 같아지면서 고를 이유가 없어졌다(D-060).

    외부 호출은 3회(searchKeyword2 + detailCommon2 + detailIntro2)에서 1회
    (detailCommon2)로 준다. overview·homepage는 캐시에 없어 그 1회가 남는다.
    """
    if settings.resolved_place_provider == "fake":
        return get_place_provider(client)

    repository = SupabasePlaceRepository(
        supabase_url=_require_key(settings.supabase_url, "SUPABASE_URL"),
        secret_key=_require_key(settings.supabase_secret_key, "SUPABASE_SECRET_KEY"),
        client=client,
        timeout_seconds=settings.external_api_timeout_seconds,
    )
    return HybridPlaceDetailsProvider(
        location_repository=repository,
        details_repository=repository,
        # overview·homepage는 캐시에 없어 TourAPI가 계속 필요하다. fake 모드는 위에서
        # tour_api로 빠지므로 여기 도달하면 항상 실 provider다.
        common_provider=get_place_provider(client),
    )


def get_recommendation_card_tool(
    client: httpx.AsyncClient,
) -> RecommendationCardTool:
    """추천 카드 조립 Tool을 places 저장소와 함께 준비한다.

    카드 정보는 전부 동기화된 places 행에서 오므로 PLACE_DETAILS_SOURCE가 아니라
    Supabase 설정 유무로만 갈린다 — TourAPI 직접 조회 경로에는 대응 데이터가 없다.
    설정이 없는 개발·테스트 환경은 fake 저장소를 쓴다.
    """
    google_photo_provider = get_google_place_photo_provider(client)
    if not settings.supabase_url.strip() or not settings.supabase_secret_key.strip():
        return RecommendationCardTool(
            FakePlaceDetailsRepository(),
            google_photo_provider=google_photo_provider,
        )
    return RecommendationCardTool(
        SupabasePlaceRepository(
            supabase_url=settings.supabase_url,
            secret_key=settings.supabase_secret_key,
            client=client,
            timeout_seconds=settings.external_api_timeout_seconds,
        ),
        google_photo_provider=google_photo_provider,
    )


def get_festival_provider(client: httpx.AsyncClient) -> FestivalProvider:
    """행사 조회 provider. 장소 provider와 같은 PLACE_PROVIDER 설정을 따른다."""
    if settings.resolved_place_provider == "fake":
        return FakeFestivalProvider()
    return RealFestivalProvider(
        api_key=_require_key(settings.tour_api_service_key, "TOUR_API_SERVICE_KEY"),
        client=client,
        timeout_seconds=settings.external_api_timeout_seconds,
    )


def get_concentration_provider(client: httpx.AsyncClient) -> ConcentrationProvider:
    if settings.resolved_concentration_provider == "fake":
        return FakeConcentrationProvider()
    return RealConcentrationProvider(
        api_key=_require_key(settings.tour_api_service_key, "TOUR_API_SERVICE_KEY"),
        client=client,
        timeout_seconds=settings.external_api_timeout_seconds,
    )


def get_realtime_commercial_provider(
    client: httpx.AsyncClient,
) -> RealtimeCommercialProvider:
    """서울시 실시간 상권현황 Provider를 설정에 맞춰 만든다."""

    if settings.resolved_seoul_citydata_provider == "fake":
        return FakeRealtimeCommercialProvider()
    return RealRealtimeCommercialProvider(
        api_key=_require_key(settings.seoul_open_data_api_key, "SEOUL_OPEN_DATA_API_KEY"),
        client=client,
        timeout_seconds=settings.external_api_timeout_seconds,
    )


def get_realtime_citydata_provider(client: httpx.AsyncClient) -> RealtimeCityDataProvider:
    if settings.resolved_seoul_citydata_provider == "fake":
        return FakeRealtimeCityDataProvider()
    return RealRealtimeCityDataProvider(
        api_key=_require_key(settings.seoul_open_data_api_key, "SEOUL_OPEN_DATA_API_KEY"),
        client=client,
        timeout_seconds=settings.external_api_timeout_seconds,
    )


def get_municipal_parking_provider(client: httpx.AsyncClient) -> MunicipalParkingProvider:
    """시영·공영주차장 최신 대수 Provider. 서울시 도시데이터와 같은 키를 쓴다."""

    if settings.resolved_seoul_citydata_provider == "fake":
        return FakeMunicipalParkingProvider()
    return RealMunicipalParkingProvider(
        api_key=_require_key(settings.seoul_open_data_api_key, "SEOUL_OPEN_DATA_API_KEY"),
        client=client,
        timeout_seconds=settings.external_api_timeout_seconds,
    )


def get_municipal_parking_catalog_repository(
    client: httpx.AsyncClient,
) -> SupabaseMunicipalParkingRepository | FakeMunicipalParkingCatalogRepository:
    """공영주차장 좌표 카탈로그. Supabase 미설정 개발 환경은 빈 fake를 쓴다."""

    if not settings.supabase_url.strip() or not settings.supabase_secret_key.strip():
        return FakeMunicipalParkingCatalogRepository()
    return SupabaseMunicipalParkingRepository(
        supabase_url=settings.supabase_url,
        secret_key=settings.supabase_secret_key,
        client=client,
        timeout_seconds=settings.external_api_timeout_seconds,
    )


def get_public_toilet_provider(client: httpx.AsyncClient) -> PublicToiletProvider:
    """공중화장실 위치 전량 조회 Provider. 서울시 도시데이터와 같은 키를 쓴다.

    사용자 요청 경로에는 쓰이지 않는다 — 적재 스크립트 전용이다(구·좌표 필터가
    없어 매 요청 전량을 받을 수 없다).
    """

    if settings.resolved_seoul_citydata_provider == "fake":
        return FakePublicToiletProvider()
    return RealPublicToiletProvider(
        api_key=_require_key(settings.seoul_open_data_api_key, "SEOUL_OPEN_DATA_API_KEY"),
        client=client,
        timeout_seconds=settings.external_api_timeout_seconds,
    )


def get_public_toilet_repository(
    client: httpx.AsyncClient,
) -> SupabasePublicToiletRepository | FakePublicToiletRepository:
    """적재된 공중화장실 위치 저장소. Supabase 미설정 개발 환경은 fake를 쓴다.

    fake는 인사동 주변 3곳만 들고 있어(providers/public_toilet.py의 Fake와 같은
    데이터) 개발 환경에서도 카드 렌더까지 확인할 수 있다.
    """

    if not settings.supabase_url.strip() or not settings.supabase_secret_key.strip():
        return FakePublicToiletRepository(FAKE_TOILETS)
    return SupabasePublicToiletRepository(
        supabase_url=settings.supabase_url,
        secret_key=settings.supabase_secret_key,
        client=client,
        timeout_seconds=settings.external_api_timeout_seconds,
    )


def get_holiday_provider(client: httpx.AsyncClient) -> HolidayProvider:
    if settings.resolved_holiday_provider == "fake":
        return FakeHolidayProvider()
    return RealHolidayProvider(
        api_key=_require_key(settings.tour_api_service_key, "TOUR_API_SERVICE_KEY"),
        client=client,
        timeout_seconds=settings.external_api_timeout_seconds,
    )


# (환경변수명, 값 getter) — "real" 모드에서 반드시 채워져야 하는 자격증명 목록.
_REQUIRED_KEYS: dict[str, tuple[tuple[str, str], ...]] = {
    "LLM_PROVIDER": (("LLM_API_KEY", "llm_api_key"),),
    "WEATHER_PROVIDER": (("WEATHER_API_KEY", "weather_api_key"),),
    "PLACE_PROVIDER": (("TOUR_API_SERVICE_KEY", "tour_api_service_key"),),
    "CONCENTRATION_PROVIDER": (("TOUR_API_SERVICE_KEY", "tour_api_service_key"),),
    "SEOUL_CITYDATA_PROVIDER": (("SEOUL_OPEN_DATA_API_KEY", "seoul_open_data_api_key"),),
    "HOLIDAY_PROVIDER": (("TOUR_API_SERVICE_KEY", "tour_api_service_key"),),
    # 이동수단마다 벤더가 다르므로 따로 검증한다 — 도보만 real로 쓰는 설정에서
    # 네이버 키를 요구하면 부팅이 불필요하게 막힌다.
    "TRAVEL_ROUTE_PROVIDER": (("KAKAO_MAP_REST_API_KEY", "kakao_map_rest_api_key"),),
    "TRAVEL_ROUTE_TRANSIT_PROVIDER": (("KAKAO_MAP_REST_API_KEY", "kakao_map_rest_api_key"),),
    "TRAVEL_ROUTE_DRIVING_PROVIDER": (
        ("NAVER_MAP_CLIENT_ID", "naver_map_client_id"),
        ("NAVER_MAP_CLIENT_SECRET", "naver_map_client_secret"),
    ),
    "LOCAL_SEARCH_PROVIDER": (
        ("NAVER_LOCAL_SEARCH_CLIENT_ID", "naver_local_search_client_id"),
        ("NAVER_LOCAL_SEARCH_CLIENT_SECRET", "naver_local_search_client_secret"),
    ),
    "GEOCODING_PROVIDER": (
        ("NAVER_MAP_CLIENT_ID", "naver_map_client_id"),
        ("NAVER_MAP_CLIENT_SECRET", "naver_map_client_secret"),
    ),
}

_RESOLVED_ATTRS: dict[str, str] = {
    "LLM_PROVIDER": "resolved_llm_provider",
    "WEATHER_PROVIDER": "resolved_weather_provider",
    "PLACE_PROVIDER": "resolved_place_provider",
    "CONCENTRATION_PROVIDER": "resolved_concentration_provider",
    "SEOUL_CITYDATA_PROVIDER": "resolved_seoul_citydata_provider",
    "HOLIDAY_PROVIDER": "resolved_holiday_provider",
    "GEOCODING_PROVIDER": "resolved_geocoding_provider",
    "LOCAL_SEARCH_PROVIDER": "resolved_local_search_provider",
    "TRAVEL_ROUTE_PROVIDER": "travel_route_provider",
    "TRAVEL_ROUTE_DRIVING_PROVIDER": "travel_route_driving_provider",
    "TRAVEL_ROUTE_TRANSIT_PROVIDER": "travel_route_transit_provider",
}


# tour_api 상세로 감당할 수 있는 후보 수의 상한. 이 값을 넘으면 부팅을 막는다.
#
# 10으로 잡은 근거는 TourAPI 일일 한도다. 추천 경로는 후보 전량의 상세를 받으므로
# 후보 1곳당 detailCommon2 + detailIntro2 2회가 나가고, tour_api_daily_call_limit이
# 오퍼레이션별 1000이라 후보 10곳이면 100요청, 30곳이면 33요청 만에 소진된다.
# 이전 기본값이 10이었던 것도 사실상 이 선이었다.
_MAX_TOUR_API_DETAIL_CANDIDATE_LIMIT = 10


def _validate_details_source_against_candidate_limit(current: Settings) -> None:
    """상세를 TourAPI로 받으면서 후보 한도를 높게 잡은 조합을 부팅에서 막는다.

    조용히 도는 대신 부팅에서 끊는 이유는 D-042와 같다 — 이 조합은 오류를 내지
    않고 **일일 한도만 빠르게 태운다**. 첫 요청이 아니라 부팅에서 드러나야 한다.

    지연이 아니라 호출 수가 문제다(안국역 실측: 후보 30곳에 61회, 2.1초). 느린
    것은 견딜 수 있지만 한도 소진은 그날 서비스가 멈추는 일이다.

    fake 모드는 상세도 Fake가 담당하므로 검사하지 않는다
    (`resolved_place_details_source`).
    """
    if current.resolved_place_provider != "real":
        return
    if current.resolved_place_details_source != "tour_api":
        return
    if current.recommendation_candidate_limit <= _MAX_TOUR_API_DETAIL_CANDIDATE_LIMIT:
        return
    raise ValueError(
        "PLACE_DETAILS_SOURCE=tour_api에서는 "
        f"RECOMMENDATION_CANDIDATE_LIMIT이 {_MAX_TOUR_API_DETAIL_CANDIDATE_LIMIT} "
        f"이하여야 합니다(현재 {current.recommendation_candidate_limit}). "
        "추천은 후보 전량의 상세를 조회하므로 후보 1곳당 TourAPI 호출 2회가 나가고, "
        "일일 한도가 금방 소진됩니다. PLACE_DETAILS_SOURCE=supabase로 두거나 "
        "후보 한도를 낮추세요."
    )


def validate_provider_config(target: Settings | None = None) -> None:
    """real 모드 provider에 필요한 자격증명이 모두 있는지 부팅 시점에 확인한다.

    누락된 항목을 하나씩 발견해 재배포하는 왕복을 없애기 위해 전부 모아서 보고한다.
    Settings 자체에 넣지 않는 이유: 설정 해석만 검증하는 테스트가 자격증명 없이
    Settings(provider_mode="real")을 만들 수 있어야 한다.
    """
    current = target if target is not None else settings
    # 같은 키를 여러 provider가 공유하므로(TOUR_API_SERVICE_KEY) 키 기준으로 모아
    # 어느 provider 때문에 필요한지 함께 보고한다.
    required_by: dict[str, list[str]] = {}
    for provider_variable, required in _REQUIRED_KEYS.items():
        if getattr(current, _RESOLVED_ATTRS[provider_variable]) != "real":
            continue
        for variable_name, attribute in required:
            if not getattr(current, attribute):
                required_by.setdefault(variable_name, []).append(provider_variable)
    if required_by:
        missing = [
            f"{variable_name} ({', '.join(providers)}=real)"
            for variable_name, providers in required_by.items()
        ]
        raise ValueError(
            "real provider 설정에 필요한 환경변수가 비어 있습니다: " + ", ".join(missing)
        )

    _validate_details_source_against_candidate_limit(current)

    # 폐지된 단일 모델 설정이 .env에 남아 있으면 부팅을 막는다. 값이 무시될 뿐 동작은
    # 하므로 그냥 두면 `.env`에 적힌 모델과 실제로 호출되는 모델이 다른 채로 돌고,
    # 그 차이는 응답이 이상해진 뒤에야 드러난다. 실패는 첫 요청이 아니라 부팅에서
    # 드러나야 한다(D-042).
    legacy_settings = [
        variable_name
        for variable_name, attribute in (
            ("LLM_MODEL_NAME", "legacy_llm_model_name"),
            ("LLM_FALLBACK_MODEL_NAMES", "legacy_llm_fallback_model_names"),
        )
        if getattr(current, attribute)
    ]
    if legacy_settings:
        raise ValueError(
            "폐지된 LLM 모델 설정이 남아 있습니다: "
            + ", ".join(legacy_settings)
            + ". 이 값은 더 이상 사용되지 않으니 지우고, 역할별 설정으로 옮기세요 — "
            "의도 분류·조건 추출은 LLM_FAST_MODEL_NAME/LLM_FAST_FALLBACK_MODEL_NAMES, "
            "답변·비교·일정 생성은 LLM_GENERATION_MODEL_NAME/"
            "LLM_GENERATION_FALLBACK_MODEL_NAMES입니다."
        )

    # 역할별 폴백 목록에 1순위 모델과 같은 이름이 중복되면 폴백처럼 보이지만 실제로는
    # 같은 모델을 또 재시도하는 것뿐이라 부팅 시점에 막는다.
    if current.resolved_llm_provider == "real":
        for setting_name, models in (
            ("LLM_FAST_FALLBACK_MODEL_NAMES", current.resolved_llm_fast_models),
            ("LLM_GENERATION_FALLBACK_MODEL_NAMES", current.resolved_llm_generation_models),
        ):
            if len(models) != len(set(models)):
                raise ValueError(
                    f"{setting_name}에 1순위 모델과 중복되는 모델이 있습니다: " + ", ".join(models)
                )

    # 상세조회 출처는 provider 모드와 축이 다르므로 별도로 검증한다.
    if current.resolved_place_details_source == "supabase":
        missing_supabase = [
            variable_name
            for variable_name, attribute in (
                ("SUPABASE_URL", "supabase_url"),
                ("SUPABASE_SECRET_KEY", "supabase_secret_key"),
            )
            if not getattr(current, attribute)
        ]
        if missing_supabase:
            raise ValueError(
                "PLACE_DETAILS_SOURCE=supabase에 필요한 환경변수가 비어 있습니다: "
                + ", ".join(missing_supabase)
            )

    # Package B State 저장소 백엔드도 provider 모드와 축이 다른 별도 설정이다.
    if current.state_store_backend == "supabase":
        missing_state_store = [
            variable_name
            for variable_name, attribute in (
                ("SUPABASE_URL", "supabase_url"),
                ("SUPABASE_SECRET_KEY", "supabase_secret_key"),
            )
            if not getattr(current, attribute)
        ]
        if missing_state_store:
            raise ValueError(
                "STATE_STORE_BACKEND=supabase에 필요한 환경변수가 비어 있습니다: "
                + ", ".join(missing_state_store)
            )


def get_google_place_photo_provider(
    client: httpx.AsyncClient,
) -> GooglePlacePhotoProviderProtocol | None:
    """Google 사진 보강 Provider를 만든다. 꺼져 있으면 None이다.

    None이면 관광공사 이미지가 없는 장소가 지금처럼 자리표시로 나간다 — 추천
    자체는 달라지지 않는다.

    LLM이 fake인 환경에서는 Fake를 준다. 실제 키로 돈이 나가는 호출을 개발·
    테스트에서 내지 않기 위해서다. Fake는 사진을 찾지 못하는 경우도 재현하므로
    "못 찾으면 원래대로 둔다"는 분기가 그 환경에서도 실제로 돈다.
    """
    if not settings.google_place_photo_enabled:
        return None
    if settings.resolved_llm_provider == "fake":
        return FakeGooglePlacePhotoProvider()
    if not settings.google_places_api_key.strip():
        # 부팅을 막지 않는다. 사진은 있으면 좋은 값이라 없어도 추천은 동작하고,
        # 여기서 죽이면 설정 하나 때문에 서비스 전체가 안 뜬다. 대신 왜 안
        # 켜졌는지는 남긴다 — 조용히 사라지면 "켰는데 왜 사진이 그대로냐"를
        # 추적할 방법이 없다.
        logger.warning(
            "GOOGLE_PLACE_PHOTO_ENABLED=true인데 GOOGLE_PLACES_API_KEY가 비어 있어"
            " Google 사진 보강을 끕니다."
        )
        return None
    return GooglePlacePhotoProvider(
        api_key=settings.google_places_api_key,
        client=client,
        timeout_seconds=settings.external_api_timeout_seconds,
        max_width_px=settings.google_place_photo_max_width_px,
        cache_ttl_seconds=settings.google_place_photo_cache_ttl_seconds,
    )


def get_place_evidence_provider(
    client: httpx.AsyncClient,
) -> PlaceEvidenceProvider | None:
    """취향 근거 검색 Provider를 만든다. 꺼져 있으면 None이다.

    None이면 채점이 taste Feature를 아예 쓰지 않는다 — 후보 일부만 점수를
    갖는 상태가 생기지 않도록 요청 단위로 켜고 끈다(scoring.py).
    """
    if not settings.taste_evidence_enabled:
        return None
    if not settings.supabase_url or not settings.supabase_secret_key:
        # 부팅을 막지 않는다. 취향은 순위를 다듬는 축이라 없어도 추천은
        # 동작하고, 여기서 죽이면 설정 하나 때문에 서비스 전체가 안 뜬다.
        # 대신 왜 안 켜졌는지는 로그로 남긴다 — 조용히 사라지면 "켰는데 왜
        # 순위가 그대로냐"를 추적할 방법이 없다.
        logger.warning(
            "TASTE_EVIDENCE_ENABLED=true인데 SUPABASE_URL/SUPABASE_SECRET_KEY가"
            " 비어 있어 취향 근거 검색을 끕니다."
        )
        return None
    repository = SupabasePlaceRepository(
        supabase_url=settings.supabase_url,
        secret_key=settings.supabase_secret_key,
        client=client,
        timeout_seconds=settings.external_api_timeout_seconds,
    )
    return PlaceEvidenceProvider(get_shared_encoder(), repository)


def get_place_mood_provider(
    client: httpx.AsyncClient,
) -> PlaceMoodProvider | None:
    """장소 분위기 Provider를 만든다. 꺼져 있으면 None이다.

    None이면 분위기 축이 채점에 아예 안 들어간다 — 후보 일부만 점수를 갖는
    상태가 생기지 않도록 요청 단위가 아니라 여기서 한 번에 끊는다.

    **인코더가 없어도 Provider는 만든다.** 발화 경로(축 점수 조회)는 모델 없이
    돌기 때문이다. 사진 경로만 못 쓰고, 그건 Provider가 스스로
    `photo_search_available`로 알린다.
    """
    if not settings.place_mood_enabled:
        return None
    if not settings.supabase_url or not settings.supabase_secret_key:
        # 취향 쪽과 같은 이유로 부팅을 막지 않는다. 분위기는 순위를 다듬는
        # 축이라 없어도 추천은 동작한다. 대신 왜 안 켜졌는지는 남긴다 — 조용히
        # 사라지면 "켰는데 왜 순위가 그대로냐"를 추적할 방법이 없다.
        logger.warning(
            "PLACE_MOOD_ENABLED=true인데 SUPABASE_URL/SUPABASE_SECRET_KEY가"
            " 비어 있어 장소 분위기 기능을 끕니다."
        )
        return None

    repository = SupabasePlaceRepository(
        supabase_url=settings.supabase_url,
        secret_key=settings.supabase_secret_key,
        client=client,
        timeout_seconds=settings.external_api_timeout_seconds,
    )
    return PlaceMoodProvider(
        repository,
        _get_mood_encoder(),
        mean_center=settings.place_mood_mean_center_enabled,
        axis_weight=settings.place_mood_axis_weight,
    )


def get_place_mood_reranker(
    client: httpx.AsyncClient,
) -> GeminiPhotoReranker | None:
    """사진 검색 VLM 재랭커를 만든다. 꺼져 있거나 못 쓰면 None이다.

    None이면 사진 검색이 임베딩 순서를 그대로 낸다 — 기능이 하나 빠질 뿐 검색은
    정상이다. 그래서 부팅을 막지 않는다.

    **Fake LLM 모드에서는 만들지 않는다.** 가짜 응답으로 순서를 바꾸면 임베딩이
    낸 순서가 근거 없이 뒤집히는데, 오류가 없어 알아채기 어렵다(D-042와 같은
    성격의 조용한 fake다).
    """
    if not settings.place_mood_rerank_enabled:
        return None
    if settings.resolved_llm_provider != "real":
        logger.warning(
            "PLACE_MOOD_RERANK_ENABLED=true인데 LLM이 fake 모드라 사진 검색"
            " 재랭킹을 끕니다."
        )
        return None
    if not settings.llm_api_key:
        logger.warning(
            "PLACE_MOOD_RERANK_ENABLED=true인데 LLM_API_KEY가 비어 있어 사진 검색"
            " 재랭킹을 끕니다."
        )
        return None
    return GeminiPhotoReranker(
        api_key=settings.llm_api_key,
        model_name=settings.resolved_place_mood_rerank_model_name,
        timeout_seconds=settings.resolved_llm_timeout_seconds,
        http_client=client,
    )


def _get_mood_encoder() -> object | None:
    """SigLIP 인코더를 만든다. 설치돼 있지 않으면 None이다.

    **import 실패를 삼키지만 조용히 넘어가지는 않는다.** 선택 의존성이라 없는
    환경이 정상이고 부팅을 막을 이유는 없지만, 사진 검색이 소리 없이 사라지면
    "사진을 올렸는데 왜 아무 일도 안 일어나냐"를 알 수 없다.
    """
    try:
        from app.providers.place_mood_encoder import get_shared_encoder as get_encoder
    except ImportError:  # pragma: no cover - 설치 환경 의존
        logger.warning(
            "사진 임베딩 인코더를 불러오지 못해 사진 검색 없이 동작합니다."
            ' 필요하면 `pip install -e ".[mood]"`로 설치하세요.'
        )
        return None

    encoder = get_encoder()
    if settings.place_mood_warmup_enabled:
        # 적재를 백그라운드로 돌린다. 동기로 부르면 그만큼 부팅이 늦어진다.
        encoder.warmup_in_background()
    return encoder
