"""사진 검색 라우트의 입력 검증과 응답 조립 테스트."""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from app.errors import AppError
from app.main import app
from app.routes import photo_similar as route
from app.services.photo_similar import PhotoSimilarPlaceRow, PhotoSimilarResult
from app.state.store import get_store

_URL = "/api/places/similar-by-photo"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _image(name: str = "a.jpg", mime: str = "image/jpeg", data: bytes = b"\xff\xd8fake"):
    return {"image": (name, io.BytesIO(data), mime)}


def test_unsupported_format_is_rejected(client) -> None:
    response = client.post(
        _URL, files={"image": ("a.gif", io.BytesIO(b"GIF8"), "image/gif")},
        data={"latitude": "37.5", "longitude": "127.0"},
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_image_format"


def test_empty_image_is_rejected(client) -> None:
    response = client.post(
        _URL, files={"image": ("a.jpg", io.BytesIO(b""), "image/jpeg")},
        data={"latitude": "37.5", "longitude": "127.0"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "empty_image"


def test_oversized_image_is_rejected(client, monkeypatch) -> None:
    monkeypatch.setattr(route, "_MAX_IMAGE_BYTES", 8)
    response = client.post(
        _URL, files=_image(data=b"0123456789"),
        data={"latitude": "37.5", "longitude": "127.0"},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "image_too_large"


def test_successful_response_shape(client, monkeypatch) -> None:
    async def _fake(query, **kwargs):
        assert query.image_bytes
        assert query.latitude == pytest.approx(37.5)
        return PhotoSimilarResult(
            places=(
                PhotoSimilarPlaceRow("2946087", "마우스래빗", "카페", 0.4, 0.89, 6),
            ),
            center_name="기기 GPS 위치",
            center_latitude=37.5,
            center_longitude=127.0,
            candidate_count=42,
            truncated_count=0,
        )

    monkeypatch.setattr(route, "build_photo_similar_places", _fake)
    response = client.post(
        _URL, files=_image(), data={"latitude": "37.5", "longitude": "127.0"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["center_name"] == "기기 GPS 위치"
    assert body["candidate_count"] == 42
    assert body["places"][0]["title"] == "마우스래빗"
    assert body["places"][0]["photo_count"] == 6
    assert body["elapsed_ms"] >= 0


def test_limit_is_clamped(client, monkeypatch) -> None:
    """상한을 넘겨도 응답만 커지고 얻는 게 없다. 조용히 자른다."""
    seen: dict[str, int] = {}

    async def _fake(query, **kwargs):
        seen["limit"] = query.limit
        return PhotoSimilarResult((), "여기", 0.0, 0.0, 0, 0)

    monkeypatch.setattr(route, "build_photo_similar_places", _fake)
    client.post(
        _URL, files=_image(),
        data={"latitude": "37.5", "longitude": "127.0", "limit": "999"},
    )
    assert seen["limit"] == route._MAX_LIMIT


def test_blank_location_query_falls_back_to_gps(client, monkeypatch) -> None:
    """공백만 든 지역명은 안 적은 것과 같게 다룬다."""
    seen: dict[str, object] = {}

    async def _fake(query, **kwargs):
        seen["location_query"] = query.location_query
        return PhotoSimilarResult((), "여기", 0.0, 0.0, 0, 0)

    monkeypatch.setattr(route, "build_photo_similar_places", _fake)
    client.post(
        _URL, files=_image(),
        data={"latitude": "37.5", "longitude": "127.0", "location_query": "   "},
    )
    assert seen["location_query"] is None


def test_session_location_is_used_when_no_explicit_query(client, monkeypatch) -> None:
    """앞 턴에서 "안국역"이라고 말했으면 사진도 거기서 찾는다.

    사진만 다른 규칙으로 위치를 정하면 같은 대화 안에서 "추천은 안국역인데
    사진은 내 위치"가 된다.
    """
    seen: dict[str, object] = {}

    class _Conditions:
        search_center = "안국역"
        current_location = None

    class _Context:
        session_exists = True
        user_conditions = _Conditions()

    monkeypatch.setattr(
        route.state_service, "get_session_context", lambda *a, **k: _Context()
    )

    async def _fake(query, **kwargs):
        seen["location_query"] = query.location_query
        return PhotoSimilarResult((), "안국역", 0.0, 0.0, 0, 0)

    monkeypatch.setattr(route, "build_photo_similar_places", _fake)
    client.post(
        _URL, files=_image(),
        data={"session_id": "s-1", "latitude": "37.5", "longitude": "127.0"},
    )

    assert seen["location_query"] == "안국역"


def test_explicit_query_beats_the_session(client, monkeypatch) -> None:
    """사용자가 이번에 지역을 적었으면 그쪽이 이긴다."""
    seen: dict[str, object] = {}

    class _Conditions:
        search_center = "안국역"
        current_location = None

    class _Context:
        session_exists = True
        user_conditions = _Conditions()

    monkeypatch.setattr(
        route.state_service, "get_session_context", lambda *a, **k: _Context()
    )

    async def _fake(query, **kwargs):
        seen["location_query"] = query.location_query
        return PhotoSimilarResult((), "성수동", 0.0, 0.0, 0, 0)

    monkeypatch.setattr(route, "build_photo_similar_places", _fake)
    client.post(_URL, files=_image(), data={"session_id": "s-1", "location_query": "성수동"})

    assert seen["location_query"] == "성수동"


def test_session_lookup_failure_falls_back_to_coordinates(client, monkeypatch) -> None:
    """세션 조회가 실패해도 사진 검색 자체를 막지 않는다."""
    seen: dict[str, object] = {}

    def _boom(*args, **kwargs):
        raise RuntimeError("state store down")

    monkeypatch.setattr(route.state_service, "get_session_context", _boom)

    async def _fake(query, **kwargs):
        seen["location_query"] = query.location_query
        seen["latitude"] = query.latitude
        return PhotoSimilarResult((), "기기 GPS 위치", 0.0, 0.0, 0, 0)

    monkeypatch.setattr(route, "build_photo_similar_places", _fake)
    response = client.post(
        _URL, files=_image(),
        data={"session_id": "s-1", "latitude": "37.5", "longitude": "127.0"},
    )

    assert response.status_code == 200
    assert seen["location_query"] is None
    assert seen["latitude"] == pytest.approx(37.5)


# ------------------------------------------------------- 대화 기록 (사진 검색 턴)


def _result(*names: str, center_name: str = "성수동", candidate_count: int = 42):
    return PhotoSimilarResult(
        places=tuple(
            PhotoSimilarPlaceRow(f"id-{index}", name, "카페", 0.4, 0.9, 6)
            for index, name in enumerate(names)
        ),
        center_name=center_name,
        center_latitude=37.5,
        center_longitude=127.0,
        candidate_count=candidate_count,
        truncated_count=0,
    )


def test_사진으로_시작한_대화도_세션을_받는다(client, monkeypatch) -> None:
    """세션 없이 사진부터 올려도 서버가 발급해 응답에 싣는다.

    홈에서 발화 없이 사진을 고르는 경로가 이렇다. 발급하지 않으면 이 턴이
    어디에도 안 남고, 이어지는 발화가 또 새 대화를 시작한다.
    """
    monkeypatch.setattr(
        route, "build_photo_similar_places", lambda *a, **k: _async(_result("마우스래빗"))
    )
    response = client.post(
        _URL, files=_image(), data={"latitude": "37.5", "longitude": "127.0"}
    )

    assert response.status_code == 200
    assert response.json()["session_id"]


def test_기록_두_건이_함께_남는다(client, monkeypatch) -> None:
    """최근 대화(모델 맥락)와 화면 기록을 같이 남긴다.

    한쪽만 남기면 복원 판정("화면 기록 수 >= 최근 대화 수")이 뒤집혀 그 세션이
    통째로 옛 복원 방식으로 떨어진다.
    """
    monkeypatch.setattr(
        route,
        "build_photo_similar_places",
        lambda *a, **k: _async(_result("마우스래빗", "어니언")),
    )
    body = client.post(
        _URL, files=_image(), data={"latitude": "37.5", "longitude": "127.0"}
    ).json()

    state = get_store().get_state(body["session_id"])
    assert state is not None
    turn = state.recent_turns[-1]
    assert turn.user_input == route.PHOTO_SEARCH_USER_INPUT
    # 다음 턴이 "그중에 첫 번째"를 풀려면 이름이 남아 있어야 한다.
    assert turn.place_names == ["마우스래빗", "어니언"]
    assert "성수동" in (turn.assistant_message or "")
    # 인텐트를 타지 않는 턴이라 분류된 값이 없다. 지어내지 않는다.
    assert turn.intent is None

    messages = get_store().get_session_messages(body["session_id"])
    assert len(messages) == 1
    assert messages[0].payload["kind"] == route.PHOTO_SEARCH_RECORD_KIND
    assert messages[0].user_input == route.PHOTO_SEARCH_USER_INPUT


def test_사진은_기록에_담기지_않는다(client, monkeypatch) -> None:
    """올린 사진은 임베딩만 하고 버린다. 기록에도 남지 않아야 한다."""
    monkeypatch.setattr(
        route, "build_photo_similar_places", lambda *a, **k: _async(_result("마우스래빗"))
    )
    body = client.post(
        _URL,
        files=_image(data=b"\xff\xd8secret-photo-bytes"),
        data={"latitude": "37.5", "longitude": "127.0"},
    ).json()

    messages = get_store().get_session_messages(body["session_id"])
    assert "secret-photo-bytes" not in str(messages[0].payload)
    assert "image" not in messages[0].payload


def test_제목은_지역명으로_찾았을_때만_기준점을_넣는다(client, monkeypatch) -> None:
    """좌표로만 찾았으면 기준점 이름이 "현재 위치"인데, 목록에서 다시 볼 때
    그 말은 아무것도 가리키지 않는다."""
    monkeypatch.setattr(
        route,
        "build_photo_similar_places",
        lambda *a, **k: _async(_result("마우스래빗", center_name="성수동")),
    )
    named = client.post(
        _URL, files=_image(), data={"location_query": "성수동"}
    ).json()
    assert get_store().get_state(named["session_id"]).title == "성수동 사진으로 찾은 곳"

    monkeypatch.setattr(
        route,
        "build_photo_similar_places",
        lambda *a, **k: _async(_result("마우스래빗", center_name="현재 위치")),
    )
    gps = client.post(
        _URL, files=_image(), data={"latitude": "37.5", "longitude": "127.0"}
    ).json()
    assert get_store().get_state(gps["session_id"]).title == "사진으로 찾은 곳"


def test_이미_제목이_있는_대화는_뺏기지_않는다(client, monkeypatch) -> None:
    """대화 도중의 사진 검색이 첫 발화로 붙은 제목을 덮어쓰면 안 된다."""
    monkeypatch.setattr(
        route, "build_photo_similar_places", lambda *a, **k: _async(_result("마우스래빗"))
    )
    first = client.post(
        _URL, files=_image(), data={"latitude": "37.5", "longitude": "127.0"}
    ).json()
    state = get_store().get_state(first["session_id"])
    state.title = "성수동 카페 추천해줘"
    get_store().save_state(state)

    client.post(
        _URL,
        files=_image(),
        data={
            "session_id": first["session_id"],
            "latitude": "37.5",
            "longitude": "127.0",
        },
    )

    assert get_store().get_state(first["session_id"]).title == "성수동 카페 추천해줘"


def test_위치를_못_잡으면_세션이_생기지_않는다(client, monkeypatch) -> None:
    """실패로 끝난 요청까지 세션을 만들면 빈 대화가 목록에 쌓인다.

    세션 확보를 검색 성공 뒤에 두는 이유가 이것이다 — 일반 발화도 해석이 끝난
    자리에서 발급하지, 요청이 도착한 자리에서 발급하지 않는다.
    """

    def _no_location(*args, **kwargs):
        raise AppError(
            code="location_required",
            message="어디 근처에서 찾을까요?",
            status_code=422,
            retryable=True,
        )

    monkeypatch.setattr(route, "build_photo_similar_places", _no_location)
    before = set(get_store()._states)

    response = client.post(_URL, files=_image())

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "location_required"
    assert set(get_store()._states) == before


def test_기록_저장이_실패해도_응답은_그대로_나간다(client, monkeypatch) -> None:
    """이미 다 만들어진 결과를 저장 장애가 뒤집으면 안 된다."""
    monkeypatch.setattr(
        route, "build_photo_similar_places", lambda *a, **k: _async(_result("마우스래빗"))
    )

    def _boom(*args, **kwargs):
        raise RuntimeError("state store down")

    monkeypatch.setattr(route.state_service, "append_conversation_turn", _boom)

    response = client.post(
        _URL, files=_image(), data={"latitude": "37.5", "longitude": "127.0"}
    )

    assert response.status_code == 200
    assert response.json()["places"][0]["title"] == "마우스래빗"


async def _async(value):
    return value
