"""저장한 일정 저장소의 계약을 고정한다. (SCHEDULE, 카드 2)

역할: 계정 단위 보관·멱등·격리·이름 변경·삭제가 규칙대로 도는지 검증한다.
입력: InMemoryStateStore와 saved_schedules 모듈.
출력: 위 다섯 가지에 대한 assertion.
호출 시점: pytest 실행 시.

**이 파일이 지키는 핵심은 둘이다.** 하나는 남의 일정이 목록에 섞이지 않는 것,
다른 하나는 같은 턴의 일정을 두 번 저장해도 한 줄인 것 — 둘 다 조용히 깨지면
사용자가 화면을 보고서야 알게 되는 종류다.
"""

from app.state import saved_schedules
from app.state.store import InMemoryStateStore

USER = "3f1a9c04-0000-4000-8000-000000000001"
OTHER = "3f1a9c04-0000-4000-8000-000000000002"
PAYLOAD = {"items": [{"order": 1, "place_id": "p1"}], "total_duration_min": 180}


def _store() -> InMemoryStateStore:
    return InMemoryStateStore()


def test_저장하면_id가_채워져_돌아온다() -> None:
    store = _store()

    saved = saved_schedules.save(
        store, USER, title="종로 반나절", payload=PAYLOAD, session_id="sess_1", run_id="run_1"
    )

    assert saved.id
    assert saved.user_id == USER
    assert saved.title == "종로 반나절"
    assert saved.payload == PAYLOAD


def test_같은_턴을_두_번_저장해도_한_줄이다() -> None:
    """저장 버튼을 두 번 누르거나 요청이 재시도되는 경우다. 목록에 같은 일정이
    두 줄로 보이면 사용자에게는 그 자체가 버그다."""
    store = _store()

    first = saved_schedules.save(store, USER, title="종로 반나절", payload=PAYLOAD, run_id="run_1")
    second = saved_schedules.save(store, USER, title="또 눌렀다", payload=PAYLOAD, run_id="run_1")

    assert first.id == second.id
    # 먼저 저장한 제목이 남는다 — 두 번째 요청이 덮어쓰면 사용자가 붙인 이름이
    # 재시도 한 번에 날아간다.
    assert second.title == "종로 반나절"
    assert len(saved_schedules.list_for_user(store, USER)) == 1


def test_run_id가_없으면_멱등_판정을_하지_않는다() -> None:
    """run_id 없이 끝나는 경로가 있다. 판정할 근거가 없으므로 그대로 새로 만든다."""
    store = _store()

    saved_schedules.save(store, USER, title="하나", payload=PAYLOAD)
    saved_schedules.save(store, USER, title="둘", payload=PAYLOAD)

    assert len(saved_schedules.list_for_user(store, USER)) == 2


def test_남의_일정은_목록에_섞이지_않는다() -> None:
    store = _store()
    saved_schedules.save(store, USER, title="내 것", payload=PAYLOAD, run_id="run_1")
    saved_schedules.save(store, OTHER, title="남의 것", payload=PAYLOAD, run_id="run_2")

    mine = saved_schedules.list_for_user(store, USER)

    assert [item.title for item in mine] == ["내 것"]


def test_목록은_최근_저장순이다() -> None:
    store = _store()
    for index in range(3):
        saved_schedules.save(
            store, USER, title=f"일정 {index}", payload=PAYLOAD, run_id=f"run_{index}"
        )

    titles = [item.title for item in saved_schedules.list_for_user(store, USER)]

    assert titles == ["일정 2", "일정 1", "일정 0"]


def test_이름을_바꿀_수_있다() -> None:
    store = _store()
    saved = saved_schedules.save(store, USER, title="종로 반나절", payload=PAYLOAD, run_id="run_1")

    renamed = saved_schedules.rename(store, saved.id or "", "엄마랑 가는 날")

    assert renamed is not None
    assert renamed.title == "엄마랑 가는 날"
    assert saved_schedules.get(store, saved.id or "").title == "엄마랑 가는 날"


def test_없는_일정을_바꾸거나_지워도_터지지_않는다() -> None:
    """호출부가 존재 여부를 먼저 확인하지 않아도 되게 한다 — saved_places의
    멱등 처리와 같은 이유다."""
    store = _store()

    assert saved_schedules.rename(store, "sched_없음", "새 이름") is None
    assert saved_schedules.remove(store, "sched_없음") is False


def test_지우면_목록에서_빠진다() -> None:
    store = _store()
    saved = saved_schedules.save(store, USER, title="종로 반나절", payload=PAYLOAD, run_id="run_1")

    assert saved_schedules.remove(store, saved.id or "") is True
    assert saved_schedules.list_for_user(store, USER) == []


def test_payload를_해석하지_않고_그대로_돌려준다() -> None:
    """B가 ScheduleResult 구조를 파싱하면 A의 스키마가 바뀔 때마다 따라가야 한다.
    모르는 모양이 들어와도 그대로 나가야 한다."""
    store = _store()
    weird = {"모르는키": [1, 2, {"중첩": True}]}

    saved = saved_schedules.save(store, USER, title="이상한 것", payload=weird, run_id="run_1")

    assert saved_schedules.get(store, saved.id or "").payload == weird
