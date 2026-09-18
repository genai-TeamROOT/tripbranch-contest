"""/api/schedules — 계정 단위 저장 일정. (SCHEDULE 카드 2)

**이 파일에서 가장 중요한 것은 격리와 멱등 둘이다.**
남의 일정이 목록에 섞이거나 남의 일정을 지울 수 있으면 그 순간 기능이 아니라
사고가 되고, 저장 버튼을 두 번 눌러 목록이 두 줄이 되면 사용자에게는 그 자체가
버그다. 저장·조회가 되는지는 그다음이다.

/preferences와 같이 넷 다 인증이 필수다 — 신원이 곧 저장 키라, 토큰이 없으면
어디에 저장하고 무엇을 돌려줄지가 정해지지 않는다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.state.store import get_store
from tests.auth.conftest import make_token


@pytest.fixture(autouse=True)
def _empty_store() -> None:
    """저장소가 프로세스 전역이라 이 파일의 테스트들이 서로의 일정을 본다.

    격리·멱등 판정이 앞 테스트가 남긴 행에 기대면 통과가 우연이 된다 — 실제로
    "저장한 일정이 그대로 돌아온다"가 앞 테스트의 멱등 경로에 걸려 session_id를
    잃은 채 통과할 뻔했다. 실행 순서가 섞여도 같은 결과가 나오게 비우고 시작한다.
    """
    store = get_store()
    clear = getattr(store, "clear", None)
    if clear is not None:
        clear()

ME = "3f1a9c04-0000-4000-8000-000000000001"
OTHER = "3f1a9c04-0000-4000-8000-000000000002"

PAYLOAD = {
    "items": [{"order": 1, "place_id": "p1", "place_name": "경복궁"}],
    "total_duration_min": 180,
    "route_summary": "종로 반나절 코스",
}


def _headers(signing_key, sub: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(signing_key, sub=sub)}"}


def _save(client: TestClient, signing_key, sub: str, **overrides) -> dict:
    body = {"title": "종로 반나절", "payload": PAYLOAD, "run_id": "run_1", **overrides}
    response = client.post("/api/schedules", json=body, headers=_headers(signing_key, sub))
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------- 신원

def test_토큰_없이_목록을_부르면_401(signing_key) -> None:
    assert TestClient(app).get("/api/schedules").status_code == 401


def test_토큰_없이_저장하면_401이고_아무것도_안_남는다(signing_key) -> None:
    client = TestClient(app)

    response = client.post("/api/schedules", json={"title": "몰래", "payload": PAYLOAD})

    assert response.status_code == 401
    mine = client.get("/api/schedules", headers=_headers(signing_key, ME))
    assert mine.json()["items"] == []


# ---------------------------------------------------------------- 격리

def test_남의_일정은_목록에_보이지_않는다(signing_key) -> None:
    client = TestClient(app)
    _save(client, signing_key, ME)

    response = client.get("/api/schedules", headers=_headers(signing_key, OTHER))

    assert response.status_code == 200
    assert response.json()["items"] == []


def test_남의_일정은_열어볼_수_없다(signing_key) -> None:
    client = TestClient(app)
    saved = _save(client, signing_key, ME)

    response = client.get(f"/api/schedules/{saved['id']}", headers=_headers(signing_key, OTHER))

    assert response.status_code == 403


def test_남의_일정은_지울_수_없다(signing_key) -> None:
    """삭제는 없는 것에 대해 멱등이지만, **있는데 남의 것**은 멱등의 범위가 아니다.
    여기서 조용히 성공하면 id만 알면 남의 일정을 지울 수 있다."""
    client = TestClient(app)
    saved = _save(client, signing_key, ME)

    response = client.delete(f"/api/schedules/{saved['id']}", headers=_headers(signing_key, OTHER))

    assert response.status_code == 403
    still = client.get(f"/api/schedules/{saved['id']}", headers=_headers(signing_key, ME))
    assert still.status_code == 200


def test_남의_일정은_이름을_바꿀_수_없다(signing_key) -> None:
    client = TestClient(app)
    saved = _save(client, signing_key, ME)

    response = client.patch(
        f"/api/schedules/{saved['id']}/title",
        json={"title": "가로챔"},
        headers=_headers(signing_key, OTHER),
    )

    assert response.status_code == 403
    mine = client.get(f"/api/schedules/{saved['id']}", headers=_headers(signing_key, ME))
    assert mine.json()["title"] == "종로 반나절"


# ---------------------------------------------------------------- 멱등

def test_같은_턴을_두_번_저장해도_목록은_한_줄이다(signing_key) -> None:
    client = TestClient(app)

    first = _save(client, signing_key, ME)
    second = _save(client, signing_key, ME, title="또 눌렀다")

    assert first["id"] == second["id"]
    # 먼저 붙인 이름이 남는다 — 재시도가 사용자가 지은 이름을 덮으면 안 된다.
    assert second["title"] == "종로 반나절"
    assert len(client.get("/api/schedules", headers=_headers(signing_key, ME)).json()["items"]) == 1


# ---------------------------------------------------------------- 저장·조회

def test_저장한_일정이_그대로_돌아온다(signing_key) -> None:
    client = TestClient(app)
    saved = _save(client, signing_key, ME, session_id="sess_1")

    response = client.get(f"/api/schedules/{saved['id']}", headers=_headers(signing_key, ME))

    body = response.json()
    assert body["title"] == "종로 반나절"
    assert body["session_id"] == "sess_1"
    # payload는 열어보지 않고 그대로 오간다.
    assert body["payload"] == PAYLOAD


def test_목록에는_payload를_싣지_않는다(signing_key) -> None:
    """50줄이 각자 일정을 통째로 들고 오면 목록 한 번에 수백 KB가 나간다."""
    client = TestClient(app)
    _save(client, signing_key, ME)

    items = client.get("/api/schedules", headers=_headers(signing_key, ME)).json()["items"]

    assert items and "payload" not in items[0]


def test_이름을_바꾸면_목록에도_반영된다(signing_key) -> None:
    client = TestClient(app)
    saved = _save(client, signing_key, ME)

    renamed = client.patch(
        f"/api/schedules/{saved['id']}/title",
        json={"title": "엄마랑 가는 날"},
        headers=_headers(signing_key, ME),
    )

    assert renamed.status_code == 200
    items = client.get("/api/schedules", headers=_headers(signing_key, ME)).json()["items"]
    assert items[0]["title"] == "엄마랑 가는 날"


def test_지우면_목록에서_빠지고_다시_지워도_오류가_아니다(signing_key) -> None:
    client = TestClient(app)
    saved = _save(client, signing_key, ME)

    first = client.delete(f"/api/schedules/{saved['id']}", headers=_headers(signing_key, ME))
    second = client.delete(f"/api/schedules/{saved['id']}", headers=_headers(signing_key, ME))

    assert first.json()["deleted"] is True
    assert second.status_code == 200 and second.json()["deleted"] is False
    assert client.get("/api/schedules", headers=_headers(signing_key, ME)).json()["items"] == []


def test_없는_일정을_열면_404(signing_key) -> None:
    response = TestClient(app).get(
        "/api/schedules/11111111-2222-4333-8444-555555555555",
        headers=_headers(signing_key, ME),
    )

    assert response.status_code == 404


def test_빈_제목은_받지_않는다(signing_key) -> None:
    """DB의 check 제약이 최종 방어이지만, 거기까지 가면 502로 뭉뚱그려진다."""
    response = TestClient(app).post(
        "/api/schedules",
        json={"title": "", "payload": PAYLOAD},
        headers=_headers(signing_key, ME),
    )

    assert response.status_code == 422
