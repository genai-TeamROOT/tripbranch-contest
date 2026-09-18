"""장소 보관함 담기/빼기 시나리오. (SCHEDULE-12)

역할: 보관함이 추천 이력과 독립적으로 유지되는지, 담기 조건(그 세션에서 노출된
장소만)과 멱등성이 지켜지는지 검증한다.
"""

from __future__ import annotations

import pytest

from app.auth.principal import Principal
from app.state import history as history_module
from app.state import saved_places as saved_places_module
from app.state import service as svc
from app.state.errors import SavedPlaceNotRecommendedError, SessionOwnershipError
from app.state.store import InMemoryStateStore


@pytest.fixture
def store() -> InMemoryStateStore:
    return InMemoryStateStore()


def _apply(store, *, session_id=None, principal=None) -> svc.StateApplyResponse:
    return svc.apply(
        svc.StateApplyRequest(
            session_id=session_id, intent="RECOMMEND", confirmed=True
        ),
        store=store,
        principal=principal,
    )


def _record(store, session_id: str, run_id: str, places: list[tuple[str, str]]) -> None:
    """places는 (place_id, name) 목록. rank는 순서대로 매긴다."""
    svc.record_recommendation(
        svc.RecordRecommendationRequest(
            session_id=session_id,
            run_id=run_id,
            recommended=[
                svc.RecommendedPlace(place_id=pid, rank=index, name=name)
                for index, (pid, name) in enumerate(places, start=1)
            ],
        ),
        store=store,
    )


def _save(store, session_id: str, place_id: str, principal=None):
    return svc.save_place(
        session_id, svc.SavePlaceRequest(place_id=place_id), store=store,
        principal=principal,
    )


# ---------------------------------------------------------------- 담기

def test_담은_장소가_추천_시점_이름과_함께_보관된다(store) -> None:
    applied = _apply(store)
    _record(store, applied.session_id, applied.run_id, [("p1", "경복궁")])

    response = _save(store, applied.session_id, "p1")

    assert response.changed is True
    assert [item.place_id for item in response.items] == ["p1"]
    assert response.items[0].name == "경복궁"
    assert response.items[0].saved_from_run_id == applied.run_id


def test_노출된_적_없는_장소는_담을_수_없다(store) -> None:
    applied = _apply(store)
    _record(store, applied.session_id, applied.run_id, [("p1", "경복궁")])

    with pytest.raises(SavedPlaceNotRecommendedError):
        _save(store, applied.session_id, "unknown")


def test_세션이_없으면_같은_오류로_답한다(store) -> None:
    """세션 존재 여부를 별도 코드로 구분해 주면 session_id 유효성 확인 통로가 된다."""
    with pytest.raises(SavedPlaceNotRecommendedError):
        _save(store, "sess_없음", "p1")


def test_같은_장소를_두_번_담아도_한_줄만_남는다(store) -> None:
    applied = _apply(store)
    _record(store, applied.session_id, applied.run_id, [("p1", "경복궁")])

    first = _save(store, applied.session_id, "p1")
    second = _save(store, applied.session_id, "p1")

    assert first.changed is True
    assert second.changed is False
    assert [item.place_id for item in second.items] == ["p1"]


def test_담은_순서가_유지된다(store) -> None:
    """일정 편성에서 상한을 넘을 때 무엇을 남길지 이 순서로 정한다."""
    applied = _apply(store)
    _record(
        store,
        applied.session_id,
        applied.run_id,
        [("p1", "경복궁"), ("p2", "북촌"), ("p3", "인사동")],
    )

    _save(store, applied.session_id, "p3")
    _save(store, applied.session_id, "p1")
    response = _save(store, applied.session_id, "p2")

    assert [item.place_id for item in response.items] == ["p3", "p1", "p2"]


