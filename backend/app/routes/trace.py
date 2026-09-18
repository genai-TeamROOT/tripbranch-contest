"""LLMOps 실행 Trace 조회 API 라우터.

역할: A/C/D가 기록한 실행 단계(step)를 dev-ops 패널에서 볼 수 있도록 집계해 제공한다.
입력: GET /api/trace/stats?since=&until=&recent_errors_limit=
출력: TraceStatsResponse.
호출 시점: GET stats는 dev-ops 패널(TracePanel, TP-157)이 호출한다.
      세션 단위 조회(get_traces)는 별도 API로 노출하지 않는다 — 현재
      필요한 것은 "step별 평균 지연시간", "최근 에러"처럼 세션을 가리지
      않는 통계뿐이다(agent-state-contract-v1.md 경계 원칙: B는 세션 내부
      trace의 의미를 해석하지 않고 저장·집계만 한다).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter

from app.auth.dependency import OptionalPrincipal
from app.state import service as state_service

router = APIRouter(tags=["trace"])


@router.get("/trace/stats", response_model=state_service.TraceStatsResponse)
async def get_trace_stats(
    principal: OptionalPrincipal,
    since: datetime | None = None,
    until: datetime | None = None,
    recent_errors_limit: int = 20,
) -> state_service.TraceStatsResponse:
    return state_service.get_trace_stats(
        since=since, until=until, recent_errors_limit=recent_errors_limit
    )
