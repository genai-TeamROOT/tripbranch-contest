"""C(Tool Intelligence)/D(Recommendation) 테스트용 Fake 구현.

역할: 고정된 가짜 결과로 ToolProvider/RecommendationProvider 계약을 만족시켜, C/D 없이도
Agent Runtime 흐름을 끝까지 테스트할 수 있게 한다. app/providers/stub.py의 Fake provider들과
같은 결 — 실제 계산이 아니라 고정/임시 데이터를 반환한다.
ToolProvider·RecommendationProvider 모두 계약이 확정됐으므로([TECH-02]) run_agent()는
FakeToolProvider/FakeRecommendationProvider 대신 실제 구현체(get_context_provider(),
RealRecommendationProvider)를 기본 주입한다. 여기 두 Fake는 이제 테스트에서 Provider를
직접 주입할 때만 쓴다.
"""

from __future__ import annotations

from app.agent_context.enrichment_schemas import (
    CandidateEnrichmentRequest,
    CandidateEnrichmentResponse,
    CandidateEnrichmentResult,
    ConcentrationForecastData,
)
from app.agent_context.info_schemas import RealtimeInfoDetailItem
from app.agent_context.schemas import (
    AgentContextRequest,
    AgentContextResponse,
    Clarification,
    ContextValue,
    Coordinates,
    PlaceCandidate,
    RecommendationContext,
    ResponseMetadata,
    WeatherForecast,
)
from app.schemas import (
    CompareCriteria,
    ComparisonItem,
    RecommendationItem,
    RecommendationResponse,
    UserConditions,
)
from app.services.runtime.compare_context_schemas import (
    CompareCandidate,
    CompareContextRequest,
    CompareContextResponse,
)
from app.services.runtime.info_context_schemas import (
    ConcentrationInfoResult,
    EventInfoResult,
    EventItem,
    InfoContextRequest,
    InfoContextResponse,
    PlaceCard,
    PlaceInfoResult,
    RealtimeCityInfoResult,
    RealtimeCommercialInfoResult,
)
from app.state.schema import now_kst

# concentration-conditions.md §3.3 근접치 fallback을 흉내 내는 고정 데이터.
# "관광지" 계열 이름은 직접 조회 성공(is_proxy=False), 그 외(카페 등)는 근접치
# fallback 성공(is_proxy=True)으로 시뮬레이션한다 — 실제 오케스트레이션은 C 구현.
_FAKE_ATTRACTION_NAMES = ("경복궁", "창덕궁", "종묘", "인사동", "광화문", "북촌한옥마을")
_FAKE_NEAREST_ATTRACTION = "경복궁"

# COMPARE가 해석할 수 있는 place_id → 장소명. 여기 없는 id는 실제 C에서 "저장소에
# 없거나 비활성"인 경우에 해당한다 — 미조회 경로를 A가 테스트할 수 있게 남겨둔다.
_FAKE_COMPARE_PLACE_NAMES: dict[str, str] = {
    "fake-place-1": "경복궁",
    "fake-place-2": "창덕궁",
    "fake-place-3": "북촌한옥마을",
    "runtime-stub-museum-1": "런타임 스텁 박물관",
    "runtime-stub-cafe-1": "런타임 스텁 카페",
    "runtime-stub-park-1": "런타임 스텁 공원",
    "runtime-stub-gallery-1": "런타임 스텁 갤러리",
    "runtime-stub-restaurant-1": "런타임 스텁 식당",
    "runtime-stub-market-1": "런타임 스텁 시장",
}
# TRAVEL_TIME 실측 연결(2026-08-21) — 실제 C처럼 place_id별 좌표를 함께 흉내 낸다.
# 값 자체는 종로 일대의 임의 좌표로, 실측 provider가 실제로 거리를 계산할 수 있게
# 서로 떨어뜨려 둔다.
_FAKE_COMPARE_PLACE_COORDINATES: dict[str, tuple[float, float]] = {
    "fake-place-1": (37.5796, 126.9770),
    "fake-place-2": (37.5824, 126.9910),
    "fake-place-3": (37.5735, 126.9788),
    # _FAKE_CANDIDATES(RECOMMEND 고정 후보)의 location과 같은 값 — COMPARE로
    # 이어지는 통합 테스트가 실제 RECOMMEND 결과를 그대로 재사용하므로 여기서도
    # 좌표가 있어야 한다.
    "runtime-stub-museum-1": (37.5796, 126.9770),
    "runtime-stub-cafe-1": (37.5798, 126.9772),
    "runtime-stub-park-1": (37.5800, 126.9774),
    "runtime-stub-gallery-1": (37.5802, 126.9776),
    "runtime-stub-restaurant-1": (37.5804, 126.9778),
    "runtime-stub-market-1": (37.5806, 126.9780),
}
# 비교가 성립하는 최소 후보 수. C(agent_context.service)의 _MIN_COMPARE_ITEMS와 같다.
_FAKE_MIN_COMPARE_ITEMS = 2
# criteria별로 "이 값이 없으면 비교할 게 없는" 필드. overall은 세 값을 함께 설명하는
# 방식이라 특정 필드를 요구하지 않는다.
_FAKE_COMPARE_CRITERIA_FIELDS: dict[CompareCriteria, str] = {
    CompareCriteria.TIME: "remaining_minutes",
    CompareCriteria.TRAVEL_TIME: "latitude",
}

