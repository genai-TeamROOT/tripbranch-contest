"""기능 스위치 조회 API(GET /api/features) 테스트.

화면은 이 값으로 "취향 설정" 메뉴와 /preferences를 숨긴다. 서버 설정과 어긋나면
취향이 순위에 아무 영향도 주지 않는 화면이 남거나, 켜 둔 기능이 안 보인다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


@pytest.mark.parametrize("enabled", [False, True])
def test_features_follows_taste_switch(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    monkeypatch.setattr(settings, "taste_evidence_enabled", enabled)

    response = TestClient(app).get("/api/features")

    assert response.status_code == 200
    # 설정값을 통째로 내보내지 않는다 — 화면이 쓰는 판정 하나만 싣는다.
    assert response.json() == {"taste_enabled": enabled}


def test_features_needs_no_auth() -> None:
    """관문을 통과하기 전에도 메뉴를 그려야 하므로 토큰 없이 열린다(health와 같다)."""

    response = TestClient(app).get(
        "/api/features", headers={"Authorization": "Bearer not-a-real-token"}
    )

    assert response.status_code == 200
