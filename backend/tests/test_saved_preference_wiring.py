"""저장된 취향을 읽어 채점에 넘기는 배선(A 영역) 테스트.

`domain/saved_preference.py`가 **무엇을 질의로 쓸지**를 정하고, 이 배선이
**어디서 읽어 어디로 넘길지**를 맡는다. 순수 함수 테스트만으로는 이 줄이
지워져도 안 잡혀서 따로 못 박는다 — 1.9.0에서 provider 배선 2줄이 그랬다.
"""

from __future__ import annotations

import pytest

from app.auth.principal import Principal
from app.schemas import Companion, ConcentrationIntent, Environment, UserConditions
from app.services.runtime.agent_runtime import _saved_taste_query
from app.state import preferences as state_preferences
from app.state.schema import UserPreference
from app.state.store import InMemoryStateStore

_USER = "user-1"


def _principal() -> Principal:
    return Principal(user_id=_USER, is_anonymous=False)


def _store_with(*items: UserPreference) -> InMemoryStateStore:
    store = InMemoryStateStore()
    state_preferences.replace(store, _USER, list(items))
    return store


def _chip(label: str, source: str, *codes: str) -> UserPreference:
    return UserPreference(label=label, source=source, codes=list(codes))


def test_저장된_취향을_읽어_질의로_만든다() -> None:
    store = _store_with(
        _chip("아늑한 공간", "preference", "cozy"),
        _chip("전망 좋은", "preference", "good_view"),
    )

    assert _saved_taste_query(UserConditions(), _principal(), store) == "아늑한 공간 전망 좋은"


def test_발화에_취향이_있어도_저장소를_읽는다() -> None:
    """발화는 정본이지 저장값을 지우는 스위치가 아니다.

    발화가 정하지 않은 축의 칩은 그대로 살아남아 발화 뒤에 붙으므로, 발화가
    있다고 조회를 건너뛰면 그 칩들이 통째로 사라진다.
    """
    store = _store_with(_chip("아늑한 공간", "preference", "cozy"))
    calls: list[str] = []
    original = store.get_preferences
    store.get_preferences = lambda uid: (calls.append(uid), original(uid))[1]  # type: ignore[method-assign]

    result = _saved_taste_query(UserConditions(taste_query="조용한"), _principal(), store)

    assert result == "아늑한 공간"
    assert calls == [_principal().user_id]


def test_발화가_정한_축의_칩은_배선이_빼고_넘긴다() -> None:
    """D의 축 규칙이 이 경로로 실제로 걸리는지 못 박는다.

    `conditions`에서 세 축을 꺼내 D에 넘기는 줄이 빠지면 여기서만 깨진다 —
    라벨을 잇는 것만 보는 테스트는 통과해 버린다.
    """
    store = _store_with(
        _chip("조용한 곳", "preference", "quiet"),
        _chip("사진 명소", "preference", "photo_spot"),
    )

    result = _saved_taste_query(
        UserConditions(taste_query="북적이는", concentration_intent=ConcentrationIntent.SEEK),
        _principal(),
        store,
    )

    assert result == "사진 명소"


@pytest.mark.parametrize(
    ("conditions", "expected"),
    [
        pytest.param(
            UserConditions(concentration_intent=ConcentrationIntent.SEEK),
            "사진 명소 자연·공원 아이와 함께",
            id="혼잡도-모순인 조용한 곳만 빠짐",
        ),
        pytest.param(
            UserConditions(companion=Companion.SOLO),
            "조용한 곳 사진 명소 자연·공원",
            id="동행-동행 칩만 통째로 빠짐",
        ),
        pytest.param(
            UserConditions(environment=Environment.INDOOR),
            "조용한 곳 사진 명소 자연·공원 아이와 함께",
            id="실내외-아무것도 안 빠짐(실측으로 판정을 뺐다)",
        ),
    ],
)
def test_두_축을_각각_D에_넘긴다(conditions: UserConditions, expected: str) -> None:
    """축 하나라도 안 넘기면 그 축의 칩이 살아서 질의에 섞인다.

    한 축만 보는 테스트는 나머지 줄이 빠져도 통과한다 — 축을 따로 못 박는다.
    실내외 건은 **안 빠지는 것**을 못 박는다(`_CONTRADICTIONS` 주석의 실측).
    """
    store = _store_with(
        _chip("조용한 곳", "preference", "quiet"),
        _chip("사진 명소", "preference", "photo_spot"),
        _chip("자연·공원", "place_tag", "공원", "산", "호수", "계곡", "수목원"),
        _chip("아이와 함께", "preference", "with_kids"),
    )

    assert _saved_taste_query(conditions, _principal(), store) == expected


