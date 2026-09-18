"""LLMOutput을 B(Agent State)의 StateApplyRequest로 변환한다.

역할: A(LLM 해석 결과)와 B(Agent State) 사이의 유일한 변환 지점. LLMOutput의 intent별
payload를 읽고 operations/rejected_places/reset_scope로 바꾸는 건 해석 행위이므로 A의
책임이다(llm-output-schema.md §9 확정 사항 #1). B는 LLMOutput 원본을 받지 않는다.
입력: LLMOutput, 변환 시점의 SessionContextResponse(get_session_context() 응답),
사용자 원문 발화(reset_scope 판정에 필요 — LLMOutput 자체엔 MODIFY 원문이 없다).
출력: app.state.service.StateApplyRequest(B의 apply()가 그대로 받는 요청 모델).
"""

from __future__ import annotations

from app.prompts.registry import turn_prompt_version
from app.schemas import (
    ConcentrationIntent,
    Environment,
    Intent,
    LLMOutput,
    ModifyType,
    OutputStatus,
    PlaceTag,
    PlaceType,
    UserConditions,
    WeatherIntent,
)
from app.state.operations import Operation
from app.state.schema import UserConditions as StateUserConditions
from app.state.service import RejectedPlace, SessionContextResponse, StateApplyRequest

_SINGLE_FIELDS = (
    "current_location",
    "search_center",
    "weather",
    "weather_intent",
    # (2026-08-05, B-06 완료 — PR #78) B의 FIELD_SPECS(app/state/field_spec.py)에
    # weather_intent와 동일 스펙(_single(str, OP_UPDATE, OP_REMOVE))으로 등록됐다.
    "concentration_intent",
    "transport",
    "max_travel_time",
    "time_available",
    "environment",
    "companion",
    "budget",
    # (2026-08-19) 취향 발화 원문. budget과 동일 스펙(_single(str, Update, Remove)).
    # 이 목록에서 빠지면 추출은 되는데 연산이 안 만들어져 상태 병합에서 값이
    # 조용히 사라진다 — 실제로 그렇게 한 번 놓쳤다.
    "taste_query",
    # (2026-08-22) 이동시간 출발점 판정("안국역에서" vs "안국역 근처"). taste_query와
    # 같은 이유로 이 목록에 반드시 있어야 한다.
    "travel_origin",
)
# agent-state-contract-v1.md §2.2: place_types는 Update/Remove만, place_tags는
# Add/Update/Remove 다 허용 — 둘 다 Update로 둔다. exclude_tags/special_requirements는
# Add/Remove만 허용해 Update를 보내면 unsupported_operation으로 조용히 드롭된다.
_MULTI_FIELDS_UPDATE = ("place_types", "place_tags")
_MULTI_FIELDS_ADD = ("exclude_tags", "special_requirements", "accessibility_needs")
_MULTI_FIELDS = _MULTI_FIELDS_UPDATE + _MULTI_FIELDS_ADD  # _KNOWN_FIELDS 계산용
_KNOWN_FIELDS = frozenset(_SINGLE_FIELDS) | frozenset(_MULTI_FIELDS)

# 위치 되묻기 답변은 보통 새 검색 중심점만 제공한다. 이때 LLM의 기본값
# NO_MENTION/IGNORE는 "기존 조건을 해제"가 아니라 "이번 턴에 언급하지 않음"이므로
# 앞 턴의 날씨·혼잡도 의도를 덮어쓰면 안 된다.
#
# environment도 같은 이유로 넣는다 — Environment에는 WeatherIntent의 NO_MENTION에
# 해당하는 값이 없어 "언급 안 함"과 "실내외 상관없음"이 둘 다 ANY로 뭉개진다. 되묻기
# 답변에 실내외 무관 선언이 함께 오는 경우는 드물어, ANY를 미언급으로 보는 쪽이 앞 턴의
# indoor/outdoor를 지키는 데 안전하다. 근본 방지는 추출 프롬프트가 미언급 시 null을
# 내도록 지시하는 쪽이고(gemini_prompts.py), 여기는 LLM이 그 규칙을 어겼을 때의 안전망이다.
_CLARIFICATION_DEFAULT_FIELDS = {
    "weather_intent": WeatherIntent.NO_MENTION,
    "concentration_intent": ConcentrationIntent.IGNORE,
    "environment": Environment.ANY,
}

