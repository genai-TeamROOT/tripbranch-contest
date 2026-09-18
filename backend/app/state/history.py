"""Package B - 추천·거절 이력 관리.

계약 문서: docs/package-b/agent-state-contract-v1.md (3절)

이력은 append-only이며 기존 항목을 수정하지 않는다.
B는 place_id만 저장하고 장소 상세 정보를 보관하지 않는다 — 단, SCHEDULE
재조정(SCHEDULE-06)을 위해 일정 세부 필드(도착시각/체류시간/이동시간/이유),
COMPARE 데이터 출처(2026-08-11)를 위해 추천 시점 Feature 스냅샷
(거리/남은 운영시간/환경유형), SCHEDULE-09 2단계를 위해 장소 이름,
SCHEDULE-12를 위해 추천 시점 좌표만 예외적으로 저장한다. RecommendedItem 참고.
"""

from app.auth.principal import Principal
from app.state import saved_places as saved_places_module
from app.state.schema import (
    ClosedExclusionItem,
    RecommendationHistory,
    RecommendedItem,
    RecommendedItemInput,
    RejectedItem,
    now_kst,
)
from app.state.store import StateStore


def get_or_create(store: StateStore, session_id: str) -> RecommendationHistory:
    """이력을 조회하고, 없으면 빈 이력을 만든다."""
    history = store.get_history(session_id)
    if history is None:
        history = RecommendationHistory(session_id=session_id)
    return history


def attach_user_id(history: RecommendationHistory, principal: Principal | None) -> None:
    """검증된 신원을 이력에 연결한다. (TP-101 3단계, D-063 결정 3)

    AgentState.user_id와 동일한 규칙 — session.attach_user_id() 참고.
    비어 있으면 채우고, 이미 값이 있으면 절대 덮어쓰지 않는다.
    """
    if principal is None:
        return
    if history.user_id is not None:
        return
    history.user_id = principal.user_id


# ---------------------------------------------------------------- 기록

def record_recommended(
    store: StateStore,
    session_id: str,
    run_id: str,
    items: list[RecommendedItemInput],
    principal: Principal | None = None,
) -> int:
    """추천 결과를 기록한다. (계약 6.4절)

    items는 실제로 노출이 확정된 것만 전달받는다. 노출 여부를 아는 주체는
    Agent Runtime이므로 호출도 Runtime이 담당한다.

    중복 place_id도 오류로 처리하지 않고 추가한다. (계약 3.5절)
    """
    history = get_or_create(store, session_id)
    attach_user_id(history, principal)
    shown_at = now_kst()

    for item in items:
        history.recommended.append(
            RecommendedItem(
                place_id=item.place_id,
                run_id=run_id,
                rank=item.rank,
                shown_at=shown_at,
                name=item.name,
                latitude=item.latitude,
                longitude=item.longitude,
                estimated_arrival=item.estimated_arrival,
                estimated_duration_min=item.estimated_duration_min,
                travel_to_next_min=item.travel_to_next_min,
                reason=item.reason,
                distance_km=item.distance_km,
                remaining_minutes=item.remaining_minutes,
                environment_type=item.environment_type,
            )
        )

    history.updated_at = shown_at
    store.save_history(history)
    return len(items)


