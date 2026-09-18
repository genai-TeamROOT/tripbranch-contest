"""주기 휴무 판정 — 그 달의 몇 번째 요일인지까지 본다. (TP-231)

이 판정은 **하드 필터로 이어진다.** 틀리면 점수가 깎이는 게 아니라 후보가 통째로
사라진다(`_derive_unlisted_weekday_closures()` 주석). 그래서 "쉬는 주에 잡는가"만큼
"안 쉬는 주에 안 잡는가"를 함께 본다.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.candidate_mapper import (
    _is_regular_closure,
    _weekday_ordinal,
    has_uncertain_closure,
)


def _at(day: int) -> datetime:
    """2026년 11월 N일 정오. 11월 1일은 일요일이다."""
    return datetime(2026, 11, day, 12, 0, tzinfo=UTC)


def _schedule(**rule: object) -> dict[str, object]:
    return {"closure_rules": [{"source_text": "테스트", **rule}]}


class Test주차_세기:
    """"그 달의 N번째 그 요일"로 센다 — 주 경계 정의를 끌어들이지 않는다."""

    @pytest.mark.parametrize(
        ("day", "expected"), [(1, 1), (7, 1), (8, 2), (14, 2), (15, 3), (22, 4), (29, 5)]
    )
    def test_일자로_순번을_센다(self, day: int, expected: int) -> None:
        assert _weekday_ordinal(_at(day)) == expected


class Test매주_휴무는_예전과_같다:
    """`week_ordinals`가 없으면 매주다. 기존 데이터가 이 모양이다."""

    @pytest.mark.parametrize("day", [2, 9, 16, 23, 30])  # 11월의 모든 월요일
    def test_모든_해당_요일에_휴무다(self, day: int) -> None:
        assert _is_regular_closure(_schedule(weekdays=["monday"]), _at(day)) is True

    def test_다른_요일은_휴무가_아니다(self) -> None:
        assert _is_regular_closure(_schedule(weekdays=["monday"]), _at(3)) is False


class Test주차를_지정하면_그_주에만_쉰다:
    """`2,4주 일요일`. 안 쉬는 주에 잡으면 멀쩡한 장소가 사라진다."""

    # 2026-11의 일요일: 1(1번째)·8(2번째)·15(3번째)·22(4번째)·29(5번째)
    @pytest.mark.parametrize(("day", "closed"),
                             [(1, False), (8, True), (15, False), (22, True), (29, False)])
    def test_지정한_주에만_휴무다(self, day: int, closed: bool) -> None:
        schedule = _schedule(weekdays=["sunday"], week_ordinals=[2, 4])
        assert _is_regular_closure(schedule, _at(day)) is closed

    def test_주차가_맞아도_요일이_다르면_아니다(self) -> None:
        schedule = _schedule(weekdays=["sunday"], week_ordinals=[2])
        assert _is_regular_closure(schedule, _at(9)) is False  # 9일은 둘째 월요일


class Test불확실한_주기_휴무:
    """`월 1회 월요일` — 몇 번째인지 원문에 없다."""

    @pytest.mark.parametrize("day", [2, 9, 16, 23, 30])
    def test_휴무로_치지_않는다(self, day: int) -> None:
        """매주로 치면 안 쉬는 3주의 월요일에 후보가 통째로 사라진다."""
        schedule = _schedule(weekdays=["monday"], uncertain=True)
        assert _is_regular_closure(schedule, _at(day)) is False

    def test_대신_안내할_수_있게_알린다(self) -> None:
        schedule = _schedule(weekdays=["monday"], uncertain=True)
        assert has_uncertain_closure(schedule, _at(2)) is True

    def test_다른_요일에는_안내하지_않는다(self) -> None:
        schedule = _schedule(weekdays=["monday"], uncertain=True)
        assert has_uncertain_closure(schedule, _at(3)) is False

    def test_확실한_휴무는_안내_대상이_아니다(self) -> None:
        schedule = _schedule(weekdays=["monday"])
        assert has_uncertain_closure(schedule, _at(2)) is False


class Test기존_데이터가_깨지지_않는다:
    """DB에 있는 1,068곳의 모양이 `{weekdays, source_text}`뿐이다."""

    def test_새_키가_없어도_읽힌다(self) -> None:
        legacy = {"closure_rules": [{"weekdays": ["monday"], "source_text": "매주 월요일"}]}
        assert _is_regular_closure(legacy, _at(2)) is True
        assert has_uncertain_closure(legacy, _at(2)) is False

    def test_모양이_망가진_행은_건너뛴다(self) -> None:
        broken = {"closure_rules": ["문자열", None, {"weekdays": "monday"}]}
        assert _is_regular_closure(broken, _at(2)) is False
        assert has_uncertain_closure(broken, _at(2)) is False
