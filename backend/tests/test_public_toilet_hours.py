"""개방시간 표기 해석 테스트.

케이스는 실제 4,447건에서 실제로 나온 표기를 그대로 가져왔다 — 손으로 적힌 값이라
상상한 형식을 검사하면 의미가 없다.
"""

import pytest

from app.public_toilet_hours import describe_open_hours, is_open_at, parse_open_hours

# 월요일 오전 2시 7분(급한 새벽 상황)과 월요일 낮 3시.
_MONDAY_EARLY = (0, 2 * 60 + 7)
_MONDAY_AFTERNOON = (0, 15 * 60)
_SATURDAY_AFTERNOON = (5, 15 * 60)


@pytest.mark.parametrize(
    "raw",
    ["상시(24시간)|", "상시(24시간)", "정시(00:00~24:00)"],
)
def test_always_open_is_open_at_any_hour(raw: str) -> None:
    hours = parse_open_hours(raw)

    assert hours.always is True
    assert is_open_at(hours, *_MONDAY_EARLY) is True


def test_time_range_closes_overnight() -> None:
    hours = parse_open_hours("기타|10:30~20:30")

    assert (hours.opens, hours.closes) == (630, 1230)
    assert is_open_at(hours, *_MONDAY_AFTERNOON) is True
    # 새벽 2시는 닫혀 있다 — 쌈지길이 50m라도 답으로 내놓으면 안 된다.
    assert is_open_at(hours, *_MONDAY_EARLY) is False


def test_next_day_notation_spans_midnight() -> None:
    # 실제 표기: 지하철 화장실 다수가 이 형식이다.
    hours = parse_open_hours("기타|05:00~익일01:00")

    assert hours.closes is not None and hours.closes > 1440
    assert is_open_at(hours, 0, 6 * 60) is True
    assert is_open_at(hours, 0, 0 * 60 + 30) is True
    # 03:00은 닫힌 시간대다.
    assert is_open_at(hours, 0, 3 * 60) is False


@pytest.mark.parametrize(
    "raw",
    ["기타|9시~18시30분", "기타|07~19시", "기타|06::00~23:00", "기타|05시~24시"],
)
def test_handwritten_time_notations_are_parsed(raw: str) -> None:
    hours = parse_open_hours(raw)

    assert hours.resolvable is True


def test_weekday_only_is_closed_on_weekend() -> None:
    hours = parse_open_hours("정시(09:00~18:00,평일)")

    assert hours.weekday_only is True
    assert is_open_at(hours, *_MONDAY_AFTERNOON) is True
    assert is_open_at(hours, *_SATURDAY_AFTERNOON) is False


@pytest.mark.parametrize("raw", ["정시(영업시작~종료)", "기타", "15시간", "", None])
def test_unresolvable_notations_return_none_instead_of_guessing(raw: str | None) -> None:
    """시각이 없는 표기를 열림/닫힘으로 단정하지 않는다.

    실측 11%가 여기 해당하고 대부분 ``정시(영업시작~종료)``(398건)다. 열림으로
    뭉개면 급한 사용자를 닫힌 문 앞으로 보내고, 닫힘으로 뭉개면 실제로 갈 수 있는
    곳을 지운다.
    """

    hours = parse_open_hours(raw)

    assert hours.resolvable is False
    assert is_open_at(hours, *_MONDAY_EARLY) is None


def test_always_wins_when_range_part_is_unreadable() -> None:
    # 실제 표기: 24시간과 "영업시작~종료"가 한 칸에 같이 적힌 행이 2건 있다.
    hours = parse_open_hours("상시(24시간)|정시(영업시작~종료)")

    assert hours.always is True


def test_describe_open_hours_shows_raw_text_when_unresolvable() -> None:
    assert describe_open_hours(parse_open_hours("상시(24시간)|")) == "24시간"
    assert describe_open_hours(parse_open_hours("기타|10:30~20:30")) == "10:30~20:30"
    # 해석 실패는 원문을 그대로 보여줘 사용자가 판단하게 한다.
    assert describe_open_hours(parse_open_hours("정시(영업시작~종료)")) == "정시(영업시작~종료)"


def test_describe_open_hours_marks_overnight_and_notes() -> None:
    assert describe_open_hours(parse_open_hours("기타|05:00~익일01:00")) == "05:00~익일 01:00"
    assert "평일만" in describe_open_hours(parse_open_hours("정시(09:00~18:00,평일)"))
    assert "휴무" in describe_open_hours(parse_open_hours("기타|10:00~22:00, 일 휴무"))