def record_rejected(
    store: StateStore,
    session_id: str,
    run_id: str,
    items: list[tuple[str, str | None]],
    principal: Principal | None = None,
) -> int:
    """거절 장소를 기록하고, 같은 장소를 보관함에서 뺀다.

    items는 (place_id, reason_code) 목록이다.
    reason_code는 Package A가 해석한 값을 그대로 저장하며 검증하지 않는다.

    추천 이력에 없는 place_id가 전달되어도 검증하지 않는다. (계약 3.3절)

    **보관함 동기화(SCHEDULE-12)**: 거절한 장소는 보관함에서도 빠진다. 이 덕분에
    `saved ∩ rejected = ∅`이 구조적으로 보장되고, 후보 복귀 판정
    (`agent_runtime._revivable_place_ids()`)이 두 목록의 시간 순서를 비교할 필요가
    없어진다 — 담아둔 것을 되살리면서 거절 이력을 무력화하는 경로(TP-180에서 실제로
    테스트 4건이 깨졌던 지점)가 애초에 생기지 않는다.

    이 처리를 service.py가 아니라 여기 두는 이유는, 호출부가 두 번 부르는 것을
    잊으면 그 불변식이 조용히 깨지기 때문이다. 지금 `record_rejected()`의 호출부는
    `service.apply()` 한 곳뿐이지만, 불변식은 호출 규약이 아니라 코드로 지켜야 한다.
    """
    history = get_or_create(store, session_id)
    attach_user_id(history, principal)
    rejected_at = now_kst()

    for place_id, reason_code in items:
        history.rejected.append(
            RejectedItem(
                place_id=place_id,
                run_id=run_id,
                reason_code=reason_code,
                rejected_at=rejected_at,
            )
        )

    history.updated_at = rejected_at
    store.save_history(history)

    for place_id, _reason_code in items:
        # 담겨 있지 않으면 아무 일도 하지 않는다(saved_places.remove는 멱등).
        saved_places_module.remove(store, session_id, place_id, principal=principal)

    return len(items)


def record_closed_excluded(
    store: StateStore,
    session_id: str,
    run_id: str,
    place_ids: list[str],
    principal: Principal | None = None,
) -> int:
    """D의 하드 필터가 폐점이라 걸러낸 후보 id를 기록한다. (TP-82)

    D 응답(`RecommendationResponse.excluded_closed_place_ids`)을 그대로
    받아 저장만 한다 — 폐점 여부 판단은 D의 책임이고, B는 검증하지 않는다.
    recommended/rejected와 마찬가지로 중복 place_id도 오류로 처리하지
    않는다(계약 3.5절과 동일 원칙).
    """
    if not place_ids:
        return 0

    history = get_or_create(store, session_id)
    attach_user_id(history, principal)
    excluded_at = now_kst()

    for place_id in place_ids:
        history.closed_excluded.append(
            ClosedExclusionItem(
                place_id=place_id,
                run_id=run_id,
                excluded_at=excluded_at,
            )
        )

    history.updated_at = excluded_at
    store.save_history(history)
    return len(place_ids)


# ---------------------------------------------------------------- 조회

def get_exclusion_place_ids(store: StateStore, session_id: str) -> list[str]:
    """추천 제외 대상 ID. (계약 3.3절, TP-82로 closed_excluded 추가)

    exclusion = recommended의 place_id ∪ rejected의 place_id ∪
    closed_excluded의 place_id

    중복은 제거하되, 결과가 실행마다 달라지지 않도록 등장 순서를 유지한다.
    (set을 그대로 반환하면 순서가 매번 바뀌어 로그 비교가 어렵다)
    """
    history = store.get_history(session_id)
    if history is None:
        return []

    seen: set[str] = set()
    result: list[str] = []

    for item in history.recommended:
        if item.place_id not in seen:
            seen.add(item.place_id)
            result.append(item.place_id)

    for item in history.rejected:
        if item.place_id not in seen:
            seen.add(item.place_id)
            result.append(item.place_id)

    for item in history.closed_excluded:
        if item.place_id not in seen:
            seen.add(item.place_id)
            result.append(item.place_id)

    return result


def get_shown_place_ids(store: StateStore, session_id: str) -> list[str]:
    """마지막 실행에서 노출된 장소 ID. rank 순. (계약 3.4절)

    COMPARE의 "첫 번째", "두 번째" 지시 표현은 방금 본 추천을 가리키므로
    누적 목록이 아니라 마지막 실행 기준이어야 한다.
    """
    history = store.get_history(session_id)
    if history is None or not history.recommended:
        return []

    last_run_id = history.recommended[-1].run_id
    items = [item for item in history.recommended if item.run_id == last_run_id]
    items.sort(key=lambda x: x.rank)

    return [item.place_id for item in items]


