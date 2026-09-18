"""사용자 자연어 입력을 Intent + Conditions로 해석하는 서비스.

역할: 2단계 LLM 호출(① Intent 분류 ② Intent별 조건 추출)을 오케스트레이션해서
LLMOutput을 만든다. Fake/Real 여부는 LLMProvider 구현체가 갈라 처리하고, 이 모듈은
어느 쪽이든 동일한 흐름을 탄다 (services/recommendations.py의
get_recommendations()/build_recommendations() 분리와 동일한 패턴).
입력: InterpretRequest (user_input + 이전 추천 이력 컨텍스트).
출력: LLMOutput 모델.
호출 시점: /api/interpret 라우터가 interpret_user_input()을 호출한다.
TODO: B(Agent State) 연동(state_transform.py/session_orchestrator.py)을 이 흐름에
실제로 통합하는 작업은 다음 세션에서 진행한다 — InterpretRequest/응답 계약이 함께 바뀐다.
"""

from __future__ import annotations

import logging

from app.config import settings
from app.providers.protocols import LLMProvider
from app.schemas import (
    ClarificationOption,
    ClarificationPayload,
    ConversationTurnView,
    GeneralPayload,
    GeneralTopic,
    Intent,
    IntentClassificationResult,
    InteractionMode,
    InterpretRequest,
    LLMOutput,
    OutOfScopeCategory,
    OutOfScopePayload,
    OutputStatus,
    PlaceContext,
    StatedWeather,
    UserConditions,
    WeatherIntent,
)
from app.state.schema import now_kst

logger = logging.getLogger(__name__)

_SERVICE_IDENTITY_MARKERS = (
    "넌 누구",
    "너 누구",
    "너는 누구",
    "이름이 뭐",
    "이름 뭐",
    "뭘 할 수",
    "뭐 할 수",
    "무엇을 할 수",
    "트리비",
    "TripBranch",
    "tripbranch",
)


def _is_service_identity_question(user_input: str) -> bool:
    """챗봇/서비스 정체성 질문은 LLM 1차 분류 전에 GENERAL로 고정한다.

    Gemini가 "넌 누구야?"를 role_request/OUT_OF_SCOPE로 밀 수 있어 생기는
    회귀를 막는다. int-06-outofscope.md §12도 서비스 소개 요청은 GENERAL로 둔다.
    """

    return any(marker in user_input for marker in _SERVICE_IDENTITY_MARKERS)


# 목적어 없는 재시작("처음부터 다시")과 목적어 있는 재시작("처음부터 다시 추천해줘")을
# 구분한다 — 후자는 D-053/기존 MODIFY 규칙이 이미 잘 처리하므로 여기서 손대지 않는다
# (docs/design/clarification-options.md 케이스 4/5).
_BARE_RESTART_MARKERS = ("처음부터 다시", "다시 처음부터")

# 이전 추천이 있어도, 새 장소 유형을 명시하며 다시 찾아 달라는 발화는 기존 결과의
# 단순 수정이 아니라 새 추천으로 다룬다. 다만 "그중", "말고", "바꿔"처럼 직전
# 결과를 가리키는 표현이 있으면 MODIFY를 보존한다.
_FRESH_RECOMMEND_REQUEST_MARKERS = ("추천", "알려줘", "찾아줘", "찾아봐", "골라줘")
_FRESH_RECOMMEND_PLACE_MARKERS = (
    "카페", "식당", "맛집", "박물관", "미술관", "전시", "공원", "시장", "쇼핑몰",
    "서점", "산책", "공연",
)
_MODIFY_REFERENCE_MARKERS = ("그중", "그 곳", "그곳", "말고", "빼", "제외", "바꿔", "더 ")


