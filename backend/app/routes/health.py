"""헬스체크 API 라우터.

역할: 서버가 살아 있고 기본 라우팅이 동작하는지 확인하는 엔드포인트를 제공한다.
입력: GET /api/health 요청.
출력: HealthResponse 형태의 {"status": "ok"} JSON.
호출 시점: 브라우저, 모니터링, 테스트가 백엔드 상태를 확인할 때 호출된다.
TODO: DB/provider 의존성이 추가되면 얕은/깊은 헬스체크를 분리한다.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def get_health() -> HealthResponse:
    return HealthResponse(status="ok")
