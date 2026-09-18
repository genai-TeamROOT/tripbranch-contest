"""A의 조건 요청을 받아 필요한 C Tool을 실행하고 공통 Context를 반환한다."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from time import perf_counter
from typing import Literal, cast
from urllib.parse import quote
from zoneinfo import ZoneInfo

from app.agent_context.assembler import (
    ContextAssemblyInput,
    assemble_agent_context_response,
)
from app.agent_context.category_rules import (
    CategoryQueryPlan,
    ExcludedCategoryPlan,
    build_category_query_plan,
    build_excluded_category_plan,
)
from app.agent_context.compare_schemas import (
    CompareContextRequest,
    CompareContextResponse,
)
from app.agent_context.concentration_proxy import (
    ConcentrationMappingCache,
    select_nearest_mapped_places,
)
from app.agent_context.enrichment_service import (
    execute_concentration_by_search_keys,
    parse_concentration_forecast_date,
    select_concentration_forecast,
    select_concentration_forecasts,
)
from app.agent_context.info_field_rules import (
    clean_barrier_free_text,
    clean_text,
    compose_accessible_restroom,
    compose_nursing_room,
    compose_seating,
    compose_visual_guide,
    extract_info_fields,
    resolve_stroller_rental,
)
from app.agent_context.info_schemas import (
    CommercialPaymentCategoryInfo,
    ConcentrationForecastInfo,
    ConcentrationInfoResult,
    DistrictAreaCongestionInfo,
    DistrictPopulationInfoResult,
    EventInfoResult,
    EventItem,
    InfoContextRequest,
    InfoContextResponse,
    PlaceCard,
    PlaceInfoResult,
    PlacePhotoItem,
    PopulationAgeShareInfo,
    PopulationForecastInfo,
    RealtimeCityInfoResult,
    RealtimeCommercialInfoResult,
    RealtimeInfoDetailItem,
    RealtimePopulationInfoResult,
    ReviewEvidenceItem,
    RoadIncidentCategoryCountInfo,
    SeoulRealtimeSummaryInfo,
)
from app.agent_context.schemas import (
    AgentContextRequest,
    AgentContextResponse,
    Clarification,
    ContextError,
    Coordinates,
    DistrictScope,
    ResponseMetadata,
    parse_candidate_names,
)
from app.agent_context.schemas import ProviderMetadata as ContextProviderMetadata
from app.agent_context.seoul_realtime_areas import (
    COMMERCIAL_AREA_PROXY_MAX_DISTANCE_KM,
    POPULATION_AREAS,
    SeoulRealtimeArea,
    population_areas_in_district,
    select_nearest_commercial_area,
    select_nearest_population_area,
)
from app.agent_context.tool_rules import (
    TOOL_EXECUTION_RULE_VERSION,
    ContextTool,
    build_tool_execution_plan,
)
from app.concentration_policy import (
    INFO_CONCENTRATION_FALLBACK_ATTEMPT_LIMIT,
    INFO_CONCENTRATION_FALLBACK_RADIUS_KM,
    concentration_signgu_code,
    is_valid_concentration_rate,
    normalize_concentration,
)
from app.config import settings
from app.domain.models import (
    AccessibilityNeed,
    ConcentrationResult,
    MunicipalParkingStatus,
    PlaceDetails,
    PlacePhoto,
    RealtimeBusStop,
    RealtimeCityEvent,
    RealtimeCommercialCategory,
    RealtimeCommercialResult,
    RealtimeParkingLot,
    RealtimePopulationResult,
    RealtimeSubwayArrival,
)
from app.domain.review_evidence import usable_snippets
from app.errors import AppError
from app.geo import haversine_km
from app.place_search_policy import (
    DEFAULT_PLACE_SEARCH_RADIUS_KM,
    MAX_PLACE_SEARCH_RADIUS_KM,
    MIN_PLACE_SEARCH_RADIUS_KM,
    WALKING_SPEED_KM_PER_MINUTE,
)
from app.providers.contracts import ProviderMetadata, ProviderSource, ProviderStatus
from app.providers.festival import FestivalEvent
from app.providers.place_evidence import PlaceEvidenceProvider
from app.public_toilet_hours import describe_open_hours
from app.recommendation_limits import (
    MAX_RECOMMENDATION_CANDIDATE_LIMIT,
    MIN_RECOMMENDATION_LIMIT,
)
from app.repositories.protocols import (
    MunicipalParkingCatalogRepository,
    PlacePhotoRepository,
)
from app.schemas import CompareCriteria, ComparisonItem, StaleAreaProbeDebug
from app.service_area import SUPPORTED_DISTRICTS, ServiceDistrict
from app.tools.concentration import (
    GetConcentrationTool,
)
from app.tools.contracts import ToolError, ToolStatus
from app.tools.festival import FestivalQuery, GetFestivalsTool
from app.tools.holiday import GetHolidaysTool, HolidayQuery
from app.tools.municipal_parking import GetMunicipalParkingTool, MunicipalParkingQuery
from app.tools.nearby_place_details import (
    CANDIDATE_POOL_EXHAUSTED_WARNING,
    CANDIDATE_POOL_TRUNCATED_WARNING,
    UNKNOWN_ACCESSIBILITY_NEED_WARNING,
    EnrichedPlace,
    NearbyPlaceDetailsQuery,
    NearbyPlaceDetailsResult,
    NearbyPlaceDetailsTool,
)
from app.tools.place_detail import (
    GetPlaceDetailTool,
    PlaceDetailQuery,
)
from app.tools.public_toilet import (
    GetPublicToiletTool,
    NearbyToilet,
    PublicToiletQuery,
)
from app.tools.realtime_citydata import GetRealtimeCityDataTool, RealtimeCityDataQuery
from app.tools.realtime_commercial import (
    GetRealtimeCommercialTool,
)
from app.tools.recommendation_cards import RecommendationCardTool
from app.tools.resolve_location import (
    LocationPurpose,
    LocationSource,
    ResolutionConfidence,
    ResolutionMethod,
    ResolvedLocation,
    ResolveLocationQuery,
    ResolveLocationResult,
    ResolveLocationTool,
)
from app.tools.weather_forecast import (
    GetWeatherForecastTool,
    WeatherForecastQuery,
)

_KST = ZoneInfo("Asia/Seoul")
_CATEGORY_RULE_VERSION = "tour-category-v1"
_SEARCH_RADIUS_RULE_VERSION = "walking-radius-v1"

# 비교가 성립하는 최소 후보 수. 1건이 남으면 비교가 아니라 단일 안내다.
_MIN_COMPARE_ITEMS = 2

# criteria별로 "이 값이 없으면 비교할 게 없는" 필드. overall은 세 값을 함께 설명하는
# 방식이라(A 확정) 특정 필드를 요구하지 않는다.
# TRAVEL_TIME은 travel_* 수단별 값을 여기서 채우지 않는다(A가 실측 호출 후 채운다)
# — 대신 실측에 필요한 좌표(latitude)가 있는지로 판정한다.
_COMPARE_CRITERIA_FIELDS: dict[CompareCriteria, str] = {
    CompareCriteria.TIME: "remaining_minutes",
    CompareCriteria.TRAVEL_TIME: "latitude",
}

# INFO 행사 응답에 싣는 최대 건수. 챗봇 말풍선 한 번에 읽히는 분량으로 제한한다.
INFO_EVENT_RESULT_LIMIT = 5

# 후기 질의가 장소 한 곳에서 받아 올 근거 수. RPC가 글 단위로 중복을 제거한 뒤
# 상한을 적용하므로 서로 다른 글 여덟 개에서 한 문장씩 온다(실측: 장소당 서로 다른
# 글 29~31개). 이 여덟 개를 선별 단계가 읽고 고른다.
REVIEW_EVIDENCE_MATCH_COUNT = 8

# 검색 단계 유사도 컷. **답할 수 있는 질문인지를 여기서 가르지 않는다.**
# 실측(2026-09-14, 질문 24개)에서 답 가능/불가의 유사도 분포가 0.45~0.70에서 겹쳐
# 어떤 값을 잡아도 한쪽을 잃는다 — "북촌한옥마을 뭐가 맛있대?"는 답이 없는데 근처
# 만둣국집 후기가 0.683이었고, "경복궁 아이와 가기 좋대?"는 0.453이었다. 그래서
# 낮게 잡아 후보를 넉넉히 넘기고 판정은 선별 단계에 맡긴다.
REVIEW_EVIDENCE_MIN_SIMILARITY = 0.25
_CURRENT_ACTIVITY_MARKERS = ("지금", "현재", "오늘")
_COMMERCIAL_CATEGORY_MARKERS = ("카페", "커피", "제과", "패스트푸드")
_REALTIME_CITYDATA_QUESTION_TYPES = {
    "realtime_parking",
    "realtime_subway",
    "realtime_bus",
    "realtime_event",
    "realtime_traffic",
}
_PUBLIC_PARKING_QUESTION_TYPE = "realtime_public_parking"

# 구 이름만으로 물어도 구 단위 조회로 답할 수 있는 주차 질문. 서울시 GetParkingInfo가
# 원래 구 단위 API라, 특정 장소를 못 집어도 답이 나온다.
_DISTRICT_PARKING_QUESTION_TYPES = frozenset(
    {"parking", "realtime_parking", _PUBLIC_PARKING_QUESTION_TYPE}
)
_PUBLIC_TOILET_QUESTION_TYPE = "public_toilet"
# 급해서 묻는 질문이라 걸어갈 수 있는 거리만 본다. 1km를 넘기면 "근처"가 아니고,
# 실측(인사동 기준 1km 내 101곳)상 이 범위 안에서 답이 충분히 나온다.
_PUBLIC_TOILET_RADIUS_KM = 1.0
# 말풍선과 카드에 싣는 곳 수. 급한 사람에게 목록을 길게 주면 고르는 게 일이 된다.
_PUBLIC_TOILET_RESULT_LIMIT = 2
# 행사를 찾는 질의. 지명이 없을 때 되묻는 문장이 다른 유형과 달라야 해서 따로 묶는다
# — "축제 추천해줘"는 장소를 물은 발화가 아니다(TP-237).
_EVENT_QUESTION_TYPES = {"event", "realtime_event"}
_CITYDATA_SOURCE_URL = "https://data.seoul.go.kr/dataList/OA-21285/F/1/datasetView.do"
_MUNICIPAL_PARKING_SOURCE_URL = "https://data.seoul.go.kr/dataList/OA-21709/S/1/datasetView.do"
_PUBLIC_TOILET_SOURCE_URL = "https://data.seoul.go.kr/dataList/OA-22586/S/1/datasetView.do"

logger = logging.getLogger(__name__)

# 인구 목록(POPULATION_AREAS)에 있는 이름 집합. 낡음 감지 probe가 "서울시 API는
# 지원하는데 우리 목록엔 없다"를 판정하는 기준이다.
_POPULATION_AREA_NAMES = {area.name for area in POPULATION_AREAS}

# 같은 장소 이름을 반복 probe하지 않기 위한 프로세스 메모리 캐시. 값은 "서울시
# API가 실제로 지원하는지" 여부다. 재시작하면 비워지는데, probe는 그 정도로도
# 충분한 저비용 모니터링이라 별도 TTL·영속화를 두지 않는다(TP-141/D-084).
_stale_area_probe_cache: dict[str, bool] = {}


@dataclass(frozen=True)
class ContextTools:
    """Context 수집에 필요한 Tool 묶음."""

    location: ResolveLocationTool
    places: NearbyPlaceDetailsTool
    weather: GetWeatherForecastTool
    holidays: GetHolidaysTool
    # INFO 혼잡도는 RECOMMEND Context와 별도 경로다. 기존 RECOMMEND 조립 코드와
    # 테스트의 호환을 위해 선택적으로 두고, Factory에서는 항상 실제 Tool을 주입한다.
    concentration: GetConcentrationTool | None = None
    # INFO 상세 질의(concentration 외) 전용. 위와 같은 이유로 선택적이다.
    place_detail: GetPlaceDetailTool | None = None
    # INFO 행사 질의(question_type=event) 전용. 위와 같은 이유로 선택적이다.
    festivals: GetFestivalsTool | None = None
    # 서울시 실시간 도시데이터의 지역·업종별 상권 활동 조회 전용.
    realtime_commercial: GetRealtimeCommercialTool | None = None
    realtime_citydata: GetRealtimeCityDataTool | None = None
    # 명시적 공영/시영주차장 질문은 도시데이터의 근접 목록이 아니라 구 단위
    # GetParkingInfo를 쓴다. 좌표 카탈로그는 한 번 지오코딩한 정적 값만 보관한다.
    municipal_parking: GetMunicipalParkingTool | None = None
    municipal_parking_catalog: MunicipalParkingCatalogRepository | None = None
    # 근처 공중화장실 조회. 서울시 API가 구·좌표 필터를 지원하지 않아 적재된
    # 저장소를 감싼 Tool이다 — 외부 API를 요청 때마다 부르지 않는다.
    public_toilets: GetPublicToiletTool | None = None
    # COMPARE의 place_id → 장소명 해석 전용. 추천 카드와 같은 Tool을 쓴다 —
    # 같은 places 행에서 같은 이름을 읽어야 카드와 비교 답변이 어긋나지 않는다.
    cards: RecommendationCardTool | None = None
    # 상세 카드에 여러 장을 싣기 위한 사진 목록 저장소. 없으면 대표 이미지
    # 한 장만 나가고 나머지 경로는 그대로다.
    place_photos: PlacePhotoRepository | None = None
    # 후기로 답하는 INFO 질의(question_type=review_opinion) 전용 벡터 검색.
    # 없으면 그 질문도 기존 상세 조회로 답한다 — 기능이 꺼진 환경에서 "못 찾았어요"로
    # 퇴보시키지 않는다.
    place_evidence: PlaceEvidenceProvider | None = None


class ContextService:
    """A가 지정한 조건으로 C의 Tool 실행 계획을 만들고 결과를 조립한다."""

    def __init__(
        self,
        tools: ContextTools,
        *,
        candidate_limit: int,
        clock: Callable[[], datetime] | None = None,
        search_radius_km: float = DEFAULT_PLACE_SEARCH_RADIUS_KM,
        concentration_mapping_cache: ConcentrationMappingCache | None = None,
    ) -> None:
        if not (MIN_RECOMMENDATION_LIMIT <= candidate_limit <= MAX_RECOMMENDATION_CANDIDATE_LIMIT):
            raise ValueError(
                "candidate_limit은 "
                f"{MIN_RECOMMENDATION_LIMIT} 이상 "
                f"{MAX_RECOMMENDATION_CANDIDATE_LIMIT} 이하여야 합니다."
            )
        self._tools = tools
        self._clock = clock or (lambda: datetime.now(_KST))
        self._search_radius_km = search_radius_km
        self._candidate_limit = candidate_limit
        # 없으면 INFO fallback을 건너뛴다(기존 RECOMMEND 테스트 호환).
        self._concentration_mapping_cache = concentration_mapping_cache

    async def fetch_context(
        self,
        request: AgentContextRequest,
    ) -> AgentContextResponse:
        conditions = request.conditions
        # 보충 조회는 A가 기준점을 확정해 넘긴다 — 그때는 장소만 다시 받는다.
        refill_center = request.resolved_search_center
        execution_plan = build_tool_execution_plan(
            conditions, places_only=refill_center is not None
        )
        location_query = conditions.search_center or conditions.current_location
        if refill_center is None and location_query is None and request.gps_location is None:
            return assemble_agent_context_response(
                ContextAssemblyInput(request=request, location_result=None),
                rule_versions=_rule_versions(),
            )

        category_plan = build_category_query_plan(
            conditions.place_types,
            conditions.place_tags,
        )
        if category_plan.has_unsupported_conditions or category_plan.has_conflicts:
            return _unsupported_category_response(request, category_plan)

        # "강남구"처럼 구 이름으로 들어온 요청은 후보를 그 구 전체에서 모은다(D-119).
        # 반경 검색으로 풀면 대표점 주변 수백 미터만 보게 된다 — 반경 2km 원은
        # 12.6km²인데 강남구는 39.5km²다.
        #
        # 보충 조회(refill_center)는 같은 턴의 이어받기라 이미 정해진 기준점을 쓴다.
        district = (
            _supported_district(location_query)
            if refill_center is None and location_query
            else None
        )

        if refill_center is not None:
            # 위치 해석을 건너뛴다. 같은 턴이라 기준점이 바뀔 일이 없고, 보충 배치의
            # location은 A가 어차피 버린다(_merge_recommendation_context_places).
            location_result = _resolved_center_location_result(
                refill_center, self._clock()
            )
        elif location_query is not None:
            location_result = await self._tools.location.execute(
                # 추천은 반경 검색의 기준 좌표만 필요하다. 저장소 정체성 확정은
                # 후보 보강 단계가 place_id로 따로 한다(enrichment_service).
                #
                # 구 이름은 행정구역 좌표로 바로 확정한다. 지역 검색에 걸면 주변
                # 명소·역 후보가 여럿 잡혀 불필요한 되묻기가 된다 — 주차장 경로가
                # 같은 이유로 같은 처리를 한다(fetch_info_context).
                ResolveLocationQuery(
                    f"서울특별시 {district.name}" if district else location_query,
                    purpose=LocationPurpose.SEARCH_CENTER,
                    skip_local_search=district is not None,
                )
            )
        else:
            location_result = _gps_location_result(request, self._clock())
        if location_result.status is not ToolStatus.SUCCESS or location_result.location is None:
            return assemble_agent_context_response(
                ContextAssemblyInput(
                    request=request,
                    location_result=location_result,
                    # 기준점을 못 풀어 추천이 성립하지 않는 응답이다. 발화 위치를 따로
                    # 지오코딩하는 비용은 추천이 나가는 요청에서만 치르고, 여기서는
                    # 기기 GPS만 싣는다(TP-109까지의 동작 그대로).
                    user_location_result=self._device_gps_location(request),
                ),
                rule_versions=_rule_versions(),
            )

        visit_at = _as_kst(self._clock())
        location = location_result.location
        # **보충 조회에서도 사용자 위치를 구한다.** 이 배치의 `location`(검색 기준점)은
        # A가 병합에서 버리지만 사용자 위치는 버리지 않는다 — 거리를 재는 기준점이기
        # 때문이다(domain/ranking_origin.py). 전에는 둘을 함께 껐고, 그래서 첫 배치는
        # 사용자 위치에서, 보충 배치는 검색 기준점에서 잰 거리가 한 카드 묶음에 섞였다.
        # GPS를 강남에 두고 "강서구 갈만한곳"을 물으면 같은 응답에서 가막골이 0.21km
        # (강서구청 기준), 황금내근린공원이 16.07km(강남 기준)로 나왔다(2026-09-09).
        #
        # **외부 호출은 늘지 않는 편이 보통이다.** 기기 GPS만 있으면 좌표로 결과를
        # 만들 뿐 Tool을 부르지 않는다. 발화가 검색 기준점과 다른 출발지를 말했을
        # 때만 지오코딩 1회가 붙는데, 그 경우엔 그 값이 있어야 거리가 맞다.
        user_location_task = asyncio.create_task(
            self._resolve_user_location(
                request,
                location_query=location_query,
                location_result=location_result,
            )
        )
        weather_task = (
            asyncio.create_task(
                self._tools.weather.execute(
                    WeatherForecastQuery(
                        latitude=location.latitude,
                        longitude=location.longitude,
                        visit_at=visit_at,
                    )
                )
            )
            if execution_plan.requires(ContextTool.GET_WEATHER)
            else None
        )
        holidays_task = (
            asyncio.create_task(
                self._tools.holidays.execute(HolidayQuery(year=visit_at.year, month=visit_at.month))
            )
            if execution_plan.requires(ContextTool.GET_HOLIDAYS)
            else None
        )
        places_task = asyncio.create_task(
            self._collect_places(
                category_plan,
                excluded_plan=build_excluded_category_plan(conditions.exclude_tags),
                latitude=location.latitude,
                longitude=location.longitude,
                search_radius_km=_resolve_search_radius_km(
                    conditions.max_travel_time,
                    default_radius_km=self._search_radius_km,
                ),
                excluded_place_ids=frozenset(request.excluded_place_ids),
                accessibility_needs=conditions.accessibility_needs,
                district_code=district.district_code if district else None,
            )
        )
        weather_result = await weather_task if weather_task is not None else None
        holidays_result = await holidays_task if holidays_task is not None else None
        places_result = await places_task
        user_location_result = (
            await user_location_task if user_location_task is not None else None
        )

        return assemble_agent_context_response(
            ContextAssemblyInput(
                request=request,
                location_result=location_result,
                user_location_result=user_location_result,
                weather_result=weather_result,
                places_result=places_result,
                holidays_result=holidays_result,
                weather_requested=execution_plan.requires(ContextTool.GET_WEATHER),
                holidays_requested=execution_plan.requires(ContextTool.GET_HOLIDAYS),
                district_scope=(
                    DistrictScope(
                        district_code=district.district_code, district_name=district.name
                    )
                    if district
                    else None
                ),
            ),
            rule_versions=_rule_versions(),
        )

    async def _resolve_user_location(
        self,
        request: AgentContextRequest,
        *,
        location_query: str | None,
        location_result: ResolveLocationResult,
    ) -> ResolveLocationResult | None:
        """사용자가 있는 곳을 해석한다. 발화(current_location)가 기기 GPS보다 앞선다.

        기준점(`location`)이 search_center → current_location → GPS 순인 것과 같은
        우선순위다. 기준점만 발화를 앞세우고 사용자 위치만 GPS를 앞세우면 한 요청
        안에서 두 좌표가 서로 다른 규칙으로 정해진다(TP-112).

        `state/field_spec.py`의 "v0.3에서 current_location의 필수 지위가
        api_context.gps_location으로 이관되었다"는 **위치를 하나도 모를 때 무엇이
        빈칸을 채우는가**에 대한 것이지, 둘 다 있을 때 무엇이 이기는가가 아니다.
        낡은 발화 위치를 버리는 것은 A의 몫이다 — 되묻기의 "다른 지역" 선택이
        current_location을 명시적으로 지운다(agent_runtime.py).

        발화를 앞세우지 않으면 GPS가 없을 때 사용자 위치가 통째로 사라진다.
        `"지금 서대문역인데 혜화역 근처"`에서 location_query가 혜화역으로 정해지면
        서대문역은 지오코딩조차 되지 않기 때문이다(GPS 만료는 TTL 1시간이라 흔하다).
        """

        spoken = request.conditions.current_location
        if spoken is None:
            return self._device_gps_location(request)
        if spoken == location_query:
            # 기준점이 이미 같은 문자열을 푼 결과다. 같은 질의를 두 번 지오코딩하지
            # 않는다 — search_center가 없거나 발화와 같을 때가 여기 해당한다.
            return location_result
        result = await self._tools.location.execute(
            # 기준점과 같은 이유로 좌표만 있으면 된다. PLACE_IDENTITY를 쓰면 종로구
            # 코퍼스 밖 이름("서대문역")에서 저장소 조회만 헛돈다(resolve_location.py).
            ResolveLocationQuery(spoken, purpose=LocationPurpose.SEARCH_CENTER)
        )
        if result.status is ToolStatus.SUCCESS and result.location is not None:
            return result
        # 발화를 못 풀면 기기 GPS로 내려간다. D-042(Real 실패 시 Fake로 자동 전환하지
        # 않는다)와는 다른 상황이다 — 지어낸 값이 아니라 같은 질문에 대한 다른 사실이다.
        return self._device_gps_location(request)

    def _device_gps_location(self, request: AgentContextRequest) -> ResolveLocationResult | None:
        """기기 GPS만으로 사용자 위치 결과를 만든다. GPS가 없으면 그 사실대로 None."""

        if request.gps_location is None:
            return None
        return _gps_location_result(request, self._clock())

    async def fetch_compare_context(
        self,
        request: CompareContextRequest,
    ) -> CompareContextResponse:
        """비교 후보의 place_id를 장소명으로 해석해 비교 사실을 조립한다.

        수치(거리·남은 운영시간·실내외)는 B가 보관한 추천 시점 스냅샷이므로 다시
        조회하지 않고 그대로 통과시킨다 — 사용자가 카드에서 본 값과 어긋나면 안 된다
        (D-050, docs/design/int-04-compare.md §13). C는 우열을 판정하지 않는다.
        그건 A의 LLM 요약 몫이다.

        좌표(latitude/longitude)는 예외다(TRAVEL_TIME, 2026-08-21) — 카드 조회
        시점에 항상 함께 실어 보낸다. 스냅샷이 아니라 "지금 이 장소가 어디 있는지"
        라는 불변에 가까운 사실이라 D-050이 막으려던 문제(스냅샷과 최신값의 어긋남)
        와 무관하고, A가 이 좌표로 실측 경로를 조회할 때만 쓴다 — C는 여기서도
        거리·시간을 계산하거나 우열을 매기지 않는다.
        """

        card_tool = self._tools.cards
        if card_tool is None:
            return _compare_error_response(
                request,
                status="unavailable",
                error=ContextError(
                    code="place_lookup_not_configured",
                    message="비교에 필요한 장소 정보를 조회할 수 없습니다.",
                    retryable=False,
                ),
            )

        candidates = sorted(request.candidates, key=lambda item: item.rank)
        card_result = await card_tool.get_cards([candidate.place_id for candidate in candidates])
        if card_result.status is ToolStatus.UNAVAILABLE:
            return _compare_error_response(
                request,
                status="unavailable",
                error=_context_error_from_tool(
                    card_result.error,
                    fallback_code="unavailable",
                    fallback_message="비교에 필요한 장소 정보를 불러오지 못했습니다.",
                    retryable=True,
                ),
            )

        cards_by_id = {card.content_id: card for card in card_result.cards if card.name is not None}
        items = [
            ComparisonItem(
                place_id=candidate.place_id,
                place_name=cards_by_id[candidate.place_id].name,
                rank=candidate.rank,
                distance_km=candidate.distance_km,
                remaining_minutes=candidate.remaining_minutes,
                environment_type=candidate.environment_type,
                # TRAVEL_TIME 전용 — 사실 그대로 전달만 한다(우열 판정은 A 몫).
                latitude=cards_by_id[candidate.place_id].latitude,
                longitude=cards_by_id[candidate.place_id].longitude,
            )
            for candidate in candidates
            if candidate.place_id in cards_by_id
        ]
        missing = [
            candidate.place_id for candidate in candidates if candidate.place_id not in cards_by_id
        ]

        # 이름을 못 찾은 후보는 빼고 진행하되, 남은 수가 비교를 이루지 못하면
        # no_data다 — 한 곳만 남겨두고 "비교"라고 답할 수는 없다.
        if len(items) < _MIN_COMPARE_ITEMS:
            return _compare_error_response(request, status="no_data", missing_place_ids=missing)

        # 기준에 해당하는 값이 전원 비어 있으면 비교할 사실이 없다. 그대로 넘기면
        # A의 LLM이 빈 값에서 뭔가 지어낼 여지가 생긴다(프롬프트가 "C가 준 값만
        # 쓰라"고 제한하는 취지와도 어긋난다).
        field = _COMPARE_CRITERIA_FIELDS.get(request.criteria)
        if field is not None and all(getattr(item, field) is None for item in items):
            return _compare_error_response(request, status="no_data", missing_place_ids=missing)

        return CompareContextResponse(
            request_id=request.request_id,
            status="partial" if missing else "success",
            criteria=request.criteria,
            items=items,
            missing_place_ids=missing,
        )

    async def fetch_info_context(
        self,
        request: InfoContextRequest,
    ) -> InfoContextResponse:
        """INFO 단일 장소 질의를 question_type에 맞는 경로로 처리한다.

        장소 식별(ResolveLocationTool)까지는 모든 question_type이 공통이고, 그
        뒤가 갈린다.

        - ``concentration`` → 집중률 API + D-036 인근 대체 조회
        - ``event`` → 주변 행사 조회
        - ``realtime_commercial`` → 가까운 서울시 제공 상권의 카페 활동 조회
        - 그 외 → 장소 상세 조회(TourAPI detailCommon2/detailIntro2)
        """

        place_name = request.place_name
        # "급한데 근처에 화장실 있어?"는 지명을 말하지 않는 게 자연스럽다. 기기
        # 위치가 있으면 그걸 기준점으로 삼아 되묻지 않고 바로 답한다 — 급한
        # 상황에 "어디 근처요?"를 되묻는 건 답을 안 준 것과 같다.
        #
        # **지명을 말했으면 그 지명이 이긴다.** 기기 위치가 있어도 여기서 가로채면
        # 강남에서 "인사동 화장실 어디야?"를 물었을 때 강남 화장실을 인사동이라고
        # 답하게 된다. 지명이 있는 경우는 아래 공통 위치 해석을 거친다.
        if (
            request.question_type == _PUBLIC_TOILET_QUESTION_TYPE
            and place_name is None
            and request.origin_coordinates is not None
        ):
            return await self._fetch_public_toilet_info(
                request,
                latitude=request.origin_coordinates.latitude,
                longitude=request.origin_coordinates.longitude,
                place_name=None,
                resolved_place_name="현재 위치",
                location_metadata=(),
            )
        if place_name is None:
            return InfoContextResponse(
                request_id=request.request_id,
                status="needs_clarification",
                clarification=Clarification(
                    code=(
                        "event_place_required"
                        if request.question_type in _EVENT_QUESTION_TYPES
                        else "place_required"
                    ),
                    missing_fields=["place_name"],
                    candidates=[],
                ),
            )

        current_activity_candidate = _is_current_activity_candidate(request)
        current_population_candidate = _is_current_population_candidate(request, self._clock())
        # 구 이름 하나로 주차를 물었나("강서구 공영주차장 자리 있어?"). 그렇다면 지역
        # 검색으로 관광지 후보를 찾는 대신 행정구역 좌표로 확정한다(아래 skip_local_search).
        #
        # **세 유형을 함께 본다.** 전에는 `parking`만 봤는데, 그러면 공영주차장을 명시한
        # 질문(realtime_public_parking)이 오히려 이 경로에서 빠졌다 — 구 단위
        # GetParkingInfo를 쓰겠다고 가장 분명히 말한 발화가 되묻기로 끝나고, 되묻기
        # 선택지가 그 구 이름 하나뿐이라 눌러도 같은 자리로 돌아왔다(TP-261).
        parking_district = (
            _supported_district_name(place_name)
            if request.question_type in _DISTRICT_PARKING_QUESTION_TYPES
            else None
        )
        # 구 이름 하나로 혼잡도를 물었나("강서구 지금 사람 많아?").
        #
        # **서울시 실시간 인구 데이터에는 구 단위 값이 없다.** 핫스팟 121곳 기준의 장소
        # 단위라, 구를 지오코딩해 봐야 구청 좌표 하나가 나올 뿐이고 그 둘레 1km 안에
        # 제공 지역이 없으면 관광지 일 단위 예측으로 내려간다. 실제로는 "강서구"가
        # 애매한 지명으로 판정돼 되묻기로 끝나고, 선택지가 그 구 이름 하나뿐이라
        # 눌러도 제자리였다(TP-261).
        #
        # 그래서 구에 속한 지역을 목록에서 직접 찾는다. 지오코딩도 지역 검색도 거치지
        # 않으므로 되묻기가 생길 자리가 없다.
        concentration_district = (
            _supported_district_name(place_name)
            if request.question_type == "concentration"
            else None
        )
        if concentration_district is not None:
            district_areas = population_areas_in_district(concentration_district)
            if len(district_areas) == 1:
                # 1곳뿐인 구(금천구·성북구·은평구·도봉구·노원구)는 그 지역을 물은 것과
                # 사실상 같다. 기존 한 곳짜리 경로로 보내 12시간 예측과 지도까지
                # 그대로 살린다 — 여러 곳일 때만 그것들을 못 쓴다.
                only = district_areas[0]
                return await self._fetch_realtime_population_or_concentration_info(
                    request,
                    place_name=place_name,
                    resolved_location=_area_resolved_location(only),
                    location_metadata=(),
                    # 목록 최신성 탐침은 끈다. 그 탐침은 place_name을 장소 이름으로
                    # 보고 "우리 목록에 없는데 서울시는 아는 지역인가"를 확인하는데,
                    # 여기 들어오는 것은 구 이름이라 언제나 다르게 보인다 — 서울시
                    # API를 한 번 더 부르고 "금천구가 목록에 없다"는 틀린 경고까지
                    # 남긴다(2026-09-09 실측).
                    probe_stale_area=False,
                )
            return await self._fetch_district_population_info(
                request,
                district_name=concentration_district,
                areas=district_areas,
            )

        is_realtime_citydata_purpose = (
            request.question_type == "realtime_commercial"
            or request.question_type == _PUBLIC_PARKING_QUESTION_TYPE
            # 화장실 질문의 지명은 "인사동"·"강남역 근처"처럼 관광지가 아니라 동네
            # 범위다. 저장소(관광지 코퍼스)를 먼저 보면 못 찾으므로 지오코딩으로
            # 바로 가는 편이 맞다 — 필요한 건 좌표 하나뿐이다.
            or request.question_type == _PUBLIC_TOILET_QUESTION_TYPE
            or request.question_type in _REALTIME_CITYDATA_QUESTION_TYPES
            or parking_district is not None
        )
        location_purpose = (
            LocationPurpose.REALTIME_CITYDATA
            if is_realtime_citydata_purpose
            else LocationPurpose.PLACE_IDENTITY
        )
        location_result = await self._tools.location.execute(
            # INFO는 좌표가 아니라 "집중률 매핑이 걸린 그 장소"를 확정해야 한다(D-043).
            # 단, 실시간 상권은 서울시 82개 제공 지역을 별도로 쓰므로 종로구 추천
            # 범위를 위치 해석 단계에 적용하지 않는다.
            #
            # 오늘 날짜 혼잡 질문은 저장소를 먼저 봐야 명동성당·아시아프처럼
            # TourAPI 코퍼스엔 있지만 Naver 지역 검색·Geocoding으론 못 찾는
            # 장소가 산다(TP-171) — 그래서 PLACE_IDENTITY를 쓴다. 다만 서울대공원처럼
            # 지원 25개 구(D-107, 서울 전역) 밖의 실시간 인구 허브도 여전히 답해야
            # 하므로(2026-08-30 재실측: 121곳 중 2곳·82곳 중 0곳만 지원 구 밖 — 25개 구
            # 확대 전에는 121곳 중 49곳·82곳 중 32곳이었다), 지역 제한만 명시적으로
            # 끈다 — PLACE_IDENTITY의 기본 지역 제한과 저장소 우선 순위는 원래 서로
            # 다른 이유로 묶여 있던 게 아니다.
            ResolveLocationQuery(
                # "종로 주차장 정보"의 종로는 특정 관광지가 아니라 구 단위 범위다.
                # 지역 검색 후보를 되묻지 말고 행정구역 좌표로 확정해 해당 구의
                # 공영주차장 최신 현황을 찾는다.
                f"서울특별시 {parking_district}" if parking_district else place_name,
                purpose=location_purpose,
                enforce_service_area=(False if current_population_candidate else None),
                skip_local_search=parking_district is not None,
            )
        )
        if location_result.status is ToolStatus.NO_DATA:
            cause = location_result.error.cause if location_result.error else None
            if cause == "ambiguous_location":
                if is_realtime_citydata_purpose:
                    # "교대역"처럼 호선이 갈려 하나로 못 좁혀도, 실시간 행사·주차 같은
                    # citydata 계열은 대표 좌표만으로 최인접 서울시 제공 지역을 찾아
                    # 답할 수 있다(concentration의 이름 전용 폴백과 대칭, D-036 계열).
                    fallback_response = await self._fetch_realtime_citydata_by_coords_only(
                        request,
                        place_name=place_name,
                        error_details=(
                            location_result.error.details if location_result.error else {}
                        ),
                        # 한 겹 더 감싸면 안 된다. 받는 쪽 handler들은 평탄한
                        # tuple[ProviderMetadata, ...]을 기대하고 그대로
                        # _info_response_metadata()에 넘기는데, 그 함수는 인자 하나를
                        # 그룹 하나로 보고 한 겹만 벗긴다 — 이중 튜플이면 항목이
                        # ProviderMetadata가 아니라 튜플이라 AttributeError로 터진다.
                        # 실제로 "인사동 주차장 자리 있어?"처럼 후보가 갈리는 지명이
                        # 들어오면 realtime_public_parking·realtime_bus·
                        # realtime_commercial이 모두 500이었다(2026-09-05 실측).
                        location_metadata=location_result.provider_metadata,
                    )
                    if fallback_response is not None:
                        return fallback_response
                candidate_names = parse_candidate_names(
                    location_result.error.details.get("candidate_names", "")
                    if location_result.error
                    else ""
                )
                filtered_candidates = await self._filter_info_place_candidates(
                    candidate_names,
                    question_type=request.question_type,
                    location_purpose=location_purpose,
                    is_realtime_citydata_purpose=is_realtime_citydata_purpose,
                    current_population_candidate=current_population_candidate,
                )
                return InfoContextResponse(
                    request_id=request.request_id,
                    status="needs_clarification",
                    clarification=Clarification(
                        code="place_ambiguous",
                        missing_fields=[],
                        # 걸러서 하나도 안 남으면 거르기 전 원본을 그대로 보여준다 —
                        # 버튼 없는 것보다 낫다(RECOMMEND의 location_ambiguous와 같은
                        # 원칙).
                        candidates=filtered_candidates or candidate_names,
                    ),
                )
            if request.question_type == "concentration":
                # 위치 해석이 완전히 실패해도 집중률만으로는 답할 수 있는 경우가
                # 있다(TP-171, 사용자 결정: "실시간 혼잡도가 없으면 집중률이라도").
                # 좌표가 없어 D-036 인근 대체(_fetch_info_concentration_fallback)는
                # 못 쓰므로, 이름이 매핑에 정확히 하나만 일치할 때만 시도한다.
                return await self._fetch_concentration_by_name_only(
                    request,
                    reference_date=_info_reference_date(request.visit_time, self._clock()),
                    provider_metadata=(location_result.provider_metadata,),
                )
            return _info_no_data_response(request, location_result.provider_metadata)
        if location_result.status is ToolStatus.UNSUPPORTED:
            return _info_error_response(
                request,
                status="unsupported",
                error=_context_error_from_tool(
                    location_result.error,
                    fallback_code="unsupported",
                    fallback_message="현재 지원하지 않는 위치입니다.",
                    retryable=False,
                ),
                provider_metadata=(location_result.provider_metadata,),
            )
        if location_result.status is ToolStatus.UNAVAILABLE:
            return _info_error_response(
                request,
                status="unavailable",
                error=_context_error_from_tool(
                    location_result.error,
                    fallback_code="location_unavailable",
                    fallback_message="위치 정보를 가져오지 못했습니다.",
                    retryable=True,
                ),
                provider_metadata=(location_result.provider_metadata,),
            )

        resolved_location = location_result.location
        if resolved_location is None:
            # ResolveLocationTool 계약상 success에는 location이 있어야 한다.
            # 예기치 않은 구현 불일치는 외부 연동 오류로 정규화한다.
            return _info_error_response(
                request,
                status="unavailable",
                error=ContextError(
                    code="location_result_invalid",
                    message="위치 정보를 확인하지 못했습니다.",
                    retryable=True,
                ),
                provider_metadata=(location_result.provider_metadata,),
            )

        if request.question_type == "event":
            return await self._fetch_event_info(
                request,
                place_name=place_name,
                resolved_location=resolved_location,
                location_metadata=location_result.provider_metadata,
            )

        if request.question_type == "realtime_commercial" or (
            request.question_type == "concentration"
            and current_activity_candidate
            and _is_commercial_place_category(resolved_location.place_category)
        ):
            return await self._fetch_realtime_commercial_info(
                request,
                place_name=place_name,
                resolved_location=resolved_location,
                location_metadata=location_result.provider_metadata,
            )
        if current_population_candidate:
            return await self._fetch_realtime_population_or_concentration_info(
                request,
                place_name=place_name,
                resolved_location=resolved_location,
                location_metadata=location_result.provider_metadata,
            )
        if request.question_type == _PUBLIC_TOILET_QUESTION_TYPE:
            # "인사동 근처 화장실"처럼 지명을 말한 경우. 기준점은 그 지명을
            # 지오코딩한 좌표다(위쪽 GPS 경로와 달리 여기는 지명이 있다).
            return await self._fetch_public_toilet_info(
                request,
                latitude=resolved_location.latitude,
                longitude=resolved_location.longitude,
                place_name=place_name,
                resolved_place_name=resolved_location.resolved_name,
                location_metadata=location_result.provider_metadata,
            )
        if request.question_type == _PUBLIC_PARKING_QUESTION_TYPE:
            return await self._fetch_realtime_public_parking_info(
                request,
                place_name=place_name,
                resolved_location=resolved_location,
                location_metadata=location_result.provider_metadata,
            )
        if request.question_type in _REALTIME_CITYDATA_QUESTION_TYPES:
            return await self._fetch_realtime_city_info(
                request,
                place_name=place_name,
                resolved_location=resolved_location,
                location_metadata=location_result.provider_metadata,
            )

        if request.question_type == "parking" and parking_district is not None:
            return await self._fetch_realtime_public_parking_info(
                request,
                place_name=place_name,
                resolved_location=resolved_location,
                location_metadata=location_result.provider_metadata,
            )

        if request.question_type == "review_opinion":
            return await self._fetch_place_review_info(
                request,
                place_name=place_name,
                resolved_location=resolved_location,
                location_metadata=location_result.provider_metadata,
            )

        if request.question_type != "concentration":
            return await self._fetch_place_detail_info(
                request,
                place_name=place_name,
                resolved_location=resolved_location,
                location_metadata=location_result.provider_metadata,
            )

        return await self._fetch_concentration_info(
            request,
            place_name=place_name,
            resolved_location=resolved_location,
            location_metadata=location_result.provider_metadata,
        )

    async def _filter_info_place_candidates(
        self,
        candidate_names: list[str],
        *,
        question_type: str,
        location_purpose: LocationPurpose,
        is_realtime_citydata_purpose: bool,
        current_population_candidate: bool,
    ) -> list[str]:
        """되묻기 후보 중 이번 질문 유형의 정보가 실제로 조회 가능한 곳만 남긴다.

        후보 이름마다 다시 한번 개별 해석해 identity/좌표를 얻은 뒤 판정한다 —
        resolve_location.py는 question_type을 모르므로(좌표/신원 해석만 책임진다)
        가용성 판정은 이 계층에서만 한다. 재해석 자체가 또 애매하게 나오면
        (예: DB에 동명 타이틀 2건) "조회 불가"로 단정하지 않고 후보를 그대로
        남긴다 — 확실히 실패했을 때만 버린다. 호출부가 결과가 비면 원본 목록으로
        되돌린다.
        """
        kept: list[str] = []
        for name in candidate_names:
            result = await self._tools.location.execute(
                ResolveLocationQuery(name, purpose=location_purpose)
            )
            if result.status is ToolStatus.NO_DATA:
                kept.append(name)
                continue
            if result.status is not ToolStatus.SUCCESS or result.location is None:
                continue
            if _place_candidate_has_data(
                result.location,
                question_type=question_type,
                is_realtime_citydata_purpose=is_realtime_citydata_purpose,
                current_population_candidate=current_population_candidate,
            ):
                kept.append(name)
        return kept

    async def _fetch_district_population_info(
        self,
        request: InfoContextRequest,
        *,
        district_name: str,
        areas: tuple[SeoulRealtimeArea, ...],
    ) -> InfoContextResponse:
        """구 안 지역들의 현재 혼잡도를 한 번에 모아 돌려준다.

        서울시 실시간 인구 데이터에는 "강서구"에 해당하는 값이 없다. 핫스팟 121곳
        기준의 장소 단위라(seoul_realtime_areas), 구를 물으면 그 안의 지역을 각각
        조회해 묶는 수밖에 없다.

        **일부가 실패해도 나머지로 답한다.** 종로구처럼 14곳을 부르는 구에서 한 곳이
        느리다고 답 전체를 버리면, 사용자가 얻는 것이 없어진다. 대신 몇 곳을 못 봤는지
        `unavailable_area_count`로 남겨 숨기지 않는다.
        """

        tool = self._tools.realtime_citydata
        if tool is None:
            return _info_error_response(
                request,
                status="unavailable",
                error=ContextError(
                    code="realtime_population_not_configured",
                    message="실시간 인구 혼잡도 조회 기능을 사용할 수 없습니다.",
                    retryable=False,
                ),
                provider_metadata=(),
            )

        results = await asyncio.gather(
            *(tool.execute(RealtimeCityDataQuery(area.code)) for area in areas)
        )

        collected: list[DistrictAreaCongestionInfo] = []
        unavailable = 0
        observed_at: str | None = None
        metadata: list[tuple[ProviderMetadata, ...]] = []
        for area, tool_result in zip(areas, results, strict=True):
            metadata.append(tool_result.provider_metadata)
            citydata = tool_result.citydata
            population = citydata.population if citydata is not None else None
            if (
                tool_result.status is not ToolStatus.SUCCESS
                or population is None
                or population.current_congestion_level is None
            ):
                unavailable += 1
                continue
            observed_at = observed_at or population.observed_at
            collected.append(
                DistrictAreaCongestionInfo(
                    area_name=population.area_name or area.name,
                    congestion_level=population.current_congestion_level,
                    message=population.current_congestion_message,
                )
            )

        collected.sort(key=lambda item: _congestion_rank(item.congestion_level))
        return InfoContextResponse(
            request_id=request.request_id,
            status="success" if collected else "no_data",
            result=DistrictPopulationInfoResult(
                status="success" if collected else "no_data",
                district_name=district_name,
                areas=collected,
                unavailable_area_count=unavailable,
                observed_at=observed_at,
                source_url=_CITYDATA_SOURCE_URL,
            ),
            metadata=_info_response_metadata(*metadata),
        )

    async def _fetch_realtime_population_or_concentration_info(
        self,
        request: InfoContextRequest,
        *,
        place_name: str,
        resolved_location: ResolvedLocation,
        location_metadata: tuple[ProviderMetadata, ...],
        probe_stale_area: bool = True,
    ) -> InfoContextResponse:
        """현재형 혼잡 질문은 가까운 실시간 인구 값을 먼저 확인한다.

        서울시가 제공하는 지역 중심점이 1km 밖이거나 인구 객체가 비어 있으면,
        현재값인 것처럼 꾸며내지 않고 기존 관광지 일 단위 예측으로 낮춘다. 반면
        서울시 API 자체 장애는 실시간 조회 실패로 드러낸다.
        """

        nearest = select_nearest_population_area(
            latitude=resolved_location.latitude,
            longitude=resolved_location.longitude,
            requested_name=resolved_location.resolved_name,
        )
        if nearest is None:
            return await self._fetch_concentration_info(
                request,
                place_name=place_name,
                resolved_location=resolved_location,
                location_metadata=location_metadata,
            )

        tool = self._tools.realtime_citydata
        if tool is None:
            return _info_error_response(
                request,
                status="unavailable",
                error=ContextError(
                    code="realtime_population_not_configured",
                    message="실시간 인구 혼잡도 조회 기능을 사용할 수 없습니다.",
                    retryable=False,
                ),
                provider_metadata=(location_metadata,),
            )

        area, distance_km = nearest
        tool_result = await tool.execute(RealtimeCityDataQuery(area.code))
        if tool_result.status is ToolStatus.UNAVAILABLE:
            return _info_error_response(
                request,
                status="unavailable",
                error=_context_error_from_tool(
                    tool_result.error,
                    fallback_code="realtime_population_unavailable",
                    fallback_message="실시간 인구 혼잡도를 가져오지 못했습니다.",
                    retryable=True,
                ),
                provider_metadata=(location_metadata, tool_result.provider_metadata),
            )

        population = tool_result.citydata.population if tool_result.citydata is not None else None
        if (
            tool_result.status is ToolStatus.NO_DATA
            or population is None
            or population.current_congestion_level is None
        ):
            return await self._fetch_concentration_info(
                request,
                place_name=place_name,
                resolved_location=resolved_location,
                location_metadata=(*location_metadata, *tool_result.provider_metadata),
            )

        stale_area_detected = (
            await self._probe_stale_population_area(
                place_name=place_name,
                matched_area_name=area.name,
                matched_area_distance_km=distance_km,
                tool=tool,
            )
            if probe_stale_area
            else None
        )

        return InfoContextResponse(
            request_id=request.request_id,
            status="success",
            result=RealtimePopulationInfoResult(
                status="success",
                requested_place_name=place_name,
                resolved_place_name=resolved_location.resolved_name,
                area_name=population.area_name or area.name,
                area_code=population.area_code or area.code,
                proxy_distance_km=distance_km,
                current_congestion_level=population.current_congestion_level,
                current_congestion_message=population.current_congestion_message,
                observed_at=population.observed_at,
                population_forecasts=[
                    PopulationForecastInfo(
                        forecast_at=slot.forecast_at,
                        congestion_level=slot.congestion_level,
                        population_min=slot.population_min,
                        population_max=slot.population_max,
                    )
                    for slot in population.forecasts
                ]
                if population.forecast_available
                else [],
                source_url=_CITYDATA_SOURCE_URL,
                map_url=_seoul_realtime_map_url(area),
                # 상권 값은 별도 호출이 아니라 방금 받은 같은 응답에서 꺼낸다.
                realtime_summary=_to_seoul_realtime_summary(
                    population,
                    tool_result.citydata.commercial if tool_result.citydata is not None else None,
                ),
                stale_area_detected=stale_area_detected,
            ),
            metadata=_info_response_metadata(location_metadata, tool_result.provider_metadata),
        )

    async def _probe_stale_population_area(
        self,
        *,
        place_name: str,
        matched_area_name: str,
        matched_area_distance_km: float,
        tool: GetRealtimeCityDataTool,
    ) -> StaleAreaProbeDebug | None:
        """우리 121곳 목록엔 없지만 서울시 API는 지원하는 지역을 조용히 찾는다.

        응답(추천 판정)에는 절대 개입하지 않는다 — 이 메서드는 항상 감사용
        신호만 만들거나 아무것도 하지 않는다(TP-141/D-084). 탐색 실패는 이유를
        따지지 않고 "신호 없음"으로 취급한다 — 서울시 API 자체 장애와 미지원
        지역을 구분하려 들면 이 probe가 본 요청의 실패 판정에 영향을 줄 수 있다.
        """

        if not settings.seoul_area_staleness_probe_enabled:
            return None
        if matched_area_name == place_name:
            # 대체가 안 일어났다 — place_name이 이미 우리 목록에 있다는 뜻이라
            # 확인할 게 없다.
            return None
        if place_name in _POPULATION_AREA_NAMES:
            # 좌표 기준 최근접은 다른 지역으로 잡혔지만(드문 경우), place_name
            # 자체는 이미 우리 목록에 있다 — probe로 확인할 새 사실이 없다.
            return None

        cached = _stale_area_probe_cache.get(place_name)
        if cached is None:
            try:
                probe_result = await tool.execute(RealtimeCityDataQuery(place_name))
            except Exception:  # noqa: BLE001 - probe 실패가 본 요청에 번지면 안 된다.
                logger.warning("낡음 감지 probe 호출 실패: %s", place_name, exc_info=True)
                _stale_area_probe_cache[place_name] = False
                return None
            supported = (
                probe_result.status is not ToolStatus.UNAVAILABLE
                and probe_result.citydata is not None
                and probe_result.citydata.population is not None
            )
            _stale_area_probe_cache[place_name] = supported
            cached = supported

        if not cached:
            return None

        logger.warning(
            "서울시 실시간 도시데이터가 지원하는데 우리 121곳 목록엔 없는 지역: %s",
            place_name,
        )
        return StaleAreaProbeDebug(
            probed_area_name=place_name,
            matched_area_name=matched_area_name,
            matched_area_distance_km=matched_area_distance_km,
        )

    async def _fetch_realtime_commercial_info(
        self,
        request: InfoContextRequest,
        *,
        place_name: str,
        resolved_location: ResolvedLocation,
        location_metadata: tuple[ProviderMetadata, ...],
    ) -> InfoContextResponse:
        """개별 매장 대신 최근접 서울시 제공 상권의 카페 활동을 안내한다."""

        nearest = select_nearest_commercial_area(
            latitude=resolved_location.latitude,
            longitude=resolved_location.longitude,
            requested_name=resolved_location.resolved_name,
        )
        if nearest is None:
            return _info_error_response(
                request,
                status="unsupported",
                error=ContextError(
                    code="realtime_commercial_unsupported_region",
                    message="서울시 실시간 상권 데이터 제공 지역이 아닙니다.",
                    retryable=False,
                ),
                provider_metadata=(location_metadata,),
            )

        area, distance_km = nearest
        tool = self._tools.realtime_citydata
        if tool is None:
            return _info_error_response(
                request,
                status="unavailable",
                error=ContextError(
                    code="realtime_commercial_not_configured",
                    message="실시간 상권 조회 기능을 사용할 수 없습니다.",
                    retryable=False,
                ),
                provider_metadata=(location_metadata,),
            )

        tool_result = await tool.execute(RealtimeCityDataQuery(area.code))
        if tool_result.status is ToolStatus.UNAVAILABLE:
            return _info_error_response(
                request,
                status="unavailable",
                error=_context_error_from_tool(
                    tool_result.error,
                    fallback_code="realtime_commercial_unavailable",
                    fallback_message="실시간 상권 정보를 가져오지 못했습니다.",
                    retryable=True,
                ),
                provider_metadata=(location_metadata, tool_result.provider_metadata),
            )

        citydata = tool_result.citydata
        commercial = citydata.commercial if citydata is not None else None
        population = citydata.population if citydata is not None else None
        selected_category = _select_commercial_category(
            commercial.categories if commercial is not None else (),
            request.specific_question,
        )
        if tool_result.status is ToolStatus.NO_DATA or commercial is None:
            return InfoContextResponse(
                request_id=request.request_id,
                status="no_data",
                result=RealtimeCommercialInfoResult(
                    status="no_data",
                    requested_place_name=place_name,
                    resolved_place_name=resolved_location.resolved_name,
                    area_name=area.name,
                    area_code=area.code,
                    proxy_distance_km=distance_km,
                ),
                metadata=_info_response_metadata(location_metadata, tool_result.provider_metadata),
            )

        if selected_category is not None:
            category_label, commercial_level = selected_category
            commercial_scope = "cafe_category"
        else:
            # 실 API는 조회 시점에 대표 업종 한 건만 내려줄 수 있다. 카페 세부 업종이
            # 빠졌다고 지역 전체 활동값까지 버리면 "용리단길 카페" 같은 질문이 매번
            # no_data가 된다. 단, 카페 값처럼 보이지 않도록 응답 범위를 명시한다.
            category_label = None
            commercial_level = commercial.area_activity_level
            commercial_scope = "area_overall"
        if commercial_level is None:
            return InfoContextResponse(
                request_id=request.request_id,
                status="no_data",
                result=RealtimeCommercialInfoResult(
                    status="no_data",
                    requested_place_name=place_name,
                    resolved_place_name=resolved_location.resolved_name,
                    area_name=area.name,
                    area_code=area.code,
                    proxy_distance_km=distance_km,
                ),
                metadata=_info_response_metadata(location_metadata, tool_result.provider_metadata),
            )
        return InfoContextResponse(
            request_id=request.request_id,
            status="success",
            result=RealtimeCommercialInfoResult(
                status="success",
                requested_place_name=place_name,
                resolved_place_name=resolved_location.resolved_name,
                area_name=commercial.area_name or area.name,
                area_code=commercial.area_code or area.code,
                proxy_distance_km=distance_km,
                category_label=category_label,
                commercial_level=commercial_level,
                commercial_scope=commercial_scope,
                observed_at=commercial.observed_at,
                population_current_level=(
                    population.current_congestion_level if population is not None else None
                ),
                population_observed_at=population.observed_at if population is not None else None,
                population_forecasts=(
                    [
                        PopulationForecastInfo(
                            forecast_at=slot.forecast_at,
                            congestion_level=slot.congestion_level,
                            population_min=slot.population_min,
                            population_max=slot.population_max,
                        )
                        for slot in population.forecasts
                    ]
                    if population is not None and population.forecast_available
                    else []
                ),
                detail_items=_to_commercial_detail_items(
                    commercial.categories,
                    area_activity_level=commercial.area_activity_level,
                ),
                source_url=_CITYDATA_SOURCE_URL,
                realtime_summary=_to_seoul_realtime_summary(population, commercial),
            ),
            metadata=_info_response_metadata(location_metadata, tool_result.provider_metadata),
        )

    async def _fetch_realtime_public_parking_info(
        self,
        request: InfoContextRequest,
        *,
        place_name: str,
        resolved_location: ResolvedLocation,
        location_metadata: tuple[ProviderMetadata, ...],
    ) -> InfoContextResponse:
        """공영/시영주차장을 명시한 질문에 구 단위 최신 대수를 돌려준다.

        GetParkingInfo에는 좌표가 없어 카탈로그가 있으면 거리순으로, 아직 동기화되지
        않았으면 같은 구의 실시간 수치가 있는 항목 우선으로 보인다. 이 fallback은
        주소를 요청 중에 지오코딩하지 않으므로 API 비용·응답시간을 늘리지 않는다.
        """

        district = _district_from_address(resolved_location.address)
        if district is None:
            return _realtime_city_info_no_data_response(
                request,
                place_name=place_name,
                resolved_location=resolved_location,
                provider_metadata=location_metadata,
            )
        if self._tools.municipal_parking is None:
            return _info_error_response(
                request,
                status="unavailable",
                error=ContextError(
                    code="municipal_parking_unavailable",
                    message="공영주차장 실시간 조회 도구가 설정되지 않았습니다.",
                    retryable=False,
                ),
                provider_metadata=(location_metadata,),
            )
        tool_result = await self._tools.municipal_parking.execute(MunicipalParkingQuery(district))
        if tool_result.status is ToolStatus.UNAVAILABLE:
            return _info_error_response(
                request,
                status="unavailable",
                error=_context_error_from_tool(
                    tool_result.error,
                    fallback_code="municipal_parking_unavailable",
                    fallback_message="공영주차장 실시간 정보를 가져오지 못했습니다.",
                    retryable=True,
                ),
                provider_metadata=(location_metadata, tool_result.provider_metadata),
            )

        catalog = {}
        if self._tools.municipal_parking_catalog is not None:
            try:
                catalog = await self._tools.municipal_parking_catalog.find_by_codes(
                    [lot.code for lot in tool_result.lots]
                )
            except AppError:
                # 좌표 카탈로그 장애가 공영주차장 실측 수치까지 막으면 안 된다. 거리만
                # 생략하고 구 단위 목록으로 안전하게 계속한다.
                logger.warning("공영주차장 좌표 카탈로그 조회 실패", exc_info=True)

        entries = [
            _municipal_status_to_realtime_lot(lot, catalog.get(lot.code))
            for lot in tool_result.lots
            if lot.is_live
            and lot.current_parked_count is not None
            and not _is_bus_only_lot(lot.name)
        ]
        entries.sort(
            key=lambda item: (
                0 if item.available_spaces is not None else 1,
                _parking_distance_or_inf(resolved_location, item),
                -(item.available_spaces or -1),
            )
        )
        fields = {
            f"[공영] {item.name}": _format_realtime_parking(
                item, distance_km=_parking_distance_km_or_none(resolved_location, item)
            )
            for item in entries[:5]
        }
        observed_at = next((item.observed_at for item in entries if item.observed_at), None)
        return InfoContextResponse(
            request_id=request.request_id,
            status="success" if fields else "no_data",
            result=RealtimeCityInfoResult(
                status="success" if fields else "no_data",
                question_type="realtime_public_parking",
                requested_place_name=place_name,
                resolved_place_name=resolved_location.resolved_name,
                area_name=district,
                observed_at=observed_at,
                fields=fields,
                detail_items=_to_parking_detail_items(
                    entries[:15],
                    latitude=resolved_location.latitude,
                    longitude=resolved_location.longitude,
                ),
                source_url=_MUNICIPAL_PARKING_SOURCE_URL,
            ),
            metadata=_info_response_metadata(location_metadata, tool_result.provider_metadata),
        )

    async def _fetch_public_toilet_info(
        self,
        request: InfoContextRequest,
        *,
        latitude: float,
        longitude: float,
        place_name: str | None,
        resolved_place_name: str | None,
        location_metadata: tuple[ProviderMetadata, ...],
    ) -> InfoContextResponse:
        """기준 좌표에서 걸어갈 만한 공중화장실 두 곳을 돌려준다.

        기준 좌표는 두 갈래로 들어온다 — 지명을 말했으면 그것을 지오코딩한 값,
        "근처에 화장실 있어?"처럼 지명이 없으면 기기 GPS다. 어느 쪽이든 이 아래는
        같다: 적재된 목록에서 반지름 안을 추려 "지금 열린 곳 → 가까운 곳" 순으로
        두 곳만 싣는다.
        """

        if self._tools.public_toilets is None:
            return _info_error_response(
                request,
                status="unavailable",
                error=ContextError(
                    code="public_toilet_unavailable",
                    message="근처 공중화장실 조회 도구가 설정되지 않았습니다.",
                    retryable=False,
                ),
                provider_metadata=(location_metadata,),
            )

        tool_result = await self._tools.public_toilets.execute(
            PublicToiletQuery(
                latitude=latitude,
                longitude=longitude,
                radius_km=_PUBLIC_TOILET_RADIUS_KM,
                now=self._clock(),
            )
        )
        if tool_result.status is ToolStatus.UNAVAILABLE:
            return _info_error_response(
                request,
                status="unavailable",
                error=_context_error_from_tool(
                    tool_result.error,
                    fallback_code="public_toilet_unavailable",
                    fallback_message="근처 공중화장실 정보를 가져오지 못했습니다.",
                    retryable=True,
                ),
                provider_metadata=(location_metadata,),
            )

        entries = tool_result.toilets[:_PUBLIC_TOILET_RESULT_LIMIT]
        fields = {
            entry.toilet.name: _format_public_toilet(entry) for entry in entries
        }
        return InfoContextResponse(
            request_id=request.request_id,
            status="success" if fields else "no_data",
            result=RealtimeCityInfoResult(
                status="success" if fields else "no_data",
                question_type="public_toilet",
                requested_place_name=place_name,
                resolved_place_name=resolved_place_name,
                fields=fields,
                detail_items=_to_public_toilet_detail_items(entries),
                source_url=_PUBLIC_TOILET_SOURCE_URL,
            ),
            metadata=_info_response_metadata(location_metadata),
        )

    async def _fetch_realtime_city_info(
        self,
        request: InfoContextRequest,
        *,
        place_name: str,
        resolved_location: ResolvedLocation,
        location_metadata: tuple[ProviderMetadata, ...],
    ) -> InfoContextResponse:
        """서울시 citydata의 주차·지하철·버스·행사 객체를 INFO 카드로 정규화한다.

        citydata(통합)는 상권 82개보다 넓은 121개 지역을 지원하므로(경복궁·한강공원
        등) 인구 목록으로 조회한다. 거리 허용치는 기존 상권 조회와 같은 2km를
        유지한다 — 이 조회는 인구 혼잡도처럼 "지금 여기" 정확도가 중요한 값이
        아니라 주차·지하철 같은 주변 정보라 더 넓게 대체해도 된다.
        """

        nearest = select_nearest_population_area(
            latitude=resolved_location.latitude,
            longitude=resolved_location.longitude,
            max_distance_km=COMMERCIAL_AREA_PROXY_MAX_DISTANCE_KM,
            requested_name=resolved_location.resolved_name,
        )
        if nearest is None:
            if request.question_type == "realtime_event":
                # 서울시 실시간이 지원하지 않는 지역이어도 TourAPI에는 그 구 행사가 있다.
                # 아래 "행사만 두 출처를 잇는다" 주석과 같은 이유다.
                return await self._fetch_event_info(
                    request,
                    place_name=place_name,
                    resolved_location=resolved_location,
                    location_metadata=location_metadata,
                )
            return _realtime_city_info_no_data_response(
                request,
                place_name=place_name,
                resolved_location=resolved_location,
                provider_metadata=location_metadata,
            )
        if self._tools.realtime_citydata is None:
            return _info_error_response(
                request,
                status="unavailable",
                error=ContextError(
                    code="realtime_citydata_unavailable",
                    message="실시간 도시데이터 조회 도구가 설정되지 않았습니다.",
                    retryable=False,
                ),
                provider_metadata=(location_metadata,),
            )
        area, _ = nearest
        tool_result = await self._tools.realtime_citydata.execute(RealtimeCityDataQuery(area.code))
        if tool_result.status is ToolStatus.UNAVAILABLE or tool_result.citydata is None:
            return _info_error_response(
                request,
                status="unavailable",
                error=_context_error_from_tool(
                    tool_result.error,
                    fallback_code="realtime_citydata_unavailable",
                    fallback_message="실시간 도시데이터를 가져오지 못했습니다.",
                    retryable=True,
                ),
                provider_metadata=(location_metadata, tool_result.provider_metadata),
            )
        citydata = tool_result.citydata
        question_type = request.question_type
        detail_items: list[RealtimeInfoDetailItem]
        if question_type == "realtime_parking":
            all_entries = sorted(
                (item for item in citydata.parking_lots if not _is_bus_only_lot(item.name)),
                key=lambda item: haversine_km(
                    resolved_location.latitude,
                    resolved_location.longitude,
                    item.latitude,
                    item.longitude,
                )
                if item.latitude is not None and item.longitude is not None
                else float("inf"),
            )
            # 공영/민영으로 나눠 보여준다 — 한쪽이 비어도(예: 공원 주변은 민영이
            # 없거나, 역세권은 공영이 없는 경우) 있는 쪽만으로 정상 응답한다.
            grouped_lots: dict[str, list[RealtimeParkingLot]] = {
                "공영": [],
                "민영": [],
                "기타": [],
            }
            for item in all_entries:
                grouped_lots[item.lot_type or "기타"].append(item)
            # 같은 공영/민영 묶음 안에서는 실시간 대수 제공 항목을 먼저 보여준다.
            # 기존에는 단순 거리순이라 실제 잔여 여부가 있는 주차장이 4번째 이후로
            # 밀려 카드에 안 나오는 문제가 있었다.
            for lots in grouped_lots.values():
                lots.sort(
                    key=lambda item: (
                        (
                            0
                            if item.current_available and item.current_parked_count is not None
                            else 1
                        ),
                        _parking_distance_or_inf(resolved_location, item),
                    )
                )
            entries: list[RealtimeParkingLot] = []
            fields = {}
            for label in ("공영", "민영", "기타"):
                for item in grouped_lots[label][:3]:
                    entries.append(item)
                    key = item.name if label == "기타" else f"[{label}] {item.name}"
                    fields[key] = _format_realtime_parking(
                        item, distance_km=_parking_distance_km_or_none(resolved_location, item)
                    )
            observed_at = next((item.observed_at for item in entries if item.observed_at), None)
            detail_items = _to_parking_detail_items(
                grouped_lots["공영"][:10] + grouped_lots["민영"][:10] + grouped_lots["기타"][:10],
                latitude=resolved_location.latitude,
                longitude=resolved_location.longitude,
            )
        elif question_type == "realtime_subway":
            all_entries = citydata.subway_arrivals
            # 역+호선 단위로 묶어 서로 다른 방향을 우선 살린다. 예전에는
            # all_entries[:4]로 원본 순서대로만 잘라 같은 역의 두 방향(상행/하행)이
            # 겹치면 한쪽이 밀려났다.
            by_station_line: dict[str, list[RealtimeSubwayArrival]] = {}
            station_line_order: list[str] = []
            for item in all_entries:
                key = f"{item.station_name}|{item.line or ''}"
                if key not in by_station_line:
                    by_station_line[key] = []
                    station_line_order.append(key)
                by_station_line[key].append(item)
            entries = []
            for key in station_line_order:
                entries.extend(by_station_line[key][:2])  # 방향은 최대 2개(상/하행)
                if len(entries) >= 4:
                    break
            fields = {
                _subway_field_key(item): _format_subway_arrival(item) for item in entries
            }
            observed_at = None
            detail_items = _to_subway_detail_items(all_entries[:12])
        elif question_type == "realtime_bus":
            all_entries = citydata.bus_stops
            entries = all_entries[:5]
            fields = {
                item.name: f"정류장 번호 {item.ars_id}" if item.ars_id else "주변 정류장"
                for item in entries
            }
            observed_at = None
            detail_items = _to_bus_detail_items(all_entries[:12])
        elif question_type == "realtime_traffic":
            traffic = citydata.road_traffic
            fields = (
                {
                    key: value
                    for key, value in {
                        "도로소통 단계": traffic.level,
                        "평균 주행속도": (
                            f"{traffic.average_speed_kmh:.0f}km/h"
                            if traffic.average_speed_kmh is not None
                            else None
                        ),
                        "안내": traffic.message,
                    }.items()
                    if value is not None
                }
                if traffic is not None
                else {}
            )
            observed_at = traffic.observed_at if traffic is not None else None
            detail_items = (
                [
                    RealtimeInfoDetailItem(
                        title="도로소통 안내",
                        subtitle=traffic.level,
                        details={"안내": traffic.message} if traffic.message else {},
                    )
                ]
                if traffic is not None and traffic.message is not None
                else []
            )
        else:
            all_entries = citydata.events
            entries = all_entries[:5]
            fields = {
                item.name: " · ".join(part for part in (item.period, item.place) if part)
                for item in entries
            }
            observed_at = None
            detail_items = _to_event_detail_items(all_entries[:10])
        result_status: Literal["success", "no_data", "unavailable"] = (
            "success" if fields else "no_data"
        )
        if not fields and question_type == "realtime_event":
            # **행사만 두 출처를 잇는다.** 서울시 실시간(citydata `EVENT_STTS`)과 TourAPI
            # (searchFestival2)가 거의 겹치지 않아서다 — 2026-09-04 실측에서 그날 진행 중인
            # 행사가 서울시 95건·TourAPI 21건인데 양쪽에 다 있는 것은 3건뿐이었다. 담는
            # 것도 다르다(서울시는 미술관 기획전·문화재단 프로그램, TourAPI는 왕궁수문장
            # 교대의식·한강야경투어 같은 관광 콘텐츠). 그래서 서울시가 비었다고 "행사가
            # 없다"고 답하면 TourAPI에 있는 것을 못 본 채로 끝난다.
            #
            # 주차·지하철 같은 다른 realtime 유형에는 이런 두 번째 출처가 없어 이 분기가
            # 없다. 여기서도 TourAPI가 비면 그 결과(no_data)를 그대로 내보낸다.
            return await self._fetch_event_info(
                request,
                place_name=place_name,
                resolved_location=resolved_location,
                location_metadata=location_metadata,
            )
        return InfoContextResponse(
            request_id=request.request_id,
            status="success" if fields else "no_data",
            result=RealtimeCityInfoResult(
                status=result_status,
                question_type=cast(
                    Literal[
                        "realtime_parking",
                        "realtime_public_parking",
                        "realtime_subway",
                        "realtime_bus",
                        "realtime_event",
                        "realtime_traffic",
                        "public_toilet",
                    ],
                    question_type,
                ),
                requested_place_name=place_name,
                resolved_place_name=resolved_location.resolved_name,
                area_name=area.name,
                observed_at=observed_at,
                fields=fields,
                detail_items=detail_items,
                source_url=_CITYDATA_SOURCE_URL,
                map_url=(
                    _seoul_realtime_map_url(area) if question_type == "realtime_traffic" else None
                ),
                road_incident_counts=(
                    [
                        RoadIncidentCategoryCountInfo(label=c.label, count=c.count)
                        for c in citydata.road_traffic.incident_counts
                    ]
                    if question_type == "realtime_traffic" and citydata.road_traffic is not None
                    else []
                ),
            ),
            metadata=_info_response_metadata(location_metadata, tool_result.provider_metadata),
        )

    async def _fetch_concentration_info(
        self,
        request: InfoContextRequest,
        *,
        place_name: str,
        resolved_location: ResolvedLocation,
        location_metadata: tuple[ProviderMetadata, ...],
    ) -> InfoContextResponse:
        """INFO 혼잡도 질의를 집중률 API로 처리한다(기존 경로 그대로).

        직접 조회가 안 되면 D-036 인근 관광지 대체 조회로 낮춘다.
        """

        reference_date = _info_reference_date(request.visit_time, self._clock())
        concentration_tool = self._tools.concentration
        if concentration_tool is None:
            return _info_error_response(
                request,
                status="unavailable",
                error=ContextError(
                    code="concentration_not_configured",
                    message="집중률 조회 기능을 사용할 수 없습니다.",
                    retryable=False,
                ),
                provider_metadata=(location_metadata,),
            )

        concentration_place_name = resolved_location.concentration_name
        if concentration_place_name is None:
            # 매핑이 없는 이름을 tAtsNm에 그대로 넣으면 안 된다. 부분 일치 검색이라
            # "종로"가 낙지볶음 골목·세종로공원·대학천 책방거리를 함께 끌어와, 그중
            # 하나의 값을 "종로의 혼잡도"로 답하게 된다(2026-08-04 실측). 활성 장소
            # 847건 중 매핑은 100건뿐이라 이 경로가 다수다.
            return await self._fetch_info_concentration_fallback(
                request,
                latitude=resolved_location.latitude,
                longitude=resolved_location.longitude,
                reference_date=reference_date,
                concentration_tool=concentration_tool,
                provider_metadata=(location_metadata,),
            )
        # 조회는 검색어로, 대조는 정식 명칭으로 한다. tAtsNm은 공백이 든 값에 0건을
        # 돌려주므로 "종묘 [유네스코 세계유산]"은 "종묘"로 조회해야 한다. 대신 그
        # 응답에는 "종묘광장공원"도 섞여 오므로 고를 때는 정식 명칭을 써야 한다.
        # 조회할 구는 해석된 장소의 것을 쓴다. concentration_name이 있다는 건 저장소
        # 에서 푼 장소라는 뜻이라 district_code도 함께 온다.
        signgu_code = concentration_signgu_code(resolved_location.district_code)
        if signgu_code is None:
            # 구를 모르면 직접 조회를 하지 않는다. 종로구로 대신 물으면 다른 구
            # 장소는 언제나 0건이라, 틀린 조회가 "정보 없음"으로 보인다. 인근
            # 대체 경로는 그대로 탄다 - 매핑 없는 이름일 때와 같은 처리다.
            return await self._fetch_info_concentration_fallback(
                request,
                latitude=resolved_location.latitude,
                longitude=resolved_location.longitude,
                reference_date=reference_date,
                concentration_tool=concentration_tool,
                provider_metadata=(location_metadata,),
            )
        concentration_result = await execute_concentration_by_search_keys(
            concentration_tool,
            search_keys=resolved_location.concentration_search_keys,
            canonical_name=concentration_place_name,
            signgu_code=signgu_code,
        )
        if concentration_result.status is ToolStatus.UNAVAILABLE:
            return _info_error_response(
                request,
                status="unavailable",
                error=_context_error_from_tool(
                    concentration_result.error,
                    fallback_code="concentration_unavailable",
                    fallback_message="집중률 정보를 가져오지 못했습니다.",
                    retryable=True,
                ),
                provider_metadata=(
                    location_metadata,
                    concentration_result.provider_metadata,
                ),
            )
        if concentration_result.status is ToolStatus.NO_DATA:
            return await self._fetch_info_concentration_fallback(
                request,
                latitude=resolved_location.latitude,
                longitude=resolved_location.longitude,
                reference_date=reference_date,
                concentration_tool=concentration_tool,
                provider_metadata=(
                    location_metadata,
                    concentration_result.provider_metadata,
                ),
            )

        forecast = select_concentration_forecast(
            concentration_result.concentration,
            candidate_name=concentration_place_name,
            reference_date=reference_date,
        )
        rate = forecast.concentration_rate if forecast is not None else None
        if forecast is None or not is_valid_concentration_rate(rate):
            # 매핑된 장소인데도 쓸 값이 없다 — 여러 장소가 섞여 와 특정하지 못했거나
            # 해당 날짜 예보가 없는 경우다. 인근 장소로 답할 수 있으면 답한다.
            return await self._fetch_info_concentration_fallback(
                request,
                latitude=resolved_location.latitude,
                longitude=resolved_location.longitude,
                reference_date=reference_date,
                concentration_tool=concentration_tool,
                provider_metadata=(
                    location_metadata,
                    concentration_result.provider_metadata,
                ),
            )

        normalized = normalize_concentration(rate)
        return InfoContextResponse(
            request_id=request.request_id,
            status="success",
            result=ConcentrationInfoResult(
                status="success",
                is_proxy=False,
                requested_place_name=place_name,
                resolved_place_name=forecast.place_name,
                forecast_date=reference_date.isoformat(),
                concentration_rate=rate,
                concentration_level=cast(
                    Literal["quiet", "normal", "slightly_crowded", "crowded"],
                    normalized.level.value,
                ),
                concentration_label=normalized.label.value,
                forecasts=_to_concentration_forecast_infos(
                    concentration_result.concentration,
                    candidate_name=concentration_place_name,
                    start_date=reference_date,
                ),
            ),
            metadata=_info_response_metadata(
                location_metadata,
                concentration_result.provider_metadata,
            ),
        )

    async def _fetch_realtime_citydata_by_coords_only(
        self,
        request: InfoContextRequest,
        *,
        place_name: str,
        error_details: dict[str, str],
        location_metadata: tuple[ProviderMetadata, ...],
    ) -> InfoContextResponse | None:
        """지명이 여럿으로 갈려도(예: "교대역" 2/3호선) 대표 좌표로 citydata를 찾는다.

        resolve_location.py가 ambiguous_location에 실어 보낸 1순위 후보 좌표를 쓴다.
        좌표가 없으면 None을 돌려줘 호출부가 기존 되묻기로 진행하게 한다.
        """

        raw_latitude = error_details.get("fallback_latitude")
        raw_longitude = error_details.get("fallback_longitude")
        if raw_latitude is None or raw_longitude is None:
            return None
        try:
            latitude = float(raw_latitude)
            longitude = float(raw_longitude)
        except ValueError:
            return None

        resolved_location = ResolvedLocation(
            requested_query=place_name,
            provider_query=place_name,
            resolved_name=place_name,
            latitude=latitude,
            longitude=longitude,
            resolution_method=ResolutionMethod.FALLBACK,
            confidence=ResolutionConfidence.APPROXIMATE,
        )
        if request.question_type == "realtime_commercial":
            return await self._fetch_realtime_commercial_info(
                request,
                place_name=place_name,
                resolved_location=resolved_location,
                location_metadata=location_metadata,
            )
        if request.question_type == _PUBLIC_TOILET_QUESTION_TYPE:
            # "인사동"처럼 후보가 여럿으로 갈리는 지명이 화장실 질문에는 흔하다
            # (동네 이름이라 관광지·역·상호와 겹친다). 대표 좌표로 답하는 편이
            # 급한 사람에게 "어느 인사동이요?"를 되묻는 것보다 낫다 — 어느 후보든
            # 반경 1km 안 화장실은 크게 다르지 않다.
            return await self._fetch_public_toilet_info(
                request,
                latitude=latitude,
                longitude=longitude,
                place_name=place_name,
                resolved_place_name=place_name,
                location_metadata=location_metadata,
            )
        if request.question_type == _PUBLIC_PARKING_QUESTION_TYPE:
            return await self._fetch_realtime_public_parking_info(
                request,
                place_name=place_name,
                resolved_location=resolved_location,
                location_metadata=location_metadata,
            )
        if request.question_type in _REALTIME_CITYDATA_QUESTION_TYPES:
            return await self._fetch_realtime_city_info(
                request,
                place_name=place_name,
                resolved_location=resolved_location,
                location_metadata=location_metadata,
            )
        return None

    async def _fetch_concentration_by_name_only(
        self,
        request: InfoContextRequest,
        *,
        reference_date: date,
        provider_metadata: tuple[tuple[ProviderMetadata, ...], ...],
    ) -> InfoContextResponse:
        """위치 해석이 완전히 실패했을 때 이름만으로 집중률 매핑을 대조한다(TP-171).

        좌표가 없어 D-036 인근 대체(``_fetch_info_concentration_fallback``)는 쓸 수
        없다 — 이름이 매핑에 정확히 하나만 일치할 때만 답하고, 없거나 여럿이면
        억지로 하나를 고르지 않고 no_data로 끝낸다.
        """

        place_name = request.place_name
        concentration_tool = self._tools.concentration
        if (
            place_name is None
            or concentration_tool is None
            or self._concentration_mapping_cache is None
        ):
            return _info_no_data_response(request, *provider_metadata)
        try:
            mapped_places = await self._concentration_mapping_cache.places()
        except AppError:
            return _info_no_data_response(request, *provider_metadata)

        normalized_query = _normalize_place_name(place_name)
        matches = [
            place
            for place in mapped_places
            if place.concentration_name is not None
            and _normalize_place_name(place.title) == normalized_query
        ]
        if len(matches) != 1:
            return _info_no_data_response(request, *provider_metadata)
        matched_place = matches[0]

        signgu_code = concentration_signgu_code(matched_place.district_code)
        if signgu_code is None:
            return _info_no_data_response(request, *provider_metadata)

        concentration_result = await execute_concentration_by_search_keys(
            concentration_tool,
            search_keys=matched_place.concentration_search_keys,
            canonical_name=cast(str, matched_place.concentration_name),
            signgu_code=signgu_code,
        )
        if concentration_result.status is ToolStatus.UNAVAILABLE:
            return _info_error_response(
                request,
                status="unavailable",
                error=_context_error_from_tool(
                    concentration_result.error,
                    fallback_code="concentration_unavailable",
                    fallback_message="집중률 정보를 가져오지 못했습니다.",
                    retryable=True,
                ),
                provider_metadata=(*provider_metadata, concentration_result.provider_metadata),
            )
        if concentration_result.status is ToolStatus.NO_DATA:
            return _info_no_data_response(
                request, *provider_metadata, concentration_result.provider_metadata
            )

        forecast = select_concentration_forecast(
            concentration_result.concentration,
            candidate_name=cast(str, matched_place.concentration_name),
            reference_date=reference_date,
        )
        rate = forecast.concentration_rate if forecast is not None else None
        if forecast is None or not is_valid_concentration_rate(rate):
            return _info_no_data_response(
                request, *provider_metadata, concentration_result.provider_metadata
            )

        normalized = normalize_concentration(rate)
        return InfoContextResponse(
            request_id=request.request_id,
            status="success",
            result=ConcentrationInfoResult(
                status="success",
                is_proxy=False,
                requested_place_name=place_name,
                resolved_place_name=forecast.place_name,
                forecast_date=reference_date.isoformat(),
                concentration_rate=rate,
                concentration_level=cast(
                    Literal["quiet", "normal", "slightly_crowded", "crowded"],
                    normalized.level.value,
                ),
                concentration_label=normalized.label.value,
                forecasts=_to_concentration_forecast_infos(
                    concentration_result.concentration,
                    candidate_name=cast(str, matched_place.concentration_name),
                    start_date=reference_date,
                ),
            ),
            metadata=_info_response_metadata(
                *provider_metadata, concentration_result.provider_metadata
            ),
        )

    async def _fetch_info_concentration_fallback(
        self,
        request: InfoContextRequest,
        *,
        latitude: float,
        longitude: float,
        reference_date: date,
        concentration_tool: GetConcentrationTool,
        provider_metadata: tuple[tuple[ProviderMetadata, ...], ...],
    ) -> InfoContextResponse:
        """직접 데이터가 없는 INFO 장소를 인근 관광지 기준으로 대체 조회한다.

        D-036의 INFO 전용 경로다. 추천 후보 보강에는 이 함수를 사용하지 않는다.
        집중률 매핑이 있는 장소를 가까운 순으로 시도해 첫 성공을 채택한다 — 매핑에
        이름이 있어도 조회가 실패할 수 있어(표기 차이·API 갱신) 한 곳만 보고
        포기하지 않는다.
        """

        if self._concentration_mapping_cache is None:
            return _info_no_data_response(request, *provider_metadata)
        try:
            mapped_places = await self._concentration_mapping_cache.places()
        except AppError as exc:
            return _info_error_response(
                request,
                status="unavailable",
                error=ContextError(
                    code="concentration_mapping_unavailable",
                    message="인근 관광지를 찾지 못했습니다.",
                    retryable=exc.retryable,
                ),
                provider_metadata=provider_metadata,
            )

        proxy_places = select_nearest_mapped_places(
            mapped_places,
            latitude=latitude,
            longitude=longitude,
            radius_km=INFO_CONCENTRATION_FALLBACK_RADIUS_KM,
            limit=INFO_CONCENTRATION_FALLBACK_ATTEMPT_LIMIT,
        )
        if not proxy_places:
            return _info_no_data_response(request, *provider_metadata)

        attempted_metadata: list[tuple[ProviderMetadata, ...]] = []
        for proxy_place in proxy_places:
            # 매핑 테이블이 보유한 집중률 API 기준 이름을 쓴다 — TourAPI 장소명을
            # 그대로 던지던 기존 방식은 이름이 달라 조회에 실패하는 경우가 있었다.
            # 직접 조회와 같이 조회는 검색어로, 대조는 정식 명칭으로 한다.
            # 구를 모르는 장소는 건너뛴다. 종로구로 대신 물으면 다른 구 장소는
            # 언제나 0건이라, 조회 실패가 "정보 없음"과 구분되지 않는다.
            proxy_signgu_code = concentration_signgu_code(proxy_place.district_code)
            if proxy_signgu_code is None:
                continue
            proxy_result = await execute_concentration_by_search_keys(
                concentration_tool,
                search_keys=proxy_place.concentration_search_keys,
                canonical_name=proxy_place.concentration_name,
                signgu_code=proxy_signgu_code,
            )
            attempted_metadata.append(proxy_result.provider_metadata)

            if proxy_result.status is ToolStatus.UNAVAILABLE:
                # 외부 장애는 다음 후보로 넘어가도 같은 결과일 가능성이 높다.
                return _info_error_response(
                    request,
                    status="unavailable",
                    error=_context_error_from_tool(
                        proxy_result.error,
                        fallback_code="concentration_unavailable",
                        fallback_message="집중률 정보를 가져오지 못했습니다.",
                        retryable=True,
                    ),
                    provider_metadata=(*provider_metadata, *attempted_metadata),
                )

            if proxy_result.status is ToolStatus.NO_DATA:
                continue

            forecast = select_concentration_forecast(
                proxy_result.concentration,
                candidate_name=proxy_place.concentration_name,
                reference_date=reference_date,
            )
            rate = forecast.concentration_rate if forecast is not None else None
            if forecast is None or not is_valid_concentration_rate(rate):
                # 이 장소는 해당 날짜 예보가 없다 — 다음으로 가까운 곳을 시도한다.
                continue

            normalized = normalize_concentration(rate)
            return InfoContextResponse(
                request_id=request.request_id,
                status="success",
                result=ConcentrationInfoResult(
                    status="success",
                    is_proxy=True,
                    requested_place_name=request.place_name,
                    resolved_place_name=forecast.place_name,
                    forecast_date=reference_date.isoformat(),
                    concentration_rate=rate,
                    concentration_level=cast(
                        Literal["quiet", "normal", "slightly_crowded", "crowded"],
                        normalized.level.value,
                    ),
                concentration_label=normalized.label.value,
                forecasts=_to_concentration_forecast_infos(
                    proxy_result.concentration,
                    candidate_name=proxy_place.concentration_name,
                    start_date=reference_date,
                ),
                ),
                metadata=_info_response_metadata(*provider_metadata, *attempted_metadata),
            )

        return _info_no_data_response(request, *provider_metadata, *attempted_metadata)

    async def _fetch_place_review_info(
        self,
        request: InfoContextRequest,
        *,
        place_name: str,
        resolved_location: ResolvedLocation,
        location_metadata: tuple[ProviderMetadata, ...],
    ) -> InfoContextResponse:
        """후기를 묻는 INFO 질의를 블로그·리뷰 문장 검색으로 처리한다.

        여기서는 **후보만 모은다.** 어느 문장이 실제로 그 장소 이야기이고 질문에
        답이 되는지는 A가 LLM으로 판정한다 — C는 Tool과 저장소만 다루는 계층이라
        생성 모델을 부르지 않는다.

        저장소에서 해석되지 않은 장소는 `place_id`가 없다(네이버 지역검색·지오코딩
        경로). 그런 장소는 임베딩도 없으므로 조회하지 않고 빈손으로 끝낸다.
        """
        evidence_provider = self._tools.place_evidence
        place_id = resolved_location.place_id
        if evidence_provider is None or not place_id:
            return await self._fetch_place_detail_info(
                request,
                place_name=place_name,
                resolved_location=resolved_location,
                location_metadata=location_metadata,
            )

        # 근거 검색과 장소 상세를 **함께** 부른다. 후기 답변도 다른 INFO 답변과 같은
        # 상세 카드(사진·개요·운영시간)를 달고 나가야 하는데, 순서대로 부르면 두
        # 왕복이 그대로 더해진다. 상세 조회가 실패해도 답변은 근거만으로 나간다.
        query = request.specific_question or place_name
        result, detail_result = await asyncio.gather(
            evidence_provider.search_one_place(
                query,
                place_id,
                match_count=REVIEW_EVIDENCE_MATCH_COUNT,
                min_similarity=REVIEW_EVIDENCE_MIN_SIMILARITY,
            ),
            self._place_detail_for_card(resolved_location),
        )
        match = result.data
        snippets = (
            usable_snippets(match.snippets, limit=REVIEW_EVIDENCE_MATCH_COUNT)
            if match
            else ()
        )
        place_card = None
        detail_metadata: tuple[ProviderMetadata, ...] = ()
        if detail_result is not None:
            details, detail_metadata = detail_result
            place_card = _to_place_card(
                details,
                place_id,
                photos=await self._fetch_place_photos(details.content_id or place_id),
            )
        return _place_review_response(
            request,
            requested_place_name=place_name,
            resolved_place_name=(
                place_card.place_name if place_card else resolved_location.resolved_name
            ),
            place_id=place_id,
            destination_coordinates=_to_info_destination_coordinates(resolved_location),
            evidence=tuple(
                ReviewEvidenceItem(
                    text=snippet.source_text.strip(),
                    source_url=snippet.source_url,
                    source_type=snippet.source_type,
                    published_at=(
                        snippet.published_at.isoformat() if snippet.published_at else None
                    ),
                )
                for snippet in snippets
            ),
            place_card=place_card,
            provider_metadata=(location_metadata, (result.metadata,), detail_metadata),
        )

    async def _place_detail_for_card(
        self, resolved_location: ResolvedLocation
    ) -> tuple[PlaceDetails, tuple[ProviderMetadata, ...]] | None:
        """후기 답변에 붙일 상세 카드용 조회. 실패는 None으로 삼킨다.

        답의 근거는 후기이지 이 값이 아니다 — 상세가 없다고 답변을 막으면, 카드를
        꾸미려다 답할 수 있는 질문을 못 답하게 된다.
        """
        tool = self._tools.place_detail
        if tool is None:
            return None
        try:
            detail_result = await tool.execute(
                PlaceDetailQuery(place_name=resolved_location.resolved_name)
            )
        except Exception:
            logger.warning("후기 답변의 상세 카드 조회 실패(답변은 그대로 나간다)", exc_info=True)
            return None
        if detail_result.status is not ToolStatus.SUCCESS or detail_result.details is None:
            return None
        return detail_result.details, detail_result.provider_metadata

    async def _fetch_place_detail_info(
        self,
        request: InfoContextRequest,
        *,
        place_name: str,
        resolved_location: ResolvedLocation,
        location_metadata: tuple[ProviderMetadata, ...],
    ) -> InfoContextResponse:
        """INFO 상세 질의(concentration 외)를 장소 상세 조회로 처리한다.

        location_info는 장소 해석 결과만으로 답할 수 있어 상세 API를 호출하지
        않는다 — 주소는 ResolveLocationTool이 이미 들고 나온다. 전화번호까지
        필요하면 상세 조회가 필요하지만, 주소만으로 질문이 성립하므로 외부 호출을
        한 번 아끼는 쪽을 택했다.
        """

        if request.question_type == "location_info":
            fields: dict[str, str] = {}
            if resolved_location.address:
                fields["address"] = resolved_location.address
            return _place_info_response(
                request,
                requested_place_name=place_name,
                resolved_place_name=resolved_location.resolved_name,
                place_id=resolved_location.place_id,
                destination_coordinates=_to_info_destination_coordinates(resolved_location),
                fields=fields,
                provider_metadata=(location_metadata,),
            )

        detail_tool = self._tools.place_detail
        if detail_tool is None:
            return _info_error_response(
                request,
                status="unavailable",
                error=ContextError(
                    code="place_detail_not_configured",
                    message="장소 상세 조회 기능을 사용할 수 없습니다.",
                    retryable=False,
                ),
                provider_metadata=(location_metadata,),
            )

        # 사용자 발화가 아니라 해석된 정식 명칭으로 조회한다 — provider가 이름
        # 정확 일치로 후보를 고르기 때문이다("종묘" → "종묘 [유네스코 세계유산]").
        detail_result = await detail_tool.execute(
            PlaceDetailQuery(place_name=resolved_location.resolved_name)
        )
        if detail_result.status is ToolStatus.UNAVAILABLE:
            return _info_error_response(
                request,
                status="unavailable",
                error=_context_error_from_tool(
                    detail_result.error,
                    fallback_code="place_detail_unavailable",
                    fallback_message="장소 상세정보를 가져오지 못했습니다.",
                    retryable=True,
                ),
                provider_metadata=(location_metadata, detail_result.provider_metadata),
            )
        if detail_result.status is ToolStatus.NO_DATA or detail_result.details is None:
            # "종각역 주차장 정보"처럼 장소 좌표는 확정됐지만 TourAPI 관광 DB에는
            # 없는 역·상권도 있다. 이때 "확인할 수 없음"으로 끝내지 않고, 해당
            # 좌표를 기준으로 가까운 공영주차장의 최신 잔여 면수를 안내한다. 반대로
            # 관광지 상세에 주차 필드가 있으면 아래 기존 상세 경로를 유지한다.
            if request.question_type == "parking":
                return await self._fetch_realtime_public_parking_info(
                    request,
                    place_name=place_name,
                    resolved_location=resolved_location,
                    location_metadata=location_metadata,
                )
            return _place_info_response(
                request,
                requested_place_name=place_name,
                resolved_place_name=resolved_location.resolved_name,
                place_id=resolved_location.place_id,
                destination_coordinates=_to_info_destination_coordinates(resolved_location),
                fields={},
                provider_metadata=(location_metadata, detail_result.provider_metadata),
            )

        fields = extract_info_fields(request.question_type, detail_result.details)
        if request.question_type == "parking" and not fields:
            # 상세 행은 찾았어도 주차 정보가 비어 있으면 같은 기준으로 주변
            # 공영주차장으로 대체한다. "주차 불가"라는 명시값은 fields에 남으므로
            # 대체하지 않는다.
            return await self._fetch_realtime_public_parking_info(
                request,
                place_name=place_name,
                resolved_location=resolved_location,
                location_metadata=location_metadata,
            )

        return _place_info_response(
            request,
            requested_place_name=place_name,
            resolved_place_name=(detail_result.details.title or resolved_location.resolved_name),
            place_id=detail_result.details.content_id or resolved_location.place_id,
            destination_coordinates=_to_info_destination_coordinates(resolved_location),
            fields=fields,
            # 카드는 질문 유형과 무관하게 채운다. status는 위 fields로만 정해진다.
            place_card=_to_place_card(
                detail_result.details,
                resolved_location.place_id,
                photos=await self._fetch_place_photos(
                    detail_result.details.content_id or resolved_location.place_id
                ),
            ),
            provider_metadata=(location_metadata, detail_result.provider_metadata),
        )

    async def _fetch_place_photos(self, place_id: str | None) -> tuple[PlacePhoto, ...]:
        """상세 카드에 실을 사진 목록을 읽는다. 실패는 사진 없음으로 삼킨다.

        사진 조회가 실패했다고 상세 정보 전체를 못 보여주면 손해가 크다 — 운영시간·
        주차·요금은 사진과 무관하게 이미 손에 있다. 대신 로그로 남겨 조회가 조용히
        비는 상태를 알아챌 수 있게 한다.
        """
        repository = self._tools.place_photos
        if repository is None or not place_id:
            return ()
        try:
            found = await repository.find_place_photos([place_id])
        except Exception:
            logger.warning("장소 사진 목록을 읽지 못했습니다: place_id=%s", place_id, exc_info=True)
            return ()
        return found.get(place_id, ())

    async def _fetch_event_info(
        self,
        request: InfoContextRequest,
        *,
        place_name: str,
        resolved_location: ResolvedLocation,
        location_metadata: tuple[ProviderMetadata, ...],
    ) -> InfoContextResponse:
        """INFO 행사 질의를 지역 행사 목록 + 좌표 근접으로 처리한다(D-055).

        TourAPI에 장소별 행사 조회가 없어, 종로구 행사를 받아 진행 중인 것만
        남기고 대상 장소에서 가까운 순으로 정렬한다. 대부분은 그 장소의 행사가
        아니라 근처 행사이므로 is_direct_match/distance_km으로 구분을 넘긴다 —
        집중률의 is_proxy와 같은 취지다(D-036).
        """

        festival_tool = self._tools.festivals
        if festival_tool is None:
            return _info_error_response(
                request,
                status="unavailable",
                error=ContextError(
                    code="festival_not_configured",
                    message="행사 조회 기능을 사용할 수 없습니다.",
                    retryable=False,
                ),
                provider_metadata=(location_metadata,),
            )

        reference_date = _info_reference_date(request.visit_time, self._clock())
        festival_result = await festival_tool.execute(FestivalQuery(reference_date=reference_date))
        if festival_result.status is ToolStatus.UNAVAILABLE:
            return _info_error_response(
                request,
                status="unavailable",
                error=_context_error_from_tool(
                    festival_result.error,
                    fallback_code="festival_unavailable",
                    fallback_message="행사 정보를 가져오지 못했습니다.",
                    retryable=True,
                ),
                provider_metadata=(location_metadata, festival_result.provider_metadata),
            )

        events = _to_event_items(
            festival_result.events,
            resolved_name=resolved_location.resolved_name,
            latitude=resolved_location.latitude,
            longitude=resolved_location.longitude,
        )
        status: Literal["success", "no_data"] = "success" if events else "no_data"
        return InfoContextResponse(
            request_id=request.request_id,
            status=status,
            result=EventInfoResult(
                status=status,
                requested_place_name=place_name,
                resolved_place_name=resolved_location.resolved_name,
                reference_date=reference_date.isoformat(),
                events=events,
                has_direct_match=any(item.is_direct_match for item in events),
            ),
            metadata=_info_response_metadata(location_metadata, festival_result.provider_metadata),
        )

    async def _collect_places(
        self,
        plan: CategoryQueryPlan,
        *,
        excluded_plan: ExcludedCategoryPlan,
        latitude: float,
        longitude: float,
        search_radius_km: float,
        excluded_place_ids: frozenset[str] = frozenset(),
        accessibility_needs: Sequence[str] = (),
        district_code: str | None = None,
    ) -> NearbyPlaceDetailsResult:
        """분류별 장소 조회를 병렬 실행하고 중복·제외 후보를 걸러 한 결과로 합친다.

        `excluded_place_ids`는 분류별 조회 각각에 같은 집합으로 넘긴다 — 분류마다
        결과 집합이 다르므로 "앞에서 몇 건"이 아니라 id로 걸러야 맞다.

        `accessibility_needs`는 A가 보낸 문자열 그대로다. 여기서 C가 아는 어휘로
        옮기고, 모르는 값은 버리되 경고로 남긴다 — 계약이 `list[str]`이라 A가
        어휘를 늘려도 요청은 깨지지 않지만, 흔적 없이 버리면 사용자가 요구한
        조건이 사라진 것을 아무도 모른다.
        """

        started_at = perf_counter()
        needs, unknown_accessibility_need = _resolve_accessibility_needs(
            accessibility_needs
        )
        if district_code is not None:
            # 구 단위는 분류마다 따로 부르지 않는다. 분류 수만큼 구 전량을 다시 읽는
            # 것도 있지만, 그보다 분류 몫이 호출마다 따로 적용돼 합친 결과가 몫을
            # 넘는 것이 문제다. 조건은 한 번에 넘기고 Tool 안에서 건다.
            district_result = await self._tools.places.execute(
                NearbyPlaceDetailsQuery(
                    latitude=latitude,
                    longitude=longitude,
                    search_radius_km=search_radius_km,
                    limit=self._candidate_limit,
                    preferred_categories=plan.resolved_place_tags,
                    category_filters=plan.filters,
                    district_scope=district_code,
                    excluded_place_ids=excluded_place_ids,
                    accessibility_needs=needs,
                )
            )
            return _merge_place_results(
                [district_result],
                limit=self._candidate_limit,
                started_at=started_at,
                excluded_plan=excluded_plan,
                unknown_accessibility_need=unknown_accessibility_need,
            )

        results = await asyncio.gather(
            *(
                self._tools.places.execute(
                    NearbyPlaceDetailsQuery(
                        latitude=latitude,
                        longitude=longitude,
                        search_radius_km=search_radius_km,
                        limit=self._candidate_limit,
                        preferred_categories=plan.resolved_place_tags,
                        category_filter=category_filter,
                        excluded_place_ids=excluded_place_ids,
                        accessibility_needs=needs,
                    )
                )
                for category_filter in plan.filters
            )
        )
        return _merge_place_results(
            results,
            limit=self._candidate_limit,
            started_at=started_at,
            excluded_plan=excluded_plan,
            unknown_accessibility_need=unknown_accessibility_need,
        )


def _gps_location_result(
    request: AgentContextRequest, retrieved_at: datetime
) -> ResolveLocationResult:
    """장소명이 없을 때 A가 전달한 기기 GPS를 위치 Tool 성공 결과로 정규화한다."""

    gps = request.gps_location
    if gps is None:
        raise ValueError("gps_location이 필요합니다.")
    return ResolveLocationResult(
        status=ToolStatus.SUCCESS,
        location=ResolvedLocation(
            requested_query="gps_location",
            provider_query="device_gps",
            resolved_name="기기 GPS 위치",
            source=LocationSource.DEVICE_GPS,
            latitude=gps.latitude,
            longitude=gps.longitude,
            resolution_method=ResolutionMethod.DIRECT,
            confidence=ResolutionConfidence.EXACT,
        ),
        error=None,
        provider_metadata=(
            ProviderMetadata(
                source=ProviderSource.DEVICE_GPS,
                status=ProviderStatus.SUCCESS,
                retrieved_at=_as_kst(retrieved_at).astimezone(UTC),
            ),
        ),
    )


def _resolved_center_location_result(
    center: Coordinates, retrieved_at: datetime
) -> ResolveLocationResult:
    """보충 조회에서 A가 넘긴 검색 기준점을 위치 Tool 성공 결과로 정규화한다.

    `_gps_location_result()`와 같은 모양이다 — 위치 Tool을 부르지 않고 좌표만으로
    결과를 만든다. 다만 출처가 기기 GPS가 아니라 **같은 턴의 첫 조회에서 C가 이미
    해석한 기준점**이라 source를 QUERY로 둔다.

    이름을 실을 수 없어 resolved_name이 비는데, 그래도 되는 이유는 A가 보충 배치의
    location을 쓰지 않기 때문이다(_merge_recommendation_context_places). 근거 문장에
    나가는 기준점 이름은 첫 배치 값이다.
    """
    return ResolveLocationResult(
        status=ToolStatus.SUCCESS,
        location=ResolvedLocation(
            requested_query="resolved_search_center",
            provider_query="resolved_search_center",
            resolved_name="",
            latitude=center.latitude,
            longitude=center.longitude,
            resolution_method=ResolutionMethod.DIRECT,
            confidence=ResolutionConfidence.EXACT,
        ),
        error=None,
        provider_metadata=(),
    )


def _info_reference_date(visit_time: str | None, clock_value: datetime) -> date:
    """INFO 요청의 방문일이 없으면 C 조회 시각의 한국 날짜를 사용한다."""

    if visit_time is not None:
        return date.fromisoformat(visit_time)
    return _as_kst(clock_value).date()


def _select_commercial_category(
    categories: tuple[RealtimeCommercialCategory, ...],
    question: str | None,
) -> tuple[str, str] | None:
    """질문에 언급된 업종을 서울시 상권 응답의 모든 업종에서 고른다.

    명시 업종이 없을 때만 카페·커피 계열을 우선한다. 업종값이 없으면 지역 전체
    상권으로 낮춰 고지하며, 다른 업종 값을 요청 업종처럼 바꾸지 않는다.
    """

    normalized_question = (question or "").replace(" ", "")
    for category in categories:
        label = " · ".join(
            value
            for value in (category.large_category, category.middle_category)
            if value is not None
        )
        category_terms = tuple(
            value.replace(" ", "")
            for value in (category.large_category, category.middle_category)
            if value
        )
        if category.activity_level is not None and any(
            term in normalized_question for term in category_terms
        ):
            return label, category.activity_level
    for category in categories:
        label = " · ".join(
            value
            for value in (category.large_category, category.middle_category)
            if value is not None
        )
        if category.activity_level is not None and any(
            marker in label for marker in _COMMERCIAL_CATEGORY_MARKERS
        ):
            return label, category.activity_level
    return None


def _top_payment_categories(
    categories: tuple[RealtimeCommercialCategory, ...],
) -> list[CommercialPaymentCategoryInfo]:
    """결제 금액이 큰 순서로 업종 최대 3건을 고른다.

    서울시가 준 업종을 거르지 않는다 — 강남역은 금액 1위가 "의료 · 병원"인데
    여행지 카드에 안 어울린다고 빼면, 우리가 만든 순위를 서울시 데이터인 것처럼
    보여주게 된다. 금액은 구간으로만 오므로 상한 기준으로 정렬하고 상한이 없으면
    하한으로 대신한다(둘 다 없는 업종은 순위에서 빠진다).
    """

    ranked = [
        (category, category.payment_amount_max or category.payment_amount_min)
        for category in categories
    ]
    ranked = [(category, amount) for category, amount in ranked if amount is not None]
    ranked.sort(key=lambda pair: pair[1], reverse=True)
    return [
        CommercialPaymentCategoryInfo(
            label=" · ".join(
                value
                for value in (category.large_category, category.middle_category)
                if value is not None
            )
            or "기타",
            activity_level=category.activity_level,
            payment_count=category.payment_count,
            payment_amount_min=category.payment_amount_min,
            payment_amount_max=category.payment_amount_max,
        )
        for category, _ in ranked[:3]
    ]


def _to_seoul_realtime_summary(
    population: RealtimePopulationResult | None,
    commercial: RealtimeCommercialResult | None,
) -> SeoulRealtimeSummaryInfo | None:
    """같은 citydata 응답의 인구·상권 값을 공통 요약 한 덩이로 묶는다.

    두 구획은 서로 독립이다 — 상권 미제공 지역(121곳 중 39곳, D-084)은 인구만,
    인구 값이 비면 상권만 찬다. 둘 다 비면 아예 만들지 않아 소비 측이 빈 구획을
    그리지 않게 한다.
    """

    summary = SeoulRealtimeSummaryInfo(
        population_min=population.current_population_min if population is not None else None,
        population_max=population.current_population_max if population is not None else None,
        age_shares=[
            PopulationAgeShareInfo(label=share.label, rate=share.rate)
            for share in (population.age_shares if population is not None else ())
        ],
        commercial_level=commercial.area_activity_level if commercial is not None else None,
        commercial_observed_at=commercial.observed_at if commercial is not None else None,
        payment_count=commercial.payment_count if commercial is not None else None,
        payment_amount_min=commercial.payment_amount_min if commercial is not None else None,
        payment_amount_max=commercial.payment_amount_max if commercial is not None else None,
        top_payment_categories=_top_payment_categories(
            commercial.categories if commercial is not None else ()
        ),
    )
    has_population = summary.population_max is not None or bool(summary.age_shares)
    has_commercial = summary.commercial_level is not None or bool(summary.top_payment_categories)
    return summary if has_population or has_commercial else None


def _to_commercial_detail_items(
    categories: tuple[RealtimeCommercialCategory, ...],
    *,
    area_activity_level: str | None,
) -> list[RealtimeInfoDetailItem]:
    """서울시가 제공한 업종별 상권 활동을 상세 카드용 목록으로 정리한다."""

    items = [
        RealtimeInfoDetailItem(
            title=" · ".join(
                value
                for value in (category.large_category, category.middle_category)
                if value is not None
            )
            or "업종별 상권 활동",
            subtitle=category.activity_level,
            details={"실시간 활동": category.activity_level}
            if category.activity_level is not None
            else {},
        )
        for category in categories
    ]
    if not items and area_activity_level is not None:
        items.append(
            RealtimeInfoDetailItem(
                title="지역 전체 상권",
                subtitle=area_activity_level,
                details={"실시간 활동": area_activity_level},
            )
        )
    return items


def _to_parking_detail_items(
    entries: list[RealtimeParkingLot] | tuple[RealtimeParkingLot, ...],
    *,
    latitude: float,
    longitude: float,
) -> list[RealtimeInfoDetailItem]:
    """주차장은 가까운 순으로, 현재 제공된 실측 필드만 상세 카드에 싣는다."""

    items: list[RealtimeInfoDetailItem] = []
    for item in entries:
        details = {
            key: value
            for key, value in {
                "거리": _distance_from_location_label(
                    latitude,
                    longitude,
                    item.latitude,
                    item.longitude,
                ),
                "주소": item.address,
                "유형": item.lot_type or "기타",
                "총 주차": f"총 {item.capacity}대" if item.capacity is not None else None,
                "현재 주차": (
                    f"{item.current_parked_count}대 주차 중"
                    if item.current_available and item.current_parked_count is not None
                    else None
                ),
                "가능 주차": (
                    f"{item.available_spaces}대 가능" if item.available_spaces is not None else None
                ),
                "요금": "유료" if item.paid is True else "무료" if item.paid is False else None,
                "기준 시각": item.observed_at,
            }.items()
            if value is not None
        }
        items.append(
            RealtimeInfoDetailItem(
                title=item.name,
                subtitle=_format_realtime_parking(item),
                details=details,
            )
        )
    return items


def _public_toilet_distance_label(distance_km: float) -> str:
    """걸어가는 거리라 1km 미만은 미터로 말하는 게 감이 온다."""

    if distance_km < 1:
        return f"도보 {round(distance_km * 1000 / 10) * 10}m"
    return f"도보 {distance_km:.1f}km"


def _public_toilet_open_label(entry: NearbyToilet) -> str:
    """지금 들어갈 수 있는지 한마디로. 모르면 모른다고 한다.

    개방시간을 시각으로 못 읽은 곳(실측 11%, 대부분 ``정시(영업시작~종료)``)을
    "지금 열림"으로 뭉개면 급한 사용자를 닫힌 문 앞으로 보내게 된다.
    """

    if entry.open_now is True:
        return "지금 이용 가능"
    if entry.open_now is False:
        return "지금은 닫혀 있음"
    return "개방시간 확인 필요"


def _format_public_toilet(entry: NearbyToilet) -> str:
    """말풍선 아래 요약 한 줄. 거리·개방여부·개방시간 순으로 급한 순서대로 놓는다."""

    parts = [
        _public_toilet_distance_label(entry.distance_km),
        _public_toilet_open_label(entry),
        describe_open_hours(entry.hours),
    ]
    return " · ".join(parts)


def _to_public_toilet_detail_items(
    entries: tuple[NearbyToilet, ...],
) -> list[RealtimeInfoDetailItem]:
    """화장실 카드는 항목마다 좌표를 싣는다 — 각 항목이 곧 길찾기 목적지다."""

    items: list[RealtimeInfoDetailItem] = []
    for entry in entries:
        toilet = entry.toilet
        details = {
            key: value
            for key, value in {
                "거리": _public_toilet_distance_label(entry.distance_km),
                "개방 여부": _public_toilet_open_label(entry),
                "개방시간": describe_open_hours(entry.hours),
                "주소": toilet.address_new or toilet.address_old,
                "유형": _clean_pipe_text(toilet.open_type),
                "화장실": _clean_pipe_text(toilet.restroom_status),
                "장애인화장실": _clean_pipe_text(toilet.accessible_status),
                "편의시설": _clean_pipe_text(toilet.amenities),
                "위치": _clean_pipe_text(toilet.location_type),
                "관리": toilet.manager,
                "전화": toilet.tel,
            }.items()
            if value is not None
        }
        items.append(
            RealtimeInfoDetailItem(
                title=toilet.name,
                subtitle=_format_public_toilet(entry),
                details=details,
                latitude=toilet.latitude,
                longitude=toilet.longitude,
            )
        )
    return items


def _clean_pipe_text(value: str | None) -> str | None:
    """원본이 ``남자|여자|``처럼 파이프로 구분해 주므로 쉼표 목록으로 바꾼다."""

    if value is None:
        return None
    parts = [part.strip() for part in value.split("|") if part.strip()]
    return ", ".join(parts) or None


def _to_subway_detail_items(
    entries: list[RealtimeSubwayArrival] | tuple[RealtimeSubwayArrival, ...],
) -> list[RealtimeInfoDetailItem]:
    return [
        RealtimeInfoDetailItem(
            title=f"{item.station_name} {item.line or ''}".strip(),
            subtitle=_format_subway_arrival(item),
            details={
                key: value
                for key, value in {
                    "방면": item.direction,
                    "종착역": item.destination,
                    "도착 안내": item.arrival_message,
                }.items()
                if value is not None
            },
        )
        for item in entries
    ]


def _to_bus_detail_items(
    entries: list[RealtimeBusStop] | tuple[RealtimeBusStop, ...],
) -> list[RealtimeInfoDetailItem]:
    return [
        RealtimeInfoDetailItem(
            title=item.name,
            subtitle=f"정류장 번호 {item.ars_id}" if item.ars_id else "주변 버스정류장",
            details={"정류장 번호": item.ars_id} if item.ars_id else {},
        )
        for item in entries
    ]


def _to_event_detail_items(
    entries: list[RealtimeCityEvent] | tuple[RealtimeCityEvent, ...],
) -> list[RealtimeInfoDetailItem]:
    return [
        RealtimeInfoDetailItem(
            title=item.name,
            subtitle=" · ".join(part for part in (item.period, item.place) if part) or None,
            details={
                key: value
                for key, value in {"기간": item.period, "장소": item.place}.items()
                if value is not None
            },
            thumbnail_url=item.thumbnail_url,
            external_url=item.url,
        )
        for item in entries
    ]


def _distance_from_location_label(
    latitude: float,
    longitude: float,
    target_latitude: float | None,
    target_longitude: float | None,
) -> str | None:
    if target_latitude is None or target_longitude is None:
        return None
    distance_km = haversine_km(latitude, longitude, target_latitude, target_longitude)
    return f"약 {round(distance_km * 1000):,}m"


def _seoul_realtime_map_url(area: SeoulRealtimeArea) -> str:
    """서울시 실시간 도시데이터 지도의 제공 지역 딥링크를 만든다.

    지도 URL은 ``y=경도``, ``x=위도`` 순서를 사용한다. 응답의 AREA_NM보다
    고정 지역 목록의 이름·대표 좌표가 URL 파라미터 계약에 맞으므로 그 값을 쓴다.
    """

    return (
        "https://data.seoul.go.kr/SeoulRtd/map?hotspotNm="
        f"{quote(area.name, safe='')}&y={area.longitude}&x={area.latitude}"
    )


def _format_realtime_parking(item: RealtimeParkingLot, *, distance_km: float | None = None) -> str:
    """카드 한 줄에 실을 요약. 거리를 붙이면 화면에서 가까움/멂을 바로 비교할 수 있다.

    거리는 ``_parking_distance_or_inf``로 정렬에만 쓰고 버려지던 값이었다 —
    좌표가 없는 항목(구 단위 API가 카탈로그에 없는 신규 주차장 코드를 준 경우)은
    거리를 못 재므로 ``None``이면 그냥 뺀다.
    """
    capacity = f"총 {item.capacity}대" if item.capacity is not None else "총 주차 대수 미제공"
    paid = "유료" if item.paid is True else "무료" if item.paid is False else "요금 정보 미제공"
    distance = f"약 {round(distance_km * 1000):,}m" if distance_km is not None else None
    if item.available_spaces is not None:
        status = f"현재 {item.available_spaces}대 주차 가능"
    elif item.current_available and item.current_parked_count is not None:
        status = f"현재 {item.current_parked_count}대 주차 중"
    else:
        status = "실시간 주차 현황 미제공"
    detail = ", ".join(part for part in (distance, capacity, paid) if part)
    return f"{status}({detail})"


def _district_from_address(address: str | None) -> str | None:
    """주소에서 서울시 자치구를 꺼낸다. 공영주차장 API의 구 단위 파라미터다."""

    if not address:
        return None
    for token in address.replace(",", " ").split():
        if token.endswith("구") and len(token) >= 2:
            return token
    return None


def _municipal_status_to_realtime_lot(
    status: MunicipalParkingStatus,
    catalog_entry: object | None,
) -> RealtimeParkingLot:
    """공영주차장 최신값과 좌표 카탈로그를 기존 실시간 카드 모델로 합친다."""

    latitude = getattr(catalog_entry, "latitude", None)
    longitude = getattr(catalog_entry, "longitude", None)
    capacity = (
        status.capacity
        if status.capacity is not None
        else getattr(catalog_entry, "capacity", None)
    )
    available_spaces = (
        max(0, capacity - status.current_parked_count)
        if capacity is not None and status.current_parked_count is not None and status.is_live
        else None
    )
    return RealtimeParkingLot(
        name=status.name,
        latitude=latitude,
        longitude=longitude,
        capacity=capacity,
        current_parked_count=status.current_parked_count,
        current_available=status.is_live,
        paid=status.paid if status.paid is not None else getattr(catalog_entry, "paid", None),
        observed_at=status.observed_at,
        address=status.address or getattr(catalog_entry, "address", None),
        code=status.code,
        lot_type="공영",
        available_spaces=available_spaces,
    )


def _parking_distance_or_inf(location: ResolvedLocation, item: RealtimeParkingLot) -> float:
    if item.latitude is None or item.longitude is None:
        return float("inf")
    return haversine_km(location.latitude, location.longitude, item.latitude, item.longitude)


def _parking_distance_km_or_none(
    location: ResolvedLocation, item: RealtimeParkingLot
) -> float | None:
    """카드 표시용 거리. 정렬용 ``_parking_distance_or_inf``와 달리 좌표가 없으면 None이다."""

    if item.latitude is None or item.longitude is None:
        return None
    return haversine_km(location.latitude, location.longitude, item.latitude, item.longitude)


# 관광버스 전용 주차장은 명칭에 "버스"가 들어간다("탑골공원 관광버스전용 주차장(시)").
# 승용차로 온 사용자에게는 세울 수 없는 자리라 목록에서 빼는 편이 카드를 짧게 만든다.
def _is_bus_only_lot(name: str) -> bool:
    return "버스" in name


def _subway_field_key(item: RealtimeSubwayArrival) -> str:
    """같은 역·같은 호선의 두 방향이 같은 키로 겹쳐 한쪽이 지워지는 걸 막는다."""

    base = f"{item.station_name} {item.line or ''}".strip()
    return f"{base} · {item.direction}" if item.direction else base


def _format_subway_arrival(item: RealtimeSubwayArrival) -> str:
    destination = f"{item.destination}행" if item.destination else "방면 정보 미제공"
    arrival = item.arrival_message or (
        f"약 {max(1, round(item.arrival_seconds / 60))}분 후"
        if item.arrival_seconds is not None
        else "도착 정보 미제공"
    )
    direction = f" · {item.direction}" if item.direction else ""
    return f"{destination}{direction} · {arrival}"


def _to_concentration_forecast_infos(
    concentration: ConcentrationResult | None,
    *,
    candidate_name: str,
    start_date: date,
) -> list[ConcentrationForecastInfo]:
    """관광지 집중률의 방문일 이후 7일 예측을 INFO 계약으로 정규화한다."""

    items: list[ConcentrationForecastInfo] = []
    for forecast in select_concentration_forecasts(
        concentration,
        candidate_name=candidate_name,
        start_date=start_date,
    ):
        forecast_date = parse_concentration_forecast_date(forecast.forecast_date)
        rate = forecast.concentration_rate
        if forecast_date is None or not is_valid_concentration_rate(rate):
            continue
        normalized = normalize_concentration(rate)
        items.append(
            ConcentrationForecastInfo(
                forecast_date=forecast_date.isoformat(),
                concentration_rate=rate,
                concentration_level=cast(
                    Literal["quiet", "normal", "slightly_crowded", "crowded"],
                    normalized.level.value,
                ),
                concentration_label=normalized.label.value,
            )
        )
    return items


def _normalize_place_name(value: str) -> str:
    """공백·대소문자 차이를 무시하고 장소명을 대조한다(TP-171 이름-일치 폴백 전용)."""

    return value.casefold().replace(" ", "")


def _area_resolved_location(area: SeoulRealtimeArea) -> ResolvedLocation:
    """서울시 제공 지역 하나를 위치 해석 결과 모양으로 만든다.

    지오코딩을 부르지 않는다 — 목록에 실린 좌표가 이미 그 지역의 중심점이고, 우리가
    조회할 대상도 바로 그 지역이다. `_resolved_center_location_result()`가 보충 조회에서
    같은 이유로 하는 일과 같다.
    """

    return ResolvedLocation(
        requested_query=area.name,
        provider_query=area.name,
        resolved_name=area.name,
        latitude=area.latitude,
        longitude=area.longitude,
        resolution_method=ResolutionMethod.DIRECT,
        confidence=ResolutionConfidence.EXACT,
    )


# 혼잡한 순으로 줄 세우는 기준. 서울시 실시간 도시데이터가 쓰는 네 단계다.
#
# **목록에 없는 값은 맨 뒤로 보낸다.** 서울시가 등급 이름을 바꾸거나 새 값을 넣어도
# 정렬이 예외로 끊기지 않게 한다 — 순서가 조금 어긋나는 것이 답을 통째로 잃는 것보다
# 낫다. 다만 그런 값이 오면 화면에서 눈에 띄므로 조용히 묻히지는 않는다.
CONGESTION_LEVEL_ORDER = ("붐빔", "약간 붐빔", "보통", "여유")

# "지금 붐빈다"고 셀 등급. **보통은 넣지 않는다.** 한 번 "여유가 아닌 것"으로 셌더니
# 종로구 14곳에서 "붐비는 곳 13곳"이 나왔는데 실제 구성은 약간 붐빔 6곳 + 보통 7곳이었다
# (2026-09-09). 사용자가 "지금 붐비나"를 물을 때 알고 싶은 것은 피해야 할 곳이지 평소만큼
# 사람이 있는 곳이 아니다.
BUSY_CONGESTION_LEVELS = frozenset({"붐빔", "약간 붐빔"})


def _congestion_rank(level: str) -> int:
    try:
        return CONGESTION_LEVEL_ORDER.index(level)
    except ValueError:
        return len(CONGESTION_LEVEL_ORDER)


def _supported_district(value: str) -> ServiceDistrict | None:
    """추천 요청의 위치 표현이 지원 구를 통째로 가리키는지 본다(D-119).

    `_supported_district_name()`과 같은 정규화를 쓰되 구 코드까지 필요해서 객체를
    돌려준다 — 구 단위 후보 조회가 lDongSignguCd로 읽기 때문이다.

    임의 지명까지 넓히지 않는다. "강남"은 받지만 "강남역"은 받지 않는다 — 역은
    지금까지처럼 그 좌표 둘레를 반경으로 보는 것이 맞다.
    """

    normalized = _supported_district_name(value)
    if normalized is None:
        return None
    return next(
        district for district in SUPPORTED_DISTRICTS if district.name == normalized
    )


def _supported_district_name(value: str) -> str | None:
    """'종로'·'종로구'처럼 지원 구를 가리키는 짧은 권역명을 정규화한다.

    주차장 질문의 이 표현은 특정 관광지 식별이 아니라 구 안의 주차장 목록을
    찾으려는 뜻이다. 임의 지명까지 넓히지 않고, 서비스가 실제로 지원하는 구
    이름만 받는다. 따라서 '종각'처럼 역·명소와 혼동될 수 있는 입력은 기존
    후보 되묻기 흐름을 유지한다.
    """

    normalized = value.casefold().replace(" ", "")
    for district in SUPPORTED_DISTRICTS:
        full_name = district.name.casefold()
        short_name = full_name.removesuffix("구")
        if normalized in {full_name, short_name}:
            return district.name
    return None


def _is_current_activity_candidate(request: InfoContextRequest) -> bool:
    """'지금 사람 많아?'처럼 현재 상권 경로 전환 가능성이 있는지 판단한다."""

    question = request.specific_question or ""
    return any(marker in question for marker in _CURRENT_ACTIVITY_MARKERS)


def _is_current_population_candidate(request: InfoContextRequest, clock_value: datetime) -> bool:
    """현재형 INFO 혼잡 질문을 실시간 인구 경로로 보낼지 결정한다.

    날짜가 없는 질문은 INFO 추출 규칙상 오늘으로 정규화된다. 반대로 내일·주말처럼
    방문일이 오늘보다 뒤면 관광지 집중률 예측만 사용한다. 이 판정은 LLM 프롬프트를
    바꾸지 않고, 같은 ``concentration`` question_type 안에서 데이터 출처만 고른다.

    (TP-171) 위치 해석에도 영향을 준다 — True면 저장소 지역 제한(enforce_service_area)
    을 명시적으로 끈다. 저장소 조회 자체는 끄지 않는다(그래서 명동성당류가 산다) —
    끄는 건 "지원 25개 구(D-107, 서울 전역) 밖이면 막는다"는 지역 제한 하나뿐이다.
    자세한 이유는 fetch_info_context() 호출부의 주석 참고.
    """

    return (
        request.question_type == "concentration"
        and request.specific_question is not None
        and _info_reference_date(request.visit_time, clock_value) == _as_kst(clock_value).date()
    )


def _place_candidate_has_data(
    location: ResolvedLocation,
    *,
    question_type: str,
    is_realtime_citydata_purpose: bool,
    current_population_candidate: bool,
) -> bool:
    """되묻기 후보 하나가 이번 질문 유형에 실제로 답할 데이터를 갖고 있는지.

    fetch_info_context()가 실제로 타는 갈래(위 question_type 분기)와 같은 기준을
    쓴다 — 여기서 통과시켜 놓고 정작 그 갈래에서 no_data가 나면 되묻기가
    무의미해진다.
    """
    if question_type == "realtime_commercial":
        return (
            select_nearest_commercial_area(
                latitude=location.latitude,
                longitude=location.longitude,
                requested_name=location.resolved_name,
            )
            is not None
        )
    if is_realtime_citydata_purpose:
        return (
            select_nearest_population_area(
                latitude=location.latitude,
                longitude=location.longitude,
                requested_name=location.resolved_name,
            )
            is not None
        )
    if question_type == "concentration":
        if location.concentration_name is not None:
            return True
        # 현재형 혼잡 질문은 집중률 매핑이 없어도 실시간 인구로 답할 수 있다
        # (_fetch_realtime_population_or_concentration_info와 같은 기준).
        if current_population_candidate:
            return (
                select_nearest_population_area(
                    latitude=location.latitude,
                    longitude=location.longitude,
                    requested_name=location.resolved_name,
                )
                is not None
            )
        return False
    # 그 외(주차·화장실 등 시설 상세)는 우리 DB에 저장된 장소인지만 본다 —
    # 특정 필드까지 미리 조회하진 않는다(비용 대비 실익 작음, 클릭 후 그
    # 필드가 비어 있으면 기존과 같은 "정보 없음"으로 끝난다).
    return location.place_id is not None


def _is_commercial_place_category(category: str | None) -> bool:
    """Naver Local Search 업종이 상권 활동 대체 대상인지 확인한다."""

    return category is not None and any(
        marker in category for marker in _COMMERCIAL_CATEGORY_MARKERS
    )


def _info_no_data_response(
    request: InfoContextRequest,
    *provider_metadata: tuple[ProviderMetadata, ...],
) -> InfoContextResponse:
    """위치 해석 자체가 실패했을 때의 INFO 응답을 question_type에 맞는 결과 타입으로 만든다.

    예전에는 question_type과 무관하게 항상 ConcentrationInfoResult를 반환해서,
    "교대쪽 오늘 열리는 행사 알려줘"처럼 event/realtime_event 질문도 "혼잡도 데이터가
    없어요"라는 엉뚱한 메시지로 나갔다(response_composer의 결과 타입 기반 디스패치가
    ConcentrationInfoResult를 catch-all로 받기 때문). question_type을 보존해야
    올바른 no_data 메시지가 나간다.
    """

    question_type = request.question_type
    if question_type == "event":
        result: (
            ConcentrationInfoResult
            | EventInfoResult
            | RealtimeCityInfoResult
            | RealtimeCommercialInfoResult
            | RealtimePopulationInfoResult
            | PlaceInfoResult
        ) = EventInfoResult(
            status="no_data",
            requested_place_name=request.place_name,
        )
    elif (
        question_type in _REALTIME_CITYDATA_QUESTION_TYPES
        or question_type == _PUBLIC_PARKING_QUESTION_TYPE
        or question_type == _PUBLIC_TOILET_QUESTION_TYPE
    ):
        result = RealtimeCityInfoResult(
            status="no_data",
            question_type=question_type,
            requested_place_name=request.place_name,
        )
    elif question_type == "realtime_commercial":
        result = RealtimeCommercialInfoResult(
            status="no_data",
            requested_place_name=request.place_name,
        )
    elif question_type == "concentration":
        result = ConcentrationInfoResult(
            status="no_data",
            requested_place_name=request.place_name,
        )
    else:
        result = PlaceInfoResult(
            status="no_data",
            question_type=question_type,
            requested_place_name=request.place_name,
        )

    return InfoContextResponse(
        request_id=request.request_id,
        status="no_data",
        result=result,
        metadata=_info_response_metadata(*provider_metadata),
    )


def _realtime_city_info_no_data_response(
    request: InfoContextRequest,
    *,
    place_name: str,
    resolved_location: ResolvedLocation,
    provider_metadata: tuple[ProviderMetadata, ...],
) -> InfoContextResponse:
    """citydata 제공 지역 밖에서도 질문 유형을 보존한 no_data 응답을 만든다."""

    return InfoContextResponse(
        request_id=request.request_id,
        status="no_data",
        result=RealtimeCityInfoResult(
            status="no_data",
            question_type=cast(
                Literal[
                    "realtime_parking",
                    "realtime_public_parking",
                    "realtime_subway",
                    "realtime_bus",
                    "realtime_event",
                    "realtime_traffic",
                ],
                request.question_type,
            ),
            requested_place_name=place_name,
            resolved_place_name=resolved_location.resolved_name,
        ),
        metadata=_info_response_metadata(provider_metadata),
    )


def _to_event_items(
    events: tuple[FestivalEvent, ...],
    *,
    resolved_name: str,
    latitude: float,
    longitude: float,
) -> list[EventItem]:
    """행사를 대상 장소 기준으로 정렬해 계약 모델로 옮긴다.

    정렬은 (직접 매칭 우선, 가까운 순)이다. 좌표가 없는 행사는 거리 없이 뒤로
    보낸다 — 목록에서 빼면 "행사가 없다"로 잘못 보일 수 있어 남긴다.

    반경으로 자르지 않는 이유: 조회가 지원 구로 한정돼 있어 지역 필터가 반경
    역할을 한다. 여기서 임의 반경을 하나 더 두면 근거 없는 숫자가 늘어난다.
    대신 개수만 상한을 둔다.

    **지원 범위가 종로구 한 곳이던 시절의 설명을 고쳤다(TP-253).** 지금은 서울
    25개 구 전체이고, 조회도 시도(`lDongRegnCd=11`)로 받아 응답의 `lDongSignguCd`
    로 거른다(D-025). 구가 넓어졌으므로 지역 필터가 곧 반경이라는 말은 예전만큼
    좁지 않다 — 상한이 실제로 무엇을 자르는지는 다시 볼 값이 됐다.
    """

    def distance_of(event: FestivalEvent) -> float | None:
        if event.latitude is None or event.longitude is None:
            return None
        return haversine_km(latitude, longitude, event.latitude, event.longitude)

    def is_direct(event: FestivalEvent) -> bool:
        # "경복궁 별빛야행"처럼 제목이 장소를 지목하는 경우만 직접 매칭으로 본다.
        # eventplace를 보려면 행사마다 detailIntro2를 열어야 해(N+1) 이번 단계는
        # 제목 매칭까지만 한다.
        return resolved_name in event.title

    scored = [(event, distance_of(event), is_direct(event)) for event in events]
    scored.sort(
        key=lambda entry: (
            not entry[2],  # 직접 매칭 먼저
            entry[1] if entry[1] is not None else float("inf"),
        )
    )
    return [
        EventItem(
            title=event.title,
            start_date=event.start_date.isoformat(),
            end_date=event.end_date.isoformat(),
            address=event.address,
            distance_km=round(distance, 2) if distance is not None else None,
            is_direct_match=direct,
            image_url=event.image_url,
        )
        for event, distance, direct in scored[:INFO_EVENT_RESULT_LIMIT]
    ]


def _place_review_response(
    request: InfoContextRequest,
    *,
    requested_place_name: str,
    resolved_place_name: str,
    place_id: str | None,
    destination_coordinates: Coordinates | None,
    evidence: tuple[ReviewEvidenceItem, ...],
    place_card: PlaceCard | None = None,
    provider_metadata: tuple[tuple[ProviderMetadata, ...], ...] = (),
) -> InfoContextResponse:
    """후기 질의 응답. status를 `fields`가 아니라 근거 유무로 정한다.

    `_place_info_response`의 규칙(fields가 비면 no_data)을 그대로 쓸 수 없다 — 이
    경로는 TourAPI 필드를 아예 조회하지 않아 fields가 항상 비기 때문이다. 대신
    후보 근거가 하나도 없으면 no_data다. 근거가 있어도 A의 선별에서 전부 떨어질 수
    있고, 그때 A가 no_data로 되돌린다.
    """

    status: Literal["success", "no_data"] = "success" if evidence else "no_data"
    return InfoContextResponse(
        request_id=request.request_id,
        status=status,
        result=PlaceInfoResult(
            status=status,
            question_type=request.question_type,
            requested_place_name=requested_place_name,
            resolved_place_name=resolved_place_name,
            place_id=place_id,
            destination_coordinates=destination_coordinates,
            fields={},
            review_evidence=evidence,
            place_card=place_card,
        ),
        metadata=_info_response_metadata(*provider_metadata),
    )


def _place_info_response(
    request: InfoContextRequest,
    *,
    requested_place_name: str,
    resolved_place_name: str,
    place_id: str | None,
    destination_coordinates: Coordinates | None,
    fields: dict[str, str],
    place_card: PlaceCard | None = None,
    provider_metadata: tuple[tuple[ProviderMetadata, ...], ...] = (),
) -> InfoContextResponse:
    """장소 상세 INFO 응답을 한 형태로 유지한다.

    뽑아낸 필드가 하나도 없으면 no_data다 — 장소는 찾았지만 그 질문에 답할 값이
    TourAPI에 없는 경우다. 이때도 resolved_place_name은 채워 보낸다(A가 "OO의
    주차 정보는 없어요"처럼 장소를 짚어 안내할 수 있게).

    **status 판정에는 fields만 쓴다.** place_card는 질문과 무관하게 채우므로
    판정에 넣으면 "주차 정보는 없어요"가 영영 나오지 않는다 — overview가 거의
    항상 있어 카드가 비는 일이 없기 때문이다.
    """

    status: Literal["success", "no_data"] = "success" if fields else "no_data"
    return InfoContextResponse(
        request_id=request.request_id,
        status=status,
        result=PlaceInfoResult(
            status=status,
            question_type=request.question_type,
            requested_place_name=requested_place_name,
            resolved_place_name=resolved_place_name,
            place_id=place_id,
            destination_coordinates=destination_coordinates,
            fields=fields,
            place_card=place_card,
        ),
        metadata=_info_response_metadata(*provider_metadata),
    )


def _to_info_destination_coordinates(location: ResolvedLocation) -> Coordinates:
    """C가 확정한 INFO 목적지를 A의 도보 경로 입력 형태로만 재노출한다."""

    return Coordinates(latitude=location.latitude, longitude=location.longitude)


def _to_place_card(
    details: PlaceDetails,
    place_id: str | None,
    *,
    photos: tuple[PlacePhoto, ...] = (),
) -> PlaceCard:
    """상세 조회 결과를 카드 표시용 묶음으로 옮긴다.

    fields와 같은 clean_text를 태워 HTML·엔티티 정리 결과가 두 곳에서 갈리지 않게
    한다. 값이 없으면 None으로 두고 문구를 지어내지 않는다.

    사진은 상세 조회가 아니라 place_image_embeddings에서 따로 읽어 넘어온다.
    thumbnail_url은 그대로 둔다 — 사진 목록이 비는 장소가 절반이 넘고, 그쪽은
    대표 이미지 한 장이 유일한 그림이다.
    """
    # 유모차는 두 원문 중 하나만 쓴다. 어느 쪽을 쓸지는 답변 경로와 같은 함수가
    # 정한다 — 같은 장소가 말풍선과 카드에서 다르게 읽히면 안 된다.
    stroller_rental, baby_carriage = resolve_stroller_rental(details)
    return PlaceCard(
        place_id=details.content_id or place_id,
        place_name=clean_text(details.title),
        thumbnail_url=details.thumbnail_url,
        photos=[
            PlacePhotoItem(url=photo.url, image_name=photo.image_name)
            for photo in photos
        ],
        overview=clean_text(details.overview),
        operating_hours=clean_text(details.operating_hours),
        rest_date=clean_text(details.rest_date),
        parking=clean_text(details.parking),
        parking_fee=clean_text(details.parking_fee),
        fee=clean_text(details.fee),
        baby_carriage=baby_carriage,
        pet=clean_text(details.pet),
        credit_card=clean_text(details.credit_card),
        restroom=clean_text(details.restroom),
        homepage=clean_text(details.homepage),
        # 무장애 아홉 항목. 접근로·주출입구(단차 서술)와 대중교통 접근은 카드에
        # 싣지 않는다 — 답변 경로의 wheelchair_access는 그대로 둔다.
        accessible_restroom=compose_accessible_restroom(details),
        accessible_parking=clean_barrier_free_text(details.accessible_parking_raw),
        elevator=clean_barrier_free_text(details.elevator_raw),
        visual_guide=compose_visual_guide(details),
        wheelchair_rental=clean_barrier_free_text(details.wheelchair_rental_raw),
        nursing_room=compose_nursing_room(details),
        seating=compose_seating(details),
        stroller_rental=stroller_rental,
        guide_dog=clean_barrier_free_text(details.guide_dog_raw),
    )


def _info_error_response(
    request: InfoContextRequest,
    *,
    status: Literal["unsupported", "unavailable"],
    error: ContextError,
    provider_metadata: tuple[tuple[ProviderMetadata, ...], ...] = (),
) -> InfoContextResponse:
    return InfoContextResponse(
        request_id=request.request_id,
        status=status,
        error=error,
        metadata=_info_response_metadata(*provider_metadata),
    )


def _compare_error_response(
    request: CompareContextRequest,
    *,
    status: Literal["no_data", "unavailable"],
    missing_place_ids: list[str] | None = None,
    error: ContextError | None = None,
) -> CompareContextResponse:
    """비교를 진행할 수 없을 때의 응답을 한 형태로 유지한다."""

    return CompareContextResponse(
        request_id=request.request_id,
        status=status,
        criteria=request.criteria,
        items=[],
        missing_place_ids=missing_place_ids or [],
        error=error,
    )


def _context_error_from_tool(
    error: ToolError | None,
    *,
    fallback_code: str,
    fallback_message: str,
    retryable: bool,
) -> ContextError:
    """Tool 오류 세부 정보는 유지하되 INFO 계약의 오류 모델로 변환한다."""

    return ContextError(
        code=error.code if error is not None else fallback_code,
        message=error.message if error is not None else fallback_message,
        retryable=error.retryable if error is not None else retryable,
    )


def _info_response_metadata(
    *provider_metadata_groups: tuple[ProviderMetadata, ...],
) -> ResponseMetadata:
    """INFO의 직접·대체 조회 전 과정을 A가 추적할 수 있게 누적한다."""

    return ResponseMetadata(
        provider_metadata=[
            ContextProviderMetadata(
                source=item.source.value,
                status=item.status.value,
                retrieved_at=item.retrieved_at,
            )
            for group in provider_metadata_groups
            for item in group
        ]
    )


def _resolve_search_radius_km(
    max_travel_time: int | None,
    *,
    default_radius_km: float,
) -> float:
    """최대 이동시간을 MVP 도보 속도로 환산한 후보 수집 반경으로 변환한다."""

    if max_travel_time is None:
        return default_radius_km
    estimated_radius = max_travel_time * WALKING_SPEED_KM_PER_MINUTE
    return min(
        max(estimated_radius, MIN_PLACE_SEARCH_RADIUS_KM),
        MAX_PLACE_SEARCH_RADIUS_KM,
    )


def _is_excluded(place: EnrichedPlace, excluded_small_codes: frozenset[str]) -> bool:
    """후보의 TourAPI 소분류가 제외 태그에 걸리는지.

    소분류(lcls_systm3)가 비어 있는 후보는 제외 여부를 판단할 근거가 없으므로
    남긴다 — 판단 못 하는 것을 제외로 취급하면 후보가 조용히 사라진다.
    """

    if not excluded_small_codes:
        return False
    small_code = place.candidate.lcls_systm3
    return small_code is not None and small_code.strip() in excluded_small_codes


def _place_warnings(
    status: ToolStatus,
    excluded_plan: ExcludedCategoryPlan,
    *,
    truncated: bool = False,
    exhausted: bool = False,
    unknown_accessibility_need: bool = False,
) -> tuple[str, ...]:
    warnings: list[str] = []
    if status is ToolStatus.PARTIAL:
        warnings.append("partial_data")
    if excluded_plan.has_unmapped_tags:
        # 분류 매핑이 없어 걸러내지 못한 제외 태그가 있다는 걸 A/D가 알 수 있게 남긴다.
        warnings.append("exclude_tags_unmapped")
    if truncated:
        # 분류 조회 중 하나라도 Provider 행 상한에 걸렸다. 합쳐진 결과가 비어도
        # "더 없음"이 아니라 "더 못 받아옴"이라는 뜻이라 A가 구분해야 한다.
        warnings.append(CANDIDATE_POOL_TRUNCATED_WARNING)
    if exhausted:
        warnings.append(CANDIDATE_POOL_EXHAUSTED_WARNING)
    if unknown_accessibility_need:
        # A가 보낸 무장애 어휘 중 C가 모르는 값이 있었다. 그 값은 좁히는 데 쓰지
        # 못했으므로, 결과가 요구를 다 반영하지 못했다는 것을 A가 알아야 한다.
        warnings.append(UNKNOWN_ACCESSIBILITY_NEED_WARNING)
    return tuple(warnings)


def _resolve_accessibility_needs(
    values: Sequence[str],
) -> tuple[tuple[AccessibilityNeed, ...], bool]:
    """A가 보낸 무장애 어휘를 C가 아는 값으로 옮긴다.

    돌려주는 두 번째 값은 "모르는 값이 있었는가"다. 모르는 값은 좁히는 데 쓸 수
    없으므로 버리되, 버렸다는 사실은 경고로 남겨야 한다 — C의 조건 계약이
    `list[str]`이라 A가 어휘를 늘려도 요청이 깨지지 않는 대신(그게 의도다),
    아무 흔적도 없으면 요구가 반영되지 않은 결과를 반영된 것으로 읽게 된다.

    중복은 없앤다. 같은 값을 두 번 보내도 조건은 하나다.
    """
    known: list[AccessibilityNeed] = []
    has_unknown = False
    for value in values:
        normalized = value.strip()
        if not normalized:
            continue
        try:
            need = AccessibilityNeed(normalized)
        except ValueError:
            has_unknown = True
            continue
        if need not in known:
            known.append(need)
    return tuple(known), has_unknown


def _merge_place_results(
    results: list[NearbyPlaceDetailsResult],
    *,
    limit: int,
    started_at: float,
    excluded_plan: ExcludedCategoryPlan | None = None,
    unknown_accessibility_need: bool = False,
) -> NearbyPlaceDetailsResult:
    excluded_plan = excluded_plan or ExcludedCategoryPlan()
    if not results:
        return NearbyPlaceDetailsResult(
            places=(),
            status=ToolStatus.UNSUPPORTED,
            source="agent_context_service",
            retrieved_at=datetime.now(UTC),
            elapsed_ms=(perf_counter() - started_at) * 1000,
            error=ToolError(
                code="unsupported_category",
                message="지원하는 장소 분류를 찾지 못했습니다.",
                cause="unsupported_category",
                retryable=False,
            ),
        )

    places: list[EnrichedPlace] = []
    seen_place_ids: set[str] = set()
    excluded_count = 0
    for result in results:
        for place in result.places:
            place_id = place.candidate.place_id
            if place_id in seen_place_ids:
                continue
            seen_place_ids.add(place_id)
            # 제외는 limit을 적용하기 전에 건다 — 나중에 걸러내면 제외될 후보가
            # 먼저 정원을 차지해 실제 추천 가능한 후보가 줄어든다.
            if _is_excluded(place, excluded_plan.small_codes):
                excluded_count += 1
                continue
            places.append(place)
            if len(places) == limit:
                break
        if len(places) == limit:
            break

    statuses = tuple(result.status for result in results)
    if places:
        status = (
            ToolStatus.PARTIAL
            if any(item in {ToolStatus.PARTIAL, ToolStatus.UNAVAILABLE} for item in statuses)
            else ToolStatus.SUCCESS
        )
        error = None
    elif excluded_count or all(item is ToolStatus.NO_DATA for item in statuses):
        # 조회는 됐지만 전부 제외 태그에 걸린 경우도 "조건에 맞는 후보 없음"이다.
        # 장애(unavailable)로 떨어뜨리면 사용자에게 오류처럼 보인다.
        status = ToolStatus.NO_DATA
        error = None
    elif all(item is ToolStatus.UNSUPPORTED for item in statuses):
        status = ToolStatus.UNSUPPORTED
        error = next((result.error for result in results if result.error), None)
    else:
        status = ToolStatus.UNAVAILABLE
        error = next((result.error for result in results if result.error), None)

    return NearbyPlaceDetailsResult(
        places=tuple(places),
        status=status,
        source="agent_context_service",
        retrieved_at=max(result.retrieved_at for result in results),
        elapsed_ms=(perf_counter() - started_at) * 1000,
        error=error,
        warnings=_place_warnings(
            status,
            excluded_plan,
            truncated=any(
                CANDIDATE_POOL_TRUNCATED_WARNING in result.warnings for result in results
            ),
            exhausted=bool(results)
            and all(CANDIDATE_POOL_EXHAUSTED_WARNING in result.warnings for result in results),
            unknown_accessibility_need=unknown_accessibility_need,
        ),
        provider_metadata=tuple(
            metadata for result in results for metadata in result.provider_metadata
        ),
    )


def _unsupported_category_response(
    request: AgentContextRequest,
    plan: CategoryQueryPlan,
) -> AgentContextResponse:
    details = [
        *plan.unsupported_place_types,
        *plan.unsupported_place_tags,
        *plan.conflicting_place_tags,
    ]
    message = "지원하지 않거나 서로 맞지 않는 장소 분류입니다."
    if details:
        message = f"{message} ({', '.join(details)})"
    return AgentContextResponse(
        request_id=request.request_id,
        intent=request.intent,
        status="unsupported",
        context=None,
        error=ContextError(
            code="unsupported_category",
            message=message,
            retryable=False,
        ),
        metadata=ResponseMetadata(rule_versions=_rule_versions()),
    )


def _rule_versions() -> dict[str, str]:
    return {
        "category": _CATEGORY_RULE_VERSION,
        "search_radius": _SEARCH_RADIUS_RULE_VERSION,
        "tool_execution": TOOL_EXECUTION_RULE_VERSION,
    }


def _as_kst(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=_KST)
    return value.astimezone(_KST)


__all__ = ["ContextService", "ContextTools"]
