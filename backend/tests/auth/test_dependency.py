"""Authorization 헤더 처리 규칙 (D-062 4·5절).

토큰이 없는 것과 토큰이 잘못된 것을 구분하는지가 핵심이다. 없으면 통과시키되
로그로 드러내고, 잘못됐으면 조용히 익명으로 강등하지 않고 401로 끊는다.
"""

from __future__ import annotations

import logging

import pytest
from starlette.requests import Request

from app.auth.dependency import get_principal, require_principal
from app.config import settings
from app.errors import AppError
from tests.auth.conftest import make_token

pytestmark = pytest.mark.asyncio


def _request(path: str = "/api/chat", **headers: str) -> Request:
    raw = [(key.lower().encode(), value.encode()) for key, value in headers.items()]
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": raw,
        }
    )


async def test_헤더가_없으면_통과시키되_경고를_남긴다(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="app.auth.dependency"):
        principal = await get_principal(_request())

    assert principal is None
    assert "신원 토큰 없이" in caplog.text


async def test_유효한_토큰이면_신원을_돌려준다(signing_key) -> None:
    token = make_token(signing_key)

    principal = await get_principal(_request(authorization=f"Bearer {token}"))

    assert principal is not None
    assert principal.is_anonymous is True


async def test_Bearer가_아니면_401(signing_key) -> None:
    token = make_token(signing_key)

    with pytest.raises(AppError) as exc:
        await get_principal(_request(authorization=f"Basic {token}"))

    assert exc.value.status_code == 401
    assert exc.value.code == "invalid_auth_header"


async def test_토큰이_비어_있으면_401() -> None:
    with pytest.raises(AppError) as exc:
        await get_principal(_request(authorization="Bearer "))

    assert exc.value.status_code == 401


async def test_검증에_실패하면_익명으로_강등하지_않고_401(signing_key) -> None:
    """조용한 통과 금지. 망가진 토큰을 보내는 클라이언트가 정상 동작으로 보이면 안 된다."""
    with pytest.raises(AppError) as exc:
        await get_principal(_request(authorization="Bearer forged.token.value"))

    assert exc.value.status_code == 401
    assert exc.value.code == "invalid_token"


async def test_사용자_메시지는_실패_사유를_노출하지_않는다() -> None:
    """어느 검증에서 걸렸는지가 공격자에게 힌트가 된다."""
    with pytest.raises(AppError) as exc:
        await get_principal(_request(authorization="Bearer forged.token.value"))

    assert "로그인 정보가 유효하지 않아요" in exc.value.message
    assert "signature" not in exc.value.message


async def test_설정이_없으면_검증하지_못했음을_남기고_통과시킨다(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    signing_key,
) -> None:
    """검증 실패가 아니라 검증 불가다. Phase 4에서는 이 경로가 부팅 실패가 된다."""
    monkeypatch.setattr(settings, "supabase_url", "")
    token = make_token(signing_key)

    with caplog.at_level(logging.WARNING, logger="app.auth.dependency"):
        principal = await get_principal(_request(authorization=f"Bearer {token}"))

    assert principal is None
    assert "SUPABASE_URL이 없어" in caplog.text


async def test_require_principal은_신원이_없으면_401() -> None:
    with pytest.raises(AppError) as exc:
        await require_principal(None)

    assert exc.value.status_code == 401
    assert exc.value.code == "authentication_required"
