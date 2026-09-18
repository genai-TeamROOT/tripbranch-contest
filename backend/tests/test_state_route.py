"""세션 상태 조회/삭제 라우터 테스트.

역할: GET/DELETE /api/state/{session_id}가 B 서비스에 위임되는지 검증한다.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.state import service as state_service


def test_get_state_returns_session_context() -> None:
    client = TestClient(app)

    applied = state_service.apply(
        state_service.StateApplyRequest(intent="RECOMMEND", confirmed=True)
    )

    response = client.get(f"/api/state/{applied.session_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == applied.session_id
    assert body["session_exists"] is True


def test_delete_state_removes_session() -> None:
    client = TestClient(app)

    applied = state_service.apply(
        state_service.StateApplyRequest(intent="RECOMMEND", confirmed=True)
    )
    session_id = applied.session_id

    delete_response = client.delete(f"/api/state/{session_id}")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"session_id": session_id, "deleted": True}

    get_response = client.get(f"/api/state/{session_id}")
    assert get_response.status_code == 200
    assert get_response.json()["session_exists"] is False


# ---------------------------------------------------------------- 보관함 (SCHEDULE-12)

def _seed_recommendation(place_id: str, name: str) -> str:
    """추천 이력이 1건 있는 세션을 만들고 session_id를 돌려준다."""
    applied = state_service.apply(
        state_service.StateApplyRequest(intent="RECOMMEND", confirmed=True)
    )
    state_service.record_recommendation(
        state_service.RecordRecommendationRequest(
            session_id=applied.session_id,
            run_id=applied.run_id,
            recommended=[
                state_service.RecommendedPlace(place_id=place_id, rank=1, name=name)
            ],
        )
    )
    return applied.session_id


def test_save_place_route_adds_to_list() -> None:
    client = TestClient(app)
    session_id = _seed_recommendation("p1", "경복궁")

    response = client.post(
        f"/api/state/{session_id}/saved-places", json={"place_id": "p1"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["changed"] is True
    assert [item["place_id"] for item in body["items"]] == ["p1"]
    assert body["items"][0]["name"] == "경복궁"


def test_save_place_route_rejects_unknown_place() -> None:
    client = TestClient(app)
    session_id = _seed_recommendation("p1", "경복궁")

    response = client.post(
        f"/api/state/{session_id}/saved-places", json={"place_id": "unknown"}
    )

    assert response.status_code == 400


def test_remove_saved_place_route_removes_from_list() -> None:
    client = TestClient(app)
    session_id = _seed_recommendation("p1", "경복궁")
    client.post(f"/api/state/{session_id}/saved-places", json={"place_id": "p1"})

    response = client.delete(f"/api/state/{session_id}/saved-places/p1")

    assert response.status_code == 200
    assert response.json()["changed"] is True
    assert response.json()["items"] == []


def test_get_saved_places_route_returns_list() -> None:
    client = TestClient(app)
    session_id = _seed_recommendation("p1", "경복궁")
    client.post(f"/api/state/{session_id}/saved-places", json={"place_id": "p1"})

    response = client.get(f"/api/state/{session_id}/saved-places")

    assert response.status_code == 200
    assert [item["place_id"] for item in response.json()["items"]] == ["p1"]


def test_get_state_route_includes_saved_places() -> None:
    client = TestClient(app)
    session_id = _seed_recommendation("p1", "경복궁")
    client.post(f"/api/state/{session_id}/saved-places", json={"place_id": "p1"})

    response = client.get(f"/api/state/{session_id}")

    assert response.status_code == 200
    assert [item["place_id"] for item in response.json()["saved_places"]] == ["p1"]