# concentration 외 question_type(D-054)의 고정 fields — 키는
# info-question-types-handoff.md의 question_type별 fields 표를 그대로 따른다.
_FAKE_PLACE_FIELDS_BY_QUESTION_TYPE: dict[str, dict[str, str]] = {
    "operating_hours": {"operating_hours": "09:00~18:00", "rest_date": "매주 월요일"},
    "fee": {"fee": "성인 3,000원"},
    "parking": {"parking": "가능", "parking_fee": "무료"},
    "facility": {"baby_carriage": "가능", "restroom": "있음"},
    "location_info": {"address": "서울특별시 종로구 사직로 161"},
    "general_info": {
        "overview": "조선 왕조의 법궁으로 1395년에 창건된 궁궐이다.",
        "homepage": "http://www.royalpalace.go.kr",
    },
}

_FAKE_PLACE_CARD = PlaceCard(
    place_id="fake-place-id",
    place_name="경복궁",
    thumbnail_url="https://images.example.test/gyeongbokgung.jpg",
    overview="조선 왕조의 법궁으로 1395년에 창건된 궁궐이다.",
    operating_hours="09:00~18:00",
    rest_date="매주 월요일",
    parking="가능",
    parking_fee="무료",
    fee="성인 3,000원",
    baby_carriage="가능",
    pet="불가",
    credit_card="가능",
    restroom="있음",
    homepage="http://www.royalpalace.go.kr",
)

_FAKE_CANDIDATES = (
    PlaceCandidate(
        place_id="runtime-stub-museum-1",
        name="런타임 스텁 박물관",
        category="cultural_facility",
        lcls_systm1="VE",
        lcls_systm2="VE07",
        lcls_systm3="VE070100",
        location={"latitude": 37.5796, "longitude": 126.9770},
        operating_hours_raw="09:00-18:00",
    ),
    PlaceCandidate(
        place_id="runtime-stub-cafe-1",
        name="런타임 스텁 카페",
        category="restaurant",
        lcls_systm1="FD",
        lcls_systm2="FD05",
        lcls_systm3="FD050100",
        location={"latitude": 37.5798, "longitude": 126.9772},
        operating_hours_raw="08:00-22:00",
    ),
    PlaceCandidate(
        place_id="runtime-stub-park-1",
        name="런타임 스텁 공원",
        category="attraction",
        lcls_systm1="VE",
        lcls_systm2="VE03",
        lcls_systm3="VE030100",
        location={"latitude": 37.5800, "longitude": 126.9774},
        operating_hours_raw="00:00-24:00",
    ),
    PlaceCandidate(
        place_id="runtime-stub-gallery-1",
        name="런타임 스텁 갤러리",
        category="cultural_facility",
        lcls_systm1="VE",
        lcls_systm2="VE07",
        lcls_systm3="VE070600",
        location={"latitude": 37.5802, "longitude": 126.9776},
        operating_hours_raw="10:00-19:00",
    ),
    # SCHEDULE-07부터 ScheduleLLMPlan.items가 min_length=3이라, 일정 재조정
    # 테스트(SCHEDULE-06)가 첫 턴에서 3곳을 제외한 뒤에도 두 번째 일정을 짤 만큼
    # 후보가 남아 있어야 한다. 4개로는 재조정 시 1개만 남아 planner.py의
    # "후보 3개 미만이면 LLM 미호출" 가드에 걸려버려 6개로 늘렸다.
    PlaceCandidate(
        place_id="runtime-stub-restaurant-1",
        name="런타임 스텁 식당",
        category="restaurant",
        lcls_systm1="FD",
        lcls_systm2="FD01",
        lcls_systm3="FD010100",
        location={"latitude": 37.5804, "longitude": 126.9778},
        operating_hours_raw="11:00-21:00",
    ),
    PlaceCandidate(
        place_id="runtime-stub-market-1",
        name="런타임 스텁 시장",
        category="attraction",
        lcls_systm1="VE",
        lcls_systm2="VE03",
        lcls_systm3="VE030300",
        location={"latitude": 37.5806, "longitude": 126.9780},
        operating_hours_raw="09:00-20:00",
    ),
)


