"""Package B - 사용자가 저장한 일정. (SCHEDULE, 카드 2)

사용자가 "이 일정을 쓰겠다"고 고른 일정을 계정에 보관한다.

**화면 기록(session_messages)과 겸하지 않는다.** TP-222 후속으로 그 테이블이
생기면서 일정은 이미 저장되고 있지만, 그것은 "그때 화면에 나갔던 것"이고
"현재 상태로 다시 읽는 소비자를 두지 않는다"는 전제 위에 서 있다. 이름을 붙이고
나중에 열고 고치는 대상을 그 스냅샷으로 겸하게 하면 그 전제가 깨진다 — 보관함
(saved_places.py)을 추천 이력과 분리한 것과 같은 판단이다.

**이 모듈은 preferences.py와 같은 계정 단위 저장소다.** 세션 TTL과 30일 정리에
걸리지 않는다. 사용자가 이름 붙여 저장한 것이 30일 뒤 조용히 사라지면 그것은
저장이 아니다. 대신 DB가 auth.users에 FK cascade를 걸어 계정과 함께 사라진다
(마이그레이션 202609030005 주석).

**payload를 열어보지 않는다.** ScheduleResult를 직렬화한 그대로 담고 B는 파싱하지
않는다(SessionMessage.payload와 같은 취급). 그래서 **제목을 payload에서 뽑지
않는다** — 호출부가 넘겨야 한다. 화면이 일정을 그리고 있으니 기본 제목을 제안할
자리도 거기다.

**소유권은 여기서 보지 않는다.** 이 모듈은 저장소를 감싸기만 하고, 남의 것인지는
service.py가 principal과 대조한다. list_for_user()만 예외인데 그것은 키가 곧
신원이라 남의 것이 섞일 경로가 없기 때문이다(list_user_sessions와 같은 근거).
"""

from typing import Any

from app.state.schema import SavedSchedule
from app.state.store import StateStore

# 목록에 실어 보낼 최대 개수. MAX_LISTED_SESSIONS와 같은 값을 쓴다 — 두 목록이
# 같은 화면에서 나란히 보이는데 한쪽만 더 멀리 거슬러 올라가면 설명하기 어렵다.
MAX_LISTED_SCHEDULES = 50


def save(
    store: StateStore,
    user_id: str,
    *,
    title: str,
    payload: dict[str, Any],
    session_id: str | None = None,
    run_id: str | None = None,
) -> SavedSchedule:
    """일정을 저장하고 id가 채워진 것을 돌려준다.

    같은 (user_id, run_id)를 다시 저장하면 새로 만들지 않고 이미 있는 것을
    돌려준다. 저장 버튼을 두 번 누르거나 요청이 재시도되면 목록에 같은 일정이
    두 줄로 보이는데, 사용자에게 그것은 그 자체로 버그다(saved_places.add의
    멱등 처리와 같은 이유). run_id가 없는 경로는 그 판정을 할 수 없어 그대로
    새로 만든다.

    제목은 호출부가 준다 — 모듈 docstring 참고.
    """
    return store.save_schedule(
        SavedSchedule(
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            title=title,
            payload=payload,
        )
    )


def list_for_user(store: StateStore, user_id: str) -> list[SavedSchedule]:
    """내 일정을 최근 저장순으로."""
    return store.list_schedules_for_user(user_id, MAX_LISTED_SCHEDULES)


def get(store: StateStore, schedule_id: str) -> SavedSchedule | None:
    """일정 1건. 없으면 None. **소유권 대조는 호출부(service.py)가 한다.**"""
    return store.get_schedule(schedule_id)


def rename(store: StateStore, schedule_id: str, title: str) -> SavedSchedule | None:
    """제목을 바꾼다. 없으면 None.

    빈 제목은 여기서 막지 않는다 — 형태 검증은 라우트의 요청 모델이, 최종
    방어는 DB의 check 제약(saved_schedules_title_not_blank)이 맡는다.
    """
    return store.rename_schedule(schedule_id, title)


def remove(store: StateStore, schedule_id: str) -> bool:
    """지웠으면 True. 없으면 오류가 아니라 False다 — 사용자가 원한 결과
    ("그 일정이 없다")가 이미 성립한다(saved_places.remove와 같은 이유)."""
    return store.delete_schedule(schedule_id)
