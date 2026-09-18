"""INFO 원본 데이터를 사용자 화면용으로 최소 정리한다.

C는 TourAPI에서 얻은 원문을 계약 데이터로 보존하고, A는 답변과 카드에 필요한
표시 규칙만 적용한다. 이 모듈은 원문 데이터의 성공/실패 판정에 관여하지 않는다.
"""

from __future__ import annotations

import re
from datetime import datetime

_CAR_CAPACITY_PATTERN = re.compile(r"승용차\s*[^/,\)\]]+")
_CITYDATA_TIMESTAMP_PATTERN = re.compile(
    r"^(?P<year>\d{4})[-.]?(?P<month>\d{2})[-.]?(?P<day>\d{2})"
    r"(?:[ T](?P<hour>\d{2}):?(?P<minute>\d{2}))?$"
)


def format_parking_for_display(value: str | None) -> str | None:
    """주차 원문에서 일반 사용자가 우선 보는 승용차 정보만 표시한다.

    예: ``가능 (승용차 240대 / 버스 50대)`` → ``가능 (승용차 240대)``.
    승용차 수용 대수가 없는 값은 정보 손실을 막기 위해 원문 그대로 둔다.
    """

    if value is None:
        return None

    car_match = _CAR_CAPACITY_PATTERN.search(value)
    if car_match is None:
        return value

    status = value.split("(", maxsplit=1)[0].strip()
    if not status:
        return car_match.group(0).strip()
    return f"{status} ({car_match.group(0).strip()})"


def format_citydata_timestamp(value: str | None) -> str | None:
    """서울시 도시데이터 시각을 카드·말풍선용 한국어 형식으로 정리한다.

    ``20260820 1520``과 ``2026-08-20 15:20``처럼 API마다 다른 원문 형식을
    ``8월 20일 15:20``으로 통일한다. 해석하지 못한 값은 정보 손실 없이 그대로 둔다.
    """

    if value is None:
        return None
    normalized = value.strip()
    match = _CITYDATA_TIMESTAMP_PATTERN.fullmatch(normalized)
    if match is None:
        return normalized or None
    month = int(match.group("month"))
    day = int(match.group("day"))
    hour = match.group("hour")
    minute = match.group("minute")
    date_label = f"{month}월 {day}일"
    return f"{date_label} {hour}:{minute}" if hour and minute else date_label


def parse_citydata_timestamp(value: str | None) -> datetime | None:
    """서울시 도시데이터 시각 원문을 실제 시간 계산용 ``datetime``으로 판독한다.

    ``format_citydata_timestamp``는 표시용 문자열만 만들고 시간 값은 버린다.
    피크 시간대 계산처럼 두 시각의 차이를 구해야 할 때는 이 함수를 쓴다.
    시(hour)·분(minute)이 없는 날짜만 있는 원문은 시간 차이를 계산할 수
    없으므로 ``None``을 반환한다.
    """

    if value is None:
        return None
    match = _CITYDATA_TIMESTAMP_PATTERN.fullmatch(value.strip())
    if match is None or match.group("hour") is None:
        return None
    try:
        return datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            int(match.group("hour")),
            int(match.group("minute")),
        )
    except ValueError:
        return None


__all__ = [
    "format_citydata_timestamp",
    "format_parking_for_display",
    "parse_citydata_timestamp",
]
