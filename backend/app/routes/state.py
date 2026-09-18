"""세션 상태 조회/삭제 + 장소 보관함 API 라우터.

역할: 별도 추천 호출 없이도 현재 세션 상태를 확인하고, 필요 시 세션을 정리하며,
사용자가 추천 카드에서 담은 장소를 보관함에 넣고 뺀다(SCHEDULE-12).
입력: GET/DELETE /api/state/{session_id},
      GET/POST /api/state/{session_id}/saved-places,
      DELETE /api/state/{session_id}/saved-places/{place_id},
      GET /api/sessions, GET /api/sessions/{session_id},
      POST /api/sessions/{session_id}/resume,
      PATCH /api/state/{session_id}/title
출력: SessionContextResponse / DeleteSessionResponse / SavedPlacesResponse.
호출 시점: 프론트가 상태 동기화·채팅 초기화를 수행할 때, 또는 사용자가 추천
카드의 담기 버튼을 누를 때.

보관함 담기·빼기는 인텐트 분류를 거치지 않는다 — 버튼 클릭은 해석할 여지가
없는 결정적 동작이라 LLM을 태울 이유가 없고, /api/chat을 통하면 오분류 위험과
지연이 그대로 붙는다(SCHEDULE-12 설계안 2절).
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.auth.dependency import OptionalPrincipal, RequiredPrincipal
from app.state import service as state_service

router = APIRouter(tags=["state"])


class RenameSessionRequest(BaseModel):
    title: str


@router.get("/sessions", response_model=state_service.ChatSessionsResponse)
async def list_sessions(principal: RequiredPrincipal) -> state_service.ChatSessionsResponse:
    """내 대화 목록. 사이드바 채팅 히스토리가 쓴다. (TP-222 후속)

    경로가 /state/{session_id} 아래가 아닌 이유는 이 목록이 특정 세션에 속하지
    않기 때문이다 — 넣었다면 /state/{session_id}가 "sessions"를 session_id로
    받아 삼켰을 것이다.

    /preferences와 같이 RequiredPrincipal을 쓴다. 신원이 곧 조회 키라, 토큰이
    없으면 누구의 목록인지가 정해지지 않는다.
    """
    return state_service.list_user_sessions(principal.user_id)


@router.get("/sessions/{session_id}", response_model=state_service.ChatSessionDetail)
async def get_session_detail(
    session_id: str, principal: RequiredPrincipal
) -> state_service.ChatSessionDetail:
    """지난 대화 하나. 사이드바에서 한 줄을 눌렀을 때 쓴다. (TP-222 후속)

    /state/{session_id}와 달리 **TTL을 적용하지 않는다.** 채팅 히스토리는 30분보다
    오래된 대화를 보여주는 것이 목적이라, 만료를 없는 것으로 취급하면 목록의 거의
    모든 항목이 열리지 않는다.
    """
    return state_service.get_user_session_detail(session_id, principal)


@router.post("/sessions/{session_id}/resume", response_model=state_service.ChatSessionDetail)
async def resume_session(
    session_id: str, principal: RequiredPrincipal
) -> state_service.ChatSessionDetail:
    """지난 대화를 이어갈 수 있게 되살린다. (TP-222 후속)

    GET /sessions/{id}과 같은 내용을 돌려주지만 쓰기다 — 만료된 세션을 다시
    active로 돌리고 낡은 조건을 버린다. 그래서 응답의 resumable은 항상 true다.

    조회와 나눠 둔 이유는 "열어만 보는 것"과 "이어서 말하는 것"이 다른 동작이기
    때문이다. GET이 쓰기를 겸하면 목록을 미리 불러오기만 해도 세션이 되살아난다.
    """
    return state_service.resume_user_session(session_id, principal)


@router.patch(
    "/state/{session_id}/title",
    response_model=state_service.ChatSessionSummary,
)
async def rename_session(
    session_id: str,
    request: RenameSessionRequest,
    principal: RequiredPrincipal,
) -> state_service.ChatSessionSummary:
    """대화 이름을 바꾼다.

    여기는 경로에 session_id가 들어오므로 남의 대화를 지목할 수 있다 —
    서비스가 소유권을 대조한다.
    """
    return state_service.rename_session(session_id, request.title, principal=principal)


# ---------------------------------------------------------------- 저장한 일정
#
# /sessions와 같은 이유로 /state/{session_id} 아래에 두지 않는다 — 저장한 일정은
# 특정 세션에 속하지 않는다. 세션이 30일 뒤 정리돼도 일정은 남는다.
#
# 넷 다 RequiredPrincipal이다. 신원이 없으면 누구의 일정인지가 정해지지 않는다.


@router.get("/schedules", response_model=state_service.SavedSchedulesResponse)
async def list_schedules(principal: RequiredPrincipal) -> state_service.SavedSchedulesResponse:
    """내 저장 일정 목록. 최근 저장순이다. (SCHEDULE 카드 2)

    payload를 싣지 않는다 — 한 줄을 누르면 그때 상세를 받는다.
    """
    return state_service.list_user_schedules(principal.user_id)


@router.post("/schedules", response_model=state_service.SavedScheduleDetail)
async def save_schedule(
    request: state_service.SaveScheduleRequest,
    principal: RequiredPrincipal,
) -> state_service.SavedScheduleDetail:
    """일정을 저장한다. (SCHEDULE 카드 2)

    **멱등이다.** 같은 (신원, run_id)를 다시 보내면 새로 만들지 않고 이미 있는
    것을 그대로 돌려준다 — 저장 버튼을 두 번 누르거나 요청이 재시도돼도 목록에
    두 줄이 생기지 않는다. 화면은 두 경우를 구분할 필요가 없다.
    """
    return state_service.save_user_schedule(principal.user_id, request)


@router.get("/schedules/{schedule_id}", response_model=state_service.SavedScheduleDetail)
async def get_schedule(
    schedule_id: str, principal: RequiredPrincipal
) -> state_service.SavedScheduleDetail:
    """저장 일정 하나. 목록에서 한 줄을 눌렀을 때 쓴다.

    payload는 **저장 시점의 값**이다. 도착 시각·이동 시간은 그때 기준이므로
    화면이 그 사실을 밝혀야 한다.
    """
    return state_service.get_user_schedule(schedule_id, principal)


@router.patch(
    "/schedules/{schedule_id}/title",
    response_model=state_service.SavedScheduleSummary,
)
async def rename_schedule(
    schedule_id: str,
    request: state_service.RenameScheduleRequest,
    principal: RequiredPrincipal,
) -> state_service.SavedScheduleSummary:
    """저장 일정의 이름을 바꾼다. 대화 이름 바꾸기와 같은 경로 모양이다."""
    return state_service.rename_user_schedule(schedule_id, request.title, principal)


@router.delete("/schedules/{schedule_id}", response_model=state_service.DeleteScheduleResponse)
async def delete_schedule(
    schedule_id: str, principal: RequiredPrincipal
) -> state_service.DeleteScheduleResponse:
    """저장 일정을 지운다.

    이미 없으면 오류가 아니라 `deleted=False`다. 다만 **남의 것을 지우려는 것은
    403이다** — 존재하는데 내 것이 아닌 경우는 멱등의 범위가 아니다.
    """
    return state_service.delete_user_schedule(schedule_id, principal)


@router.get("/state/{session_id}", response_model=state_service.SessionContextResponse)
async def get_state(
    session_id: str, principal: OptionalPrincipal
) -> state_service.SessionContextResponse:
    return state_service.get_session_context(session_id, principal=principal)


@router.delete("/state/{session_id}", response_model=state_service.DeleteSessionResponse)
async def delete_state(
    session_id: str, principal: OptionalPrincipal
) -> state_service.DeleteSessionResponse:
    return state_service.delete_session(session_id, principal=principal)


@router.get(
    "/state/{session_id}/saved-places",
    response_model=state_service.SavedPlacesResponse,
)
async def get_saved_places(
    session_id: str, principal: OptionalPrincipal
) -> state_service.SavedPlacesResponse:
    return state_service.get_saved_places(session_id, principal=principal)


@router.post(
    "/state/{session_id}/saved-places",
    response_model=state_service.SavedPlacesResponse,
)
async def save_place(
    session_id: str,
    request: state_service.SavePlaceRequest,
    principal: OptionalPrincipal,
) -> state_service.SavedPlacesResponse:
    return state_service.save_place(session_id, request, principal=principal)


@router.delete(
    "/state/{session_id}/saved-places/{place_id}",
    response_model=state_service.SavedPlacesResponse,
)
async def remove_saved_place(
    session_id: str, place_id: str, principal: OptionalPrincipal
) -> state_service.SavedPlacesResponse:
    return state_service.remove_saved_place(session_id, place_id, principal=principal)
