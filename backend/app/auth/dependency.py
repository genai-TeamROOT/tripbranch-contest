"""FastAPI 신원 의존성 (D-062 4·5절).

역할: Authorization 헤더를 읽어 Principal을 만들고, 실패를 401로 바꾼다.
입력: 요청 헤더.
출력: Principal 또는 None(optional 경로).
호출 시점: 라우트가 Depends(get_principal)로 선언했을 때 요청마다 실행된다.
TODO: Phase 4에서 get_principal을 require_principal로 바꿔 필수화한다.

토큰이 없는 것과 토큰이 잘못된 것을 구분한다. 없으면 통과시키되 로그로 드러내고,
있는데 검증에 실패하면 조용히 익명으로 강등하지 않고 401로 끊는다 — 망가진 토큰을
보내는 클라이언트가 정상 동작으로 보이면 아무도 문제를 모른다(D-042와 같은 방향).
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, Request

from app.auth.errors import TokenVerificationError
from app.auth.jwks import is_configured
from app.auth.principal import Principal
from app.auth.verify import verify_access_token
from app.errors import AppError

logger = logging.getLogger(__name__)

_SCHEME = "bearer"


def _unauthorized(code: str) -> AppError:
    """사용자에게는 사유를 나누지 않는다. 어디서 걸렸는지가 공격자에게 힌트가 된다."""
    return AppError(
        code=code,
        message="로그인 정보가 유효하지 않아요. 다시 시작해주세요.",
        status_code=401,
    )


async def get_principal(request: Request) -> Principal | None:
    """신원을 읽는다. 없으면 None을 돌려주고 요청은 계속 진행된다."""
    header = request.headers.get("authorization")
    path = request.url.path

    if not header:
        # Phase 4 필수화 판단에 쓸 관측치다. 프론트가 헤더를 빠뜨려도 화면상으로는
        # 정상 동작하므로, 로그가 유일한 신호다.
        logger.warning("신원 토큰 없이 들어온 요청: path=%s", path)
        return None

    scheme, _, token = header.partition(" ")
    token = token.strip()
    if scheme.lower() != _SCHEME or not token:
        logger.warning("Authorization 헤더 형식 오류: path=%s", path)
        raise _unauthorized("invalid_auth_header")

    if not is_configured():
        # 검증 실패가 아니라 검증 불가다. Phase 2에서는 인증이 optional이므로 요청을
        # 끊지 않되, 토큰이 실제로 들어오고 있는데 확인하지 못한다는 사실을 남긴다.
        # Phase 4 필수화 시점에는 이 경로가 부팅 실패로 바뀌어야 한다.
        logger.warning("SUPABASE_URL이 없어 신원 토큰을 검증하지 못했다: path=%s", path)
        return None

    try:
        principal = await verify_access_token(token)
    except TokenVerificationError as exc:
        logger.warning("신원 토큰 검증 실패: path=%s reason=%s", path, exc.reason)
        raise _unauthorized("invalid_token") from exc

    logger.info(
        "신원 확인: user=%s anonymous=%s path=%s",
        principal.user_id,
        principal.is_anonymous,
        path,
    )
    return principal


# 라우트는 이 별칭만 쓴다. Depends를 인자 기본값에 두면 ruff B008에 걸리고,
# Annotated 형태가 FastAPI의 현재 권장 방식이기도 하다.
OptionalPrincipal = Annotated[Principal | None, Depends(get_principal)]


async def require_principal(principal: OptionalPrincipal) -> Principal:
    """신원을 필수로 요구한다. Phase 4에서 라우트가 이쪽으로 옮겨간다."""
    if principal is None:
        raise _unauthorized("authentication_required")
    return principal


RequiredPrincipal = Annotated[Principal, Depends(require_principal)]