# int-01-recommend.md §7 place_tag → place_type 매핑 (39개, conditions-schema.md §2 전문 기준).
_TAG_TO_TYPE: dict[PlaceTag, PlaceType] = {
    # attraction 하위
    PlaceTag.PARK: PlaceType.ATTRACTION,
    PlaceTag.PALACE: PlaceType.ATTRACTION,
    PlaceTag.MOUNTAIN: PlaceType.ATTRACTION,
    PlaceTag.BEACH: PlaceType.ATTRACTION,
    PlaceTag.LAKE: PlaceType.ATTRACTION,
    PlaceTag.VALLEY: PlaceType.ATTRACTION,
    PlaceTag.VIEWPOINT: PlaceType.ATTRACTION,
    PlaceTag.THEME_PARK: PlaceType.ATTRACTION,
    PlaceTag.ZOO: PlaceType.ATTRACTION,
    PlaceTag.ARBORETUM: PlaceType.ATTRACTION,
    PlaceTag.TEMPLE: PlaceType.ATTRACTION,
    PlaceTag.FORTRESS: PlaceType.ATTRACTION,
    PlaceTag.VILLAGE: PlaceType.ATTRACTION,
    PlaceTag.TRAIL: PlaceType.ATTRACTION,
    PlaceTag.TRADITIONAL_EXPERIENCE: PlaceType.ATTRACTION,
    PlaceTag.CRAFT_EXPERIENCE: PlaceType.ATTRACTION,
    PlaceTag.WELLNESS: PlaceType.ATTRACTION,
    # cultural_facility 하위
    PlaceTag.MUSEUM: PlaceType.CULTURAL_FACILITY,
    PlaceTag.ART_GALLERY: PlaceType.CULTURAL_FACILITY,
    PlaceTag.LIBRARY: PlaceType.CULTURAL_FACILITY,
    PlaceTag.PERFORMANCE_HALL: PlaceType.CULTURAL_FACILITY,
    PlaceTag.SCIENCE_MUSEUM: PlaceType.CULTURAL_FACILITY,
    PlaceTag.EXHIBITION_HALL: PlaceType.CULTURAL_FACILITY,
    # festival 하위
    PlaceTag.FESTIVAL: PlaceType.FESTIVAL,
    PlaceTag.EXHIBITION: PlaceType.FESTIVAL,
    PlaceTag.PERFORMANCE: PlaceType.FESTIVAL,
    PlaceTag.CONCERT: PlaceType.FESTIVAL,
    # shopping 하위
    PlaceTag.MARKET: PlaceType.SHOPPING,
    PlaceTag.SHOPPING_MALL: PlaceType.SHOPPING,
    PlaceTag.DUTY_FREE: PlaceType.SHOPPING,
    PlaceTag.DEPARTMENT_STORE: PlaceType.SHOPPING,
    # restaurant 하위
    PlaceTag.RESTAURANT: PlaceType.RESTAURANT,
    PlaceTag.KOREAN_FOOD: PlaceType.RESTAURANT,
    PlaceTag.JAPANESE_FOOD: PlaceType.RESTAURANT,
    PlaceTag.CHINESE_FOOD: PlaceType.RESTAURANT,
    PlaceTag.WESTERN_FOOD: PlaceType.RESTAURANT,
    PlaceTag.CAFE: PlaceType.RESTAURANT,
    PlaceTag.TEA_HOUSE: PlaceType.RESTAURANT,
    PlaceTag.BAR: PlaceType.RESTAURANT,
    PlaceTag.SNACK: PlaceType.RESTAURANT,
}

# `restaurant`는 TourAPI의 음식점 전체(content type 39)를 뜻해 카페·찻집·주점까지
# 섞인다. 사용자가 이 셋을 **직접** 말했을 때는 LLM이 place_tags를 비워도 더 좁은
# 분류를 강제한다. 다만 한식·일식처럼 LLM이 이미 더 구체적인 태그를 뽑았으면 그
# 선택을 넓히지 않는다.
_FOOD_SUBCATEGORY_TAGS = frozenset(
    {
        PlaceTag.RESTAURANT,
        PlaceTag.KOREAN_FOOD,
        PlaceTag.JAPANESE_FOOD,
        PlaceTag.CHINESE_FOOD,
        PlaceTag.WESTERN_FOOD,
        PlaceTag.CAFE,
        PlaceTag.TEA_HOUSE,
        PlaceTag.BAR,
        PlaceTag.SNACK,
    }
)
_DINING_MARKERS = ("식당", "음식점", "맛집", "밥집", "혼밥")
_CAFE_MARKERS = ("카페", "찻집")
_BAR_MARKERS = ("술집", "주점", "펍")
_CATEGORY_NEGATION_SUFFIXES = ("말고", "제외", "빼고", "빼줘", "말구")