class FakeToolProvider:
    """조건과 무관하게 고정 후보·날씨를 반환하는 가짜 Tool provider.

    current_location과 search_center가 둘 다 없으면 계약(문서 §4.4)대로
    needs_clarification을 반환한다 — 형식 오류가 아니라 정상 상태다.
    """

    async def fetch_context(self, request: AgentContextRequest) -> AgentContextResponse:
        conditions = request.conditions
        metadata = ResponseMetadata()

        if not conditions.current_location and not conditions.search_center:
            return AgentContextResponse(
                request_id=request.request_id,
                intent=request.intent,
                contract_version="draft-v0",
                status="needs_clarification",
                context=None,
                clarification=Clarification(
                    code="location_required",
                    missing_fields=["current_location", "search_center"],
                    candidates=[],
                ),
                warnings=[],
                error=None,
                metadata=metadata,
            )

        return AgentContextResponse(
            request_id=request.request_id,
            intent=request.intent,
            contract_version="draft-v0",
            status="success",
            context=RecommendationContext(
                weather=ContextValue(
                    status="success",
                    # D-051: 실제 C가 내보내는 사실 3종(강수/하늘/기온)을 채운다.
                    # 하나라도 비면 D의 판정 입력이 결측이라 NEUTRAL로 굳어,
                    # 검증하려던 판정 로직이 한 줄도 실행되지 않는다.
                    data=WeatherForecast(
                        forecast_for=now_kst(),
                        precipitation="none",
                        sky="overcast",
                        temperature_celsius=22.0,
                    ),
                ),
                places=ContextValue(status="success", data=list(_FAKE_CANDIDATES)),
            ),
            warnings=[],
            error=None,
            metadata=metadata,
        )

    async def fetch_compare_context(self, request: CompareContextRequest) -> CompareContextResponse:
        """C의 비교 컨텍스트 조립을 고정 데이터로 흉내 낸다.

        place_id를 장소명으로 바꾸는 것만 가짜로 하고, 판정 규칙은 실제 C와 같게
        맞춘다 — 이름을 못 찾은 후보는 빼고 partial, 남은 수가 2건 미만이면 no_data,
        criteria에 해당하는 스냅샷이 전원 비어 있으면 no_data. 여기를 헐겁게 두면
        A의 Runtime 테스트가 실제 경로에서는 나지 않는 조합을 통과시킨다.

        스냅샷(거리·남은 운영시간·실내외)은 B가 준 값을 그대로 통과시킨다. C가
        재계산하지 않는 것과 같다(D-050).
        """

        candidates = sorted(request.candidates, key=lambda item: item.rank)

        def _build_item(candidate: CompareCandidate) -> ComparisonItem:
            coordinates = _FAKE_COMPARE_PLACE_COORDINATES.get(candidate.place_id)
            return ComparisonItem(
                place_id=candidate.place_id,
                place_name=_FAKE_COMPARE_PLACE_NAMES[candidate.place_id],
                rank=candidate.rank,
                distance_km=candidate.distance_km,
                remaining_minutes=candidate.remaining_minutes,
                environment_type=candidate.environment_type,
                latitude=coordinates[0] if coordinates else None,
                longitude=coordinates[1] if coordinates else None,
            )

        items = [
            _build_item(candidate)
            for candidate in candidates
            if candidate.place_id in _FAKE_COMPARE_PLACE_NAMES
        ]
        missing = [
            candidate.place_id
            for candidate in candidates
            if candidate.place_id not in _FAKE_COMPARE_PLACE_NAMES
        ]

        criteria_field = _FAKE_COMPARE_CRITERIA_FIELDS.get(request.criteria)
        no_facts = criteria_field is not None and all(
            getattr(item, criteria_field) is None for item in items
        )
        if len(items) < _FAKE_MIN_COMPARE_ITEMS or no_facts:
            return CompareContextResponse(
                request_id=request.request_id,
                status="no_data",
                criteria=request.criteria,
                items=[],
                missing_place_ids=missing,
            )

        return CompareContextResponse(
            request_id=request.request_id,
            status="partial" if missing else "success",
            criteria=request.criteria,
            items=items,
            missing_place_ids=missing,
        )

    async def fetch_info_context(self, request: InfoContextRequest) -> InfoContextResponse:
        """concentration-conditions.md §3.3 흐름을 고정 데이터로 흉내 낸다.

        place_name이 없으면 needs_clarification, 알려진 관광지면 직접 성공,
        그 외(카페 등)는 근접치 fallback 성공을 시뮬레이션한다. 실제 장소
        해석·근접치 탐색 오케스트레이션은 C 내부 구현(A는 하지 않음).

        question_type=concentration은 위 흐름 그대로다. realtime_commercial은 특정
        매장 대신 용리단길 카페 상권을 빌린 고정 응답을 돌린다. 그 외 7종(D-054/D-055,
        backend/docs/package-a/info-question-types-handoff.md)은 알려진
        관광지면 고정 fields/event를 채운 성공 응답을, 그 외는 no_data를
        반환한다 — C처럼 근접치 fallback을 흉내 내지는 않는다(그 오케스트레이션
        자체가 C 내부 책임이라 A 쪽 Fake에서 재현할 필요가 없다).
        """
        # 공중화장실은 지명 없이 기기 위치로도 답하므로 되묻기보다 먼저 본다.
        if request.question_type == "public_toilet":
            return self._fake_public_toilet_info(request)
        if not request.place_name:
            return InfoContextResponse(
                request_id=request.request_id,
                status="needs_clarification",
                clarification=Clarification(
                    code="place_required",
                    missing_fields=["place_name"],
                    candidates=[],
                ),
            )

        if request.question_type == "event":
            return self._fake_event_info(request)
        if request.question_type == "realtime_commercial":
            return self._fake_realtime_commercial_info(request)
        if request.question_type != "concentration":
            return self._fake_place_info(request)

        if request.place_name in _FAKE_ATTRACTION_NAMES:
            return InfoContextResponse(
                request_id=request.request_id,
                status="success",
                result=ConcentrationInfoResult(
                    status="success",
                    is_proxy=False,
                    requested_place_name=request.place_name,
                    resolved_place_name=request.place_name,
                    forecast_date=request.visit_time or now_kst().date().isoformat(),
                    concentration_rate=42.0,
                    concentration_level="normal",
                    concentration_label="보통",
                ),
            )

        return InfoContextResponse(
            request_id=request.request_id,
            status="success",
            result=ConcentrationInfoResult(
                status="success",
                is_proxy=True,
                requested_place_name=request.place_name,
                resolved_place_name=_FAKE_NEAREST_ATTRACTION,
                forecast_date=request.visit_time or now_kst().date().isoformat(),
                concentration_rate=58.0,
                concentration_level="slightly_crowded",
                concentration_label="다소 혼잡",
            ),
        )

    def _fake_public_toilet_info(self, request: InfoContextRequest) -> InfoContextResponse:
        """인사동 주변 두 곳을 고정으로 돌린다.

        좌표를 채워 프론트 카드의 도보 길찾기 경로까지 fake 모드에서 확인할 수
        있게 한다 — 좌표가 비면 카드가 주소 검색으로 폴백해 다른 경로를 타게 된다.
        """

        return InfoContextResponse(
            request_id=request.request_id,
            status="success",
            result=RealtimeCityInfoResult(
                status="success",
                question_type="public_toilet",
                requested_place_name=request.place_name,
                resolved_place_name=request.place_name or "현재 위치",
                fields={
                    "인사동마루 신관 개방화장실": "도보 50m · 지금 이용 가능 · 24시간",
                    "쌈지길(지하1층)": "도보 60m · 지금은 닫혀 있음 · 10:30~20:30",
                },
                detail_items=[
                    RealtimeInfoDetailItem(
                        title="인사동마루 신관 개방화장실",
                        subtitle="도보 50m · 지금 이용 가능 · 24시간",
                        details={
                            "거리": "도보 50m",
                            "개방 여부": "지금 이용 가능",
                            "개방시간": "24시간",
                            "주소": "서울특별시 종로구 인사동길 35-4",
                            "유형": "민간개방",
                            "화장실": "남자, 여자",
                            "장애인화장실": "남자, 여자",
                        },
                        latitude=37.57432,
                        longitude=126.98563,
                    ),
                    RealtimeInfoDetailItem(
                        title="쌈지길(지하1층)",
                        subtitle="도보 60m · 지금은 닫혀 있음 · 10:30~20:30",
                        details={
                            "거리": "도보 60m",
                            "개방 여부": "지금은 닫혀 있음",
                            "개방시간": "10:30~20:30",
                            "주소": "서울특별시 종로구 인사동길 44",
                            "유형": "민간개방",
                        },
                        latitude=37.57411,
                        longitude=126.98527,
                    ),
                ],
                source_url="https://data.seoul.go.kr/dataList/OA-22586/S/1/datasetView.do",
            ),
        )

    def _fake_realtime_commercial_info(self, request: InfoContextRequest) -> InfoContextResponse:
        return InfoContextResponse(
            request_id=request.request_id,
            status="success",
            result=RealtimeCommercialInfoResult(
                status="success",
                requested_place_name=request.place_name,
                resolved_place_name=request.place_name,
                area_name="용리단길",
                area_code="POI076",
                proxy_distance_km=0.2,
                category_label="음식·음료 · 커피·음료",
                commercial_level="바쁜 시간대",
                observed_at="2026-08-20 14:00",
            ),
        )

    def _fake_place_info(self, request: InfoContextRequest) -> InfoContextResponse:
        if request.place_name not in _FAKE_ATTRACTION_NAMES:
            return InfoContextResponse(
                request_id=request.request_id,
                status="success",
                result=PlaceInfoResult(
                    status="no_data",
                    question_type=request.question_type,
                    requested_place_name=request.place_name,
                    resolved_place_name=request.place_name,
                    fields={},
                ),
            )

        fields = _FAKE_PLACE_FIELDS_BY_QUESTION_TYPE.get(request.question_type, {})
        return InfoContextResponse(
            request_id=request.request_id,
            status="success",
            result=PlaceInfoResult(
                status="success",
                question_type=request.question_type,
                requested_place_name=request.place_name,
                resolved_place_name=request.place_name,
                place_id="fake-place-id",
                destination_coordinates=Coordinates(latitude=37.5796, longitude=126.9770),
                fields=dict(fields),
                # 실제 C와 마찬가지로 주소(location_info)는 위치 해석 결과만으로
                # 답하므로 PlaceDetails를 추가 조회하지 않는다. A가 이 응답에서도
                # 최소 InfoPlaceCard를 만들도록 Runtime 회귀 테스트를 맞춘다.
                place_card=(
                    None
                    if request.question_type == "location_info"
                    else _FAKE_PLACE_CARD.model_copy(update={"place_name": request.place_name})
                ),
            ),
        )

    def _fake_event_info(self, request: InfoContextRequest) -> InfoContextResponse:
        if request.place_name not in _FAKE_ATTRACTION_NAMES:
            return InfoContextResponse(
                request_id=request.request_id,
                status="success",
                result=EventInfoResult(
                    status="no_data",
                    requested_place_name=request.place_name,
                    resolved_place_name=request.place_name,
                    reference_date=request.visit_time or now_kst().date().isoformat(),
                ),
            )

        return InfoContextResponse(
            request_id=request.request_id,
            status="success",
            result=EventInfoResult(
                status="success",
                requested_place_name=request.place_name,
                resolved_place_name=request.place_name,
                reference_date=request.visit_time or now_kst().date().isoformat(),
                events=[
                    EventItem(
                        title=f"{request.place_name} 별빛야행",
                        start_date="2026-08-01",
                        end_date="2026-08-31",
                        address=f"서울특별시 종로구 {request.place_name}",
                        distance_km=0.0,
                        is_direct_match=True,
                    ),
                    EventItem(
                        title="종로구 전통문화행사",
                        start_date="2026-08-01",
                        end_date="2026-08-10",
                        address="서울특별시 종로구",
                        distance_km=0.21,
                        is_direct_match=False,
                    ),
                ],
                has_direct_match=True,
            ),
        )


