"""Package B - 계정 단위 취향 설정. (TP-222 후속)

취향 설정 화면에서 고른 값을 사람에게 붙여 보관한다.

**이 모듈만 session_id가 아니라 user_id로 키를 잡는다.** saved_places.py를
비롯한 다른 상태 엔티티는 전부 세션 단위이고 세션 TTL과 함께 사라지지만, 취향은
세션을 넘어 유지돼야 하는 값이다 — 세션에 얹으면 대화를 새로 시작할 때마다 다시
골라야 한다. 그래서 신원이 없으면 저장할 자리가 정해지지 않고, 라우트도
RequiredPrincipal을 쓴다(Principal이 optional인 다른 라우트들과 다른 점이다).

**항목의 내용을 검증하지 않는다.** 칩과 DB 코드의 대응은 화면이 갖고 있고
(frontend `pages/preferenceOptions.ts`), 여기서 다시 검사하면 칩 목록을 고칠
때마다 두 곳이 갈린다. 형태(label/source/codes)만 스키마가 본다.

**저장한다고 추천이 달라지지는 않는다.** 고른 값을 추천 요청에 싣는 경로는 아직
없다 — 순위가 바뀌는 변경이라 실측하고 넣기로 했다. 이 모듈은 그때 읽힐 자리를
먼저 만들어 둔 것이다.
"""

from app.state.schema import UserPreference, UserPreferenceList, now_kst
from app.state.store import StateStore


def get_or_create(store: StateStore, user_id: str) -> UserPreferenceList:
    """취향을 조회하고, 없으면 빈 목록을 만든다.

    행이 없는 것("아직 고른 적 없음")과 빈 목록("다 지웠음")을 호출부에서
    구분할 일이 없어 둘을 같게 취급한다 — 어느 쪽이든 화면에는 아무것도
    선택되지 않은 상태로 보인다.
    """
    preferences = store.get_preferences(user_id)
    if preferences is None:
        preferences = UserPreferenceList(user_id=user_id)
    return preferences


def get_items(store: StateStore, user_id: str) -> list[UserPreference]:
    """고른 취향 전체. 고른 순서(먼저 고른 것이 앞)."""
    preferences = store.get_preferences(user_id)
    if preferences is None:
        return []
    return list(preferences.items)


def replace(
    store: StateStore,
    user_id: str,
    items: list[UserPreference],
) -> UserPreferenceList:
    """취향을 통째로 바꾼다.

    항목 단위 추가/삭제가 아니라 전체 교체인 이유는 화면이 그렇게 동작하기
    때문이다 — 취향 설정은 여러 칩을 고른 뒤 "저장"을 한 번 누르는 흐름이라,
    중간 상태를 서버에 보낼 일이 없다. 담기/빼기가 각각 요청인 보관함
    (saved_places.py)과 다른 점이다.

    빈 목록도 정상적인 저장이다. 행을 지우지 않고 빈 배열을 남긴다 — 지우면
    다음 조회에서 "아직 고른 적 없음"과 "다 지웠음"이 구분되지 않는다.
    """
    preferences = get_or_create(store, user_id)
    preferences.items = list(items)
    preferences.updated_at = now_kst()
    store.save_preferences(preferences)
    return preferences