# int-03-modify.md §8 기준. 순서가 판정 우선순위다(먼저 매칭되는 문구가 채택됨).
_RESET_SCOPE_PHRASES: tuple[tuple[str, str], ...] = (
    ("처음부터 다시", "history"),
    ("조건 다시 정할게", "soft"),
    ("조건 다시 정하고 싶어", "soft"),
    ("새로 시작", "full"),
)


def transform(
    llm_output: LLMOutput,
    session_context: SessionContextResponse,
    user_input: str,
) -> StateApplyRequest:
    """LLMOutput + 현재 세션 컨텍스트를 B가 받는 StateApplyRequest로 변환한다."""

    confirmed = llm_output.status is OutputStatus.COMPLETE
    operations: list[Operation] = []
    rejected_places: list[RejectedPlace] = []
    reset_scope: str | None = None

    if (
        llm_output.intent in (Intent.RECOMMEND, Intent.SCHEDULE)
        and llm_output.recommend is not None
    ):
        # SCHEDULE도 RECOMMEND와 동일하게 취급한다 — orchestrator.py가 SCHEDULE일 때도
        # extract_recommend_conditions()를 재사용해 llm_output.recommend를 채워주므로
        # (intent 필드만 SCHEDULE로 바꿔치기됨), 조건 병합 로직은 그대로 공유해도 된다
        # (docs/design/int-07-schedule.md 4절 "A→B: 조건 병합 (기존과 동일)").
        #
        # 새 RECOMMEND는 조건을 재생성한다(conditions-schema.md §6) — soft는 조건만
        # 초기화하고 추천/거절 이력은 유지해, 이후 MODIFY("그거 말고")가 계속 동작한다.
        #
        # 다만 직전 턴이 되묻기로 끝났다면 이번 발화는 "새 요청"이 아니라 그 되묻기에
        # 답하며 같은 요청을 완성하는 중이다. 이때 초기화하면 앞 턴에서 이미 말한
        # 조건(예: place_tags=["카페"])이 사라진다 — 초기화만 건너뛰고 연산은 그대로
        # 쓴다. _full_replace_operations()가 값이 있는 필드만 Update로 만들기 때문에,
        # 언급되지 않은 필드는 연산이 없어 자동으로 유지된다.
        # 명시적 재시작 표현("처음부터 다시" 등)은 되묻기 중이라도 새 요청으로 본다.
        answers_clarification = (
            session_context.pending_clarification is not None
            and not _has_explicit_reset_phrase(user_input)
        )
        reset_scope = None if answers_clarification else "soft"
        conditions = _with_explicit_food_subcategory(llm_output.recommend.conditions, user_input)
        operations = _full_replace_operations(
            conditions,
            preserve_clarification_defaults=answers_clarification,
        )
        # 새 RECOMMEND가 목적지·현재 위치를 전혀 언급하지 않으면, soft reset 뒤에도
        # 직전 검색 중심을 다시 적용한다. "대학로 근처" → "카페 추천해줘"처럼
        # 유형만 새로 말한 경우까지 목적지를 다시 묻게 되는 것을 막는다.
        #
        # 반대로 새 search_center/current_location을 말했거나, 명시적으로 새로 시작한
        # 발화면 기존 중심을 복원하지 않는다. search_center가 C 위치 해석에서
        # current_location보다 우선하므로, 현재 위치를 새로 말한 경우도 복원 대상에서
        # 제외해야 한다.
        existing_search_center = session_context.user_conditions.search_center
        has_new_location = (
            conditions.search_center is not None or conditions.current_location is not None
        )
        if (
            reset_scope == "soft"
            and existing_search_center is not None
            and not has_new_location
            and not _has_explicit_reset_phrase(user_input)
        ):
            operations.append(
                Operation(
                    op="Update",
                    field="search_center",
                    value=existing_search_center,
                )
            )
            # travel_origin은 그 search_center에 대한 판정이라 같은 장소가
            # 이어지는 한 함께 이어진다. "안국역에서 10분" 다음 턴 "그럼
            # 조용한 데로"가 search_center만 복원되고 travel_origin은
            # 초기화돼 기준점이 사용자 위치로 도로 바뀌는 걸 막는다.
            existing_travel_origin = session_context.user_conditions.travel_origin
            if existing_travel_origin is not None:
                operations.append(
                    Operation(
                        op="Update",
                        field="travel_origin",
                        value=existing_travel_origin,
                    )
                )

    elif llm_output.intent is Intent.MODIFY and llm_output.modify is not None:
        modify = llm_output.modify
        if modify.modify_type is ModifyType.REJECT_ALL:
            rejected_places = _rejected_from_shown(session_context, "not_interested")
        elif modify.modify_type is ModifyType.REJECT_SPECIFIC:
            # SCHEDULE-09 2단계: target_indices가 가리키는 자리만 거절로
            # 기록한다 — REJECT_ALL과 달리 나머지(유지 대상)는 손대지 않는다.
            # 후보 재구성(pinned 병합·빈 슬롯 채우기)은 agent_runtime.py가
            # target_indices를 직접 읽어 처리한다.
            rejected_places = _rejected_from_indices(
                session_context, modify.target_indices, "not_interested"
            )
        elif modify.condition_changes is not None:
            operations = _changed_field_operations(
                modify.condition_changes, modify.changed_fields, session_context
            )
            operations.extend(
                _place_tag_cleanup_operations(
                    modify.condition_changes, modify.changed_fields, session_context
                )
            )
            # CHANGE_CONDITION은 사용자가 싫어서가 아니라 조건이 바뀌어서 제외되는 것이다.
            # rejected(영구 제외)로 기록하지 않는다 — 대신 reset_scope="history"로 직전
            # 노출분(recommended)만 비워서, 조건이 되돌아오면 다시 노출될 수 있게 한다.
            # (거절 이력은 그대로 유지되므로 REJECT_ALL의 not_interested는 영향 없음.)
        reset_scope = _detect_reset_scope(user_input, modify.modify_type)

    # INFO/COMPARE/GENERAL/OUT_OF_SCOPE: operations/rejected_places/reset_scope는 비운다.
    # SCHEDULE은 위 RECOMMEND 분기에서 이미 처리된다.

    return StateApplyRequest(
        session_id=session_context.session_id,
        intent=llm_output.intent.value,
        confirmed=confirmed,
        reset_scope=reset_scope,
        operations=operations,
        rejected_places=rejected_places,
        prompt_version=turn_prompt_version(llm_output.intent),
    )