def get_last_recommended_items(store: StateStore, session_id: str) -> list[RecommendedItem]:
    """마지막 실행에서 노출된 장소의 전체 항목. rank 순. (COMPARE 데이터 출처 A안, 2026-08-11)

    get_shown_place_ids()와 같은 기준(마지막 run_id)이지만 place_id만이
    아니라 distance_km/remaining_minutes/environment_type을 포함한 전체
    RecommendedItem을 반환한다. COMPARE가 "추천 시 이미 계산된 데이터"
    (int-04-compare.md §13)를 그대로 쓸 수 있게 하려는 목적이다.
    """
    history = store.get_history(session_id)
    if history is None or not history.recommended:
        return []

    last_run_id = history.recommended[-1].run_id
    items = [item for item in history.recommended if item.run_id == last_run_id]
    items.sort(key=lambda x: x.rank)

    return items


def find_recommended_item(
    store: StateStore, session_id: str, place_id: str
) -> RecommendedItem | None:
    """그 세션에서 노출된 적이 있는 장소인지 확인하고, 가장 최근 항목을 돌려준다.
    (SCHEDULE-12)

    보관함에 담을 수 있는 것은 "이 세션에서 실제로 노출된 장소"뿐이다 — 임의
    place_id 주입을 막고, 이름(RecommendedItem.name)을 그 스냅샷에서 그대로
    가져오기 위한 조회다.

    get_shown_place_ids()처럼 마지막 run으로 좁히지 않고 누적 이력 전체를 본다 —
    화면에는 이전 턴의 추천 카드도 그대로 남아 있어, 사용자가 스크롤을 올려
    3턴 전 카드를 담는 것이 정상 동작이기 때문이다. 마지막 run으로 좁히면 그
    경로가 400으로 막힌다.

    같은 place_id가 여러 run에 걸쳐 노출됐으면 **가장 최근 항목**을 쓴다. name이
    비어 있던 과거 데이터보다 최신 스냅샷이 담을 값으로 정확하다.
    """
    history = store.get_history(session_id)
    if history is None:
        return None
    for item in reversed(history.recommended):
        if item.place_id == place_id:
            return item
    return None


def get_last_recommended_run_id(store: StateStore, session_id: str) -> str | None:
    """마지막 추천이 발생한 실행 식별자."""
    history = store.get_history(session_id)
    if history is None or not history.recommended:
        return None
    return history.recommended[-1].run_id


def count_recommended(store: StateStore, session_id: str) -> int:
    """누적 노출 장소 수. 중복을 제거한 고유 개수."""
    history = store.get_history(session_id)
    if history is None:
        return 0
    return len({item.place_id for item in history.recommended})


def has_recommendation(store: StateStore, session_id: str) -> bool:
    """추천 이력이 1건 이상 존재하는지.

    Package A의 MODIFY / COMPARE 판정 전제 조건이다. (계약 6.3절)
    """
    history = store.get_history(session_id)
    return history is not None and len(history.recommended) > 0


# ---------------------------------------------------------------- 초기화

def clear_recommended(store: StateStore, session_id: str) -> None:
    """추천 이력만 비운다. 거절 이력은 유지한다. (history reset, 계약 5.5절)

    사용자가 명시적으로 거부한 장소는 어떤 초기화에서도 재노출하지 않는다.

    closed_excluded(TP-82)는 recommended와 함께 비운다 — rejected와 달리
    "사용자가 거부한" 게 아니라 "그 시점에 닫혀 있었다"는 시간 의존적
    사실이라, 새 검색 컨텍스트에서까지 영구히 제외할 근거가 아니다.
    """
    history = store.get_history(session_id)
    if history is None:
        return

    history.recommended = []
    history.closed_excluded = []
    history.updated_at = now_kst()
    store.save_history(history)


def clear_all(store: StateStore, session_id: str) -> None:
    """추천·거절 이력을 모두 비운다. (full reset)"""
    store.delete_history(session_id)