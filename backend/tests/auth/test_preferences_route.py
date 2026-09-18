"""GET/PUT /api/preferences — 계정 단위 취향. (TP-222 후속)

**이 프로젝트에서 인증을 필수로 요구하는 첫 라우트다.** 다른 라우트는 전부
토큰 없는 요청을 통과시키는데(Phase 4 전까지의 과도기), 여기서는 신원이 곧
저장 키라 토큰이 없으면 어디에 저장할지가 정해지지 않는다.

그래서 이 파일에서 가장 중요한 것은 "토큰 없이 부르면 401"과 "남의 취향이
보이지 않는다" 둘이다 — 저장·조회가 되는지는 그다음이다.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from tests.auth.conftest import make_token

ME = "3f1a9c04-0000-4000-8000-000000000001"
OTHER = "3f1a9c04-0000-4000-8000-000000000002"

CHIPS = [
    {"label": "조용한 곳", "source": "preference", "codes": ["quiet"]},
    {"label": "산책하기 좋은", "source": "place_tag", "codes": ["walk"]},
]


def _headers(signing_key, sub: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(signing_key, sub=sub)}"}


# ---------------------------------------------------------------- 신원

def test_토큰_없이_조회하면_401(signing_key) -> None:
    response = TestClient(app).get("/api/preferences")

    assert response.status_code == 401


def test_토큰_없이_저장하면_401이고_아무것도_안_남는다(signing_key) -> None:
    client = TestClient(app)

    response = client.put("/api/preferences", json={"items": CHIPS})

    assert response.status_code == 401
    # 401로 끊겼으니 어느 계정에도 저장되지 않아야 한다.
    stored = client.get("/api/preferences", headers=_headers(signing_key, ME))
    assert stored.json()["items"] == []


def test_남의_취향은_보이지_않는다(signing_key) -> None:
    client = TestClient(app)
    client.put("/api/preferences", json={"items": CHIPS}, headers=_headers(signing_key, ME))

    response = client.get("/api/preferences", headers=_headers(signing_key, OTHER))

    assert response.status_code == 200
    assert response.json()["items"] == []


# ---------------------------------------------------------------- 저장·조회

def test_저장한_취향이_그대로_돌아온다(signing_key) -> None:
    client = TestClient(app)

    saved = client.put("/api/preferences", json={"items": CHIPS}, headers=_headers(signing_key, ME))
    assert saved.status_code == 200

    response = client.get("/api/preferences", headers=_headers(signing_key, ME))

    assert [item["label"] for item in response.json()["items"]] == ["조용한 곳", "산책하기 좋은"]
    assert response.json()["items"][1]["codes"] == ["walk"]


def test_고른_순서가_유지된다(signing_key) -> None:
    """화면이 저장된 순서를 그대로 보여준다 — 정렬을 바꾸면 사용자가 고른 맥락이 사라진다."""
    client = TestClient(app)
    reversed_chips = list(reversed(CHIPS))

    client.put(
        "/api/preferences", json={"items": reversed_chips}, headers=_headers(signing_key, ME)
    )
    response = client.get("/api/preferences", headers=_headers(signing_key, ME))

    assert [item["label"] for item in response.json()["items"]] == ["산책하기 좋은", "조용한 곳"]


def test_한_번도_고른_적_없으면_빈_목록이다(signing_key) -> None:
    """404가 아니다 — 취향을 안 고른 계정은 정상 상태다."""
    response = TestClient(app).get("/api/preferences", headers=_headers(signing_key, OTHER))

    assert response.status_code == 200
    assert response.json() == {"items": [], "updated_at": None}


def test_전체_교체다_앞의_값이_남지_않는다(signing_key) -> None:
    client = TestClient(app)
    client.put("/api/preferences", json={"items": CHIPS}, headers=_headers(signing_key, ME))

    client.put(
        "/api/preferences",
        json={"items": [{"label": "혼자 가기 좋은", "source": "custom", "codes": []}]},
        headers=_headers(signing_key, ME),
    )

    response = client.get("/api/preferences", headers=_headers(signing_key, ME))
    assert [item["label"] for item in response.json()["items"]] == ["혼자 가기 좋은"]


def test_빈_목록도_정상_저장이다(signing_key) -> None:
    """전부 해제한 경우다. 행을 지우지 않으므로 updated_at은 남는다."""
    client = TestClient(app)
    client.put("/api/preferences", json={"items": CHIPS}, headers=_headers(signing_key, ME))

    client.put("/api/preferences", json={"items": []}, headers=_headers(signing_key, ME))

    body = TestClient(app).get("/api/preferences", headers=_headers(signing_key, ME)).json()
    assert body["items"] == []
    assert body["updated_at"] is not None


def test_게스트_신원도_취향을_갖는다(signing_key) -> None:
    """게스트에게도 uid가 있다. 계정 승계(2차) 때 옮길 대상이 분명해진다."""
    guest = "3f1a9c04-0000-4000-8000-000000000003"
    client = TestClient(app)

    client.put("/api/preferences", json={"items": CHIPS}, headers=_headers(signing_key, guest))

    response = client.get("/api/preferences", headers=_headers(signing_key, guest))
    assert len(response.json()["items"]) == 2
