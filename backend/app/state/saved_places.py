"""Package B - 장소 보관함 관리. (SCHEDULE-12)

사용자가 추천 카드에서 명시적으로 담은 장소를 세션 단위로 보관한다.

추천·거절 이력(history.py)과 저장소를 공유하지 않는 별도 엔티티다 — 이력은
append-only이고 `clear_recommended()`(계약 5.5절 history reset)의 대상인데,
보관함은 담기/빼기가 되는 가변 상태이고 "다른 곳 보여줘" 한 번에 사용자가
담아둔 것이 날아가면 안 된다. SavedPlaceList docstring 참고.

이 모듈은 place_id의 유효성(그 세션에서 추천된 적이 있는지)을 검증하지 않는다 —
그 판정에는 추천 이력이 필요해 두 저장소를 함께 보는 service.py가 담당한다.

반대 방향으로는 history.py가 이 모듈을 부른다. `record_rejected()`가 거절 시점에
같은 place_id를 보관함에서 빼서 `saved ∩ rejected = ∅`을 지킨다(SCHEDULE-12) —
불변식이라 호출 규약에 맡기지 않고 코드로 강제한다. 이 모듈은 history.py를 부르지
않으므로 순환 참조가 생기지 않는다.
"""

from app.auth.principal import Principal
from app.state.schema import SavedPlaceItem, SavedPlaceList, now_kst
from app.state.store import StateStore


def get_or_create(store: StateStore, session_id: str) -> SavedPlaceList:
    """보관함을 조회하고, 없으면 빈 보관함을 만든다."""
    saved = store.get_saved_places(session_id)
    if saved is None:
        saved = SavedPlaceList(session_id=session_id)
    return saved


def attach_user_id(saved: SavedPlaceList, principal: Principal | None) -> None:
    """검증된 신원을 보관함에 연결한다. (D-063 결정 3)

    AgentState.user_id / RecommendationHistory.user_id와 동일한 규칙 —
    비어 있으면 채우고, 이미 값이 있으면 절대 덮어쓰지 않는다.
    """
    if principal is None:
        return
    if saved.user_id is not None:
        return
    saved.user_id = principal.user_id


# ---------------------------------------------------------------- 변경

def add(
    store: StateStore,
    session_id: str,
    item: SavedPlaceItem,
    principal: Principal | None = None,
) -> bool:
    """장소를 보관함에 담는다. 이미 담겨 있으면 아무것도 하지 않는다.

    같은 place_id를 두 번 담아도 오류로 처리하지 않고 조용히 무시한다 —
    낙관적 갱신을 쓰는 프론트에서 같은 요청이 두 번 날아가는 것은 정상이고,
    사용자 입장에서 결과("담겨 있다")가 같기 때문이다. 이력의 중복 허용
    정책(계약 3.5절)과 달리 항목을 늘리지 않는 이유는, 보관함은 누적 기록이
    아니라 현재 상태라서 같은 장소가 두 줄로 보이면 그 자체가 버그다.

    반환값은 "실제로 담겼는지"다. 호출부가 응답에 실을 목록은 어느 쪽이든
    갱신 후 전체 목록이므로, 이 값은 로깅·테스트용이다.
    """
    saved = get_or_create(store, session_id)
    attach_user_id(saved, principal)

    if any(existing.place_id == item.place_id for existing in saved.items):
        # user_id만 새로 붙었을 수 있으니 저장은 한다.
        store.save_saved_places(saved)
        return False

    saved.items.append(item)
    saved.updated_at = now_kst()
    store.save_saved_places(saved)
    return True


def remove(
    store: StateStore,
    session_id: str,
    place_id: str,
    principal: Principal | None = None,
) -> bool:
    """장소를 보관함에서 뺀다. 담겨 있지 않으면 아무것도 하지 않는다.

    없는 place_id를 빼달라는 요청도 오류로 처리하지 않는다 — add()와 같은
    이유(멱등)이며, 사용자가 원한 결과("담겨 있지 않다")가 이미 성립한다.
    """
    saved = store.get_saved_places(session_id)
    if saved is None:
        return False

    attach_user_id(saved, principal)
    remaining = [item for item in saved.items if item.place_id != place_id]
    removed = len(remaining) != len(saved.items)

    saved.items = remaining
    if removed:
        saved.updated_at = now_kst()
    store.save_saved_places(saved)
    return removed


def clear(store: StateStore, session_id: str) -> None:
    """보관함을 통째로 비운다."""
    store.delete_saved_places(session_id)


# ---------------------------------------------------------------- 조회

def get_items(store: StateStore, session_id: str) -> list[SavedPlaceItem]:
    """담긴 장소 전체. 담은 순서(오래된 것이 앞)."""
    saved = store.get_saved_places(session_id)
    if saved is None:
        return []
    return list(saved.items)


def get_saved_place_ids(store: StateStore, session_id: str) -> list[str]:
    """담긴 장소의 place_id. 담은 순서.

    다음 SCHEDULE 턴에서 후보 복귀 대상을 정할 때 쓴다(후속 카드).
    """
    return [item.place_id for item in get_items(store, session_id)]