def test_이전_턴에_본_장소도_담을_수_있다(store) -> None:
    """화면에는 이전 턴 카드도 남아 있어, 마지막 run으로 좁히면 이 경로가 막힌다."""
    applied = _apply(store)
    _record(store, applied.session_id, applied.run_id, [("p1", "경복궁")])

    second = _apply(store, session_id=applied.session_id)
    _record(store, second.session_id, second.run_id, [("p2", "북촌")])

    # 마지막 run 기준으로는 p1이 안 보인다 — 그래도 담을 수 있어야 한다.
    assert history_module.get_shown_place_ids(store, applied.session_id) == ["p2"]

    response = _save(store, applied.session_id, "p1")

    assert response.changed is True
    assert [item.place_id for item in response.items] == ["p1"]


def test_이름이_없던_과거_데이터는_place_id로_대체한다(store) -> None:
    applied = _apply(store)
    _record(store, applied.session_id, applied.run_id, [("p1", None)])

    response = _save(store, applied.session_id, "p1")

    assert response.items[0].name == "p1"


# ---------------------------------------------------------------- 빼기

def test_담긴_장소를_뺀다(store) -> None:
    applied = _apply(store)
    _record(store, applied.session_id, applied.run_id, [("p1", "경복궁"), ("p2", "북촌")])
    _save(store, applied.session_id, "p1")
    _save(store, applied.session_id, "p2")

    response = svc.remove_saved_place(applied.session_id, "p1", store=store)

    assert response.changed is True
    assert [item.place_id for item in response.items] == ["p2"]


def test_담기지_않은_장소를_빼도_오류가_아니다(store) -> None:
    applied = _apply(store)

    response = svc.remove_saved_place(applied.session_id, "p1", store=store)

    assert response.changed is False
    assert response.items == []


# ---------------------------------------------------------------- 이력과의 독립성

def test_history_reset이_보관함을_비우지_않는다(store) -> None:
    """"다른 곳 보여줘" 한 번에 담아둔 것이 날아가면 안 된다."""
    applied = _apply(store)
    _record(store, applied.session_id, applied.run_id, [("p1", "경복궁")])
    _save(store, applied.session_id, "p1")

    history_module.clear_recommended(store, applied.session_id)

    assert history_module.get_shown_place_ids(store, applied.session_id) == []
    assert saved_places_module.get_saved_place_ids(store, applied.session_id) == ["p1"]


def test_세션_삭제는_보관함까지_지운다(store) -> None:
    applied = _apply(store)
    _record(store, applied.session_id, applied.run_id, [("p1", "경복궁")])
    _save(store, applied.session_id, "p1")

    svc.delete_session(applied.session_id, store=store)

    assert store.get_saved_places(applied.session_id) is None


# ---------------------------------------------------------------- 조회·소유권

def test_세션_컨텍스트에_보관함이_실린다(store) -> None:
    applied = _apply(store)
    _record(store, applied.session_id, applied.run_id, [("p1", "경복궁")])
    _save(store, applied.session_id, "p1")

    context = svc.get_session_context(applied.session_id, store=store)

    assert [item.place_id for item in context.saved_places] == ["p1"]


def test_보관함만_따로_조회할_수_있다(store) -> None:
    applied = _apply(store)
    _record(store, applied.session_id, applied.run_id, [("p1", "경복궁")])
    _save(store, applied.session_id, "p1")

    response = svc.get_saved_places(applied.session_id, store=store)

    assert response.changed is False
    assert [item.place_id for item in response.items] == ["p1"]


def test_담기가_소유자를_연결한다(store) -> None:
    owner = Principal(user_id="user_a", is_anonymous=True)
    applied = _apply(store, principal=owner)
    _record(store, applied.session_id, applied.run_id, [("p1", "경복궁")])

    _save(store, applied.session_id, "p1", principal=owner)

    saved = store.get_saved_places(applied.session_id)
    assert saved is not None
    assert saved.user_id == "user_a"