def _is_fresh_recommendation_request(request: InterpretRequest) -> bool:
    """이전 추천과 독립적인 새 추천 요청인지 보수적으로 판별한다."""

    normalized = request.user_input.replace(" ", "")
    has_request = any(marker in normalized for marker in _FRESH_RECOMMEND_REQUEST_MARKERS)
    has_place_type = any(marker in normalized for marker in _FRESH_RECOMMEND_PLACE_MARKERS)
    refers_to_previous = any(
        marker.replace(" ", "") in normalized for marker in _MODIFY_REFERENCE_MARKERS
    )
    # 일정 직후 "카페 추천"은 일정 재조정인지 단순 추천인지 되물어야 하는 기존
    # 계약을 보존한다. 같은 검색 중심을 다시 말한 경우도 "비 오는데 경복궁 카페"처럼
    # 기존 조건 수정일 가능성이 높아 자동으로 새 검색으로 승격하지 않는다.
    current_center = (
        request.current_conditions.search_center if request.current_conditions is not None else None
    )
    names_new_center = current_center is None or current_center not in request.user_input
    return (
        request.last_intent != Intent.SCHEDULE.value
        and request.pending_clarification is None
        and has_request
        and has_place_type
        and names_new_center
        and not refers_to_previous
    )


def _resolve_info_conversation_reference(
    output: LLMOutput,
    conversation_place_name: str | None,
) -> LLMOutput:
    """INFO 추출기가 남긴 대화 지시어를 직전 INFO 카드 장소명으로 해소한다.

    LLM 프롬프트에도 같은 컨텍스트를 전달하지만, 모델이 from_conversation만 채우고
    place_name을 비워도 C가 불필요한 ``place_required`` 되묻기를 하지 않도록 A에서
    결정적으로 보정한다. 사용자가 이번 발화에서 명시한 장소(place_name이 이미 있음)는
    절대 덮어쓰지 않는다.
    """

    info = output.info
    if (
        info is None
        or info.place_context is not PlaceContext.FROM_CONVERSATION
        or info.place_name is not None
        or not conversation_place_name
    ):
        return output
    return output.model_copy(
        update={"info": info.model_copy(update={"place_name": conversation_place_name})}
    )


def _is_bare_restart_phrase(user_input: str) -> bool:
    stripped = user_input.strip().rstrip("!?.~ ")
    return any(
        stripped == marker or stripped == f"{marker}요" for marker in _BARE_RESTART_MARKERS
    )


_WEATHER_AVOID_LABELS: dict[StatedWeather, str] = {
    StatedWeather.RAIN: "비를 피할 장소",
    StatedWeather.SNOW: "눈을 피할 장소",
    StatedWeather.HOT: "더위를 피할 장소",
    StatedWeather.COLD: "추위를 피할 장소",
}


def _compose_condition_phrase(conditions: UserConditions) -> str | None:
    """되묻기 문구/버튼에 넣을 짧은 조건 구절. 채워진 신호만 우선순위(장소 → 날씨 →
    카테고리) 순으로 최대 2개까지 이어붙인다 — 다 붙이면 문장이 길고 부자연스러워
    진다(docs/design/clarification-options.md 6절)."""
    parts: list[str] = []
    if conditions.search_center:
        parts.append(f"{conditions.search_center} 근처")
    if len(parts) < 2:
        if (
            conditions.weather_intent == WeatherIntent.AVOID
            and conditions.weather in _WEATHER_AVOID_LABELS
        ):
            parts.append(_WEATHER_AVOID_LABELS[conditions.weather])
        elif conditions.place_tags:
            parts.append(conditions.place_tags[0].value)
    return " ".join(parts) if parts else None


def _bare_restart_during_schedule_location_ask(request: InterpretRequest) -> LLMOutput | None:
    """케이스 4: SCHEDULE 위치 되묻기 중 목적어 없는 "처음부터 다시".

    되묻기가 이미 진행 중이라(pending_clarification="location_required",
    last_intent="SCHEDULE") 이 발화가 새 일정 재조정인지 전체 초기화인지 글자로는
    구분이 안 된다 — 추측 대신 되묻는다.
    """
    if (
        request.last_intent != Intent.SCHEDULE.value
        or request.pending_clarification != "location_required"
        or not _is_bare_restart_phrase(request.user_input)
    ):
        return None
    return LLMOutput(
        intent=Intent.SCHEDULE,
        status=OutputStatus.NEEDS_CLARIFICATION,
        clarification=ClarificationPayload(
            code="schedule_bare_restart",
            message="일정을 처음부터 다시 잡아드릴까요, 아니면 계속 위치만 여쭤볼까요?",
            options=[
                ClarificationOption(
                    id="restart",
                    label="네, 처음부터 다시 잡을게요",
                    resolved_intent=Intent.SCHEDULE,
                ),
                ClarificationOption(
                    id="keep_asking",
                    label="아니요, 위치만 알려드릴게요",
                    resolved_intent=Intent.SCHEDULE,
                ),
            ],
        ),
    )


