"""장소 운영시간 원문을 보존하면서 안전하게 정규화한다."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import time
from enum import StrEnum
from html.parser import HTMLParser
from typing import Any

_COURSE_CONTENT_TYPE_ID = "25"
logger = logging.getLogger(__name__)

OPERATING_PARSER_VERSION = "operating-hours-1.2.0"
# 원문에 적힌 휴무와 구분하기 위한 유도 휴무 표식. `_derive_unlisted_weekday_closures()` 참고.
DERIVED_CLOSURE_SOURCE_TEXT = "운영시간에 열거되지 않은 요일"
_WEEKDAY_INDEX = {
    "월": 0,
    "화": 1,
    "수": 2,
    "목": 3,
    "금": 4,
    "토": 5,
    "일": 6,
}
_TIME_RANGE_PATTERN = re.compile(
    r"(?P<start_hour>\d{1,2})\s*:\s*(?P<start_minute>\d{2})"
    r"\s*(?:~|∼|～|–|—|-)\s*"
    r"(?P<next_day>(?:익일|다음\s*날)\s*)?"
    r"(?P<end_hour>\d{1,2})\s*:\s*(?P<end_minute>\d{2})"
)
_LAST_ADMISSION_PATTERN = re.compile(
    r"(?:입장\s*마감|매표\s*마감|마지막\s*입장)\s*"
    r"(?P<hour>\d{1,2})\s*:\s*(?P<minute>\d{2})"
)
_MONTH_RANGE_PATTERN = re.compile(
    r"(?P<start>\d{1,2})\s*월\s*(?:~|∼|～|–|—|-)\s*"
    r"(?P<end>\d{1,2})\s*월"
)
_WEEKDAY_ALIASES = {
    "평일": frozenset({0, 1, 2, 3, 4}),
    "주중": frozenset({0, 1, 2, 3, 4}),
    "주말": frozenset({5, 6}),
}
# 범위 표기(`월요일~금요일`)와 나열 표기(`금요일, 토요일`)는 전개 규칙이 다르다 —
# 범위는 사이 요일을 채우고 나열은 적힌 요일만 쓴다. 구분자를 하나로 합쳐 읽으면
# `월요일~금요일`이 [월, 금]이 되어 사이 요일이 조용히 빠진다.
_WEEKDAY_RANGE_SEPARATORS = "~∼～–—-"
_WEEKDAY_SEPARATOR_CLASS = r"[,·/및과와~∼～–—-]"
_WEEKDAY_TOKEN = r"[월화수목금토일]요일|평일|주중|주말"
_WEEKDAY_SCAN_PATTERN = re.compile(
    rf"{_WEEKDAY_TOKEN}|[월화수목금토일]|[{_WEEKDAY_RANGE_SEPARATORS}]"
)
_WEEKLY_CLOSURE_PATTERN = re.compile(
    r"매주\s*(?P<weekdays>(?:[월화수목금토일](?:요일)?"
    rf"(?:\s*{_WEEKDAY_SEPARATOR_CLASS}\s*)?)+)"
)
# 운영시간 원문에서 뒤따르는 시간 구간의 적용 요일을 선언하는 표기.
# `1월~2월`이나 `평일미사 월 07:00`의 한 글자 표기를 요일로 오독하지 않도록
# `요일` 접미사가 붙은 형태와 평일·주중·주말만 범위 선언으로 인정한다.
_WEEKDAY_SCOPE_PATTERN = re.compile(
    rf"(?:{_WEEKDAY_TOKEN})(?:\s*{_WEEKDAY_SEPARATOR_CLASS}\s*(?:{_WEEKDAY_TOKEN}))*"
)
# 요일 표기가 적용 범위가 아니라 휴무 안내나 부속 시간(준비시간·미사)인 경우.
# `휴일`은 넣지 않는다 — `일요일 및 공휴일 10:00~17:00`의 `공휴일`에 걸려
# 일요일 구간이 통째로 앞 요일 범위에 붙는다.
_WEEKDAY_SCOPE_SUFFIX_BLOCKERS = (
    "휴관",
    "휴무",
    "휴장",
    "휴업",
    "미사",
    "브레이크",
    "준비시간",
    "휴게시간",
)
# `매주 화요일 휴관`·`매월 마지막 수요일`처럼 되풀이 예외를 가리키는 표기.
_WEEKDAY_SCOPE_PREFIX_BLOCKERS = (
    "매주",
    "매월",
    "첫째",
    "둘째",
    "셋째",
    "넷째",
    "마지막",
    "다음",
)
_WEEKDAY_SCOPE_CONTEXT_WINDOW = 8
# `[인문사회자연과학실]` 같은 시설 구획 표기. 요일이 아닌 대괄호 구획이 나오면
# 앞 구획의 적용 요일을 물려주지 않는다 — 물려주면 요일 표기가 없는 뒤 구획
# (`[자율학습실] 하절기 07:00~22:00`)이 앞 구획의 `주말` 전용으로 좁혀진다.
_BRACKET_SECTION_PATTERN = re.compile(r"\[[^\]]*\]")
# TourAPI 관광지·자연 원문에서 "24시간"만큼 흔한 상시 개방 표기. D-024와 같은 이유로
# 시간 구간을 못 읽었다는 이유만으로 후보를 운영 미확인으로 떨어뜨리지 않는다.
_ALWAYS_OPEN_PATTERN = re.compile(r"상시\s*(?:개방|운영|이용|출입)?")


class OperatingParseStatus(StrEnum):
    PARSED = "parsed"
    PARTIAL = "partial"
    UNKNOWN = "unknown"
    ASSUMED = "assumed"


class OperatingAvailability(StrEnum):
    SCHEDULED = "scheduled"
    ALL_DAY = "all_day"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TimeRange:
    start: time
    end: time
    crosses_midnight: bool


@dataclass(frozen=True)
class OperatingRule:
    months: frozenset[int] | None
    weekdays: frozenset[int] | None
    time_ranges: tuple[TimeRange, ...]
    last_admission: time | None
    source_text: str


@dataclass(frozen=True)
class ClosureRule:
    """정기 휴무 한 줄.

    `week_ordinals`가 없으면 **매주** 그 요일에 쉰다 — 지금까지의 동작이고, 이 필드를
    모르는 기존 데이터도 그대로 그 뜻으로 읽힌다.
    """

    weekdays: frozenset[int]
    source_text: str
    # 그 달의 몇 번째 그 요일인가(1부터). `2,4주 일요일`이면 {2, 4}다.
    # None이면 매주다.
    #
    # **"그 달의 N번째 그 요일"로 센다**(`(일 - 1) // 7 + 1`). "그 달 N번째 주에
    # 속한 요일"과 다를 수 있는데, 그러면 주 경계 정의(ISO인지 일요일 시작인지)를
    # 끌어들여야 하고 1일의 요일에 따라 답이 흔들린다. 사람이 "둘째 주 일요일"이라고
    # 말할 때 보통 그 달의 두 번째 일요일을 뜻하는 것도 근거다.
    week_ordinals: frozenset[int] | None = None
    # 주기 휴무인 것은 아는데 **몇 번째인지 원문에 없는** 경우(`월 1회 월요일`,
    # `격주 일요일`). 실측 활성 8,007곳 중 약 180곳이 이렇다.
    #
    # **휴무로 판정하지 않는다.** 매주로 치면 안 쉬는 3주의 그 요일에 멀쩡한 장소가
    # 통째로 사라진다 — 이 판정은 점수 손해가 아니라 하드 필터로 이어진다
    # (`_derive_unlisted_weekday_closures()` 주석 참고). 대신 카드에 안내를 붙여
    # 사용자가 확인하게 한다. 확인하지 못한 것을 확인한 척하지 않는다(D-042).
    uncertain: bool = False


@dataclass(frozen=True)
class OperatingSchedule:
    raw_operating_hours: str | None
    raw_rest_date: str | None
    cleaned_operating_hours: str | None
    cleaned_rest_date: str | None
    availability: OperatingAvailability
    rules: tuple[OperatingRule, ...]
    closure_rules: tuple[ClosureRule, ...]
    parse_status: OperatingParseStatus
    assumption_reason: str | None
    warnings: tuple[str, ...]


class _TextExtractor(HTMLParser):
    _BREAK_TAGS = {"br", "div", "p", "li", "tr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag.lower() in self._BREAK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._BREAK_TAGS - {"br"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def clean_operating_text(value: str | None) -> str | None:
    """HTML 줄바꿈·entity·태그와 불필요한 공백을 정리한다."""
    if value is None or not value.strip():
        return None
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    lines = [
        re.sub(r"[^\S\n]+", " ", line).strip()
        for line in "".join(parser.parts).splitlines()
    ]
    cleaned = "\n".join(line for line in lines if line)
    return cleaned or None


def resolve_operating_schedule(
    *,
    content_type_id: str,
    operating_hours: str | None,
    rest_date: str | None,
    stored: Mapping[str, Any] | None = None,
    stored_parser_version: str | None = None,
) -> OperatingSchedule:
    """저장된 파싱 결과를 쓰되, 파서가 그 사이 바뀌었으면 원문을 다시 읽는다.

    **읽기 경로가 요청마다 원문을 파싱하던 것을 대체한다.** 후보 30곳이면 그만큼
    정규식이 돌았고, 파싱이 LLM으로 바뀌면 그 자리에서는 아예 부를 수 없다.

    `stored_parser_version`이 지금 버전과 다르면 저장된 값을 **믿지 않는다.** 파서를
    고치고 재적재를 잊었을 때 옛 결과가 조용히 나가는 것을 막는다 — 원래 원문
    재파싱이 있던 이유가 그 걱정이었고, 그것을 버전 게이트로 옮겨 담은 것이다.

    저장된 값이 없거나 모양이 깨졌어도 원문 파싱으로 되돌아간다. 이 함수는 항상
    결과를 돌려주므로 호출부가 폴백을 따로 쓰지 않는다.
    """

    if stored is not None and stored_parser_version == OPERATING_PARSER_VERSION:
        restored = deserialize_operating_schedule(
            stored, raw_operating_hours=operating_hours, raw_rest_date=rest_date
        )
        if restored is not None:
            return restored
    return normalize_operating_schedule(
        content_type_id=content_type_id,
        operating_hours=operating_hours,
        rest_date=rest_date,
    )


def deserialize_operating_schedule(
    stored: Mapping[str, Any] | None,
    *,
    raw_operating_hours: str | None,
    raw_rest_date: str | None,
) -> OperatingSchedule | None:
    """적재 배치가 저장한 JSON을 다시 `OperatingSchedule`로 읽는다.

    **읽기 경로가 원문을 매번 다시 파싱하지 않게 하려고 만든 함수다.** 그동안은
    파서가 자주 바뀌어 "고치면 즉시 반영"이 필요했지만, 저장된 값을 그대로 쓰려면
    되돌릴 방법이 있어야 한다.

    모양이 예상과 다르면 `None`을 돌려준다 — 호출부가 원문 파싱으로 되돌아간다.
    여기서 예외를 올리면 행 하나가 망가졌을 때 그 요청 전체가 죽는다.

    직렬화(`place_sync.serialize_operating_schedule()`)와 짝이다. 한쪽만 바뀌면
    조용히 폴백으로 떨어져 "왜 느린가"로만 나타나므로 테스트로 왕복을 잠근다.
    """

    if not isinstance(stored, Mapping):
        return None
    try:
        availability = OperatingAvailability(str(stored.get("availability")))
        rules = tuple(_deserialize_rule(item) for item in stored.get("rules") or ())
        closure_rules = tuple(
            _deserialize_closure_rule(item) for item in stored.get("closure_rules") or ()
        )
    except (ValueError, TypeError, KeyError, AttributeError):
        logger.warning("operating_schedule 역직렬화 실패 — 원문 파싱으로 돌아간다", exc_info=True)
        return None

    warnings = tuple(str(item) for item in stored.get("warnings") or ())
    assumption_reason = stored.get("assumption_reason")
    return OperatingSchedule(
        raw_operating_hours=raw_operating_hours,
        raw_rest_date=raw_rest_date,
        cleaned_operating_hours=clean_operating_text(raw_operating_hours),
        cleaned_rest_date=clean_operating_text(raw_rest_date),
        availability=availability,
        rules=rules,
        closure_rules=closure_rules,
        # 저장된 값에는 상태가 없다 — 배치가 넣은 시점의 판정이므로 파싱 성공으로
        # 본다. 상태는 warnings로 이미 드러나 있다.
        parse_status=(
            OperatingParseStatus.ASSUMED
            if assumption_reason
            else OperatingParseStatus.PARSED
        ),
        assumption_reason=str(assumption_reason) if assumption_reason else None,
        warnings=warnings,
    )


def _deserialize_rule(item: Any) -> OperatingRule:
    months = item.get("months")
    weekdays = item.get("weekdays")
    last_admission = item.get("last_admission")
    return OperatingRule(
        months=frozenset(int(m) for m in months) if months is not None else None,
        weekdays=frozenset(int(w) for w in weekdays) if weekdays is not None else None,
        time_ranges=tuple(
            TimeRange(
                start=time.fromisoformat(str(entry["start"])),
                end=time.fromisoformat(str(entry["end"])),
                crosses_midnight=bool(entry.get("crosses_midnight")),
            )
            for entry in item.get("time_ranges") or ()
        ),
        last_admission=(
            time.fromisoformat(str(last_admission)) if last_admission else None
        ),
        source_text=str(item.get("source_text", "")),
    )


def _deserialize_closure_rule(item: Any) -> ClosureRule:
    ordinals = item.get("week_ordinals")
    return ClosureRule(
        weekdays=frozenset(int(w) for w in item.get("weekdays") or ()),
        source_text=str(item.get("source_text", "")),
        week_ordinals=(
            frozenset(int(o) for o in ordinals) if ordinals is not None else None
        ),
        uncertain=bool(item.get("uncertain")),
    )


def normalize_operating_schedule(
    *,
    content_type_id: str,
    operating_hours: str | None,
    rest_date: str | None,
) -> OperatingSchedule:
    """TourAPI 운영시간·휴무 원문을 해석 가능한 범위에서 정규화한다."""
    cleaned_hours = clean_operating_text(operating_hours)
    cleaned_rest = clean_operating_text(rest_date)

    if content_type_id == _COURSE_CONTENT_TYPE_ID and cleaned_hours is None:
        return OperatingSchedule(
            raw_operating_hours=operating_hours,
            raw_rest_date=rest_date,
            cleaned_operating_hours=None,
            cleaned_rest_date=cleaned_rest,
            availability=OperatingAvailability.ALL_DAY,
            rules=(),
            closure_rules=_parse_closures(cleaned_rest),
            parse_status=OperatingParseStatus.ASSUMED,
            assumption_reason="course_without_operating_hours",
            warnings=("여행코스의 운영시간 누락을 24시간 이용 가능으로 가정했습니다.",),
        )

    closure_rules = _parse_closures(cleaned_rest)
    if cleaned_hours and "24시간" in cleaned_hours:
        return OperatingSchedule(
            raw_operating_hours=operating_hours,
            raw_rest_date=rest_date,
            cleaned_operating_hours=cleaned_hours,
            cleaned_rest_date=cleaned_rest,
            availability=OperatingAvailability.ALL_DAY,
            rules=(),
            closure_rules=closure_rules,
            parse_status=OperatingParseStatus.PARSED,
            assumption_reason=None,
            warnings=(),
        )

    rules = _parse_operating_rules(cleaned_hours)
    if rules:
        warnings = ()
        status = OperatingParseStatus.PARSED
        if cleaned_rest and (
            (not closure_rules and "연중무휴" not in cleaned_rest)
            or _has_complex_closure_exception(cleaned_rest)
        ):
            status = OperatingParseStatus.PARTIAL
            warnings = ("휴무 문구의 일부 예외를 구조화하지 못했습니다.",)
        return OperatingSchedule(
            raw_operating_hours=operating_hours,
            raw_rest_date=rest_date,
            cleaned_operating_hours=cleaned_hours,
            cleaned_rest_date=cleaned_rest,
            availability=OperatingAvailability.SCHEDULED,
            rules=rules,
            closure_rules=closure_rules
            + _derive_unlisted_weekday_closures(rules, closure_rules),
            parse_status=status,
            assumption_reason=None,
            warnings=warnings,
        )

    # 구간을 하나라도 읽었으면 그 구간을 신뢰한다. "상시 개방 ※ 낙산 전시관
    # 09:00~17:00"처럼 부속 시설 시간이 함께 적힌 원문을 통째로 24시간으로
    # 넓히지 않기 위해, 상시 개방 판정은 구간을 못 읽은 경우로 한정한다.
    if cleaned_hours and _ALWAYS_OPEN_PATTERN.search(cleaned_hours):
        return OperatingSchedule(
            raw_operating_hours=operating_hours,
            raw_rest_date=rest_date,
            cleaned_operating_hours=cleaned_hours,
            cleaned_rest_date=cleaned_rest,
            availability=OperatingAvailability.ALL_DAY,
            rules=(),
            closure_rules=closure_rules,
            parse_status=OperatingParseStatus.PARSED,
            # 원문에 시각이 없어 마감을 자정으로 잡은 근거를 남긴다. Provider 명시값이라
            # parse_status는 PARSED지만, "24시간"처럼 시각이 적힌 표기와는 구분해야
            # 소비 측이 잔여시간 만점의 출처를 설명할 수 있다.
            assumption_reason="always_open_text",
            warnings=(),
        )

    warnings = ()
    status = OperatingParseStatus.UNKNOWN
    if cleaned_hours or closure_rules:
        status = OperatingParseStatus.PARTIAL
        warnings = ("운영시간을 구조화하지 못했습니다.",)
    return OperatingSchedule(
        raw_operating_hours=operating_hours,
        raw_rest_date=rest_date,
        cleaned_operating_hours=cleaned_hours,
        cleaned_rest_date=cleaned_rest,
        availability=OperatingAvailability.UNKNOWN,
        rules=(),
        closure_rules=closure_rules,
        parse_status=status,
        assumption_reason=None,
        warnings=warnings,
    )


def _parse_operating_rules(value: str | None) -> tuple[OperatingRule, ...]:
    if value is None:
        return ()
    rules: list[OperatingRule] = []
    # 요일 선언과 시간 구간이 다른 줄에 오는 원문(`[평일]<br>- 10:00~17:00`)이
    # 흔해, 적용 요일은 줄을 넘겨 다음 선언이 나올 때까지 유지한다.
    current_weekdays: frozenset[int] | None = None
    for line in value.splitlines():
        for weekdays, segment in _split_weekday_segments(line, current_weekdays):
            current_weekdays = weekdays
            time_ranges = tuple(
                parsed
                for match in _TIME_RANGE_PATTERN.finditer(segment)
                if (parsed := _try_time_range(match)) is not None
            )
            if not time_ranges:
                continue
            admission_match = _LAST_ADMISSION_PATTERN.search(segment)
            rules.append(
                OperatingRule(
                    months=_parse_months(segment) or _parse_months(line),
                    weekdays=weekdays,
                    time_ranges=time_ranges,
                    last_admission=(
                        _try_time(
                            admission_match.group("hour"),
                            admission_match.group("minute"),
                        )
                        if admission_match
                        else None
                    ),
                    source_text=segment,
                )
            )
    return tuple(rules)


def _split_weekday_segments(
    line: str, inherited: frozenset[int] | None
) -> list[tuple[frozenset[int] | None, str]]:
    """요일 선언을 경계로 한 줄을 적용 요일이 같은 구간들로 나눈다.

    `[일요일~금요일] 09:00~18:00 [토요일] 09:00~13:00`처럼 요일마다 시간이 다른
    원문을 요일 구분 없는 규칙들로 평탄화하면, 소비 측(`candidate_mapper`)이
    방문 시각을 포함하는 첫 구간을 고르면서 토요일 14:00에 09:00~18:00을 잡아
    이미 닫은 장소를 운영 중으로 판정한다.
    """
    # `※` 뒤는 예외 안내라 적용 요일을 바꾸지 않는다. 시간 구간은 그대로 읽되
    # 요일 선언만 무시해야 `09:00~18:00 ※ 매주 화요일 휴관`이 화요일 전용으로
    # 뒤집히지 않는다.
    note_start = line.find("※")
    scope_limit = len(line) if note_start < 0 else note_start
    markers: list[tuple[int, int, frozenset[int] | None]] = [
        (match.start(), match.end(), _expand_weekdays(match.group()) or None)
        for match in _WEEKDAY_SCOPE_PATTERN.finditer(line)
        if match.start() < scope_limit and _is_weekday_scope(line, match)
    ]
    markers.extend(
        (match.start(), match.end(), None)
        for match in _BRACKET_SECTION_PATTERN.finditer(line)
        if match.start() < scope_limit and not _WEEKDAY_SCOPE_PATTERN.search(match.group())
    )
    if not markers:
        return [(inherited, line)]
    markers.sort(key=lambda marker: marker[:2])
    segments: list[tuple[frozenset[int] | None, str]] = []
    if markers[0][0] > 0:
        segments.append((inherited, line[: markers[0][0]]))
    for index, (_, marker_end, weekdays) in enumerate(markers):
        end = markers[index + 1][0] if index + 1 < len(markers) else len(line)
        segments.append((weekdays, line[marker_end:end]))
    return segments


def _is_weekday_scope(line: str, match: re.Match[str]) -> bool:
    prefix = line[max(0, match.start() - _WEEKDAY_SCOPE_CONTEXT_WINDOW) : match.start()]
    suffix = line[match.end() : match.end() + _WEEKDAY_SCOPE_CONTEXT_WINDOW]
    return not any(
        token in prefix for token in _WEEKDAY_SCOPE_PREFIX_BLOCKERS
    ) and not any(token in suffix for token in _WEEKDAY_SCOPE_SUFFIX_BLOCKERS)


def _expand_weekdays(text: str) -> frozenset[int]:
    """요일 표기를 인덱스 집합으로 전개한다 — 범위 표기는 사이 요일까지 채운다."""
    result: set[int] = set()
    previous: int | None = None
    pending_range = False
    for token in _WEEKDAY_SCAN_PATTERN.findall(text):
        if token in _WEEKDAY_RANGE_SEPARATORS:
            pending_range = previous is not None
            continue
        alias = _WEEKDAY_ALIASES.get(token)
        if alias is not None:
            result.update(alias)
            previous = None
            pending_range = False
            continue
        index = _WEEKDAY_INDEX[token.replace("요일", "")]
        if pending_range and previous is not None:
            result.update(_weekday_span(previous, index))
        else:
            result.add(index)
        previous = index
        pending_range = False
    return frozenset(result)


def _weekday_span(start: int, end: int) -> set[int]:
    if start <= end:
        return set(range(start, end + 1))
    # `토요일~월요일`처럼 주 경계를 넘는 범위.
    return {*range(start, 7), *range(0, end + 1)}


def _parse_months(value: str) -> frozenset[int] | None:
    match = _MONTH_RANGE_PATTERN.search(value)
    if match is None:
        return None
    start = int(match.group("start"))
    end = int(match.group("end"))
    if not 1 <= start <= 12 or not 1 <= end <= 12:
        return None
    if start <= end:
        return frozenset(range(start, end + 1))
    return frozenset((*range(start, 13), *range(1, end + 1)))


def _parse_closures(value: str | None) -> tuple[ClosureRule, ...]:
    if value is None or "연중무휴" in value:
        return ()
    rules: list[ClosureRule] = []
    for line in value.splitlines():
        match = _WEEKLY_CLOSURE_PATTERN.search(line)
        if match is None:
            continue
        weekdays = _expand_weekdays(match.group("weekdays"))
        if weekdays:
            rules.append(ClosureRule(weekdays=weekdays, source_text=line))
    return tuple(rules)


def _derive_unlisted_weekday_closures(
    rules: tuple[OperatingRule, ...],
    closure_rules: tuple[ClosureRule, ...],
) -> tuple[ClosureRule, ...]:
    """운영시간이 요일을 열거했을 때, 빠진 요일을 정기 휴무로 유도한다(D-058).

    유도의 근거는 원문 자체다 — 활성 장소 844건 중 요일을 열거하고도 빠진 요일이
    있는 39건에서, 38건은 그 요일이 휴무 원문에 이미 명시돼 있었다. 남은 1건
    (북촌문화센터)은 규칙의 반례가 아니라 휴무 필드가 비어 있는 원본 결함이고,
    실제로도 그 요일에 문을 닫는다.

    이 유도는 소비 측에서 하드 필터(폐점)로 이어지므로, 원문에 적힌 휴무와
    구분할 수 있게 `source_text`에 근거를 남긴다. 요일 범위를 하나라도 잘못
    읽으면 후보가 점수 손해가 아니라 통째로 사라진다는 뜻이기도 하다.
    """
    listed: set[int] = set()
    for rule in rules:
        # 요일 없는 규칙이 하나라도 있으면 그 규칙이 전 요일을 덮으므로
        # "빠진 요일"이라는 개념 자체가 성립하지 않는다.
        if rule.weekdays is None:
            return ()
        listed |= rule.weekdays
    for closure in closure_rules:
        listed |= closure.weekdays
    unlisted = frozenset(range(7)) - listed
    if not unlisted:
        return ()
    return (
        ClosureRule(weekdays=unlisted, source_text=DERIVED_CLOSURE_SOURCE_TEXT),
    )


def _has_complex_closure_exception(value: str) -> bool:
    return any(
        token in value
        for token in ("공휴일", "대체공휴일", "비공휴일", "다음 날", "변동", "문의")
    )


def _try_time_range(match: re.Match[str]) -> TimeRange | None:
    start = _try_time(match.group("start_hour"), match.group("start_minute"))
    end_hour = match.group("end_hour")
    end_minute = match.group("end_minute")
    # "09:00~24:00"의 24:00은 자정 통과가 아니라 당일 종료다. time(0)으로 두면
    # end <= start가 되어 자정 통과로 오분류되고, 당일 구간만 다루는 소비 측
    # (`candidate_mapper`)에서 통째로 버려져 운영 미확인이 된다.
    end = (
        time.max
        if end_hour == "24" and end_minute == "00"
        else _try_time(end_hour, end_minute)
    )
    if start is None or end is None:
        return None
    return TimeRange(
        start=start,
        end=end,
        crosses_midnight=bool(match.group("next_day")) or end <= start,
    )


def _try_time(hour: str, minute: str) -> time | None:
    parsed_hour = int(hour)
    parsed_minute = int(minute)
    if not 0 <= parsed_hour <= 23 or not 0 <= parsed_minute <= 59:
        return None
    return time(parsed_hour, parsed_minute)
