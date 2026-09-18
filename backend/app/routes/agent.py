"""Agent Runtime 개발용 디버그 API 라우터.

역할: `run_agent()`(A가 Intent 분류 → B/C/D 조정 → 챗봇 메시지 조립까지 전부 수행하는
      전체 Agent Runtime)를 HTTP로 직접 호출해볼 수 있게 노출한다.
입력: POST /api/agent-debug JSON body의 AgentRequest.
출력: AgentResponse (LLMOutput + 병합된 SessionState + 추천 결과 + 챗봇 메시지).
호출 시점: 프론트 HomePage의 AgentRuntimeDebugPanel에서 개발자가 수동으로 실행할 때 호출된다.
TODO: 통합 `POST /api/chat` 공개 계약이 확정되면 이 라우트는 정리하거나 흡수한다
      (README "다음 작업" §1 참고). run_agent() 자체는 수정하지 않고 그대로 재사용한다.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.auth.dependency import OptionalPrincipal
from app.schemas import AgentRequest, AgentResponse
from app.services.runtime.agent_runtime import run_agent

router = APIRouter(tags=["agent"])


@router.post("/agent-debug", response_model=AgentResponse)
async def agent_debug(request: AgentRequest, principal: OptionalPrincipal) -> AgentResponse:
    return await run_agent(request, principal=principal)