def test_중복은_빼지_않는다() -> None:
    """발화와 같은 뜻인 칩도 그대로 붙는다.

    실측 5/5에서 중복을 두는 쪽이 발화를 더 잘 반영했다 — 같은 뜻이라 벡터를
    발화 쪽으로 당긴다(`domain/saved_preference.py` docstring 표).
    """
    store = _store_with(
        _chip("조용한 곳", "preference", "quiet"),
        _chip("사진 명소", "preference", "photo_spot"),
    )
    conditions = UserConditions(
        taste_query="조용한", concentration_intent=ConcentrationIntent.AVOID
    )

    assert _saved_taste_query(conditions, _principal(), store) == "조용한 곳 사진 명소"


def test_게스트는_저장할_자리가_없다() -> None:
    """취향은 세션이 아니라 사람에게 붙는 값이라 user_id가 필수다."""
    store = _store_with(_chip("아늑한 공간", "preference", "cozy"))

    assert _saved_taste_query(UserConditions(), None, store) is None


def test_store를_안_주면_전역_저장소로_대신한다(monkeypatch: pytest.MonkeyPatch) -> None:
    """`/api/chat`·`/api/chat/stream`이 store를 안 넘겨 여기까지 None으로 온다
    (2026-09-07 실사용 재현) — `get_session_context()`(state/service.py)와 같은
    `store or get_store()` 패턴이 없어서, 세션은 되는데 저장된 취향만 프로덕션에서
    한 번도 채점에 실리지 못했다. store=None을 그냥 None으로 못 박지 않는지 잠근다.
    """
    store = _store_with(_chip("아늑한 공간", "preference", "cozy"))
    monkeypatch.setattr("app.services.runtime.agent_runtime.get_store", lambda: store)

    assert _saved_taste_query(UserConditions(), _principal(), None) == "아늑한 공간"


def test_고른_적이_없으면_None() -> None:
    assert _saved_taste_query(UserConditions(), _principal(), InMemoryStateStore()) is None


def test_칩_종류를_여기서_다시_거르지_않는다() -> None:
    """무엇을 넣을지는 D가 정한다(`domain/saved_preference.py`). 배선은 그대로 따른다.

    양쪽이 각자 거르면 규칙이 갈려서, 화면이 칩을 늘렸을 때 한쪽만 고치고 넘어가게 된다.
    """
    store = _store_with(
        _chip("친구와 함께", "preference", "with_friends"),
        _chip("카페", "place_tag", "카페", "찻집"),
    )

    assert _saved_taste_query(UserConditions(), _principal(), store) == "친구와 함께 카페"


def test_조회가_실패해도_추천을_막지_않는다() -> None:
    """취향은 순위를 다듬는 축이지 후보를 만드는 축이 아니다."""

    class _BrokenStore(InMemoryStateStore):
        def get_preferences(self, user_id: str):  # type: ignore[override]
            raise RuntimeError("저장소 장애")

    assert _saved_taste_query(UserConditions(), _principal(), _BrokenStore()) is None


@pytest.mark.parametrize("spoken", ["", "   "])
def test_공백만_있는_발화는_말하지_않은_것으로_본다(spoken: str) -> None:
    store = _store_with(_chip("아늑한 공간", "preference", "cozy"))

    result = _saved_taste_query(UserConditions(taste_query=spoken), _principal(), store)

    assert result == "아늑한 공간"