def test_다른_신원은_담을_수_없다(store) -> None:
    owner = Principal(user_id="user_a", is_anonymous=True)
    applied = _apply(store, principal=owner)
    _record(store, applied.session_id, applied.run_id, [("p1", "경복궁")])

    with pytest.raises(SessionOwnershipError):
        _save(
            store, applied.session_id, "p1",
            principal=Principal(user_id="user_b", is_anonymous=True),
        )


def test_다른_신원은_뺄_수_없다(store) -> None:
    owner = Principal(user_id="user_a", is_anonymous=True)
    applied = _apply(store, principal=owner)
    _record(store, applied.session_id, applied.run_id, [("p1", "경복궁")])
    _save(store, applied.session_id, "p1", principal=owner)

    with pytest.raises(SessionOwnershipError):
        svc.remove_saved_place(
            applied.session_id, "p1", store=store,
            principal=Principal(user_id="user_b", is_anonymous=True),
        )


# ---------------------------------------------------------------- 좌표 스냅샷 (SCHEDULE-12)

def test_담은_장소가_추천_시점_좌표를_이어받는다(store) -> None:
    applied = _apply(store)
    svc.record_recommendation(
        svc.RecordRecommendationRequest(
            session_id=applied.session_id,
            run_id=applied.run_id,
            recommended=[
                svc.RecommendedPlace(
                    place_id="p1", rank=1, name="경복궁",
                    latitude=37.5796, longitude=126.9770,
                )
            ],
        ),
        store=store,
    )

    response = _save(store, applied.session_id, "p1")

    assert response.items[0].latitude == 37.5796
    assert response.items[0].longitude == 126.9770


def test_좌표가_없던_추천도_담을_수_있다(store) -> None:
    """C 컨텍스트를 안 거치는 경로(routes/recommendations.py)로 기록된 항목."""
    applied = _apply(store)
    _record(store, applied.session_id, applied.run_id, [("p1", "경복궁")])

    response = _save(store, applied.session_id, "p1")

    assert response.changed is True
    assert response.items[0].latitude is None
    assert response.items[0].longitude is None


# ---------------------------------------------------------------- 거절 동기화 (SCHEDULE-12)

def test_거절하면_보관함에서도_빠진다(store) -> None:
    """saved ∩ rejected = ∅ 을 구조적으로 보장한다 — 후보 복귀가 거절을 무력화하지
    않으려면 이 불변식이 필요하다."""
    applied = _apply(store)
    _record(store, applied.session_id, applied.run_id, [("p1", "경복궁"), ("p2", "북촌")])
    _save(store, applied.session_id, "p1")
    _save(store, applied.session_id, "p2")

    history_module.record_rejected(
        store, applied.session_id, applied.run_id, [("p1", "not_interested")]
    )

    assert saved_places_module.get_saved_place_ids(store, applied.session_id) == ["p2"]
    assert "p1" in history_module.get_exclusion_place_ids(store, applied.session_id)


def test_담기지_않은_장소를_거절해도_오류가_아니다(store) -> None:
    applied = _apply(store)
    _record(store, applied.session_id, applied.run_id, [("p1", "경복궁")])

    history_module.record_rejected(
        store, applied.session_id, applied.run_id, [("p1", "not_interested")]
    )

    assert saved_places_module.get_saved_place_ids(store, applied.session_id) == []


def test_거절_후_다시_담을_수_있다(store) -> None:
    """거절이 담기를 영구히 막지는 않는다 — 사용자가 다시 고르면 그게 최신 의사다.

    다만 거절 이력(rejected)은 그대로 남아 제외 목록에 계속 들어간다. 담긴 장소를
    후보로 되살리는 판정은 agent_runtime._revivable_place_ids()가 한다.
    """
    applied = _apply(store)
    _record(store, applied.session_id, applied.run_id, [("p1", "경복궁")])
    _save(store, applied.session_id, "p1")
    history_module.record_rejected(
        store, applied.session_id, applied.run_id, [("p1", "not_interested")]
    )

    response = _save(store, applied.session_id, "p1")

    assert response.changed is True
    assert [item.place_id for item in response.items] == ["p1"]