def to_user_conditions(state_conditions: StateUserConditions) -> UserConditions:
    """B↔A 변환의 유일한 지점: app.state.schema.UserConditions(B, 순수 문자열)를
    app.schemas.UserConditions(A, enum 타입)로 변환한다.

    A↔C 변환(app.services.runtime.context_transform.to_agent_context_request())과
    혼동하지 않는다 — 이 함수는 B→A 한 구간만 담당한다. D 계약이 확정되면 A↔D 변환
    함수가 또 하나 늘어날 텐데, 그때도 이 세 변환 지점을 서로 섞지 않는다.

    MODIFY 조건 추출 시(build_interpretation의 MODIFY 분기) 현재 조건을 다시 LLM에
    넘기려면 A의 enum 타입 UserConditions가 필요하다. 두 모델은 필드 이름·개수가
    완전히 동일하므로 dict 왕복으로 충분하다 — StrEnum이 문자열 값을 그대로 받아들인다.
    """

    return UserConditions.model_validate(state_conditions.model_dump())


def _serialize(value: object) -> object:
    """StrEnum(PlaceTag 등)을 순수 str/list[str]로 변환한다. int(max_travel_time/
    time_available)는 그대로 통과시킨다 — B(agent-state-contract-v1.md §2.2)가
    실제 int 타입을 기대하므로 str()로 감싸면 type_mismatch로 거부된다.

    StrEnum은 str 서브클래스라 B의 matches_type()는 이미 통과하지만, 로그·JSON 직렬화가
    항상 순수 문자열이 되도록 방어적으로 변환한다.
    """
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, int):
        return value
    return str(value)


