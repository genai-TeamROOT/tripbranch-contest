"""DELETE /api/account — 회원 탈퇴.

**이 파일에서 가장 중요한 것은 "남의 계정을 지울 수 없다"다.** 지우는 대상은 본문이
아니라 서명 검증을 통과한 토큰의 sub에서만 온다. 이 성질이 깨지면 토큰 하나로 아무
계정이나 지울 수 있다.

그다음은 **순서**다. 계정(GoTrue)과 데이터(PostgREST)는 다른 API라 한 트랜잭션으로
못 묶는다. 데이터를 먼저 지우고 계정을 마지막에 지워야, 중간에 실패해도 사용자가
다시 눌러 마칠 수 있다. 반대 순서면 user_id를 잃어 남은 데이터가 영영 고아가 된다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.accounts import deletion
from app.auth.admin import AccountAdminError
from app.main import app
from app.state.schema import AgentState, SavedSchedule, UserFavoriteList, UserPreferenceList
from app.state.store import InMemoryStateStore
from tests.auth.conftest import make_token

ME = "3f1a9c04-0000-4000-8000-000000000001"
OTHER = "3f1a9c04-0000-4000-8000-000000000002"


def _headers(signing_key, sub: str = ME, *, is_anonymous: bool = False) -> dict[str, str]:
    token = make_token(signing_key, sub=sub, is_anonymous=is_anonymous)
    return {"Authorization": f"Bearer {token}"}


class _FakeAdmin:
    """계정 삭제를 기록만 한다. 순서를 보려고 공용 로그에 남긴다."""

    def __init__(self, log: list[str], *, fail: bool = False) -> None:
        self._log = log
        self._fail = fail
        self.deleted: list[str] = []

    def delete_user(self, user_id: str) -> None:
        if self._fail:
            raise AccountAdminError("boom")
        self.deleted.append(user_id)
        self._log.append(f"account:{user_id}")


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> InMemoryStateStore:
    """탈퇴가 실제로 무엇을 지우는지 보려고 메모리 스토어를 꽂는다."""
    memory = InMemoryStateStore()
    monkeypatch.setattr(deletion, "get_store", lambda: memory)
    return memory


@pytest.fixture
def admin_log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    log: list[str] = []
    monkeypatch.setattr(
        deletion, "AccountAdminClient", lambda *args, **kwargs: _FakeAdmin(log)
    )
    return log


def _seed(store: InMemoryStateStore, user_id: str, session_id: str, *, title: str | None) -> None:
    store.save_state(
        AgentState(session_id=session_id, user_id=user_id, title=title)
    )
    store.save_preferences(UserPreferenceList(user_id=user_id, items=[]))
    store.save_favorites(UserFavoriteList(user_id=user_id, items=[]))


# ---------------------------------------------------------------- 신원


def test_토큰_없이_부르면_401이고_아무것도_안_지운다(store, admin_log) -> None:
    _seed(store, ME, "s-1", title="내 대화")

    response = TestClient(app).delete("/api/account")

    assert response.status_code == 401
    assert store.get_state("s-1") is not None
    assert admin_log == []


def test_게스트는_탈퇴할_수_없다(signing_key, store, admin_log) -> None:
    """게스트에게는 되돌릴 계정이 없다 — 익명 계정은 정기 정리가 걷어간다."""
    _seed(store, ME, "s-1", title="내 대화")

    response = TestClient(app).delete(
        "/api/account", headers=_headers(signing_key, ME, is_anonymous=True)
    )

    assert response.status_code == 403
    assert store.get_state("s-1") is not None
    assert admin_log == []


# ---------------------------------------------------------------- 오류 문구


# **HTTPException을 쓰면 여기서 깨진다.** main.py의 HTTPException 핸들러가 detail을
# 버리고 "요청 내용을 확인해주세요"로 덮어써서, 서버가 실패한 503인데도 사용자 입력이
# 잘못된 것처럼 읽힌다. 상태 코드만 보는 테스트로는 안 잡혀서 문구 자체를 고정한다.


def test_게스트_거부_사유가_그대로_화면에_닿는다(signing_key, store, admin_log) -> None:
    _seed(store, ME, "s-1", title="내 대화")

    response = TestClient(app).delete(
        "/api/account", headers=_headers(signing_key, ME, is_anonymous=True)
    )

    assert "게스트는 탈퇴할 계정이 없어요." in response.text
    assert "요청 내용을 확인해주세요" not in response.text


def test_실패_사유가_그대로_화면에_닿는다(signing_key, store, monkeypatch) -> None:
    monkeypatch.setattr(
        deletion,
        "AccountAdminClient",
        lambda *args, **kwargs: _FakeAdmin([], fail=True),
    )
    _seed(store, ME, "s-1", title="내 대화")

    response = TestClient(app).delete(
        "/api/account", headers=_headers(signing_key, ME)
    )

    assert "탈퇴 처리를 마치지 못했어요" in response.text
    assert "요청 내용을 확인해주세요" not in response.text


# ---------------------------------------------------------------- 가장 중요한 것


def test_남의_데이터는_건드리지_않는다(signing_key, store, admin_log) -> None:
    """지울 대상은 토큰의 sub에서만 온다. 본문으로 바꿔 넣을 자리가 없다."""
    _seed(store, ME, "mine", title="내 대화")
    _seed(store, OTHER, "theirs", title="남의 대화")

    response = TestClient(app).delete(
        "/api/account", headers=_headers(signing_key, ME)
    )

    assert response.status_code == 200
    assert store.get_state("mine") is None
    assert store.get_state("theirs") is not None
    assert store.get_preferences(OTHER) is not None
    assert store.get_favorites(OTHER) is not None
    assert admin_log == [f"account:{ME}"]


def test_본문으로_다른_user_id를_넣어도_내_계정만_지워진다(signing_key, store, admin_log) -> None:
    _seed(store, ME, "mine", title="내 대화")
    _seed(store, OTHER, "theirs", title="남의 대화")

    response = TestClient(app).request(
        "DELETE",
        "/api/account",
        headers=_headers(signing_key, ME),
        json={"user_id": OTHER},
    )

    assert response.status_code == 200
    assert store.get_state("theirs") is not None
    assert admin_log == [f"account:{ME}"]


# ---------------------------------------------------------------- 지우는 범위


def test_제목_없는_세션도_지운다(signing_key, store, admin_log) -> None:
    """사이드바 목록은 제목 없는 세션을 거르지만 탈퇴는 거르면 안 된다.

    2026-09-15 실측으로 user_id가 붙은 세션의 44%가 제목이 없었다 — 그 기준을
    그대로 쓰면 절반 가까이가 남는다.
    """
    _seed(store, ME, "제목있음", title="내 대화")
    store.save_state(AgentState(session_id="제목없음", user_id=ME, title=None))

    TestClient(app).delete("/api/account", headers=_headers(signing_key, ME))

    assert store.get_state("제목있음") is None
    assert store.get_state("제목없음") is None


def test_취향과_즐겨찾기는_비우는_게_아니라_지운다(signing_key, store, admin_log) -> None:
    """빈 목록 저장이면 user_id가 행에 남는다 — 주인 없는 개인정보가 된다."""
    _seed(store, ME, "s-1", title="내 대화")

    TestClient(app).delete("/api/account", headers=_headers(signing_key, ME))

    assert store.get_preferences(ME) is None
    assert store.get_favorites(ME) is None


def test_저장한_일정도_지운다(signing_key, store, admin_log) -> None:
    _seed(store, ME, "s-1", title="내 대화")
    saved = store.save_schedule(
        SavedSchedule(id="", user_id=ME, session_id="s-1", title="주말 코스", payload={})
    )

    TestClient(app).delete("/api/account", headers=_headers(signing_key, ME))

    assert store.get_schedule(saved.id) is None


# ---------------------------------------------------------------- 순서


def test_계정은_데이터를_다_지운_뒤에_지운다(signing_key, store, monkeypatch) -> None:
    """계정을 먼저 지우면 user_id를 잃어 남은 데이터가 영영 고아가 된다."""
    log: list[str] = []
    monkeypatch.setattr(
        deletion, "AccountAdminClient", lambda *args, **kwargs: _FakeAdmin(log)
    )
    _seed(store, ME, "s-1", title="내 대화")

    original = deletion._delete_session

    def traced(store_arg, session_id: str) -> None:
        log.append(f"session:{session_id}")
        original(store_arg, session_id)

    monkeypatch.setattr(deletion, "_delete_session", traced)

    TestClient(app).delete("/api/account", headers=_headers(signing_key, ME))

    assert log == ["session:s-1", f"account:{ME}"]


def test_계정_삭제가_실패하면_503을_주고_계정은_남는다(signing_key, store, monkeypatch) -> None:
    """데이터가 먼저 지워졌어도 계정이 살아 있으면 사용자가 다시 눌러 마칠 수 있다."""
    monkeypatch.setattr(
        deletion,
        "AccountAdminClient",
        lambda *args, **kwargs: _FakeAdmin([], fail=True),
    )
    _seed(store, ME, "s-1", title="내 대화")

    response = TestClient(app).delete(
        "/api/account", headers=_headers(signing_key, ME)
    )

    assert response.status_code == 503
    # 원문을 그대로 내보내지 않는다.
    assert "boom" not in response.text


def test_두_번_눌러도_실패하지_않는다(signing_key, store, admin_log) -> None:
    """중간에 끊긴 탈퇴를 사용자가 다시 시도하는 것이 실제 경로다 — 모두 멱등이어야 한다."""
    _seed(store, ME, "s-1", title="내 대화")
    headers = _headers(signing_key, ME)
    client = TestClient(app)

    assert client.delete("/api/account", headers=headers).status_code == 200
    assert client.delete("/api/account", headers=headers).status_code == 200
