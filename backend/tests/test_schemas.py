"""app.schemas의 UserConditions/ModifyPayload 검증 규칙 테스트.

- ModifyPayload: condition_changes에 changed_fields 밖 필드가 값을 갖고 있어도(예:
  호출자가 폴루션된 current_conditions를 실어 보내 LLM이 그 값을 그대로
  carry-forward한 경우), 생성 시점에 null/빈 배열로 정리되는지 확인한다.
  changed_fields 안의 필드(상대적 표현 계산값 포함)는 절대 건드리지 않아야 한다.
- UserConditions: max_travel_time/time_available의 0→None 정규화와 음수 거부를
  확인한다.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import (
    MAX_RECENT_FOLLOW_UPS,
    AgentRequest,
    ModifyPayload,
    ModifyType,
    ScheduleItem,
    UserConditions,
)


def test_fields_outside_changed_fields_are_cleared_even_when_populated() -> None:
    """Swagger 기본값 오염 재현: changed_fields 밖 필드에 값이 있어도 정리된다."""
    polluted = UserConditions(
        transport="walk",
        max_travel_time=0,
        time_available=0,
        companion="solo",
        budget="free",
        place_types=["attraction"],
        place_tags=["공원"],
        exclude_tags=["string"],
    )

    payload = ModifyPayload(
        modify_type=ModifyType.CHANGE_CONDITION,
        condition_changes=polluted,
        changed_fields=["budget"],
    )

    assert payload.condition_changes.budget == "free"
    assert payload.condition_changes.transport is None
    assert payload.condition_changes.max_travel_time is None
    assert payload.condition_changes.time_available is None
    assert payload.condition_changes.companion is None
    assert payload.condition_changes.place_types == []
    assert payload.condition_changes.place_tags == []
    assert payload.condition_changes.exclude_tags == []


def test_changed_field_computed_value_is_preserved() -> None:
    """"더 가까운 곳"처럼 changed_fields 안의 계산값은 검증기가 손대지 않는다."""
    current = UserConditions(max_travel_time=30, search_center="경복궁", transport="walk")
    changed = current.model_copy(update={"max_travel_time": 15})

    payload = ModifyPayload(
        modify_type=ModifyType.CHANGE_CONDITION,
        condition_changes=changed,
        changed_fields=["max_travel_time"],
    )

    assert payload.condition_changes.max_travel_time == 15
    # changed_fields 밖에 있던 search_center/transport는 정리된다.
    assert payload.condition_changes.search_center is None
    assert payload.condition_changes.transport is None


def test_reject_all_with_no_condition_changes_is_unaffected() -> None:
    payload = ModifyPayload(modify_type=ModifyType.REJECT_ALL, condition_changes=None)

    assert payload.condition_changes is None


def test_no_changes_needed_when_already_clean() -> None:
    """이미 changed_fields만 채워진 정상 입력은 그대로 유지된다(불필요한 복사 없음)."""
    clean = UserConditions(budget="free")

    payload = ModifyPayload(
        modify_type=ModifyType.CHANGE_CONDITION,
        condition_changes=clean,
        changed_fields=["budget"],
    )

    assert payload.condition_changes.budget == "free"


def test_zero_max_travel_time_normalized_to_none() -> None:
    """"시간 제한 없음"은 0이 아니라 None으로 표현하기로 확정됐다."""
    conditions = UserConditions(max_travel_time=0)

    assert conditions.max_travel_time is None


def test_zero_time_available_normalized_to_none() -> None:
    conditions = UserConditions(time_available=0)

    assert conditions.time_available is None


def test_positive_max_travel_time_preserved() -> None:
    conditions = UserConditions(max_travel_time=30, time_available=120)

    assert conditions.max_travel_time == 30
    assert conditions.time_available == 120


@pytest.mark.parametrize("field", ["max_travel_time", "time_available"])
def test_negative_time_fields_still_rejected(field: str) -> None:
    """0은 조용히 정규화되지만 음수는 여전히 ValidationError로 막힌다."""
    with pytest.raises(ValidationError):
        UserConditions(**{field: -5})


def test_cluster_id가_없는_옛_스냅샷도_그대로_읽힌다() -> None:
    """TP-243 — `saved_schedules.payload`와 `session_messages`에 이 필드가 없는
    스냅샷이 이미 쌓여 있다.

    **항목 배열을 그룹 구조로 바꾸지 않고 번호만 얹은 이유가 이것이다.** 저장된
    일정을 다시 여는 경로가 두 모양을 다 읽어야 하는데, 기본값 None이면 옛
    스냅샷은 "묶음 없음"으로 그대로 읽힌다.
    """

    old_snapshot = {
        "order": 1,
        "place_id": "p1",
        "place_name": "장소 p1",
        "estimated_arrival": "15:00",
        "estimated_duration_min": 60,
        "travel_to_next_min": 12,
        "reason": "테스트 이유",
    }

    item = ScheduleItem.model_validate(old_snapshot)

    assert item.cluster_id is None
    assert item.estimated_duration_min == 60


def test_recent_follow_ups_keeps_only_the_newest_entries() -> None:
    """제외 목록은 프롬프트에 실리므로 최근 것만 남긴다."""
    request = AgentRequest(
        user_input="카페 추천해줘",
        recent_follow_ups=[f"문구 {i}" for i in range(MAX_RECENT_FOLLOW_UPS + 5)],
    )

    assert len(request.recent_follow_ups) == MAX_RECENT_FOLLOW_UPS
    assert request.recent_follow_ups[-1] == f"문구 {MAX_RECENT_FOLLOW_UPS + 4}"


def test_recent_follow_ups_drops_malformed_entries_without_failing_the_turn() -> None:
    """화면이 보내는 값이라 무엇이든 올 수 있다.

    **422로 막지 않는다.** 버튼 중복을 막자고 대화를 끊을 이유가 없다 — 못 쓸 항목만
    버리고 나머지로 진행한다.
    """
    request = AgentRequest(
        user_input="카페 추천해줘",
        recent_follow_ups=["  다른 곳도 보여줘  ", "", "   ", "가" * 41, 123, None],
    )

    assert request.recent_follow_ups == ["다른 곳도 보여줘"]


def test_recent_follow_ups_ignores_a_value_that_is_not_a_list() -> None:
    request = AgentRequest(user_input="카페 추천해줘", recent_follow_ups="다른 곳도 보여줘")

    assert request.recent_follow_ups == []
