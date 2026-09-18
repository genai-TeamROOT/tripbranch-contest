"""프롬프트 스냅샷 테스트가 쓰는 고정 입력.

역할: 각 프롬프트 빌더를 항상 같은 값으로 호출해, 렌더 결과를 바이트 단위로 비교할 수
있게 한다. 프롬프트 본문을 Python f-string에서 Markdown 자산으로 옮기는 이관이 텍스트를
바꾸지 않았음을 증명하는 것이 목적이므로, **이 값들은 동결 대상이다** — 값을 바꾸면
스냅샷이 통째로 바뀌어 이관 검증 능력이 사라진다.

날짜·시각은 실행 시점에 의존하지 않도록 전부 상수로 고정한다.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.schedule.schemas import SchedulePartialFillRequest, SchedulePlanningRequest
from app.schemas import (
    CompareCriteria,
    Environment,
    GeneralTopic,
    Intent,
    PlaceTag,
    RecommendationItem,
    ScheduleItem,
    UserConditions,
    WeatherIntent,
)

KST = ZoneInfo("Asia/Seoul")

REFERENCE_DATE = date(2026, 8, 19)
VISIT_DATETIME = datetime(2026, 8, 19, 14, 0, tzinfo=KST)
START_TIME = "14:00"

SHOWN_PLACE_NAMES = ["경복궁", "두가헌 레스토랑", "국립고궁박물관"]


def candidate(place_id: str, name: str, *, distance_km: float, category: str) -> RecommendationItem:
    return RecommendationItem(
        place_id=place_id,
        name=name,
        category=category,
        distance_km=distance_km,
        remaining_minutes=180,
        environment_type="outdoor" if category == "attraction" else "indoor",
        recommendation_reason=f"{name}은(는) 조건에 맞는 장소예요.",
        explanations=["현재 위치에서 가까운 장소예요."],
        warnings=[],
        score=0.9,
        feature_scores={"distance": 0.9},
        weights_used={"distance": 1.0},
    )


def candidates() -> list[RecommendationItem]:
    return [
        candidate("place-1", "경복궁", distance_km=0.4, category="attraction"),
        candidate("place-2", "국립고궁박물관", distance_km=0.8, category="cultural_facility"),
        candidate("place-3", "두가헌 레스토랑", distance_km=1.2, category="restaurant"),
    ]


def current_conditions() -> UserConditions:
    return UserConditions(
        search_center="경복궁",
        place_tags=[PlaceTag.CAFE],
        environment=Environment.INDOOR,
        weather_intent=WeatherIntent.AVOID,
        time_available=240,
    )


def planning_request() -> SchedulePlanningRequest:
    items = candidates()
    return SchedulePlanningRequest(
        candidates=items,
        conditions=current_conditions(),
        visit_datetime=VISIT_DATETIME,
        pairwise_distances_km={
            ("place-1", "place-2"): 0.6,
            ("place-1", "place-3"): 1.1,
            ("place-2", "place-3"): 0.7,
        },
    )


def planning_request_with_must_include() -> SchedulePlanningRequest:
    """보관함에 담긴 장소가 있는 요청. (SCHEDULE-12)

    [반드시 포함] 섹션이 실제로 이름과 함께 렌더링되는지 스냅샷으로 잠근다 —
    빈 경우("(없음)")는 planning_request()가 이미 덮는다.
    """

    return planning_request().model_copy(
        update={"must_include_place_ids": ["place-2", "place-3"]}
    )


def fill_request() -> SchedulePartialFillRequest:
    return SchedulePartialFillRequest(
        pinned_items=[
            ScheduleItem(
                order=1,
                place_id="place-1",
                place_name="경복궁",
                estimated_arrival="14:00",
                estimated_duration_min=90,
                travel_to_next_min=15,
                reason="첫 순서로 적합한 대표 관광지예요.",
            )
        ],
        target_orders=[2],
        candidates=candidates(),
        conditions=current_conditions(),
        visit_datetime=VISIT_DATETIME,
        pairwise_distances_km={("place-1", "place-2"): 0.6},
    )


# --- 빌더별 호출 케이스 ---------------------------------------------------
# (스냅샷 파일명, 빌더 이름, kwargs) 형태. 분기가 있는 빌더는 분기마다 한 줄씩 둔다.

CLASSIFY_CASES: list[tuple[str, dict[str, object]]] = [
    (
        "classify__default",
        {"has_previous_recommendation": False, "shown_place_count": 0},
    ),
    (
        "classify__with_history",
        {"has_previous_recommendation": True, "shown_place_count": 5},
    ),
    (
        "classify__schedule_clarification_pending",
        {
            "has_previous_recommendation": False,
            "shown_place_count": 0,
            "pending_clarification": "location_ambiguous",
            "last_intent": "SCHEDULE",
        },
    ),
    (
        "classify__location_clarification_pending",
        {
            "has_previous_recommendation": True,
            "shown_place_count": 5,
            "pending_clarification": "location_required",
            "last_intent": "RECOMMEND",
        },
    ),
    (
        "classify__with_shown_names",
        {
            "has_previous_recommendation": True,
            "shown_place_count": 3,
            "shown_place_names": SHOWN_PLACE_NAMES,
        },
    ),
    (
        "classify__with_conversation_place",
        {
            "has_previous_recommendation": True,
            "shown_place_count": 3,
            "conversation_place_name": "경복궁",
        },
    ),
    (
        "classify__info_clarification_pending",
        {
            "has_previous_recommendation": True,
            "shown_place_count": 3,
            "pending_clarification": "missing:place_name",
            "last_intent": "INFO",
        },
    ),
    (
        "classify__schedule06_choice_pending",
        {
            "has_previous_recommendation": False,
            "shown_place_count": 0,
            "pending_clarification": "schedule06_ambiguous_recommend",
            "last_intent": "SCHEDULE",
        },
    ),
]

MODIFY_CASES: list[tuple[str, dict[str, object]]] = [
    ("modify_extract__default", {}),
    (
        "modify_extract__with_shown_names",
        {"shown_place_count": 3, "shown_place_names": SHOWN_PLACE_NAMES},
    ),
    (
        "modify_extract__location_clarification",
        {"pending_clarification": "location_required"},
    ),
]

COMPARE_CASES: list[tuple[str, dict[str, object]]] = [
    ("compare_extract__count_only", {"shown_place_count": 3}),
    (
        "compare_extract__with_names",
        {"shown_place_count": 3, "shown_place_names": SHOWN_PLACE_NAMES},
    ),
]

INFO_CASES: list[tuple[str, dict[str, object]]] = [
    (
        "info_extract__default",
        {"has_previous_recommendation": False, "reference_date": REFERENCE_DATE},
    ),
    (
        "info_extract__with_conversation_place",
        {
            "has_previous_recommendation": True,
            "reference_date": REFERENCE_DATE,
            "conversation_place_name": "경복궁",
        },
    ),
    (
        "info_extract__pending_question",
        {
            "has_previous_recommendation": False,
            "reference_date": REFERENCE_DATE,
            "pending_info_question_type": "concentration",
            "pending_info_specific_question": "사람많아?",
        },
    ),
]

SCHEDULE_PLAN_CASES: list[tuple[str, dict[str, object]]] = [
    ("schedule_plan__no_limit", {"item_range": (3, 5)}),
    # 240분·관광지 후보 5곳·이동 15분이면 budget.derive_item_range()가 (2, 3)을 준다.
    ("schedule_plan__with_time_available", {"time_available_min": 240, "item_range": (2, 3)}),
    # 도보로 붙어 있는 후보가 있으면 짧게 제안해 달라는 부탁이 한 문단 붙는다(TP-243).
    (
        "schedule_plan__clustered_candidates",
        {"time_available_min": 180, "item_range": (2, 4), "clustered_candidates": True},
    ),
]

GENERAL_ANSWER_TOPICS = [
    ("general_answer__service_identity", GeneralTopic.SERVICE_IDENTITY),
]

INFO_ANSWER_QUESTION_TYPES = [
    ("info_answer__operating_hours", "operating_hours"),
]

SUMMARY_INTENTS = [
    ("recommend_summary__recommend", Intent.RECOMMEND),
    ("recommend_summary__modify", Intent.MODIFY),
]

COMPARE_SUMMARY_CRITERIA = [
    ("compare_summary__travel_time", CompareCriteria.TRAVEL_TIME),
    ("compare_summary__overall", CompareCriteria.OVERALL),
]