def _bare_restart_during_active_search(request: InterpretRequest) -> LLMOutput | None:
    """케이스 5: RECOMMEND/MODIFY 진행 중(되묻기 아님) 목적어 없는 "처음부터 다시".

    되묻기 중이 아니므로 이번 조건 자체를 새로 초기화하려는 건지, 지금 조건을 유지한
    채 다시 찾아달라는 건지 글자로는 구분이 안 된다.
    """
    if (
        request.pending_clarification is not None
        or request.last_intent not in (Intent.RECOMMEND.value, Intent.MODIFY.value)
        or not _is_bare_restart_phrase(request.user_input)
    ):
        return None
    phrase = (
        _compose_condition_phrase(request.current_conditions)
        if request.current_conditions is not None
        else None
    )
    keep_label = f"{phrase}로 다시 찾아주세요" if phrase else "이대로 다시 찾아주세요"
    message = (
        f"{phrase}로 다시 알아볼까요, 아니면 새로운 목적지로 찾아볼까요?"
        if phrase
        else "다시 알아볼까요, 아니면 새로운 목적지로 찾아볼까요?"
    )
    return LLMOutput(
        intent=Intent(request.last_intent),
        status=OutputStatus.NEEDS_CLARIFICATION,
        clarification=ClarificationPayload(
            code="bare_restart_active",
            message=message,
            options=[
                ClarificationOption(
                    id="keep_context", label=keep_label, resolved_intent=Intent.MODIFY
                ),
                ClarificationOption(
                    id="full_reset", label="새로 시작할게요", resolved_intent=Intent.RECOMMEND
                ),
            ],
        ),
    )


def _bare_restart_after_schedule_completed(request: InterpretRequest) -> LLMOutput | None:
    """SCHEDULE이 되묻기 없이 완료된 뒤(케이스 5와 대칭, SCHEDULE 전용) 목적어 없는
    "처음부터 다시".

    케이스 5는 last_intent가 RECOMMEND/MODIFY일 때만 다루고 SCHEDULE은 일부러
    뺐다 — REJECT_ALL(MODIFY)이 SCHEDULE 결과에는 안 맞는 동작이라서다. 그런데
    아무 규칙도 없이 흘려보내면 SCHEDULE-06(agent_runtime.py)이 "처음부터 다시"를
    무조건 MODIFY→SCHEDULE 재라우팅 대상으로 삼아 같은 조건으로 재편성을 시도하고,
    후보가 부족하면 "일정을 만들지 못했어요" 실패 문구로 새어버린다(실사용 재현,
    2026-08-13). 케이스 5와 같은 선택지를 SCHEDULE에 맞게 준다 — "이 조건으로 다시
    짜기"(SCHEDULE 유지)/"새로 시작"(RECOMMEND로 전환, 조건 초기화).
    """
    if (
        request.pending_clarification is not None
        or request.last_intent != Intent.SCHEDULE.value
        or not _is_bare_restart_phrase(request.user_input)
    ):
        return None
    phrase = (
        _compose_condition_phrase(request.current_conditions)
        if request.current_conditions is not None
        else None
    )
    retry_label = f"{phrase}로 다시 짜주세요" if phrase else "이 조건으로 다시 짜주세요"
    message = (
        f"{phrase}로 다시 짜드릴까요, 아니면 새로운 목적지로 찾아볼까요?"
        if phrase
        else "다시 짜드릴까요, 아니면 새로운 목적지로 찾아볼까요?"
    )
    return LLMOutput(
        intent=Intent.SCHEDULE,
        status=OutputStatus.NEEDS_CLARIFICATION,
        clarification=ClarificationPayload(
            code="schedule_bare_restart_completed",
            message=message,
            options=[
                ClarificationOption(
                    id="retry_schedule", label=retry_label, resolved_intent=Intent.SCHEDULE
                ),
                ClarificationOption(
                    id="full_reset", label="새로 시작할게요", resolved_intent=Intent.RECOMMEND
                ),
            ],
        ),
    )


