"""일정 편성 모듈(app.schedule)의 입력 스키마.

역할: 상태 저장소에 의존하지 않는 신규 모듈이 LLM으로 일정을 편성하는 데
필요한 입력을 정의한다. (docs/design/int-07-schedule.md 6.0~6.1절)

출력 스키마(ScheduleItem/ScheduleResult)는 app.schemas에 있다 — AgentResponse가
그 타입을 참조해야 해서(app.schemas → app.schedule 방향 import가 생기면 순환
참조가 된다), 응답에 실리는 출력 스키마는 RecommendationResponse와 같은 위치인
app.schemas에 두고, 이 모듈에는 이 모듈만 쓰는 입력 스키마만 둔다.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.schedule_travel import ScheduleTravelCandidate, SegmentWeather
from app.schedule.associations import CoVisitedHint
from app.schemas import RecommendationItem, ScheduleItem, UserConditions


class SchedulePlanningRequest(BaseModel):
    """일정 편성 LLM 호출 입력. (docs/design/int-07-schedule.md 6.1절)"""

    candidates: list[RecommendationItem]
    # D의 공개 응답 스키마(RecommendationItem)를 쓴다. D 내부 도메인 타입
    # (app.domain.scoring.RankedCandidate)은 레이어 경계를 넘어가므로 쓰지 않는다.

    must_include_place_ids: list[str] = Field(default_factory=list)
    # 사용자가 보관함에 담아 이번 일정에 반드시 들어가야 하는 장소 (SCHEDULE-12).
    # 담은 순서(오래된 것이 앞)로 넘어온다 — 활동 가능 시간이 허용하는 항목 수
    # 상한을 넘으면 planner.py가 이 순서로 앞에서부터 자르므로, 정렬을 바꾸면
    # "왜 그 곳이 빠졌는지" 설명이 달라진다(SavedPlaceList.items docstring).
    #
    # 후보 풀에 들어가는 것과 일정에 배치되는 것은 다르다 — 채점 순위에서 밀리면
    # 그대로 빠진다. 그래서 프롬프트로 지시하고 planner.py가 응답을 하드 검증한다
    # (SCHEDULE-07의 "LLM 지시 준수보다 구조적 보장을 우선한다"와 같은 철학).
    #
    # 기본값이 빈 리스트라 이 필드를 모르는 기존 호출부는 동작이 전혀 바뀌지 않는다
    # (co_visited_hints를 추가했을 때와 같은 방식).

    conditions: UserConditions
    # 기존 15개 조건 그대로 사용(time_available, transport 등 이미 있는 필드 재사용)

    visit_datetime: datetime | None = None
    # 방문 예정 시각. **운영 경로는 항상 None으로 넘긴다** — 채우는 곳이 없다.
    #
    # 사용자가 "토요일 오후 2시부터"라고 말해도 그 값이 여기 오지 않는다. 조건
    # 스키마에 시작 시각을 담을 필드가 없고, 일부러 만들지 않았다.
    #
    # **이유는 후보를 고르는 기준이 "지금"으로 고정돼 있다는 것이다.**
    # agent_runtime이 `visit_at = now_kst()`로 그 턴의 기준 시각을 잡고, 그것이
    # 폐점 필터(domain/scoring.py::_is_closed)와 날씨 조회로 들어간다. 이 필드만
    # 미래 시각으로 채우면 타임라인은 그 시각을 적는데 후보는 지금 열린 곳으로
    # 걸러진 상태가 된다 — "그 시각에 문 여는지 아무도 확인하지 않은 일정"에
    # 그 시각을 적어 넣는 셈이다. 말풍선과 화면이 다른 조건으로 말하면 반드시
    # 어긋나는 조합이 생긴다(TP-243에서 되돌린 것과 같은 이유).
    #
    # 제대로 지원하려면 폐점 필터·날씨·혼잡도까지 같은 시각 기준으로 옮겨야
    # 한다. 그건 후보 수집·채점 전반을 건드리는 별건이다.
    #
    # **그래도 필드를 지우지 않는 이유는 테스트가 시각을 고정하는 seam이기
    # 때문이다.** 테스트 89곳이 여기에 고정 시각을 넣어 타임라인 단정을
    # 결정적으로 만든다. 지우면 그 단정이 실행 시각에 따라 흔들린다.

    pairwise_distances_km: dict[tuple[str, str], float]
    # app.geo.haversine_km()로 계산해 LLM에 근거로 제공한다. RecommendationItem에는
    # 위경도가 없어(distance_km는 랭킹 기준점 기준 거리 — 보통 사용자 위치다) D
    # 응답만으로는 후보 간 거리를 못 구한다 — A가 C의 AgentContextResponse.places
    # (위경도 보유)를 place_id로 매칭해 계산해서 넘긴다. 내부 함수 인자로만 쓰여
    # JSON으로 직렬화되지 않으므로 튜플 키를 그대로 써도 된다.

    travel_candidates: list[ScheduleTravelCandidate] = Field(default_factory=list)
    # 구간 이동시간을 추정·실측하기 위한 후보 좌표 (TP-216). pairwise_distances_km와
    # 출처가 같지만 쓰임이 다르다 — 저쪽은 LLM에 주는 참고 근거이고, 이쪽은 엔진이
    # 도착시각을 계산하는 입력이다. 비어 있으면 시간표가 구간마다 폴백값을 쓰므로
    # 이 필드를 모르는 기존 호출부는 동작이 바뀌지 않는다.

    weather: SegmentWeather | None = None
    # C가 조회한 예보. 구간 이동수단 판정에 쓴다(TP-226). `conditions.weather`와
    # 다르다 — 그쪽은 사용자가 발화에서 말한 값이고 이쪽은 실제로 조회한 사실이다.
    # 비 오는 날 20분을 걷게 할지 판단하려면 발화가 아니라 조회한 값이 필요하다.
    #
    # **두 요청 스키마에 같이 둔다.** 한쪽만 채우면 같은 사용자가 전체 편성과 부분
    # 수정에서 다른 판정을 받는다. 기본값이 None이라 이 필드를 모르는 기존 호출부는
    # 동작이 바뀌지 않는다(travel_candidates를 추가했을 때와 같은 방식).

    co_visited_hints: list[CoVisitedHint] = Field(default_factory=list)
    # place_associations(D-088) 기반 "이 후보들은 실제로 함께 방문됐다" 힌트.
    # A가 채우지 않는다 — planner.py의 plan_schedule()이 co_visited_fetcher가
    # 주어졌을 때만 자체적으로 조회해 이 필드를 채운 뒤 LLM 호출 직전에
    # model_copy()로 덮어쓴다(app.schedule.associations 참고). 기본값 빈 리스트라
    # 이 필드를 모르는 기존 호출부는 동작이 전혀 바뀌지 않는다.


class SchedulePartialFillRequest(BaseModel):
    """일부 슬롯만 새로 채우는 부분 재편성 입력. (SCHEDULE-09 2단계,
    SCHEDULE-부분수정-해결방향-설계안.md 3-3절)

    REJECT_SPECIFIC 처리 전용 — pinned_items(유지할 기존 항목, order 포함)는
    그대로 최종 결과에 들어가고, target_orders에 해당하는 자리만 LLM이 새로
    고른 항목으로 채운다. LLM에게 pinned_items를 그대로 돌려달라고 요청하지
    않는다 — echo를 신뢰하는 대신 planner.py가 구조적으로 병합해 pinned
    항목이 유실·변형될 위험을 원천 차단한다(SCHEDULE-07이 개수 제약을 하드
    검증으로 옮긴 것과 같은 철학 — LLM 지시 준수보다 구조적 보장을 우선한다).
    """

    pinned_items: list[ScheduleItem]
    target_orders: list[int]
    candidates: list[RecommendationItem]
    conditions: UserConditions
    visit_datetime: datetime | None = None
    pairwise_distances_km: dict[tuple[str, str], float]

    travel_candidates: list[ScheduleTravelCandidate] = Field(default_factory=list)
    # 구간 이동시간을 추정·실측하기 위한 후보 좌표 (TP-216). pairwise_distances_km와
    # 출처가 같지만 쓰임이 다르다 — 저쪽은 LLM에 주는 참고 근거이고, 이쪽은 엔진이
    # 도착시각을 계산하는 입력이다. 비어 있으면 시간표가 구간마다 폴백값을 쓰므로
    # 이 필드를 모르는 기존 호출부는 동작이 바뀌지 않는다.

    weather: SegmentWeather | None = None
    # C가 조회한 예보. 구간 이동수단 판정에 쓴다(TP-226). `conditions.weather`와
    # 다르다 — 그쪽은 사용자가 발화에서 말한 값이고 이쪽은 실제로 조회한 사실이다.
    # 비 오는 날 20분을 걷게 할지 판단하려면 발화가 아니라 조회한 값이 필요하다.
    #
    # **두 요청 스키마에 같이 둔다.** 한쪽만 채우면 같은 사용자가 전체 편성과 부분
    # 수정에서 다른 판정을 받는다. 기본값이 None이라 이 필드를 모르는 기존 호출부는
    # 동작이 바뀌지 않는다(travel_candidates를 추가했을 때와 같은 방식).

    co_visited_hints: list[CoVisitedHint] = Field(default_factory=list)
    # SchedulePlanningRequest.co_visited_hints와 동일한 용도(D-088/D-091) — 부분
    # 재편성에서 새로 채울 자리(candidates)를 고를 때도 같은 힌트를 참고할 수
    # 있게 한다. plan_partial_schedule()이 co_visited_fetcher가 주어졌을 때만
    # pinned_items + candidates의 place_id 전체로 조회해 채운다.


class SchedulePartialLLMPlan(BaseModel):
    """generate_schedule_fill() 구조화 출력 전용 모델.

    new_items 개수는 요청마다 다른 target_orders 길이에 달려 있어 Pydantic
    Field로 정적 강제할 수 없다(ScheduleLLMPlan.items의 min_length=3/
    max_length=5와 달리 고정 범위가 아니다) — planner.py가 응답 직후
    "new_items의 order 집합 == target_orders 집합"을 직접 검증한다.
    불일치 시 llm_output_invalid로 실패 처리한다 — 다만
    ScheduleLLMPlan과 달리 provider 레벨 자동 재시도는 적용하지 않는다
    (실패 빈도가 낮을 것으로 보고 V1은 단순 실패로 처리, 필요성이 확인되면
    나중에 추가한다).
    """

    new_items: list[ScheduleLLMItem]
    # ScheduleItem이 아니라 ScheduleLLMItem이다(TP-215) — 새로 채운 자리든 유지되는
    # 자리든 시각은 병합 후 app.schedule.timeline이 전체를 한 번에 다시 계산한다.
    # 예전에는 LLM이 준 new_items의 도착시각을 앵커로 믿고 그 뒤 pinned 항목만
    # 다시 맞췄다(_resync_downstream_arrivals) — 앵커 자체가 검증되지 않은 값이라
    # 앞자리가 틀리면 전체가 조용히 틀렸다.


class ScheduleLLMItem(BaseModel):
    """LLM이 만드는 일정 항목 1건. **시각이 들어 있지 않다.** (TP-215)

    app.schemas.ScheduleItem에서 estimated_arrival·travel_to_next_min·warnings를
    뺀 형태다. 셋 다 LLM이 만들면 안 되는 값이다.

    - estimated_arrival / travel_to_next_min: 순서와 체류시간이 정해지면 나머지
      시각은 전부 결정된다. LLM이 따로 만들면 항목들의 합과 총합이 서로 맞는지
      확인하는 곳이 없어진다 — 실제로 없었다. app.schedule.timeline이 계산한다.
    - warnings: 시스템이 운영시간을 대조해 채운다(기존과 동일).

    estimated_duration_min은 **제안값**이다. app.schedule.duration의 카테고리별
    정책이 최소~최대 범위로 클램프하므로 LLM이 "카페 10분"을 주더라도 그대로
    실리지 않는다. 값을 아예 안 받지 않는 이유는 같은 카테고리 안에서도 장소마다
    적정 체류시간이 다르고(작은 전시관과 국립박물관), 그 판단은 LLM이 후보 설명을
    보고 하는 편이 낫기 때문이다 — 판단은 LLM, 계산은 코드라는 이 카드의 구분과
    같은 선이다.
    """

    order: int
    place_id: str
    place_name: str
    estimated_duration_min: int
    reason: str


class ScheduleLLMPlan(BaseModel):
    """generate_schedule_plan() 구조화 출력 전용 모델.

    basis_note는 LLM이 생성하지 않고 app.schedule.planner가 visit_datetime 값으로
    결정적으로 채운다(docs/design/int-07-schedule.md 6.2.1절) — 이 모델은 LLM 응답
    검증에만 쓰이고 AgentResponse에는 직접 실리지 않는다.

    total_duration_min도 여기 없다(TP-215). 예전에는 LLM이 준 값을 그대로 실었고,
    항목들의 체류·이동 합과 일치하는지 검사하는 곳이 없었다 — 지금은
    app.schedule.timeline이 첫 도착부터 마지막 체류 종료까지로 계산한다.

    items에는 구조적으로 min_length=1/max_length=5만 건다(max_length는
    budget.MAX_SCHEDULE_ITEMS와 같은 수여야 한다) — "이번 요청에 맞는"
    목표 개수는 사용자의 time_available에 따라 1~5 사이에서 달라져
    (budget.derive_item_range() 참고) Pydantic Field로 고정 범위를 강제할 수 없다.
    SCHEDULE-07 때는 항상 min_length=3을 걸어 "LLM이 개수 지시를 안 지킨다"는
    문제(9절)를 막았지만, 활동 가능 시간이 짧은 요청(예: "2시간 코스 짜줘")에서는
    이 고정 하한 자체가 비현실적이라는 게 SCHEDULE-10에서 확인됐다. 목표 개수
    범위는 budget.derive_item_range()가 예산 산수로 구하고
    gemini_prompts.build_schedule_planning_instruction()이
    time_available_min으로 프롬프트에 직접 지시하는 쪽으로 옮기고, 이 모델의
    구조적 제약은 "0개도 6개 이상도 아니다"라는 최소한만 남겼다. 검증 실패 시
    app.providers.gemini.py의 _call_structured()가 이미 한 번 자동 재시도한다.
    """

    items: list[ScheduleLLMItem] = Field(min_length=1, max_length=5)
    route_summary: str


__all__ = [
    "SchedulePlanningRequest",
    "ScheduleLLMItem",
    "ScheduleLLMPlan",
    "SchedulePartialFillRequest",
    "SchedulePartialLLMPlan",
]
