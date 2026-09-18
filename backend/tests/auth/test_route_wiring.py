"""라우트에 의존성과 예외 핸들러가 실제로 배선됐는지 확인한다 (D-062 Phase 2)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_라우트에_잘못된_토큰을_보내면_401_응답이_된다() -> None:
    """의존성·예외 핸들러 배선까지 확인한다."""
    client = TestClient(app)

    response = client.get(
        "/api/state/sess_missing", headers={"Authorization": "Bearer forged.token.value"}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


def test_라우트에_토큰이_없으면_기존대로_동작한다() -> None:
    client = TestClient(app)

    response = client.get("/api/state/sess_missing")

    assert response.status_code == 200