async def _general_output(
    user_input: str,
    llm: LLMProvider,
    *,
    history: list[ConversationTurnView] | None = None,
) -> LLMOutput:
    """GENERAL 추출 결과를 반드시 GENERAL로 못 박아 돌려준다.

    extract_general_request()는 LLMOutput 전체를 모델이 채우게 두므로 intent도
    모델이 정한다. 그래서 **분류기가 GENERAL이라고 판정한 뒤에도 추출기가 그 결정을
    뒤집을 수 있다** — 실측(2026-08-30)에서 "너무 지친다"는 분류기가 GENERAL로 잘
    보냈는데 추출기가 OUT_OF_SCOPE를 돌려줘 결국 거절 문구가 나갔다. 인텐트를 정하는
    것은 분류 단계의 책임이므로 여기서 되돌린다(SCHEDULE 분기가
    extract_recommend_conditions() 결과의 intent를 바꿔치기하는 것과 같은 처리다).

    payload가 비어 있으면 답변 생성 단계가 쓸 수 없으므로 원문을 담아 채워 준다.
    """

    history_kwargs = {"history": history} if history else {}
    output = (await llm.extract_general_request(user_input, **history_kwargs)).data
    if output.intent is Intent.GENERAL and output.general is not None:
        return output
    return output.model_copy(
        update={
            "intent": Intent.GENERAL,
            "status": OutputStatus.COMPLETE,
            "out_of_scope": None,
            "general": output.general
            or GeneralPayload(
                topic=GeneralTopic.TRAVEL_TIP,
                original_question=user_input,
            ),
        }
    )


async def _extract_recommend_conditions(
    request: InterpretRequest,
    llm: LLMProvider,
    history_kwargs: dict[str, object],
) -> LLMOutput:
    """조건 추출. 페이로드가 통째로 비어 오면 한 번만 다시 뽑는다 (TP-266).

    **왜 여기냐.** `llm_output.recommend`가 None이면 state_transform이 조건 병합을
    통째로 건너뛴다. 오류도 로그도 없이 지나가므로 조건 0개로 추천·편성이 돌고,
    사용자에게는 자기가 말한 조건이 무시된 결과가 나간다(D-126). 지금 그 사실을
    감지하는 자리(`agent_runtime._condition_intake_error`)는 병합이 **끝난 뒤**라
    기록만 남기고 손을 쓸 수 없다. 되뽑을 수 있는 마지막 지점이 여기다.

    **정상 턴에서는 절대 안 걸린다.** `prompts/recommend/extract.md`가 "반드시
    recommend.conditions에 UserConditions 전체를 채우고"라고 무조건으로 못 박는다 —
    status가 needs_clarification이어도 마찬가지다. 그래서 페이로드가 통째로 없는
    것은 언제나 계약 위반이고, 되묻기 턴이 이 재시도에 걸려 값을 두 번 내는 일은
    없다. **조건이 전부 null인 것과는 다르다** — "일정 짜줘"처럼 조건을 하나도
    말하지 않은 발화는 페이로드는 있고 안이 빈 것이라 여기 안 걸린다.

    **D-052 폴백은 이 경우에 안 걸린다.** `recommend: null`은 HTTP 200이고 응답
    스키마도 통과해서 provider에게는 성공이다. 실측에서 빈손 6건에 폴백 0/18이었다.

    **재시도 모델을 새로 정하지 않는다.** `llm_fast_fallback_model_names`가 이미
    "1순위가 실패하면 이걸로 간다"를 선언해 뒀다(기본값 gemini-3.5-flash). 빈손을
    실패로 치기만 하면 쓸 모델은 이미 정해져 있다 — 그래서 fast 묶음의 2순위부터를
    그대로 쓴다. 바꾸고 싶으면 `.env`에서 그 값을 바꾸면 되고 코드는 안 건드린다.

    **한 번만 한다.** 두 번째도 빈손이면 첫 결과를 그대로 돌려주고 지금과 똑같이
    조건 없이 진행한다 — 호출 수가 발화당 무한히 늘지 않게 한다.

    근거: 2026-09-08 실측(일정 발화 18건 x 반복 3)에서 `gemini-3.5-flash-lite`가
    흔들림 6/18 · 기대 불일치 9/18, `gemini-3.5-flash`가 0/18 · 0/18이었다
    (backend/test_results/model_tier_2026-09-08/0단계_기준선.md).
    """

    result = (
        await llm.extract_recommend_conditions(request.user_input, **history_kwargs)
    ).data
    if result.recommend is not None:
        return result

    retry_models = settings.resolved_llm_fast_models[1:]
    if not retry_models:
        # 폴백 묶음이 비어 있으면 같은 모델로 다시 부를 뿐이라 값을 두 번 낸다.
        logger.warning("조건 페이로드가 비어 왔지만 폴백 모델이 없어 다시 뽑지 않는다")
        return result

    logger.warning(
        "조건 페이로드가 비어 왔다 — %s로 한 번 다시 뽑는다", retry_models[0]
    )
    retried = (
        await llm.extract_recommend_conditions(
            request.user_input, retry_models=retry_models, **history_kwargs
        )
    ).data
    if retried.recommend is None:
        logger.warning("다시 뽑아도 조건 페이로드가 비어 있다 — 조건 없이 진행한다")
        return result
    return retried