def _with_explicit_food_subcategory(
    conditions: UserConditions,
    user_input: str,
) -> UserConditions:
    """명시 음식 업종을 TourAPI 소분류 태그로 보정한다.

    모델 출력의 place_types=[restaurant]는 음식점 전체라 카페·주점까지 포함한다.
    "카페", "술집", "식당/맛집/혼밥"은 사용자가 후보 범위를 직접 정한 말이므로
    이 단계에서 태그를 보장한다. 카페·주점이 식사 일반어보다 우선한다 —
    "혼밥 카페"를 식당 전체로 넓히면 안 된다.
    "카페 말고 식당"처럼 제외를 말한 업종은 보정 대상에서 빼고 남은 업종을 쓴다.
    """
    if any(tag in _FOOD_SUBCATEGORY_TAGS for tag in conditions.place_tags):
        return conditions

    compact = "".join(user_input.casefold().split())

    def mentioned(markers: tuple[str, ...]) -> bool:
        return any(
            marker in compact
            and not any(f"{marker}{suffix}" in compact for suffix in _CATEGORY_NEGATION_SUFFIXES)
            for marker in markers
        )

    if mentioned(_CAFE_MARKERS):
        tag = PlaceTag.CAFE
    elif mentioned(_BAR_MARKERS):
        tag = PlaceTag.BAR
    elif mentioned(_DINING_MARKERS):
        tag = PlaceTag.RESTAURANT
    else:
        return conditions

    place_types = list(conditions.place_types)
    if PlaceType.RESTAURANT not in place_types:
        place_types.append(PlaceType.RESTAURANT)
    return conditions.model_copy(update={"place_types": place_types, "place_tags": [tag]})


def _full_replace_operations(
    conditions: UserConditions,
    *,
    preserve_clarification_defaults: bool = False,
) -> list[Operation]:
    """RECOMMEND: conditions의 non-null/non-empty 필드 전부를 변환한다.

    exclude_tags/special_requirements는 B의 field_spec.py상 Add/Remove만 허용되고
    Update는 없다(agent-state-contract-v1.md §2.2) — soft reset으로 baseline이 항상
    비어 있는 RECOMMEND 경로 한정으로 Add를 replace와 동치로 쓴다. MODIFY 경로
    (_changed_field_operations())는 baseline이 비어있지 않아 이 등가성이 깨지므로
    별도로 취급한다(decision-log.md 참고).
    """

    operations: list[Operation] = []
    for field in _SINGLE_FIELDS:
        value = getattr(conditions, field)
        if preserve_clarification_defaults and _CLARIFICATION_DEFAULT_FIELDS.get(field) == value:
            continue
        if value is not None:
            operations.append(Operation(op="Update", field=field, value=_serialize(value)))
    for field in _MULTI_FIELDS_UPDATE:
        value = getattr(conditions, field)
        if value:
            operations.append(Operation(op="Update", field=field, value=_serialize(value)))
    for field in _MULTI_FIELDS_ADD:
        value = getattr(conditions, field)
        if value:
            operations.append(Operation(op="Add", field=field, value=_serialize(value)))
    return operations


def _changed_field_operations(
    condition_changes: UserConditions,
    changed_fields: list[str],
    session_context: SessionContextResponse,
) -> list[Operation]:
    """MODIFY/CHANGE_CONDITION: changed_fields에 있는 필드만 Operation으로 만든다.

    changed_fields에 없는 필드는 (condition_changes에 어떤 값이 있든) Keep이므로 건드리지
    않는다 — operations 배열에 없으면 자동 Keep(tests/state/test_service.py로 확인됨).
    """

    operations: list[Operation] = []
    for field in changed_fields:
        if field not in _KNOWN_FIELDS:
            continue  # LLM 환각 등으로 알 수 없는 필드명이 오면 무시한다.
        value = getattr(condition_changes, field)
        if value is None or value == []:
            # Update에 value=None은 B에서 null_value 오류로 거부되므로 Remove를 쓴다.
            operations.append(Operation(op="Remove", field=field))
        elif field in _MULTI_FIELDS_ADD:
            operations.extend(_list_diff_operations(field, value, session_context))
        else:
            operations.append(Operation(op="Update", field=field, value=_serialize(value)))
    return operations


def _list_diff_operations(
    field: str, final_value: list[str], session_context: SessionContextResponse
) -> list[Operation]:
    """exclude_tags/special_requirements의 최종 목록을 Remove+Add 차분으로 바꾼다.

    이 두 필드는 B의 field_spec상 Add/Remove만 허용된다(agent-state-contract-v1.md §2.2).
    LLM은 "추가/제거를 반영한 최종 목록"을 주는데(gemini_prompts의 MODIFY 병합 규칙),
    최종 목록을 그대로 Update로 보내면 unsupported_operation으로 통째로 드롭돼
    "박물관도 다시 포함해줘" 같은 부분 해제가 조용히 무시된다. 현재 값과 비교해
    빠진 항목은 Remove, 새로 든 항목은 Add로 나눠 보낸다.
    """

    current = list(getattr(session_context.user_conditions, field))
    final = [str(item) for item in final_value]

    removed = [item for item in current if item not in final]
    added = [item for item in final if item not in current]

    operations: list[Operation] = []
    if removed:
        operations.append(Operation(op="Remove", field=field, value=removed))
    if added:
        operations.append(Operation(op="Add", field=field, value=added))
    return operations


