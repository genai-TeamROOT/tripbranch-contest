"""GET /api/sessions · PATCH /api/state/{session_id}/title — 채팅 히스토리. (TP-222 후속)

사이드바의 채팅 히스토리는 그동안 localStorage 목업이었고 **항목을 추가하는
코드가 아예 없어** 늘 비어 있었다. 이 파일은 그 목록이 실제 대화에서 나오고,
남의 대화가 섞이지 않는지를 본다.

가장 중요한 것 셋이다 — "남의 목록이 안 보인다", "남의 대화 이름을 못 바꾼다",
"제목이 저절로 바뀌지 않는다".
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.auth.principal import Principal
from app.main import app
from app.state import service as state_service
from tests.auth.conftest import make_token

ME = "3f1a9c04-0000-4000-8000-000000000001"
OTHER = "3f1a9c04-0000-4000-8000-000000000002"


def _headers(signing_key, sub: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(signing_key, sub=sub)}"}


def _start_chat(sub: str, *inputs: str, location: str | None = None) -> str:
    """세션을 만들고 대화를 몇 턴 넣는다. 첫 입력이 제목이 된다.

    location을 주면 그 턴의 조건에 search_center로 실린다 — 실제 흐름에서 A가
    조건을 병합한 뒤 턴을 남기는 순서와 같다.
    """
    owner = Principal(user_id=sub, is_anonymous=True)
    applied = state_service.apply(
        state_service.StateApplyRequest(intent="RECOMMEND", confirmed=True),
        principal=owner,
    )
    if location is not None:
        from app.state.store import get_store

        store = get_store()
        state = store.get_state(applied.session_id)
        assert state is not None
        state.user_conditions.search_center = location
        store.save_state(state)
    for user_input in inputs:
        state_service.append_conversation_turn(
            state_service.AppendConversationTurnRequest(
                session_id=applied.session_id,
                turn=state_service.ConversationTurn(
                    user_input=user_input,
                    assistant_message="네",
                    intent="RECOMMEND",
                ),
            )
        )
    return applied.session_id


# ---------------------------------------------------------------- 목록

def test_토큰_없이_목록을_부르면_401(signing_key) -> None:
    assert TestClient(app).get("/api/sessions").status_code == 401


def test_내_대화가_제목과_위치와_함께_나온다(signing_key) -> None:
    _start_chat(ME, "비 오는데 실내 어디 갈까", location="경복궁")

    response = TestClient(app).get("/api/sessions", headers=_headers(signing_key, ME))

    assert response.status_code == 200
    sessions = response.json()["sessions"]
    assert sessions[0]["title"] == "비 오는데 실내 어디 갈까"
    assert sessions[0]["location"] == "경복궁"


# 위치는 처음 잡힌 값으로 굳는다. 제목이 첫 질문인 것과 짝을 맞춘 규칙이다 —
# 대화 도중에 지역을 옮겨도 목록의 한 줄이 저절로 바뀌지 않는다.
def test_대화_도중_지역을_옮겨도_목록의_위치는_그대로다(signing_key) -> None:
    from app.state.store import get_store

    session_id = _start_chat(ME, "경복궁 근처", location="경복궁")
    store = get_store()
    state = store.get_state(session_id)
    assert state is not None
    state.user_conditions.search_center = "홍대"
    store.save_state(state)
    state_service.append_conversation_turn(
        state_service.AppendConversationTurnRequest(
            session_id=session_id,
            turn=state_service.ConversationTurn(user_input="홍대는 어때"),
        )
    )

    sessions = (
        TestClient(app).get("/api/sessions", headers=_headers(signing_key, ME)).json()["sessions"]
    )

    assert sessions[0]["location"] == "경복궁"


# 이어가기는 낡은 조건을 버리면서 user_conditions를 비운다. 위치를 거기서
# 그때그때 읽었다면 지난 대화를 한 번 여는 것만으로 목록에서 사라졌을 것이다.
def test_대화를_이어가도_목록의_위치가_남는다(signing_key) -> None:
    session_id = _start_chat(ME, "경복궁 근처", location="경복궁")
    _expire(session_id)
    client = TestClient(app)

    client.post(f"/api/sessions/{session_id}/resume", headers=_headers(signing_key, ME))

    sessions = client.get("/api/sessions", headers=_headers(signing_key, ME)).json()["sessions"]
    assert sessions[0]["location"] == "경복궁"


# 이 파일에서 가장 중요한 테스트다.
def test_남의_대화는_목록에_안_섞인다(signing_key) -> None:
    _start_chat(OTHER, "남의 대화")
    _start_chat(ME, "내 대화")

    response = TestClient(app).get("/api/sessions", headers=_headers(signing_key, ME))

    titles = [item["title"] for item in response.json()["sessions"]]
    assert "내 대화" in titles
    assert "남의 대화" not in titles


def test_대화를_시작하지_않은_세션은_목록에_없다(signing_key) -> None:
    """세션은 만들어졌지만 아무 말도 안 한 경우다 — 사용자가 보기에 대화가 아니다.

    인메모리 저장소가 테스트 사이에 남으므로 목록 전체를 비교하지 않고 이
    세션이 빠졌는지만 본다.
    """
    empty_session = _start_chat(ME)  # 턴 없음
    talked_session = _start_chat(ME, "진짜 대화")

    sessions = (
        TestClient(app).get("/api/sessions", headers=_headers(signing_key, ME)).json()["sessions"]
    )

    listed = {item["session_id"] for item in sessions}
    assert talked_session in listed
    assert empty_session not in listed


def test_최근_대화가_앞에_온다(signing_key) -> None:
    _start_chat(ME, "먼저 한 대화")
    _start_chat(ME, "나중에 한 대화")

    sessions = (
        TestClient(app).get("/api/sessions", headers=_headers(signing_key, ME)).json()["sessions"]
    )

    assert sessions[0]["title"] == "나중에 한 대화"


# recent_turns는 MAX_RECENT_TURNS(=5)개만 남는다. 제목을 그 배열에서 파생하면
# 대화를 이어갈수록 사이드바의 제목이 저절로 바뀐다 — 그래서 컬럼에 박는다.
def test_대화가_길어져도_제목이_바뀌지_않는다(signing_key) -> None:
    _start_chat(ME, "첫 질문", "둘", "셋", "넷", "다섯", "여섯", "일곱")

    sessions = (
        TestClient(app).get("/api/sessions", headers=_headers(signing_key, ME)).json()["sessions"]
    )

    assert sessions[0]["title"] == "첫 질문"


# ---------------------------------------------------------------- 이름 바꾸기

def test_이름을_바꾸면_목록에_반영된다(signing_key) -> None:
    session_id = _start_chat(ME, "원래 제목")
    client = TestClient(app)

    response = client.patch(
        f"/api/state/{session_id}/title",
        json={"title": "내가 정한 이름"},
        headers=_headers(signing_key, ME),
    )

    assert response.status_code == 200
    sessions = client.get("/api/sessions", headers=_headers(signing_key, ME)).json()["sessions"]
    assert sessions[0]["title"] == "내가 정한 이름"


def test_이름을_바꾼_뒤_대화를_이어가도_그_이름이_남는다(signing_key) -> None:
    session_id = _start_chat(ME, "원래 제목")
    client = TestClient(app)
    client.patch(
        f"/api/state/{session_id}/title",
        json={"title": "내가 정한 이름"},
        headers=_headers(signing_key, ME),
    )

    state_service.append_conversation_turn(
        state_service.AppendConversationTurnRequest(
            session_id=session_id,
            turn=state_service.ConversationTurn(user_input="이어서", assistant_message="네"),
        )
    )

    sessions = client.get("/api/sessions", headers=_headers(signing_key, ME)).json()["sessions"]
    assert sessions[0]["title"] == "내가 정한 이름"


def test_남의_대화_이름은_못_바꾼다(signing_key) -> None:
    session_id = _start_chat(OTHER, "남의 대화")

    response = TestClient(app).patch(
        f"/api/state/{session_id}/title",
        json={"title": "가로채기"},
        headers=_headers(signing_key, ME),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "session_ownership_mismatch"


def test_없는_대화_이름을_바꾸면_404(signing_key) -> None:
    response = TestClient(app).patch(
        "/api/state/sess_없는세션/title",
        json={"title": "아무거나"},
        headers=_headers(signing_key, ME),
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"


def test_만료된_대화도_이름을_바꿀_수_있다(signing_key) -> None:
    """목록에 뜨는 대화는 전부 이름을 바꿀 수 있어야 한다.

    TTL로 거르던 동안에는 여기가 404였다. 실측으로 신원이 붙은 대화 106개 중
    살아 있는 것이 1개뿐이라, 목록의 거의 모든 줄에서 이름 바꾸기가 실패했다.
    """
    from app.state.store import get_store

    session_id = _start_chat(ME, "만료된 대화")
    store = get_store()
    state = store.get_state(session_id)
    assert state is not None
    state.status = "expired"
    store.save_state(state)

    response = TestClient(app).patch(
        f"/api/state/{session_id}/title",
        json={"title": "바꾼 이름"},
        headers=_headers(signing_key, ME),
    )

    assert response.status_code == 200
    assert response.json()["title"] == "바꾼 이름"


def test_신원이_안_붙은_세션은_이름을_못_바꾼다(signing_key) -> None:
    """주인이 없는 세션은 누구의 것도 아니다.

    verify_ownership은 이 경우를 통과시키는데(Phase 4 전 과도기), 이 엔드포인트는
    "내 대화"만 다룬다 — 열기·이어가기와 같은 기준으로 막는다.
    """
    from app.state.store import get_store

    session_id = _start_chat(ME, "주인 없는 대화")
    store = get_store()
    state = store.get_state(session_id)
    assert state is not None
    state.user_id = None
    store.save_state(state)

    response = TestClient(app).patch(
        f"/api/state/{session_id}/title",
        json={"title": "가로채기"},
        headers=_headers(signing_key, ME),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "session_ownership_mismatch"


# ---------------------------------------------------------------- 지난 대화 열기


def test_지난_대화를_열면_주고받은_말이_나온다(signing_key) -> None:
    session_id = _start_chat(ME, "비 오는 날 갈 곳", "실내가 좋아")

    response = TestClient(app).get(f"/api/sessions/{session_id}", headers=_headers(signing_key, ME))

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "비 오는 날 갈 곳"
    assert [turn["user_input"] for turn in body["turns"]] == ["비 오는 날 갈 곳", "실내가 좋아"]


# 이 파일에서 두 번째로 중요한 테스트다. TTL을 적용하면 목록의 거의 모든 항목이
# 열리지 않는다 — 세션은 30분이면 만료되는데 히스토리는 그보다 오래된 대화를
# 보여주는 것이 목적이다.
def test_만료된_대화도_내용은_열린다(signing_key) -> None:
    from datetime import timedelta

    from app.state.schema import now_kst
    from app.state.store import get_store

    session_id = _start_chat(ME, "아주 오래된 질문")
    store = get_store()
    state = store.get_state(session_id)
    assert state is not None
    state.last_active_at = now_kst() - timedelta(days=3)
    store.save_state(state)

    response = TestClient(app).get(f"/api/sessions/{session_id}", headers=_headers(signing_key, ME))

    assert response.status_code == 200
    assert response.json()["turns"][0]["user_input"] == "아주 오래된 질문"
    # 다만 이어서 대화할 수는 없다 — 화면이 그 사실을 밝혀야 한다.
    assert response.json()["resumable"] is False


def test_남의_대화는_열리지_않는다(signing_key) -> None:
    session_id = _start_chat(OTHER, "남의 대화")

    response = TestClient(app).get(f"/api/sessions/{session_id}", headers=_headers(signing_key, ME))

    assert response.status_code == 403


def test_신원이_안_붙은_세션은_열리지_않는다(signing_key) -> None:
    """verify_ownership은 user_id가 비면 통과시키지만, 여기는 '내 대화'만 다룬다."""
    applied = state_service.apply(
        state_service.StateApplyRequest(intent="RECOMMEND", confirmed=True)
    )
    state_service.append_conversation_turn(
        state_service.AppendConversationTurnRequest(
            session_id=applied.session_id,
            turn=state_service.ConversationTurn(user_input="주인 없는 대화"),
        )
    )

    response = TestClient(app).get(
        f"/api/sessions/{applied.session_id}", headers=_headers(signing_key, ME)
    )

    assert response.status_code == 403


# ---------------------------------------------------------------- 이어서 대화하기


def _expire(session_id: str, *, days: int = 3):
    """세션을 오래 묵혀 만료 상태로 만든다."""
    from datetime import timedelta

    from app.state.schema import now_kst
    from app.state.store import get_store

    store = get_store()
    state = store.get_state(session_id)
    assert state is not None
    state.last_active_at = now_kst() - timedelta(days=days)
    store.save_state(state)
    return store


def test_만료된_대화도_이어갈_수_있다(signing_key) -> None:
    session_id = _start_chat(ME, "사흘 전 질문")
    _expire(session_id)

    response = TestClient(app).post(
        f"/api/sessions/{session_id}/resume", headers=_headers(signing_key, ME)
    )

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    assert body["resumable"] is True
    # 대화는 그대로 남는다 — 이어간다는 건 앞의 말이 남는다는 뜻이다.
    assert body["turns"][0]["user_input"] == "사흘 전 질문"


# 이 절에서 가장 중요한 테스트다. 이어가기가 실제로 하는 일은 "다음 턴이 같은
# 세션에 붙는 것"이고, 그 판정은 apply()의 get_or_create_session이 한다 —
# 되살리지 않으면 여기서 새 session_id가 발급되어 목록에 줄이 하나 더 생긴다.
# (append_conversation_turn으로는 이 테스트가 성립하지 않는다. 그 함수는 TTL을
#  보지 않고 행에 바로 붙이므로 되살리지 않아도 통과한다.)
def test_이어간_뒤_다음_턴이_같은_세션에_붙는다(signing_key) -> None:
    owner = Principal(user_id=ME, is_anonymous=True)
    session_id = _start_chat(ME, "이어갈 대화")
    _expire(session_id)

    TestClient(app).post(f"/api/sessions/{session_id}/resume", headers=_headers(signing_key, ME))
    applied = state_service.apply(
        state_service.StateApplyRequest(
            session_id=session_id, intent="RECOMMEND", confirmed=True
        ),
        principal=owner,
    )

    assert applied.session_id == session_id
    assert applied.session_created is False


def test_되살리지_않으면_다음_턴이_새_세션으로_간다(signing_key) -> None:
    """위 테스트의 대조군이다. 지금 화면이 '새 대화로 시작된다'고 밝히는 이유다."""
    owner = Principal(user_id=ME, is_anonymous=True)
    session_id = _start_chat(ME, "만료된 대화")
    _expire(session_id)

    applied = state_service.apply(
        state_service.StateApplyRequest(
            session_id=session_id, intent="RECOMMEND", confirmed=True
        ),
        principal=owner,
    )

    assert applied.session_id != session_id
    assert applied.session_created is True


def test_이어갈_때_낡은_조건은_버린다(signing_key) -> None:
    """만료가 하던 두 일 중 '낡은 조건 버리기'는 그대로 한다.

    사흘 전 "비 오는데"가 오늘의 조건으로 남으면 실내만 추천하게 된다.
    """
    session_id = _start_chat(ME, "비 오는데 어디 갈까")
    store = _expire(session_id)
    state = store.get_state(session_id)
    assert state is not None
    state.user_conditions.weather = "rain"
    state.user_conditions.place_types = ["cafe"]
    state.api_context.gps_location = "37.5,127.0"
    state.pending_clarification = "location_required"
    store.save_state(state)

    TestClient(app).post(f"/api/sessions/{session_id}/resume", headers=_headers(signing_key, ME))

    resumed = store.get_state(session_id)
    assert resumed is not None
    assert resumed.user_conditions.weather is None
    assert resumed.user_conditions.place_types == []
    assert resumed.api_context.gps_location is None
    assert resumed.pending_clarification is None
    assert resumed.status == "active"


def test_이어갈_때_거절한_곳은_계속_제외된다(signing_key) -> None:
    """추천 이력은 비우고 거절 이력은 남긴다.

    사흘 전에 본 곳을 오늘 다시 보여주는 건 문제가 아니지만, 싫다고 한 곳을
    다시 보여주는 건 문제다.
    """
    from app.state import history as history_module
    from app.state.schema import RecommendedItemInput

    session_id = _start_chat(ME, "추천받은 대화")
    store = _expire(session_id)
    history_module.record_recommended(
        store,
        session_id,
        "run_1",
        [
            RecommendedItemInput(place_id="본곳", rank=1),
            RecommendedItemInput(place_id="싫은곳", rank=2),
        ],
    )
    history_module.record_rejected(store, session_id, "run_1", [("싫은곳", "not_interested")])

    TestClient(app).post(f"/api/sessions/{session_id}/resume", headers=_headers(signing_key, ME))

    excluded = history_module.get_exclusion_place_ids(store, session_id)
    assert "싫은곳" in excluded
    assert "본곳" not in excluded


def test_살아있는_대화는_조건을_건드리지_않는다(signing_key) -> None:
    """TTL 이내면 방금까지 하던 대화다 — 버릴 낡은 조건이 없다."""
    from app.state.store import get_store

    session_id = _start_chat(ME, "방금 한 대화")
    store = get_store()
    state = store.get_state(session_id)
    assert state is not None
    state.user_conditions.weather = "rain"
    store.save_state(state)

    response = TestClient(app).post(
        f"/api/sessions/{session_id}/resume", headers=_headers(signing_key, ME)
    )

    assert response.json()["resumable"] is True
    kept = store.get_state(session_id)
    assert kept is not None
    assert kept.user_conditions.weather == "rain"


def test_남의_대화는_이어갈_수_없다(signing_key) -> None:
    session_id = _start_chat(OTHER, "남의 대화")
    _expire(session_id)

    response = TestClient(app).post(
        f"/api/sessions/{session_id}/resume", headers=_headers(signing_key, ME)
    )

    assert response.status_code == 403


def test_토큰_없이는_이어갈_수_없다(signing_key) -> None:
    session_id = _start_chat(ME, "내 대화")

    assert TestClient(app).post(f"/api/sessions/{session_id}/resume").status_code == 401


# ---------------------------------------------------------------- 그때 본 곳


def _record(session_id: str, run_id: str, *names: str):
    """추천 이력을 남긴다. 실제 흐름에서 A가 하는 일이다."""
    from app.state import history as history_module
    from app.state.schema import RecommendedItemInput
    from app.state.store import get_store

    store = get_store()
    history_module.record_recommended(
        store,
        session_id,
        run_id,
        [
            RecommendedItemInput(place_id=f"place_{name}", rank=index + 1, name=name)
            for index, name in enumerate(names)
        ],
    )
    return store


def test_지난_대화에_그때_본_곳이_함께_온다(signing_key) -> None:
    session_id = _start_chat(ME, "실내 어디 갈까")
    _record(session_id, "run_1", "국립중앙박물관", "리움미술관")

    response = TestClient(app).get(f"/api/sessions/{session_id}", headers=_headers(signing_key, ME))

    names = [item["name"] for item in response.json()["recommendations"]]
    assert names == ["국립중앙박물관", "리움미술관"]


# 이 절에서 가장 중요한 테스트다. 되살리기는 추천 이력을 비우는데(다음 추천에서
# 뺄 목록), 화면에 그릴 "그때 본 곳"은 그 같은 데이터에서 나온다. 비운 뒤에
# 읽으면 카드가 통째로 사라진다.
def test_되살려도_그때_본_곳은_응답에_남는다(signing_key) -> None:
    session_id = _start_chat(ME, "실내 어디 갈까")
    _record(session_id, "run_1", "국립중앙박물관")
    _expire(session_id)

    response = TestClient(app).post(
        f"/api/sessions/{session_id}/resume", headers=_headers(signing_key, ME)
    )

    assert [item["name"] for item in response.json()["recommendations"]] == ["국립중앙박물관"]


def test_되살린_뒤에는_그_곳이_제외_목록에서_빠진다(signing_key) -> None:
    """위 테스트의 짝이다. 화면에는 남기고 제외 목록에서는 지운다."""
    from app.state import history as history_module

    session_id = _start_chat(ME, "실내 어디 갈까")
    store = _record(session_id, "run_1", "국립중앙박물관")
    _expire(session_id)

    TestClient(app).post(f"/api/sessions/{session_id}/resume", headers=_headers(signing_key, ME))

    assert history_module.get_exclusion_place_ids(store, session_id) == []


# 추천은 그 턴이 기록되기 **전에** 남는다(실측 102쌍 중 97쌍이 0~120초 먼저).
# 그래서 "남은 가장 오래된 턴보다 먼저 나간 것을 버린다"로 자르면 안 된다 —
# 서버는 시간순으로 그대로 주고, 말풍선과 짝짓는 일은 화면이 한다.
def test_턴보다_먼저_기록된_추천도_그대로_준다(signing_key) -> None:
    from datetime import timedelta

    from app.state.schema import now_kst
    from app.state.store import get_store

    session_id = _start_chat(ME, "실내 어디 갈까")
    _record(session_id, "run_1", "턴보다먼저")
    store = get_store()
    history = store.get_history(session_id)
    assert history is not None
    history.recommended[0].shown_at = now_kst() - timedelta(seconds=97)
    store.save_history(history)

    response = TestClient(app).get(f"/api/sessions/{session_id}", headers=_headers(signing_key, ME))

    assert [item["name"] for item in response.json()["recommendations"]] == ["턴보다먼저"]


def test_추천은_시간순으로_온다(signing_key) -> None:
    session_id = _start_chat(ME, "실내 어디 갈까")
    _record(session_id, "run_1", "먼저 본 곳")
    _record(session_id, "run_2", "나중에 본 곳")

    response = TestClient(app).get(f"/api/sessions/{session_id}", headers=_headers(signing_key, ME))

    names = [item["name"] for item in response.json()["recommendations"]]
    assert names == ["먼저 본 곳", "나중에 본 곳"]


def test_추천을_받지_않은_대화는_빈_목록이다(signing_key) -> None:
    session_id = _start_chat(ME, "그냥 물어본 대화")

    response = TestClient(app).get(f"/api/sessions/{session_id}", headers=_headers(signing_key, ME))

    assert response.json()["recommendations"] == []


# ---------------------------------------------------------------- 화면 기록


def _record_message(session_id: str, user_input: str, message: str) -> None:
    """A가 한 턴을 마치고 남기는 화면 기록. payload는 AgentResponse 직렬화다."""
    state_service.record_session_message(
        state_service.RecordSessionMessageRequest(
            session_id=session_id,
            run_id="run_1",
            user_input=user_input,
            payload={"message": message, "recommendations": None},
        )
    )


def test_지난_대화에_화면_기록이_함께_온다(signing_key) -> None:
    session_id = _start_chat(ME, "실내 어디 갈까")
    _record_message(session_id, "실내 어디 갈까", "박물관을 찾아봤어요")

    response = TestClient(app).get(f"/api/sessions/{session_id}", headers=_headers(signing_key, ME))

    messages = response.json()["messages"]
    assert [item["payload"]["message"] for item in messages] == ["박물관을 찾아봤어요"]
    assert messages[0]["user_input"] == "실내 어디 갈까"


# 이 절에서 가장 중요한 테스트다. 화면 기록을 따로 둔 이유가 정확히 이것이다 —
# recent_turns는 MAX_RECENT_TURNS(=5)에서 잘리지만 화면은 대화 전체를 보여야 한다.
def test_다섯_턴을_넘겨도_화면_기록은_잘리지_않는다(signing_key) -> None:
    session_id = _start_chat(ME, "첫 질문", "둘", "셋", "넷", "다섯", "여섯", "일곱")
    for index in range(7):
        _record_message(session_id, f"{index}번 질문", f"{index}번 답변")

    body = TestClient(app).get(
        f"/api/sessions/{session_id}", headers=_headers(signing_key, ME)
    ).json()

    assert len(body["turns"]) == 5  # 모델 맥락은 잘린 채로 그대로다
    assert len(body["messages"]) == 7  # 화면은 전부 남는다


def test_되살려도_화면_기록은_지워지지_않는다(signing_key) -> None:
    """resume이 지우는 것은 '다음 추천에서 뺄 곳'이지 '그때 화면에 나갔던 것'이 아니다."""
    session_id = _start_chat(ME, "실내 어디 갈까")
    _record_message(session_id, "실내 어디 갈까", "박물관을 찾아봤어요")
    _expire(session_id)

    response = TestClient(app).post(
        f"/api/sessions/{session_id}/resume", headers=_headers(signing_key, ME)
    )

    assert [item["payload"]["message"] for item in response.json()["messages"]] == [
        "박물관을 찾아봤어요"
    ]


def test_화면_기록은_오래된_것이_앞이다(signing_key) -> None:
    session_id = _start_chat(ME, "실내 어디 갈까")
    _record_message(session_id, "먼저", "먼저 답변")
    _record_message(session_id, "나중", "나중 답변")

    body = TestClient(app).get(
        f"/api/sessions/{session_id}", headers=_headers(signing_key, ME)
    ).json()

    assert [item["payload"]["message"] for item in body["messages"]] == ["먼저 답변", "나중 답변"]


def test_없는_세션에는_화면_기록을_남기지_않는다(signing_key) -> None:
    """A의 실패 경로가 유령 세션을 만들지 않게 한다."""
    from app.state.store import get_store

    _record_message("sess_없는세션", "질문", "답변")

    assert get_store().get_session_messages("sess_없는세션") == []


def test_화면_기록에_세션의_주인이_함께_남는다(signing_key) -> None:
    from app.state.store import get_store

    session_id = _start_chat(ME, "내 대화")
    _record_message(session_id, "질문", "답변")

    assert get_store().get_session_messages(session_id)[0].user_id == ME


def test_정리_스크립트가_화면_기록도_지운다(signing_key) -> None:
    """세션 행만 지우고 화면 기록을 남기면 대화 원문이 살아남는다."""
    from app.state.store import get_store
    from scripts.cleanup_expired_sessions import _delete_one

    session_id = _start_chat(ME, "지워질 대화")
    _record_message(session_id, "질문", "답변")
    store = get_store()

    _delete_one(store, session_id)

    assert store.get_session_messages(session_id) == []


# 대화를 지우면 화면 기록도 지워져야 한다. 목록에서 사라지는 것과 실제로
# 지워지는 것이 다르면, 사용자는 지웠다고 믿는데 원문이 DB에 남는다.
def test_대화를_지우면_화면_기록도_지워진다(signing_key) -> None:
    from app.state.store import get_store

    session_id = _start_chat(ME, "지울 대화")
    _record_message(session_id, "질문", "답변")
    store = get_store()
    assert store.get_session_messages(session_id) != []

    response = TestClient(app).delete(
        f"/api/state/{session_id}", headers=_headers(signing_key, ME)
    )

    assert response.status_code == 200
    assert store.get_session_messages(session_id) == []


def test_대화를_지우면_조건_변경_기록도_지워진다(signing_key) -> None:
    """before/after 값에 사용자 발화에서 나온 문자열이 들어간다."""
    from app.state.schema import ConditionChangeLog
    from app.state.store import get_store

    session_id = _start_chat(ME, "경복궁 근처")
    store = get_store()
    store.append_change_logs(
        [
            ConditionChangeLog(
                session_id=session_id,
                run_id="run_1",
                seq=1,
                op="set",
                field="search_center",
                after_value="경복궁",
            )
        ]
    )

    TestClient(app).delete(f"/api/state/{session_id}", headers=_headers(signing_key, ME))

    assert store.get_change_logs(session_id) == []


# trace_records는 일부러 남긴다. 사용자 텍스트가 없고 LLMOps 통계가 읽는
# 지표라, 대화를 지울 때마다 빠지면 집계가 사실과 달라진다.
def test_대화를_지워도_trace는_남는다(signing_key) -> None:
    from app.state.schema import TraceRecord
    from app.state.store import get_store

    session_id = _start_chat(ME, "지울 대화")
    store = get_store()
    store.append_traces(
        [
            TraceRecord(
                session_id=session_id, run_id="run_1", trace_id="trace_1", step="llm_extract"
            )
        ]
    )

    TestClient(app).delete(f"/api/state/{session_id}", headers=_headers(signing_key, ME))

    assert len(store.get_traces(session_id)) == 1


# 근사치 카드는 화면 기록으로 되돌릴 수 없는 대화에만 쓰인다. 기록이 온전한
# 대화에까지 실어 보내면 이력을 한 번 더 읽고 쓰이지도 않는 값을 전송한다.
def test_기록이_온전하면_근사치_카드를_보내지_않는다(signing_key) -> None:
    session_id = _start_chat(ME, "실내 어디 갈까")
    _record(session_id, "run_1", "국립중앙박물관")
    _record_message(session_id, "실내 어디 갈까", "박물관을 찾아봤어요")

    body = TestClient(app).get(
        f"/api/sessions/{session_id}", headers=_headers(signing_key, ME)
    ).json()

    assert body["restore_from_messages"] is True
    assert body["recommendations"] == []
    assert len(body["messages"]) == 1


def test_기록이_없으면_근사치_카드로_되돌린다(signing_key) -> None:
    session_id = _start_chat(ME, "실내 어디 갈까")
    _record(session_id, "run_1", "국립중앙박물관")

    body = TestClient(app).get(
        f"/api/sessions/{session_id}", headers=_headers(signing_key, ME)
    ).json()

    assert body["restore_from_messages"] is False
    assert [item["name"] for item in body["recommendations"]] == ["국립중앙박물관"]


# 저장이 한 번 실패해 턴 하나가 빠진 기록. 이걸 온전한 것으로 취급하면 그
# 대화가 조용히 일부만 보인다.
def test_기록이_말풍선보다_모자라면_온전하지_않다고_알린다(signing_key) -> None:
    session_id = _start_chat(ME, "첫 질문", "둘째 질문")
    _record(session_id, "run_1", "국립중앙박물관")
    _record_message(session_id, "첫 질문", "첫 답변")  # 둘째 턴의 기록이 빠졌다

    body = TestClient(app).get(
        f"/api/sessions/{session_id}", headers=_headers(signing_key, ME)
    ).json()

    assert body["restore_from_messages"] is False
    assert [item["name"] for item in body["recommendations"]] == ["국립중앙박물관"]


def test_이어간_대화도_기록이_온전하면_그것만_준다(signing_key) -> None:
    session_id = _start_chat(ME, "실내 어디 갈까")
    _record(session_id, "run_1", "국립중앙박물관")
    _record_message(session_id, "실내 어디 갈까", "박물관을 찾아봤어요")
    _expire(session_id)

    body = TestClient(app).post(
        f"/api/sessions/{session_id}/resume", headers=_headers(signing_key, ME)
    ).json()

    assert body["restore_from_messages"] is True
    assert body["recommendations"] == []


# 만료는 "낡은 조건을 버렸다"는 뜻이지 "그 대화가 없었다"는 뜻이 아니다.
# 거르면 오래된 대화일수록 목록에서 사라지는데, 그런 대화를 열어 이어가는 것이
# 이 목록의 목적이다(실측: 신원 붙은 대화 11개가 이 필터로 가려져 있었다).
def test_만료된_대화도_목록에_나온다(signing_key) -> None:
    from app.state.store import get_store

    session_id = _start_chat(ME, "만료된 대화")
    store = get_store()
    state = store.get_state(session_id)
    assert state is not None
    state.status = "expired"
    store.save_state(state)

    sessions = (
        TestClient(app).get("/api/sessions", headers=_headers(signing_key, ME)).json()["sessions"]
    )

    assert session_id in {item["session_id"] for item in sessions}
