"""사용자 입력 해석 API 라우터.

역할: 자연어 요청을 해석(A)하고 그 결과를 Agent State(B)에 반영한 뒤,
      해석 결과와 세션 상태를 함께 반환한다.
입력: POST /api/interpret JSON body의 InterpretRequest.
출력: InterpretResponse (LLMOutput + SessionState).
호출 시점: InputPage에서 사용자가 요청을 제출할 때 호출된다.
TODO: 인증, rate limit이 필요해지면 라우터 밖 의존성으로 연결한다.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.auth.dependency import OptionalPrincipal
from app.schemas import (
    InterpretRequest,
    InterpretResponse,
    SessionState,
    UserConditions,
)
from app.services.interpret import interpret_user_input
from app.services.interpret.session_orchestrator import ensure_current_context
from app.services.interpret.state_transform import transform
from app.state import service as state_service

router = APIRouter(tags=["interpret"])


@router.post("/interpret", response_model=InterpretResponse)
async def interpret(
    request: InterpretRequest, principal: OptionalPrincipal
) -> InterpretResponse:
    # 1) 세션 컨텍스트 확보 + GPS 최신화
    #    GPS 형식이 잘못되면 이번 턴만 건너뛴다. 대화 자체는 계속되어야 하고,
    #    사용자가 위치를 직접 말하면 user_conditions.current_location으로 확보된다.
    context = await ensure_current_context(
        request.session_id,
        _valid_location(request.device_location),
        principal=principal,
    )

    # 2) 세션이 있으면 B가 조건·이력의 단일 기준이다. (계약 6.2절)
    #    세션이 없으면 호출자가 실어 보낸 컨텍스트를 그대로 신뢰한다.
    #    (InterpretRequest의 세 필드는 세션 도입 전 하위 호환 경로다)
    if context.session_exists:
        llm_request = request.model_copy(
            update={
                "has_previous_recommendation": context.has_recommendation,
                "shown_place_count": len(context.shown_place_ids),
                "current_conditions": _to_llm_conditions(context),
            }
        )
    else:
        llm_request = request

    # 3) 자연어 해석 (2단계 LLM 호출)
    llm_output = await interpret_user_input(llm_request)

    # 4) LLMOutput → StateApplyRequest 변환 (A의 책임)
    apply_request = transform(llm_output, context, request.user_input)

    # 5) State 적용 및 run_id 발급
    state_result = state_service.apply(apply_request, principal=principal)

    # 6) 최초 턴에 GPS를 심던 자리였다. 서버가 사용자 위치를 저장하지 않게 되면서
    #    (state/store.py::for_persistence) 심어도 남지 않아 없앴다.

    # 7) 응답 조립
    final_context = state_service.get_session_context(
        state_result.session_id, principal=principal
    )

    return InterpretResponse(
        output=llm_output,
        state=SessionState(
            session_id=state_result.session_id,
            run_id=state_result.run_id,
            session_created=state_result.session_created,
            condition_version=state_result.condition_version,
            condition_changed=state_result.condition_changed,
            user_conditions=_to_llm_conditions(final_context) or UserConditions(),
            shown_place_ids=final_context.shown_place_ids,
            excluded_place_ids=state_result.excluded_place_ids,
            gps_expired=state_result.api_context.gps_expired,
            weather_expired=state_result.api_context.weather_expired,
        ),
    )


def _to_llm_conditions(context) -> UserConditions | None:
    """B의 UserConditions를 A(schemas)의 UserConditions로 변환한다.

    두 모델은 필드 구성이 같지만 값 타입이 다르다.
    B는 검증하지 않는 str, A는 StrEnum이다. (계약 1.2절)

    세션이 없으면 None을 반환한다. orchestrator가 current_conditions=None을
    "아직 추천한 적 없음"으로 판정하기 때문이다.
    """
    if not context.session_exists:
        return None
    return UserConditions.model_validate(context.user_conditions.model_dump())

def _valid_location(device_location: str | None) -> str | None:
    """'위도,경도' 형식이 아니면 None으로 낮춘다.

    잘못된 GPS 문자열이 파싱 예외로 대화를 중단시키지 않도록 한다.
    """
    if not device_location:
        return None
    parts = device_location.split(",")
    if len(parts) != 2:
        return None
    try:
        float(parts[0])
        float(parts[1])
    except ValueError:
        return None
    return device_location