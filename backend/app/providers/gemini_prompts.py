"""Gemini 구조화 출력 호출에 쓰는 system instruction 모음.

역할: intent-definition.md / conditions-schema.md / int-01~05 문서의 판별 규칙과 추출
규칙을 LLM system instruction 문자열로 옮긴다. 호출/재시도/에러 처리 같은 코드는
app/providers/gemini.py에 두고, 이 모듈은 프롬프트 텍스트만 담아 gemini.py가
비대해지지 않게 한다.
호출 시점: RealGeminiProvider의 각 메서드가 build_* 함수로 동적 컨텍스트(이전 추천
이력 여부, 현재 조건 등)를 채운 system instruction을 만들 때 사용한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from app.domain.schedule_travel import ModeJudgmentContext, SegmentModeInput
from app.prompts.loader import active_variant, load_text, render_text
from app.schedule.associations import CoVisitedHint
from app.schedule.budget import SCHEDULE_CLUSTER_WALK_MINUTES
from app.schedule.duration import CLUSTERED_VISIT_MINIMUM_MIN
from app.schedule.schemas import (
    SchedulePartialFillRequest,
    SchedulePlanningRequest,
)
from app.schemas import (
    CompareCriteria,
    GeneralTopic,
    Intent,
    RecommendationItem,
    UserConditions,
)

# B의 LLMOps Trace(record_trace(prompt_version=...))와 StateApplyRequest.prompt_version에
# 넘길 값 — backend/docs/package-b/llmops-trace-contract-v1.md §7 Q2. B는 이 값의 의미를
# 해석하지 않고 문자열로만 저장한다(B-01 경계 원칙). app/domain/scoring.py의
# SCORING_VERSION과 동일한 semver 패턴. record_trace(step="llm_interpret", ...)는 턴당
# 한 번만 호출되고(agent_runtime.py) 이 모듈의 6개 build_*_instruction() 함수 중 어느 게
# 쓰였는지와 무관하게 단일 값으로 취급한다 — 함수별 개별 버전은 만들지 않는다. 판별·추출
# 규칙에 영향을 주는 변경(6개 함수 중 하나라도) 시 버전을 올린다 — 사소한 문구·주석
# 변경은 올리지 않는다.
_BASE_PROMPT_VERSION = "agent-interpret-prompts-1.0.31"
_ACTIVE_PROMPT_VARIANT = active_variant()
PROMPT_VERSION = (
    _BASE_PROMPT_VERSION
    if _ACTIVE_PROMPT_VARIANT == "current"
    else f"{_BASE_PROMPT_VERSION}+{_ACTIVE_PROMPT_VARIANT}"
)

CHATBOT_NAME = "트리비"


def build_intent_classification_instruction(
    *,
    has_previous_recommendation: bool,
    shown_place_count: int,
    pending_clarification: str | None = None,
    last_intent: str | None = None,
    shown_place_names: list[str] | None = None,
    conversation_place_name: str | None = None,
) -> str:
    """intent-definition.md §5(판별 우선순위·맥락 의존 판별·경계 사례) 기반 system instruction.

    shown_place_names는 SCHEDULE-09 후속(이름 지목)에서 추가됐다 — "두가헌
    레스토랑은 빼줘"처럼 순번 없이 노출된 항목 이름만 언급해도 MODIFY로 판단할
    근거를 준다. 없으면(이름 미저장 과거 세션 등) 이 블록은 생략된다.
    """

    # schedule06_ambiguous_recommend는 last_intent와 무관하게 코드 자체로 특정된다
    # (agent_runtime.py SCHEDULE-06 모호 되묻기) — 두 선택지가 SCHEDULE 계속/RECOMMEND
    # 전환으로 서로 다른 인텐트라서, 아래 schedule_clarification_pending의 "SCHEDULE
    # 유지" 지시를 그대로 적용하면 "추천만 해줘" 같은 답까지 SCHEDULE로 잘못 강제된다 —
    # 그래서 이 검사를 먼저 한다.
    schedule06_choice_pending = pending_clarification == "schedule06_ambiguous_recommend"
    schedule_clarification_pending = (
        last_intent == "SCHEDULE" and pending_clarification is not None
    )
    location_clarification_pending = (
        last_intent in {"RECOMMEND", "MODIFY"}
        and pending_clarification in {"location_required", "location_ambiguous"}
    )
    # INFO 되묻기(장소명 없음/장소 후보 모호)는 last_intent="INFO"와 pending_clarification이
    # 함께 있을 때만 성립한다 — B의 apply()가 매 턴 last_intent를 그 턴의 원본 intent로
    # 저장하므로(D-061), INFO가 되물은 다음 턴엔 항상 last_intent="INFO"다.
    info_clarification_pending = last_intent == "INFO" and pending_clarification is not None
    clarification_status = (
        "예 (직전 질문: 일정 계속 진행 vs 장소만 추천 중 선택)"
        if schedule06_choice_pending
        else "예 (직전 SCHEDULE 요청의 되묻기)"
        if schedule_clarification_pending
        else "예 (직전 RECOMMEND/MODIFY 요청의 위치 되묻기)"
        if location_clarification_pending
        else "예 (직전 INFO 요청의 되묻기)"
        if info_clarification_pending
        else "아니오"
    )
    shown_names_line = ""
    if shown_place_names and any(name for name in shown_place_names):
        joined = ", ".join(name for name in shown_place_names if name)
        shown_names_line = f"\n- 현재 노출된 항목 이름: {joined}"
    conversation_place_line = ""
    if conversation_place_name:
        conversation_place_line = (
            f"\n- 직전 INFO 상세 카드 장소: {conversation_place_name}\n"
            "  사용자가 이 장소를 '여기', '이곳', '거기', '이리로'처럼 가리키며 "
            "운영시간·주차·요금·위치·찾아가는 시간 등을 묻는다면 INFO다. "
            "이번 발화에 다른 장소명을 직접 말하면 그 명시 장소를 우선한다."
        )

    return render_text(
        "router/classify.md",
        intent_definitions=load_text("router/intent_definitions.md"),
        intent_priority=load_text("router/intent_priority.md"),
        context_rules=load_text("router/context_rules.md"),
        boundary_cases=load_text("router/boundary_cases.md"),
        # interaction_mode는 Intent와 직교하는 별개 축이라 판별 규칙도 따로
        # 둔다(대화층 2단계) — 인텐트 우선순위 캐스케이드에 섞으면 GENERAL이
        # 다시 만능 라벨이 된다.
        interaction_mode=load_text("router/interaction_mode.md"),
        # 이력은 이미 contents로 전달되는데(gemini.py의 _build_contents) 지금까지
        # 프롬프트는 그 존재를 몰랐다 — 그래서 "지명 단독 → MODIFY"처럼 명시된 규칙만
        # 이겼고, 새 후속 발화 패턴마다 규칙을 손으로 추가해야 했다. 이 조각이 그
        # 사용법을 한 번에 정한다(강의교재 36강의 "이력은 맥락(what)" 역할).
        conversation_history=load_text("_shared/rules/conversation_history.md"),
        # OUT_OF_SCOPE 판정 자체는 분류기가 하지만, 규칙 문구의 소유는
        # out_of_scope/에 둔다 — 그래야 해당 인텐트 담당자도 자기 폴더만 열고
        # 수정·이력 관리를 할 수 있다.
        out_of_scope_rules=load_text("out_of_scope/classify.md"),
        has_previous_recommendation="있음" if has_previous_recommendation else "없음",
        shown_place_count=shown_place_count,
        clarification_status=clarification_status,
        shown_names_line=shown_names_line,
        conversation_place_line=conversation_place_line,
    )


def build_recommend_extraction_instruction() -> str:
    """int-01-recommend.md §5~9,12(위치 처리, place_types/tags, weather_intent) 기반."""

    return render_text(
        "recommend/extract.md",
        location_rules=load_text("recommend/location_rules.md"),
        place_tag_rules=load_text("recommend/place_tag_rules.md"),
        transport_rules=load_text("_shared/rules/transport.md"),
        weather_intent_rules=load_text("_shared/rules/weather_intent.md"),
        concentration_rules=load_text("_shared/rules/concentration_intent.md"),
        environment_rules=load_text("_shared/rules/environment.md"),
        budget_rule=load_text("_shared/rules/budget.md"),
        accessibility_needs_rules=load_text("_shared/rules/accessibility_needs.md"),
        conversation_history=load_text("_shared/rules/conversation_history.md"),
    )


def _shown_place_list_block(shown_place_names: list[str] | None) -> str:
    """rank 순 이름 목록을 "1. 이름" 형태의 프롬프트 블록으로 만든다.

    이름이 하나도 없으면(과거 세션이라 저장이 안 됐거나 호출부가 안 넘김) 빈
    문자열을 돌려준다 — 목록 없이 이름 매칭을 시키면 모델이 근거 없이 순번을
    지어낸다. MODIFY와 COMPARE가 같은 형식을 쓰도록 한 곳에 둔다.
    """
    if not shown_place_names or not any(name for name in shown_place_names):
        return ""
    numbered = "\n".join(
        f"{index}. {name}" for index, name in enumerate(shown_place_names, start=1) if name
    )
    return (
        "\n"
        + render_text("_shared/rules/shown_place_list.md", shown_place_names=numbered)
        + "\n"
    )


def build_modify_extraction_instruction(
    current_conditions: UserConditions,
    *,
    pending_clarification: str | None = None,
    shown_place_count: int = 0,
    shown_place_names: list[str] | None = None,
) -> str:
    """int-03-modify.md §6,7,9,12(REJECT_ALL/CHANGE_CONDITION, 병합, "더 ~한 곳") 기반.

    shown_place_count는 SCHEDULE-09(부분 수정)에서 추가됐다. REJECT_SPECIFIC의
    target_indices가 실제 노출 범위를 벗어나는지 판별하는 데 쓰인다 —
    build_compare_extraction_instruction의 shown_place_count와 같은 역할이다.
    shown_place_names는 SCHEDULE-09 후속(이름 지목)에서 추가됐다 — rank 순
    이름 목록을 "1. 이름\\n2. 이름..." 형태로 프롬프트에 포함해 "두가헌
    레스토랑은 빼줘"처럼 이름으로 지목한 경우도 target_indices로 변환하게
    한다. 이름이 없거나(과거 세션) 전달되지 않으면 이 블록은 생략된다 —
    Gemini가 목록 없이 이름 매칭을 시도해 엉뚱한 순번을 만들어내는 것을
    막는다.
    """

    return render_text(
        "modify/extract.md",
        current_json=current_conditions.model_dump_json(indent=2),
        location_clarification_answer=(
            "예"
            if pending_clarification in {"location_required", "location_ambiguous"}
            else "아니오"
        ),
        shown_list_block=_shown_place_list_block(shown_place_names),
        type_rules=load_text("modify/type_rules.md"),
        target_rules=load_text("modify/target_rules.md"),
        shown_place_count=shown_place_count,
        relative_expression_rules=load_text("modify/relative_expression_rules.md"),
        field_merge_rules=load_text("modify/field_merge_rules.md"),
        accessibility_needs_rules=load_text("_shared/rules/accessibility_needs.md"),
        transport_rules=load_text("_shared/rules/transport.md"),
        weather_intent_rules=load_text("_shared/rules/weather_intent.md"),
        concentration_rules=load_text("_shared/rules/concentration_intent.md"),
        environment_rules=load_text("_shared/rules/environment.md"),
        budget_rule=load_text("_shared/rules/budget.md"),
        conversation_history=load_text("_shared/rules/conversation_history.md"),
    )


def _build_visit_time_rules(reference_date: date) -> str:
    """concentration-conditions.md §3.2. reference_date는 오늘(KST)."""

    return render_text("info/visit_time_rules.md", reference_date=reference_date.isoformat())


def _build_pending_info_block(
    question_type: str | None,
    specific_question: str | None,
    visit_time: str | None,
) -> str:
    """직전 INFO 되묻기(장소명 없음)가 이미 파악해둔 질문 정보를 프롬프트 블록으로 만든다.

    셋 다 없으면(직전이 INFO 되묻기가 아니었으면) 빈 문자열 — shown_names_line과 같은
    패턴으로, 관련 없는 턴에는 프롬프트에 아무것도 추가하지 않는다.
    """
    if not question_type and not specific_question:
        return ""
    return (
        "\n"
        + render_text(
            "info/pending_question_block.md",
            pending_question_type=question_type or "알 수 없음",
            pending_specific_question=specific_question or "알 수 없음",
            pending_visit_time=visit_time or "없음",
        )
        + "\n"
    )


def build_info_extraction_instruction(
    *,
    has_previous_recommendation: bool,
    reference_date: date,
    conversation_place_name: str | None = None,
    pending_info_question_type: str | None = None,
    pending_info_specific_question: str | None = None,
    pending_info_visit_time: str | None = None,
) -> str:
    """int-02-info.md §4~7(InfoQuery, question_type, place_context) 기반."""

    return render_text(
        "info/extract.md",
        question_type_rules=load_text("info/question_type_rules.md"),
        place_context_rules=load_text("info/place_context_rules.md"),
        visit_time_rules=_build_visit_time_rules(reference_date),
        conversation_history=load_text("_shared/rules/conversation_history.md"),
        has_previous_recommendation="있음" if has_previous_recommendation else "없음",
        conversation_place_name=conversation_place_name or "없음",
        pending_question_block=_build_pending_info_block(
            pending_info_question_type, pending_info_specific_question, pending_info_visit_time
        ),
    )


def build_compare_extraction_instruction(
    *,
    shown_place_count: int,
    shown_place_names: list[str] | None = None,
) -> str:
    """int-04-compare.md §5~7(targets, criteria) 기반.

    shown_place_names는 이름 지목("백인제가옥이랑 가회민화박물관 비교해줘")을
    순번으로 옮기기 위해 필요하다. 개수만 주던 때는 모델이 이름과 순번의 대응을
    알 방법이 없어 임의로 "all"이나 [1, 2]를 만들어냈고, 그래서 사용자가 지목한
    적 없는 장소가 비교 대상에 섞였다. MODIFY의 target_indices가 같은 이유로
    이미 이 목록을 받고 있다(SCHEDULE-09 후속).
    """

    return render_text(
        "compare/extract.md",
        shown_list_block=_shown_place_list_block(shown_place_names),
        target_rules=load_text("compare/target_rules.md"),
        criteria_rules=load_text("compare/criteria_rules.md"),
        conversation_history=load_text("_shared/rules/conversation_history.md"),
        shown_place_count=shown_place_count,
    )


def build_general_extraction_instruction() -> str:
    """int-05-general.md §5~6(GeneralRequest, topic) 기반.

    situation_rules는 대화층 3단계(docs/design/conversational-layer.md) — situation은
    닫힌 목록(SituationKind)이라 여기서는 값 설명만 주고, 그 상황에서 무엇을 제안할지는
    app.services.interpret.situational_offers가 코드로만 정한다.
    """

    return render_text(
        "general/extract.md",
        topic_rules=load_text("general/topic_rules.md"),
        situation_rules=load_text("general/situation_rules.md"),
        conversation_history=load_text("_shared/rules/conversation_history.md"),
    )


def build_general_answer_instruction(
    topic: GeneralTopic, *, offer_content: str | None = None
) -> str:
    """GENERAL 발화에 실제로 답하는 system instruction(docs/design/agent-response-
    generation.md §3/§6 — 6개 Intent 중 실제 LLM 자유생성이 필요한 유일한 지점).

    build_general_extraction_instruction()과 별개 호출이다 — 저건 topic·situation만
    분류하고, 이건 그 결과가 확정된 뒤 실제 답변 문장을 만든다.

    offer_content는 대화층 3단계(conversational-layer.md) 제안 문구다.
    situational_offers.offer_for()가 상황에 맞는 도움을 찾았을 때만 채워진다 — 무엇을
    제안할지는 이미 코드가 정했고, 여기서는 그 내용을 트리비 말투로 자연스러운 질문
    문장으로 바꾸는 것만 LLM에 맡긴다("제안 문장은 extract가 아니라 답변 단계가 쓴다",
    conversational-layer.md 3단계).
    """

    offer_block = (
        f'- 이번 답변 마지막에 "{offer_content}을(를) 찾아드릴까요?"처럼, 지금 상황에 맞는 '
        "도움을 자연스러운 질문 한 문장으로 제안하며 마무리하세요. 위 2~4문장 안에 포함되는 "
        "문장이지, 별도로 덧붙이는 문장이 아닙니다. 이 제안 외에 다른 도움을 새로 지어내지 "
        "마세요."
        if offer_content
        else ""
    )
    return render_text(
        "general/answer_instruction.md",
        chatbot_name=CHATBOT_NAME,
        persona=load_text("_shared/persona/trivi.md"),
        topic=topic.value,
        offer_block=offer_block,
    )


def build_info_answer_instruction(question_type: str) -> str:
    """검증된 INFO fields만 사용자용 안내문으로 바꾸는 system instruction."""

    return render_text(
        "info/answer_instruction.md",
        chatbot_name=CHATBOT_NAME,
        persona=load_text("_shared/persona/trivi.md"),
        question_type=question_type,
    )


def build_review_evidence_filter_instruction() -> str:
    """후기 문장 중 쓸 수 있는 것만 번호로 고르게 하는 system instruction.

    **페르소나를 꽂지 않는다.** 사용자에게 보이는 문장을 만드는 호출이 아니라
    판정만 하는 호출이라 말투가 필요 없다 — `build_place_reason_instruction()`이
    입력 토큰을 이유로 뺀 것과 같은 판단이다.
    """

    return render_text("info/review_evidence_filter.md")


def build_review_answer_instruction() -> str:
    """선별된 후기 문장만 근거로 답변을 만드는 system instruction."""

    return render_text(
        "info/review_answer_instruction.md",
        chatbot_name=CHATBOT_NAME,
        persona=load_text("_shared/persona/trivi.md"),
    )


def format_mode_judge_context(
    segments: Sequence[SegmentModeInput], context: ModeJudgmentContext
) -> str:
    """구간 표와 공유 조건을 판정 LLM이 읽을 텍스트로 만든다. (TP-227)

    **조회하지 못한 값은 줄 자체를 넣지 않는다.** "날씨: 없음"처럼 적으면 모델이
    그것을 사실로 읽고 판단에 쓴다 — 날씨를 모르는 것과 날씨가 없는 것은 다르다.

    도보 시간에 "직선 기준"을 붙여 둔다. 값 자체에 우회계수를 곱하지 않는 이유는
    규칙 폴백 경로(`_select_mode()`)가 쓰는 값과 갈리면 같은 구간을 두 자로 재게
    되기 때문이다(D-118이 시간 예산을 두고 지킨 원칙과 같다).
    """

    lines: list[str] = ["## 조건"]
    if context.companion is not None:
        lines.append(f"- 동행: {context.companion}")
    if context.accessibility_needs:
        lines.append(f"- 무장애 요구: {', '.join(context.accessibility_needs)}")
    weather = context.weather
    if weather is not None:
        facts = [
            f"강수 {weather.precipitation}" if weather.precipitation else None,
            f"하늘 {weather.sky}" if weather.sky else None,
            (
                f"기온 {weather.temperature_celsius:g}도"
                if weather.temperature_celsius is not None
                else None
            ),
        ]
        stated = [fact for fact in facts if fact is not None]
        if stated:
            lines.append(f"- 날씨: {', '.join(stated)}")
    if len(lines) == 1:
        lines.append("- 없음 (거리만 보고 정한다)")

    lines.append("")
    if context.sequential:
        lines.append("## 구간 (순서대로 이어진다)")
    else:
        lines.append("## 후보 (서로 대안이다. 사용자는 이 중 한 곳만 간다)")
    for segment in segments:
        lines.append(
            f"{segment.order}. {segment.distance_m}m"
            f" (직선 기준 도보 {segment.walk_minutes:g}분)"
        )
    return "\n".join(lines)


def build_closure_extraction_instruction() -> str:
    """휴무 원문에서 정기 휴무를 뽑는 system instruction. (TP-231)

    적재 배치에서만 부른다 — 읽기 경로는 저장된 결과를 쓴다
    (`resolve_operating_schedule()`). 그래서 지연이 사용자 요청에 붙지 않는다.
    """

    return render_text(
        "closure/extract.md",
        pattern_rules=load_text("closure/pattern_rules.md"),
    )


def build_mode_judge_instruction() -> str:
    """구간 이동수단을 정하는 system instruction. (TP-227)

    특정 Intent에 매이지 않는다 — 일정 편성(SCHEDULE)과 추천(RECOMMEND)이 같은
    판정을 쓴다. 두 임계값이 환산 관계라(도보 20분 x 우회계수 1.65배 = 직선
    0.85km, D-118) 한쪽만 다른 판정을 쓰면 같은 거리를 두고 서로 다른 이동수단을
    말하게 된다.

    `prompts/schedule/`에 두지 않은 이유가 이것이다. 그쪽은 OWNERS.md상 B 소유이고
    담는 내용도 "장소 순서와 머무는 시간"이라 다른 질문인데, 거기 두면 추천이 일정
    프롬프트를 읽는 모양이 된다.
    """

    return render_text(
        "mode_judge/select_instruction.md",
        mode_rules=load_text("mode_judge/mode_rules.md"),
        condition_rules=load_text("mode_judge/condition_rules.md"),
    )


def build_follow_up_suggestion_instruction(
    *, max_suggestions: int, max_label_length: int
) -> str:
    """방금 끝난 턴을 보고 다음 발화 후보를 만드는 system instruction.

    다른 build_*와 달리 특정 Intent에 매이지 않는다 — 어떤 Intent로 끝난 턴이든 그
    뒤에 한 번 돈다. 대신 `follow_up/capability_rules.md`가 서비스가 실제로 처리할 수
    있는 요청 목록을 싣는다. 버튼을 누르면 그 문구가 그대로 사용자 발화로 전송되므로,
    모델이 없는 기능을 권하면 사용자는 곧바로 OUT_OF_SCOPE 답변을 받게 된다.

    개수·길이 상한을 지침에도 넣지만 이걸 지켰는지는 호출부
    (`services/runtime/follow_up_suggester.py`)가 코드로 다시 검사한다 — 여기 적은
    상한은 부탁이고, 그쪽 검사가 실제 계약이다.
    """

    return render_text(
        "follow_up/suggest_instruction.md",
        chatbot_name=CHATBOT_NAME,
        persona=load_text("_shared/persona/trivi.md"),
        capabilities=load_text("follow_up/capability_rules.md"),
        max_suggestions=str(max_suggestions),
        max_label_length=str(max_label_length),
    )


_COMPANION_LABELS = {
    "solo": "혼자",
    "couple": "연인과",
    "friend": "친구와",
    "parent": "부모님과",
    "child": "아이와",
    "pet": "반려동물과",
}
_ENVIRONMENT_LABELS = {"indoor": "실내", "outdoor": "실외"}
_TRANSPORT_LABELS = {"walk": "도보", "public": "대중교통", "car": "자동차"}
# 무장애 어휘를 말풍선이 읽을 한국어로 옮긴다.
#
# **9개를 전부 싣는다.** 이동수단 판정(TP-227)이 셋만 넘기는 것과 다른 판단이다 —
# 저쪽은 "어떻게 갈까"를 정하는 자리라 장소 조건이 판단을 흐리지만, 말풍선은 사용자가
# 무엇을 요구했는지를 말투와 강조점에 반영하는 자리라 요구한 것을 다 알아야 한다.
# 장애인 화장실을 찾는 사람에게 그 요구를 모른 채 답하면 같은 문제가 난다.
#
# 어휘가 늘면 여기도 늘린다. 없는 값은 원문을 그대로 쓰므로 조용히 사라지지는 않는다.
_ACCESSIBILITY_LABELS = {
    "wheelchair_access": "휠체어 접근",
    "stroller_access": "유모차 접근",
    "accessible_restroom": "장애인 화장실",
    "accessible_parking": "장애인 주차구역",
    "visual_guide": "점자·음성 안내",
    "infant_facilities": "유아 시설",
    "wheelchair_rental": "휠체어 대여",
    "seating_available": "의자식 좌석",
    "low_floor_transit": "저상버스·역 엘리베이터",
}


def _stated_conditions_line(conditions: UserConditions | None) -> str:
    """사용자가 지금까지 말한 조건을 한 줄로 요약한다(말풍선 톤 조정용).

    누적 조건은 강의교재 36강이 말하는 "오래된 중요 정보의 압축 요약"에 해당한다 —
    최근 5턴 창 밖으로 밀려나도 살아 있어서, 원문 이력만 보는 것보다 견고하다.
    말풍선 생성 단계는 지금까지 카드 데이터만 받아서, 동행을 friend로 잡아 놓고도
    "혼자서도 가기 좋고"로 답하는 일이 있었다(2026-08-31 실사용).

    **무장애가 빠져 같은 사고가 한 번 더 났다**(2026-09-03 실사용). "휠체어 타고
    관광할 수 있는 곳"에 "아이와 함께 걸어서 편하게 이동할 수 있는"이라고 답했다 —
    조건이 비어 있으니 강조점을 정할 근거가 없어, 남은 재료 중 제일 강한 신호인
    `review_evidence`("아이와 함께 걷기 좋은")를 잡고 문단 전체를 그쪽으로 썼다.
    조건 추출과 무장애 검색은 정상이었고 말풍선만 틀렸다.

    **조건을 하나 더할 때는 여기도 함께 본다.** 이 함수가 비면 오류가 아니라 엉뚱한
    강조점이 나오고, 그건 테스트로도 로그로도 안 잡힌다.

    비어 있으면 빈 문자열 — 관련 없는 턴의 프롬프트를 늘리지 않는다.
    """
    if conditions is None:
        return ""

    parts: list[str] = []
    if conditions.companion is not None:
        parts.append(_COMPANION_LABELS.get(conditions.companion.value, conditions.companion.value))
    if conditions.place_tags:
        parts.append(", ".join(tag.value for tag in conditions.place_tags))
    if conditions.taste_query:
        parts.append(f"취향 표현 '{conditions.taste_query}'")
    if conditions.environment is not None and conditions.environment.value != "any":
        parts.append(_ENVIRONMENT_LABELS.get(conditions.environment.value, ""))
    if conditions.transport is not None:
        parts.append(f"{_TRANSPORT_LABELS.get(conditions.transport.value, '')} 이동")
    if conditions.budget:
        parts.append(f"예산 {conditions.budget}")
    if conditions.max_travel_time:
        parts.append(f"이동 {conditions.max_travel_time}분 이내")
    if conditions.time_available:
        parts.append(f"체류 {conditions.time_available}분")
    if conditions.accessibility_needs:
        parts.append(
            ", ".join(
                _ACCESSIBILITY_LABELS.get(need, need)
                for need in conditions.accessibility_needs
            )
        )
    if conditions.special_requirements:
        parts.append(", ".join(conditions.special_requirements))

    filled = [part for part in parts if part]
    if not filled:
        return ""
    return "\n사용자가 말한 조건: " + " / ".join(filled)


def build_recommendation_summary_instruction(
    intent: Intent, *, conditions: UserConditions | None = None
) -> str:
    """RECOMMEND/MODIFY 결과를 감싸는 짧은 말풍선 생성 system instruction."""

    return render_text(
        "recommend/summary_instruction.md",
        chatbot_name=CHATBOT_NAME,
        persona=load_text("_shared/persona/trivi.md"),
        intent=intent.value,
        stated_conditions=_stated_conditions_line(conditions),
    )


def build_place_reason_instruction() -> str:
    """상세 카드 "AI가 추천하는 이유" 1~2문장 생성용 system instruction.

    **페르소나 파일을 꽂지 않는다.** 다른 생성 슬롯과 다른 유일한 점이고 의도다 —
    trivi.md는 886자라 이 호출의 입력을 700토큰가량 늘리는데, 이 슬롯이 클릭당
    별도 호출이라 그 증가가 클릭 수만큼 곱해진다. 말투만 필요하므로 템플릿 안의
    "~해요체" 규칙 한 줄로 대신한다(실측 입력 494토큰, 2026-09-09).

    인자가 없다. 순위·조건 축·사용자 조건을 넘기지 않기로 한 결정이 그대로 반영된
    모양이다 — 순위와 축은 카드가 이미 들고 있는 고정 문장이 말하고, 사용자 조건은
    이 경로(`/chat/place-details`)에 세션이 없어 읽을 수 없다.
    """

    return render_text(
        "recommend/place_reason_instruction.md",
        chatbot_name=CHATBOT_NAME,
    )


def build_compare_summary_instruction(criteria: CompareCriteria) -> str:
    """C의 검증된 COMPARE 결과를 사용자용 문장으로 바꾸는 system instruction."""

    return render_text(
        "compare/summary_instruction.md",
        chatbot_name=CHATBOT_NAME,
        persona=load_text("_shared/persona/trivi.md"),
        criteria=criteria.value,
    )


def _schedule_candidate_line(candidate: RecommendationItem) -> str:
    """일정 편성용 후보 1건을 프롬프트 한 줄로 직렬화한다.

    operating_hours_display를 포함해 LLM이 후보별 운영시간을 보고, 뒤 순서에
    배치하면 도착 예정 시각(estimated_arrival) 기준으로 이미 닫혀 있을 곳을
    스스로 피하도록 돕는다. 다만 이 프롬프트 힌트만으로는 LLM이 지시를 놓칠
    수 있어 완전한 보장이 아니다 — app.schedule.planner가 응답을 받은 뒤
    estimated_arrival과 운영시간을 다시 대조해 구조적으로 재검증한다
    (docs/design/int-07-schedule.md 9절, "폐점 스탑 감지" 항목 해소. 이
    한 줄짜리 프롬프트 힌트만으로 부족하다고 판단한 근거는 같은 문서 6.2.1절 —
    근거 데이터가 단일 시각 기준이라 프롬프트만으로는 뒷 순서 스탑의 정확성을
    보장할 수 없다).
    """
    hours = candidate.operating_hours_display or "확인불가"
    return (
        f"- {candidate.place_id} | {candidate.name} | {candidate.category} | "
        f"운영시간={hours} | score={candidate.score:.2f} | "
        f"{candidate.recommendation_reason}"
    )


def build_schedule_planning_instruction(
    time_available_min: int | None = None,
    *,
    item_range: tuple[int, int],
    clustered_candidates: bool = False,
) -> str:
    """INT-07 SCHEDULE 일정 편성 system instruction.
    (docs/design/int-07-schedule.md 6.1~6.2절)

    format_schedule_planning_context()가 만드는 텍스트가 contents로 같이
    전달된다는 전제로 규칙만 담는다 — 다른 build_*_instruction()과 달리 원문
    사용자 발화가 아니라 구조화된 후보/조건/거리 데이터가 입력이기 때문이다.

    목표 개수 범위(`item_range`)는 **이 함수가 계산하지 않고 받는다**(TP-239).
    `budget.derive_item_range()`가 활동 가능 시간·후보 분류별 체류 최소값·후보끼리의
    실제 거리로 구하는데, 이 함수에는 후보가 없어서 같은 답을 낼 수 없다. 계산을
    여기서 한 번 더 하면 프롬프트가 말하는 개수와 편성이 실제로 쓰는 상한이 갈린다.

    **(2026-09-04, TP-239) "상한까지 채우라"는 지시를 뺐다.** 그 문구는 2026-08-18에
    과소-채움을 막으려고 넣은 것이다 — 목표 범위(예: 3~5개) 안에 들어오는데도 LLM이
    적은 개수·짧은 체류만 채우고 일찍 끝내는 문제였다. 그때는 상한이 버킷 상수라
    예산과 무관했고, "범위 안에 있는데 덜 채운다"가 실제로 아까운 상황이었다.

    지금은 상한 자체가 예산에서 나오고, 총 소요 시간은
    `budget.fit_durations_to_budget()`이 체류시간을 조절해 맞춘다. 여기서 "넉넉히
    잡아 시간을 다 쓰라"고 시키면 **그 조절과 정면으로 싸운다** — LLM이 길게 잡고
    엔진이 다시 줄이므로, 남는 것은 LLM이 장소마다 매긴 상대적 판단이 뭉개지는 것뿐이다.

    **`clustered_candidates`는 부탁이고 코드가 계약이다.** (TP-243) 도보로 붙어
    있는 자리는 `duration.policy_for(clustered=True)`가 체류 최소값을 45분까지
    열어두는데, **여는 것만으로는 짧아지지 않는다** — 실제로 그 여유를 쓰는 것은
    예산이 빡빡할 때의 `fit_durations_to_budget()`뿐이라, 시간을 넉넉히 말한
    요청에서는 붙어 있는 곳도 90분씩 그대로 나갔다(2026-09-07 실측). 그래서
    "짧게 제안해 달라"를 여기서 부탁한다. 지키지 않아도 계약(최소값·개수 상한)이
    막아주므로 이 문장은 결과를 흔들지 않는다.

    **완화 대상이 아닌 분류까지 짧게 잡지 말라고 함께 말한다.** 박물관에 45분을
    제안해도 `policy_for()`가 90분으로 되돌리므로, 안 그러면 LLM의 판단만 버려진다.
    """

    min_items, max_items = item_range
    count_phrase = f"{min_items}개" if min_items == max_items else f"{min_items}~{max_items}개"

    if time_available_min is None:
        duration_rule = (
            "조건에 활동 가능 시간(time_available, 분)이 없으면 3~4시간 내외로 "
            "구성하세요."
        )
    else:
        duration_rule = (
            f"활동 가능 시간은 {time_available_min}분입니다. 목표 개수"
            f"({count_phrase})는 그 시간에 실제로 들어가는 수로 이미 계산한 값이니 "
            "그 안에서 고르세요 — 시간을 채우려고 개수를 늘리지 마세요. 장소별 "
            "체류시간은 장소 성격에 맞는 값을 제안하면 됩니다. 총 소요 시간이 활동 "
            "가능 시간에 맞도록 시스템이 카테고리별 범위 안에서 조정하므로, 시간을 "
            "다 쓰려고 체류시간을 늘려 잡지 마세요."
        )

    if clustered_candidates:
        duration_rule += (
            f" 후보 중에는 서로 걸어서 {SCHEDULE_CLUSTER_WALK_MINUTES}분 안쪽에 붙어 "
            "있는 곳들이 있습니다. 그런 곳들을 함께 고를 때는 관광지·체험·축제처럼 "
            f"가볍게 둘러보는 자리의 체류시간을 {CLUSTERED_VISIT_MINIMUM_MIN}~60분으로 "
            "짧게 제안하세요 — 이어서 둘러보는 코스가 됩니다. 박물관·미술관 같은 "
            "문화시설과 식사 자리는 붙어 있어도 원래대로 잡으세요."
        )

    return render_text(
        "schedule/plan.md",
        count_phrase=count_phrase,
        min_items=min_items,
        max_items=max_items,
        duration_rule=duration_rule,
    )


def _co_visited_line(hint: CoVisitedHint, name_by_place_id: dict[str, str]) -> str:
    from_name = name_by_place_id.get(hint.from_place_id, hint.from_place_id)
    to_name = name_by_place_id.get(hint.to_place_id, hint.to_place_id)
    return f"- {from_name}({hint.from_place_id}) ↔ {to_name}({hint.to_place_id}) | rank={hint.rank}"


def format_schedule_planning_context(request: SchedulePlanningRequest, start_time: str) -> str:
    """SchedulePlanningRequest를 LLM에 전달할 contents 텍스트로 직렬화한다.

    build_schedule_planning_instruction()이 규칙(system instruction)을 담당하고,
    이 함수가 실제 후보/조건/거리 데이터(contents)를 담당한다. start_time은
    호출부(app.schedule.planner)가 request.visit_datetime의 fallback까지
    반영해 미리 "HH:MM"으로 계산해 넘긴다.

    co_visited_hints(place_associations 기반, D-088)는 대부분의 요청에서
    비어 있다 — planner.py가 co_visited_fetcher를 받았을 때만 채운다. 비어
    있으면 "(없음)"으로 표시해, 이 섹션이 있는지 없는지가 아니라 항상 같은
    구조로 렌더링되게 한다(format_schedule_fill_context()의 pinned_lines
    fallback과 같은 패턴).
    """

    candidate_lines = "\n".join(
        _schedule_candidate_line(c) for c in request.candidates
    )
    distance_lines = "\n".join(
        f"- {a}-{b}: {distance_km:.2f}km"
        for (a, b), distance_km in request.pairwise_distances_km.items()
    )
    name_by_place_id = {c.place_id: c.name for c in request.candidates}
    co_visited_lines = "\n".join(
        _co_visited_line(hint, name_by_place_id) for hint in request.co_visited_hints
    ) or "(없음)"
    # 보관함에 담긴 장소(SCHEDULE-12). co_visited_lines와 같은 이유로 비어 있어도
    # 섹션을 없애지 않고 "(없음)"을 채운다 — 프롬프트 구조가 요청마다 달라지지 않게.
    must_include_lines = "\n".join(
        f"- {name_by_place_id.get(place_id, place_id)}({place_id})"
        for place_id in request.must_include_place_ids
    ) or "(없음)"
    condition_lines = request.conditions.model_dump_json(exclude_none=True)

    return render_text(
        "schedule/plan_context.md",
        start_time=start_time,
        candidate_lines=candidate_lines,
        distance_lines=distance_lines,
        co_visited_lines=co_visited_lines,
        must_include_lines=must_include_lines,
        condition_lines=condition_lines,
    )


def build_schedule_fill_instruction() -> str:
    """SCHEDULE-09(부분 수정) 2단계 — 기존 일정 중 일부 자리만 새로 채우는
    system instruction. (SCHEDULE-부분수정-해결방향-설계안.md 3-3절)

    build_schedule_planning_instruction()과 달리 pinned_items를 결과에 다시
    담아 달라고 요청하지 않는다 — 유지 항목은 이미 확정돼 있으므로 LLM은
    비어있는 자리(target_orders)에 들어갈 항목만 새로 고르면 된다. echo를
    신뢰하지 않고 Python이 병합을 구조적으로 보장한다.
    """

    return load_text("schedule/fill.md")


def format_schedule_fill_context(request: SchedulePartialFillRequest, start_time: str) -> str:
    """SchedulePartialFillRequest를 LLM에 전달할 contents 텍스트로 직렬화한다.

    co_visited_hints(D-088/D-091)는 pinned_items + candidates 양쪽 place_id를
    다 조회 대상으로 삼으므로(app.schedule.planner._with_co_visited_hints),
    이름 매핑도 두 목록을 합쳐서 만든다 — 힌트 한 쪽이 이미 확정된 pinned
    항목을 가리킬 수 있어서다.
    """

    pinned_lines = "\n".join(
        f"- order={p.order} | {p.place_id} | {p.place_name} | "
        f"도착={p.estimated_arrival} | 체류={p.estimated_duration_min}분"
        for p in request.pinned_items
    ) or "(없음)"
    candidate_lines = "\n".join(
        _schedule_candidate_line(c) for c in request.candidates
    )
    distance_lines = "\n".join(
        f"- {a}-{b}: {distance_km:.2f}km"
        for (a, b), distance_km in request.pairwise_distances_km.items()
    )
    name_by_place_id = {p.place_id: p.place_name for p in request.pinned_items}
    name_by_place_id.update({c.place_id: c.name for c in request.candidates})
    co_visited_lines = "\n".join(
        _co_visited_line(hint, name_by_place_id) for hint in request.co_visited_hints
    ) or "(없음)"
    condition_lines = request.conditions.model_dump_json(exclude_none=True)

    return render_text(
        "schedule/fill_context.md",
        start_time=start_time,
        pinned_lines=pinned_lines,
        target_orders=request.target_orders,
        candidate_lines=candidate_lines,
        distance_lines=distance_lines,
        co_visited_lines=co_visited_lines,
        condition_lines=condition_lines,
    )


def format_validation_retry_note(error: Exception) -> str:
    """1차 구조화 출력이 검증에 실패했을 때 재시도 프롬프트에 덧붙이는 안내문."""

    return "\n\n" + render_text("_shared/rules/validation_retry.md", error=error)


__all__ = [
    "PROMPT_VERSION",
    "build_intent_classification_instruction",
    "build_recommend_extraction_instruction",
    "build_modify_extraction_instruction",
    "build_info_extraction_instruction",
    "build_compare_extraction_instruction",
    "build_general_extraction_instruction",
    "build_general_answer_instruction",
    "build_info_answer_instruction",
    "build_recommendation_summary_instruction",
    "build_place_reason_instruction",
    "build_follow_up_suggestion_instruction",
    "build_closure_extraction_instruction",
    "build_mode_judge_instruction",
    "format_mode_judge_context",
    "build_compare_summary_instruction",
    "build_schedule_planning_instruction",
    "format_schedule_planning_context",
    "build_schedule_fill_instruction",
    "format_schedule_fill_context",
    "format_validation_retry_note",
]