async def _extract_for_intent(
    classification: IntentClassificationResult,
    request: InterpretRequest,
    llm: LLMProvider,
) -> LLMOutput:
    """분류된 Intent에 맞는 추출기를 골라 LLMOutput을 만든다.

    build_interpretation()에서 떼어낸 이유는 상황 축(interaction_mode) 때문이다.
    반환 경로가 8개인데 각 경로에서 축을 채우면 하나는 반드시 빠진다 — 여기서는
    인텐트별 결과만 만들고, 축은 호출부가 한 번에 덧붙인다.
    """

    history_kwargs = {"history": request.recent_turns} if request.recent_turns else {}

    if classification.intent is Intent.OUT_OF_SCOPE:
        # 상황 발화는 거절하지 않는다. 분류기는 "너무 지친다"를 GENERAL로 잘
        # 보내지만(2026-08-30 실측), 우선순위 1번이 다른 무엇보다 먼저 걸리는
        # 캐스케이드라 비슷한 발화가 OUT_OF_SCOPE로 새는 일이 남는다. 같은
        # 실측에서 interaction_mode 축은 8/8 정확했으므로 축을 근거로 뒤집는다 —
        # _is_service_identity_question()이 Gemini의 알려진 오분류를 막는 것과
        # 같은 성격의 가드다.
        #
        # **유해 발언·프롬프트 인젝션은 뒤집지 않는다.** "너 진짜 바보야?"가
        # situational로 분류되는 것을 실측에서 확인했다 — 축만 보고 구제하면
        # 욕설이 GENERAL 답변을 받는다. 차단이 먼저다.
        rescuable = classification.out_of_scope_category not in {
            OutOfScopeCategory.HARMFUL,
            OutOfScopeCategory.PROMPT_INJECTION,
        }
        if classification.interaction_mode is InteractionMode.SITUATIONAL and rescuable:
            return await _general_output(
                request.user_input, llm, history=request.recent_turns
            )
        return LLMOutput(
            intent=Intent.OUT_OF_SCOPE,
            status=OutputStatus.COMPLETE,
            out_of_scope=OutOfScopePayload(
                category=classification.out_of_scope_category,
                severity=classification.out_of_scope_severity,
            ),
        )

    # SCHEDULE도 RECOMMEND와 같은 15개 조건(time_available, place_tags 등)을 쓴다
    # (docs/design/int-07-schedule.md 6.1절) — 별도 추출 메서드를 새로 만들지 않고
    # extract_recommend_conditions()를 그대로 재사용한 뒤 intent만 SCHEDULE로
    # 바꿔치기한다. status(complete/needs_clarification)와 clarification은 그대로
    # 유지된다 — RECOMMEND와 동일한 되묻기 흐름을 탄다.
    if classification.intent is Intent.SCHEDULE:
        result = await _extract_recommend_conditions(request, llm, history_kwargs)
        return result.model_copy(update={"intent": Intent.SCHEDULE})

    if classification.intent is Intent.RECOMMEND:
        return await _extract_recommend_conditions(request, llm, history_kwargs)

    if classification.intent is Intent.MODIFY:
        if request.current_conditions is None:
            return LLMOutput(
                intent=Intent.MODIFY,
                status=OutputStatus.NEEDS_CLARIFICATION,
                clarification=ClarificationPayload(
                    missing_fields=[
                        {
                            "field": "current_conditions",
                            "reason": "변경할 기존 조건 정보가 없어 어떤 추천을 기준으로 "
                            "바꿔야 할지 확인할 수 없습니다.",
                        }
                    ],
                    message="아직 추천한 결과가 없어요. 어떤 장소를 찾고 계신가요?",
                ),
            )
        return (
            await llm.extract_modify_conditions(
                request.user_input,
                request.current_conditions,
                pending_clarification=request.pending_clarification,
                shown_place_count=request.shown_place_count,
                shown_place_names=request.shown_place_names,
                **history_kwargs,
            )
        ).data

    if classification.intent is Intent.INFO:
        output = (
            await llm.extract_info_query(
                request.user_input,
                has_previous_recommendation=request.has_previous_recommendation,
                reference_date=now_kst().date(),
                conversation_place_name=request.conversation_place_name,
                pending_info_question_type=request.pending_info_question_type,
                pending_info_specific_question=request.pending_info_specific_question,
                pending_info_visit_time=request.pending_info_visit_time,
                **history_kwargs,
            )
        ).data
        return _resolve_info_conversation_reference(output, request.conversation_place_name)

    if classification.intent is Intent.COMPARE:
        return (
            await llm.extract_compare_request(
                request.user_input,
                shown_place_count=request.shown_place_count,
                shown_place_names=request.shown_place_names,
                **history_kwargs,
            )
        ).data

    # 남은 경우는 Intent.GENERAL뿐 (RECOMMEND/MODIFY/INFO/COMPARE/OUT_OF_SCOPE는 위에서 처리).
    return await _general_output(
        request.user_input, llm, history=request.recent_turns
    )


