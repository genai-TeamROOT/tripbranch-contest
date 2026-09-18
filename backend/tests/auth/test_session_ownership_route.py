"""GET/DELETE /api/state/{session_id}의 소유권 검증 (D-063 결정 2 후속, D-073).

routes/state.py는 principal을 라우트 시그니처에 이미 선언해두고도 서비스
함수로 넘기지 않아, session_id만 알면 남의 세션을 조회·삭제할 수 있었다.
이 파일은 실제 서명된 토큰으로 HTTP 요청을 보내 그 배선이 끝까지
이어졌는지 확인한다 — 단위 테스트(tests/state/test_session.py,
tests/state/test_service.py)는 함수 호출 단위만 확인하므로 라우트
연결까지는 보장하지 않는다.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.auth.principal import Principal
from app.main import app
from app.state import service as state_service
from tests.auth.conftest import make_token

OWNER_SUB = "3f1a9c04-0000-4000-8000-000000000001"
STRANGER_SUB = "3f1a9c04-0000-4000-8000-000000000002"


def _create_owned_session() -> str:
    """소유자가 확정된 세션을 서비스 계층에서 직접 만든다. (HTTP 왕복 없이)"""
    owner = Principal(user_id=OWNER_SUB, is_anonymous=True)
    applied = state_service.apply(
        state_service.StateApplyRequest(intent="RECOMMEND", confirmed=True),
        principal=owner,
    )
    return applied.session_id


def test_다른_사람_토큰으로_조회하면_403(signing_key) -> None:
    session_id = _create_owned_session()
    stranger_token = make_token(signing_key, sub=STRANGER_SUB)
    client = TestClient(app)

    response = client.get(
        f"/api/state/{session_id}",
        headers={"Authorization": f"Bearer {stranger_token}"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "session_ownership_mismatch"


def test_같은_사람_토큰으로는_조회된다(signing_key) -> None:
    session_id = _create_owned_session()
    owner_token = make_token(signing_key, sub=OWNER_SUB)
    client = TestClient(app)

    response = client.get(
        f"/api/state/{session_id}",
        headers={"Authorization": f"Bearer {owner_token}"},
    )

    assert response.status_code == 200
    assert response.json()["session_exists"] is True


def test_토큰_없이_조회하면_기존대로_통과한다(signing_key) -> None:
    """토큰 미전송 요청은 Phase 4 전까지 정상 경로라 그대로 통과해야 한다."""
    session_id = _create_owned_session()
    client = TestClient(app)

    response = client.get(f"/api/state/{session_id}")

    assert response.status_code == 200
    assert response.json()["session_exists"] is True


def test_다른_사람_토큰으로_삭제하면_403이고_세션은_남는다(signing_key) -> None:
    session_id = _create_owned_session()
    stranger_token = make_token(signing_key, sub=STRANGER_SUB)
    client = TestClient(app)

    response = client.delete(
        f"/api/state/{session_id}",
        headers={"Authorization": f"Bearer {stranger_token}"},
    )

    assert response.status_code == 403
    owner_token = make_token(signing_key, sub=OWNER_SUB)
    still_there = client.get(
        f"/api/state/{session_id}",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert still_there.json()["session_exists"] is True
