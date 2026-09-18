"""서울시 공중화장실 개방시간 표기 해석.

역할: `mgisToiletPoi`의 개방시간 원문 한 줄을 "지금 열려 있나"로 바꾼다.
입력: `상시(24시간)`·`정시(09:00~18:00)`·`기타|05:00~익일01:00` 같은 원문 문자열.
출력: 해석 결과(OpenHours)와 특정 시각의 개방 여부.
호출 시점: 화장실 조회에서 후보를 거리로 추린 뒤 "지금 이용 가능"을 가릴 때.

원문은 자치구 담당자가 손으로 적은 값이라 형식이 통일돼 있지 않다. 실측(4,447건)
기준 88.8%만 시각으로 환산되고, 나머지 11.2%는 대부분 `정시(영업시작~종료)`
(398건) — 입점 건물의 영업시간을 따라간다는 뜻이라 시각 자체가 없다. 그래서 이
모듈은 "모르면 모른다고 답하는" 3값(열림/닫힘/판단불가)을 돌려주고, 판단불가는
호출부가 원문을 그대로 사용자에게 보여주도록 한다. 억지로 열림/닫힘 둘 중 하나로
정하면 급한 사용자를 닫힌 화장실로 보내게 된다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_MINUTES_PER_DAY = 1440

# 24시간 개방 표기. 시각 구간이 함께 적힌 경우(`05시~24시`)는 구간 해석이 우선이므로
# 호출부에서 `~` 유무를 먼저 본다.
_ALWAYS_TOKENS = ("24시간", "상시")

# 요일 제약. 정규식보다 부분 문자열이 안전하다 — 표기가 문장으로 섞여 있다.
_WEEKDAY_ONLY_TOKENS = ("평일만", "평일", "월~금")

# "다 열려 있는 건 아니다"라고 알려야 하는 단서. 정확한 요일까지는 못 읽으므로
# 사용자에게 그대로 덧붙여 보여준다.
_CAVEAT_TOKENS = ("휴무", "비개방", "미개방", "행사시")


@dataclass(frozen=True)
class OpenHours:
    """개방시간 해석 결과.

    `always`면 24시간. `opens`/`closes`가 있으면 분 단위 구간이고, `closes`가
    1440을 넘으면 자정을 넘겨 다음 날까지 연다는 뜻이다(`익일01:00` → 1500).
    셋 다 없으면 시각을 못 읽은 것이라 `raw`를 그대로 보여줘야 한다.
    """

    always: bool = False
    opens: int | None = None
    closes: int | None = None
    weekday_only: bool = False
    caveat: str = ""
    raw: str = ""

    @property
    def resolvable(self) -> bool:
        """시각으로 환산됐는지. False면 개방 여부를 단정할 수 없다."""
        return self.always or (self.opens is not None and self.closes is not None)


def _minutes(hour: int, minute: int, next_day: bool) -> int:
    total = hour * 60 + minute
    return total + _MINUTES_PER_DAY if next_day else total


def _parse_side(side: str) -> tuple[int, int, bool] | None:
    """구간의 한쪽(`09:00`, `9시30분`, `익일01:00`, `07`)을 시·분으로 읽는다."""
    next_day = "익일" in side
    side = side.replace("익일", "")
    # `06::00`처럼 콜론이 겹친 오타가 실제 데이터에 있다.
    side = re.sub(r":+", ":", side)

    matched = re.search(r"(\d{1,2})\s*[:시]\s*(\d{1,2})", side)
    if matched:
        return int(matched.group(1)), int(matched.group(2)), next_day
    matched = re.search(r"(\d{1,2})", side)
    if matched:
        return int(matched.group(1)), 0, next_day
    return None


def _parse_range(text: str) -> tuple[int, int] | None:
    parts = text.split("~", 1)
    if len(parts) != 2:
        return None

    left = _parse_side(parts[0])
    right = _parse_side(parts[1])
    if left is None or right is None:
        return None

    opens = _minutes(*left)
    closes = _minutes(*right)
    # `22:00~02:00`처럼 종료가 시작보다 이르면 자정을 넘긴 것으로 읽는다.
    if closes <= opens:
        closes += _MINUTES_PER_DAY
    return opens, closes


def parse_open_hours(raw: str | None) -> OpenHours:
    """개방시간 원문을 해석한다. 빈 값이나 시각이 없는 표기도 예외 없이 받는다."""
    text = (raw or "").strip().strip("|").strip()
    if not text:
        return OpenHours(raw="")

    flat = text.replace("\n", " ")
    caveat = " ".join(token for token in _CAVEAT_TOKENS if token in flat)
    weekday_only = any(token in flat for token in _WEEKDAY_ONLY_TOKENS)
    has_range = "~" in flat

    if has_range:
        parsed = _parse_range(flat)
        if parsed is not None:
            opens, closes = parsed
            # 00:00~24:00은 사실상 24시간 개방이다.
            if opens == 0 and closes >= _MINUTES_PER_DAY:
                return OpenHours(always=True, caveat=caveat, raw=text)
            return OpenHours(
                opens=opens,
                closes=closes,
                weekday_only=weekday_only,
                caveat=caveat,
                raw=text,
            )
        # `상시(24시간)|정시(영업시작~종료)`처럼 구간 해석은 실패했지만 24시간
        # 표기가 남아 있는 값이 있다. 이때는 24시간 쪽을 믿는다.

    if any(token in flat for token in _ALWAYS_TOKENS):
        return OpenHours(always=True, caveat=caveat, raw=text)

    # `정시(영업시작~종료)`, `기타`, `운영시간내`, `15시간`처럼 시각이 없는 표기.
    return OpenHours(weekday_only=weekday_only, caveat=caveat, raw=text)


def is_open_at(hours: OpenHours, weekday: int, minute_of_day: int) -> bool | None:
    """`weekday`는 월=0..일=6. 시각을 못 읽은 표기면 None."""
    if hours.weekday_only and weekday >= 5:
        return False
    if hours.always:
        return True
    if hours.opens is None or hours.closes is None:
        return None
    # 자정을 넘겨 여는 곳은 "어제 열어서 아직 안 닫은" 경우도 열린 것이다.
    return any(
        hours.opens <= probe <= hours.closes
        for probe in (minute_of_day, minute_of_day + _MINUTES_PER_DAY)
    )


def describe_open_hours(hours: OpenHours) -> str:
    """사용자에게 보여줄 개방시간 한 줄. 해석 실패 시 원문을 그대로 쓴다."""
    if hours.always:
        return "24시간" + (f" ({hours.caveat})" if hours.caveat else "")
    if hours.opens is not None and hours.closes is not None:
        opens = hours.opens % _MINUTES_PER_DAY
        closes = hours.closes
        suffix = ""
        if closes >= _MINUTES_PER_DAY:
            closes -= _MINUTES_PER_DAY
            suffix = "익일 "
        label = f"{opens // 60:02d}:{opens % 60:02d}~{suffix}{closes // 60:02d}:{closes % 60:02d}"
        notes = [note for note in ("평일만" if hours.weekday_only else "", hours.caveat) if note]
        return label + (f" ({', '.join(notes)})" if notes else "")
    return hours.raw or "개방시간 정보 없음"


__all__ = ["OpenHours", "describe_open_hours", "is_open_at", "parse_open_hours"]