async def build_interpretation(
    request: InterpretRequest, llm: LLMProvider
) -> LLMOutput:
    """Fake/Real LLMProvider를 인자로 받는 테스트 가능한 본체."""

    if _is_service_identity_question(request.user_input):
        return LLMOutput(
            intent=Intent.GENERAL,
            status=OutputStatus.COMPLETE,
            general=GeneralPayload(
                topic=GeneralTopic.SERVICE_IDENTITY,
                original_question=request.user_input,
            ),
        )

    # 케이스 4/5(PR 4, docs/design/clarification-options.md): 목적어 없는 "처음부터
    # 다시"는 classify_intent() 호출 전에 결정적으로 되묻는다 — 글자만으로는 SCHEDULE
    # 재진입인지, 조건 유지 재조회인지, 전체 초기화인지 LLM마다 판정이 갈린다.
    bare_restart = (
        _bare_restart_during_schedule_location_ask(request)
        or _bare_restart_during_active_search(request)
        or _bare_restart_after_schedule_completed(request)
    )
    if bare_restart is not None:
        return bare_restart

    classification = (
        await llm.classify_intent(
            request.user_input,
            has_previous_recommendation=request.has_previous_recommendation,
            shown_place_count=request.shown_place_count,
            pending_clarification=request.pending_clarification,
            last_intent=request.last_intent,
            shown_place_names=request.shown_place_names,
            conversation_place_name=request.conversation_place_name,
            **({"history": request.recent_turns} if request.recent_turns else {}),
        )
    ).data

    # LLM이 이전 추천 이력만 보고 MODIFY로 기울더라도, "안국역 카페 추천해줘"처럼
    # 새 장소 유형을 명시한 독립 검색은 RECOMMEND 추출기로 보낸다. 이 경로는
    # state_transform에서 soft reset되어 이전의 동행·거리·제외 이력이 섞이지 않는다.
    if (
        classification.intent is Intent.MODIFY
        and _is_fresh_recommendation_request(request)
    ):
        classification = classification.model_copy(update={"intent": Intent.RECOMMEND})

    output = await _extract_for_intent(classification, request, llm)
    # 상황 축은 인텐트별 추출기가 모르는 값이라, 분류 결과에서 여기 한 곳에서만
    # 옮겨 담는다 — 반환 경로가 8개라 각 경로에서 채우면 반드시 하나를 빠뜨린다.
    return output.model_copy(
        update={"interaction_mode": classification.interaction_mode}
    )


async def interpret_user_input(request: InterpretRequest) -> LLMOutput:
    """라우터가 호출하는 Fake/Real 공통 진입점."""

    from app.providers.factory import get_llm_provider

    return await build_interpretation(request, get_llm_provider())