class FakeRecommendationProvider:
    """RecommendationContext.places를 그대로 고정 추천 결과로 변환하는 가짜 provider.

    excluded_place_ids 필터링은 D의 책임이라 여기서 적용한다(C는 필터링하지 않는다).
    """

    async def recommend(
        self,
        conditions: UserConditions,
        context: RecommendationContext,
        excluded_place_ids: list[str],
        limit: int = 5,
        ignore_operating_hours: bool = False,
    ) -> RecommendationResponse:
        excluded = set(excluded_place_ids)
        candidates = context.places.data if context.places and context.places.data else []
        items = [
            RecommendationItem(
                place_id=candidate.place_id,
                name=candidate.name,
                category=candidate.category,
                distance_km=0.3,
                remaining_minutes=120,
                environment_type="indoor",
                recommendation_reason="Agent Runtime 골격 검증용 고정 추천입니다.",
                explanations=[],
                warnings=[],
                score=0.5,
                feature_scores={},
                weights_used={},
            )
            for candidate in candidates
            if candidate.place_id not in excluded
        ]
        return RecommendationResponse(
            recommendations=items[:limit],
            unverified_recommendations=[],
            elapsed_ms=0,
        )

    async def rerank_with_concentration(
        self,
        conditions: UserConditions,
        context: RecommendationContext,
        first_pass: RecommendationResponse,
        concentration: CandidateEnrichmentResponse,
    ) -> RecommendationResponse:
        """(D-040 확정 — agent-runtime-contract.md §6.5.2) 1차 결과를 역순으로
        재배열해 반환한다 — 실제 재채점이 아니라, 테스트에서 "2차 Scoring이
        정말 호출돼서 순서가 바뀌었는지"를 1차 결과와 구분해 확인하기 위한
        고정 로직이다.
        """
        items = [*first_pass.recommendations, *first_pass.unverified_recommendations]
        return RecommendationResponse(
            recommendations=list(reversed(items)),
            unverified_recommendations=[],
            elapsed_ms=0,
        )


class FakeEnrichmentProvider:
    """CandidateEnrichmentService를 흉내 내는 가짜 보강 provider.

    요청된 후보 전부에 고정 집중률(success, normal/보통)을 반환한다 —
    concentration-conditions.md §2.2.3 안 B의 A→C 호출부(6-1단계)를 C/D 없이
    테스트하기 위한 것.
    """

    async def enrich(self, request: CandidateEnrichmentRequest) -> CandidateEnrichmentResponse:
        results = [
            CandidateEnrichmentResult(
                place_id=target.place_id,
                name=target.name,
                latitude=target.latitude,
                longitude=target.longitude,
                status="success",
                concentration=[
                    ConcentrationForecastData(
                        place_name=target.name,
                        forecast_date=now_kst().date().isoformat(),
                        concentration_rate=42.0,
                        concentration_level="normal",
                        concentration_label="보통",
                    )
                ],
            )
            for target in request.candidates
        ]
        return CandidateEnrichmentResponse(
            request_id=request.request_id,
            status="success",
            candidates=results,
        )


__all__ = ["FakeToolProvider", "FakeRecommendationProvider", "FakeEnrichmentProvider"]