def _tag_place_type(tag_value: str) -> PlaceType | None:
    try:
        return _TAG_TO_TYPE.get(PlaceTag(tag_value))
    except ValueError:
        return None  # 알 수 없는 태그는 정리 대상에서 제외(보수적으로 그대로 둔다).


def _place_tag_cleanup_operations(
    condition_changes: UserConditions,
    changed_fields: list[str],
    session_context: SessionContextResponse,
) -> list[Operation]:
    """place_types 교체 시 소속 안 되는 place_tags에 Remove를 자동 생성한다.

    (conditions-schema.md §5 예시5) LLM이 이미 place_tags 최종값을 changed_fields에
    넘겼으면(= place_tags도 변경 필드로 포함) 여기서 다시 계산하지 않는다.
    """

    if "place_types" not in changed_fields or "place_tags" in changed_fields:
        return []

    new_types = set(condition_changes.place_types)
    current_tags = session_context.user_conditions.place_tags
    orphaned = [
        tag
        for tag in current_tags
        if (tag_type := _tag_place_type(tag)) is not None and tag_type not in new_types
    ]
    if not orphaned:
        return []
    return [Operation(op="Remove", field="place_tags", value=orphaned)]


def _rejected_from_shown(
    session_context: SessionContextResponse, reason_code: str
) -> list[RejectedPlace]:
    return [
        RejectedPlace(place_id=place_id, reason_code=reason_code)
        for place_id in session_context.shown_place_ids
    ]


def _rejected_from_indices(
    session_context: SessionContextResponse, target_indices: list[int], reason_code: str
) -> list[RejectedPlace]:
    """1-indexed target_indices가 가리키는 place_id만 거절로 기록한다.

    (SCHEDULE-09 2단계) shown_place_ids는 마지막 실행 rank 순 목록이라
    ScheduleItem.order와 같은 체계다(history.py RecommendedItem 참고). 범위를
    벗어나는 순번은 이미 파싱 단계(gemini_prompts._MODIFY_TARGET_RULES)가
    needs_clarification으로 걸러내지만, 방어적으로 여기서도 조용히 무시한다.
    """
    shown = session_context.shown_place_ids
    return [
        RejectedPlace(place_id=shown[index - 1], reason_code=reason_code)
        for index in target_indices
        if 1 <= index <= len(shown)
    ]


def _has_explicit_reset_phrase(user_input: str) -> bool:
    """사용자가 조건 초기화를 명시적으로 요청했는지. (되묻기 답변보다 우선한다)"""

    return any(phrase in user_input for phrase, _ in _RESET_SCOPE_PHRASES)


def _detect_reset_scope(user_input: str, modify_type: ModifyType) -> str | None:
    """MODIFY에서만 호출된다. reset_scope는 B가 자동 판단하지 않으므로 A가 명시한다.

    CHANGE_CONDITION은 phrase가 없어도 기본으로 "history"를 반환한다 — 조건이
    바뀌면 직전 노출분(recommended)을 비워서, 조건이 되돌아왔을 때 다시 노출될
    수 있게 한다. REJECT_ALL은 대상이 아니다 — 그쪽은 rejected 기록으로 영구
    제외를 이미 표현하므로 기본값을 None으로 유지한다.

    REJECT_SPECIFIC도 "history"다. 지목한 자리만 거절이고 나머지는 유지 대상인데,
    그 나머지가 직전 턴의 recommended로 제외 목록에 남아 있으면 다음 채점에서 함께
    빠진다 — "두 번째만 별로야"가 REJECT_ALL과 같은 결과를 내게 된다. 거절한 자리는
    rejected로 계속 제외되므로 recommended를 비워도 되살아나지 않는다.
    SCHEDULE 부분 재편성은 pinned_items가 자리를 붙들고 있어 이 값에 영향받지
    않는다 — 유지 항목은 planner가 후보에서 직접 걸러내고(planner.py) 프롬프트도
    "pinned_items의 place_id를 다시 고르지 마세요"로 지시한다(fill.md).
    """

    for phrase, scope in _RESET_SCOPE_PHRASES:
        if phrase in user_input:
            return scope
    if modify_type in (ModifyType.CHANGE_CONDITION, ModifyType.REJECT_SPECIFIC):
        return "history"
    return None


__all__ = ["transform", "to_user_conditions"]
