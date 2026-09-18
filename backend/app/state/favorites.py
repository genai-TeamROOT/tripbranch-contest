"""Package B - 계정 단위 즐겨찾기. (위치 설정 화면, PR #361 후속)

위치 설정 화면에서 담은 장소를 사람에게 붙여 보관한다.

**preferences.py와 같은 자리의 값이다.** 세션이 아니라 사람에게 붙고, 세션 TTL과
함께 사라지면 안 된다 — 대화를 새로 시작할 때마다 즐겨찾기를 다시 담아야 한다면
즐겨찾기가 아니다. 그래서 키가 user_id이고 라우트도 RequiredPrincipal을 쓴다.

**항목의 내용을 검증하지 않는다.** 어떤 장소를 담을지는 화면이 정하고, 여기서
다시 검사하면 검색 결과의 모양을 고칠 때마다 두 곳이 갈린다. 형태만 스키마가 본다.

**여기 담긴 이름이 곧 검색어가 된다.** `search_center_name`은 화면이 추천 요청의
`selected_search_center`로 실어 보내는 값이라, 사용자가 label을 바꿔도 이 값은
그대로 둔다(schema.UserFavorite 참고).
"""

from app.state.schema import UserFavorite, UserFavoriteList, now_kst
from app.state.store import StateStore


def get_or_create(store: StateStore, user_id: str) -> UserFavoriteList:
    """즐겨찾기를 조회하고, 없으면 빈 목록을 만든다.

    행이 없는 것("아직 담은 적 없음")과 빈 목록("다 지웠음")을 호출부에서
    구분할 일이 없어 둘을 같게 취급한다 — 어느 쪽이든 화면에는 빈 목록이다.
    """
    favorites = store.get_favorites(user_id)
    if favorites is None:
        favorites = UserFavoriteList(user_id=user_id)
    return favorites


def get_items(store: StateStore, user_id: str) -> list[UserFavorite]:
    """담은 즐겨찾기 전체. 담은 순서(먼저 담은 것이 앞)."""
    favorites = store.get_favorites(user_id)
    if favorites is None:
        return []
    return list(favorites.items)


def replace(
    store: StateStore,
    user_id: str,
    items: list[UserFavorite],
) -> UserFavoriteList:
    """즐겨찾기를 통째로 바꾼다.

    담기·빼기가 각각 요청인 보관함(saved_places.py)과 달리 전체 교체인 이유는
    화면이 목록을 통째로 다루기 때문이다 — 이름 바꾸기와 순서가 있어서, 항목
    하나의 변경도 결국 목록 전체의 다음 상태로 표현된다.

    빈 목록도 정상적인 저장이다. 행을 지우지 않고 빈 배열을 남긴다 — 지우면
    다음 조회에서 "아직 담은 적 없음"과 "다 지웠음"이 구분되지 않고, 프론트가
    그 구분으로 이 기기의 값을 올릴지 정한다(favoritesSync).
    """
    favorites = get_or_create(store, user_id)
    favorites.items = list(items)
    favorites.updated_at = now_kst()
    store.save_favorites(favorites)
    return favorites
