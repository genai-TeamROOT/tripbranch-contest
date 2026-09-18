"""휴무 원문을 LLM으로 읽어 정규식이 놓친 정기 휴무를 채운다. (TP-231)

역할: 정규식 파싱 결과(`OperatingSchedule`)에 LLM이 읽은 휴무를 얹는다.
입력: 정규식 결과와 휴무 원문.
출력: 휴무 규칙이 보강된 `OperatingSchedule`.
호출 시점: **적재 배치에서만.** 읽기 경로는 저장된 결과를 쓰므로
      (`resolve_operating_schedule()`) 사용자 요청 지연에 붙지 않는다.

**정규식을 대체하지 않고 얹는다.** 시각 파싱은 정규식이 이미 90.2%를 읽고(2026-09-03
활성 8,007곳 실측), 못 읽는 것은 원문에 정보가 없어 LLM으로도 나아지지 않는다.
LLM이 이기는 자리는 휴무 문구뿐이다 — 부정·예외가 의미 판단이기 때문이다.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from app.domain.operating_hours import ClosureRule, OperatingSchedule

logger = logging.getLogger(__name__)

# 한글 요일 한 글자 → 파이썬 weekday 번호(월=0).
_WEEKDAY_INDEX: Mapping[str, int] = {
    "월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6,
}

# 한 달에 같은 요일은 최대 5번이다. 6 이상이 오면 모델이 지어낸 값이다.
_MAX_WEEK_ORDINAL = 5

# 이 말이 원문에 있으면 **LLM을 부르지 않는다.** 쉬는 날이 없다는 뜻이라 뽑을 것이
# 없고, 부르면 모델이 무언가를 담을 여지만 생긴다.
_NO_CLOSURE_MARKERS = ("연중무휴",)


class ClosureExtractor(Protocol):
    async def extract_closure_rules(self, rest_date: str) -> Any: ...


def needs_extraction(schedule: OperatingSchedule) -> bool:
    """LLM을 부를 만한 원문인지 본다.

    **전부 부르지 않는다.** 활성 8,007곳 중 휴무 원문이 있는 것은 절반쯤이고, 그중
    정규식이 이미 읽은 것은 다시 볼 이유가 없다. `매주 월요일` 한 줄에 모델을
    부르면 비용만 늘고 지어낼 여지만 생긴다.

    부르는 경우는 **원문이 있는데 정규식이 아무것도 못 읽은 때**다. 정규식이 읽은
    것은 `매주 ...` 형태라 이미 정확하다.
    """

    rest = (schedule.cleaned_rest_date or "").strip()
    if not rest:
        return False
    if any(marker in rest for marker in _NO_CLOSURE_MARKERS):
        return False
    return not schedule.closure_rules


def merge_extracted_closures(
    schedule: OperatingSchedule, extracted: Mapping[str, object]
) -> OperatingSchedule:
    """LLM이 읽은 휴무를 정규식 결과에 얹는다.

    **정규식이 읽은 규칙은 건드리지 않는다.** 둘이 겹치면 정규식을 남긴다 — `매주
    월요일`처럼 명확한 형태만 정규식이 읽으므로 그쪽이 더 믿을 만하다.

    모양이 이상한 값은 조용히 버린다. 배치 한 건이 실패해서 그 장소가 통째로
    빠지는 것보다, 그 항목만 없이 저장하는 편이 낫다. 버린 사실은 로그에 남긴다.
    """

    known = {rule.weekdays for rule in schedule.closure_rules}
    added: list[ClosureRule] = []

    for weekday in _weekdays(extracted.get("weekly")):
        rule = ClosureRule(weekdays=frozenset({weekday}), source_text="llm:weekly")
        if rule.weekdays not in known:
            added.append(rule)

    for item in extracted.get("nth_weekday") or ():
        if not isinstance(item, Mapping):
            continue
        index = _weekday_index(item.get("weekday"))
        ordinals = _ordinals(item.get("ordinals"))
        if index is None or not ordinals:
            continue
        added.append(
            ClosureRule(
                weekdays=frozenset({index}),
                source_text="llm:nth_weekday",
                week_ordinals=ordinals,
            )
        )

    for weekday in _weekdays(extracted.get("uncertain_weekdays")):
        added.append(
            ClosureRule(
                weekdays=frozenset({weekday}),
                source_text="llm:uncertain",
                uncertain=True,
            )
        )

    if not added:
        return schedule
    return OperatingSchedule(
        raw_operating_hours=schedule.raw_operating_hours,
        raw_rest_date=schedule.raw_rest_date,
        cleaned_operating_hours=schedule.cleaned_operating_hours,
        cleaned_rest_date=schedule.cleaned_rest_date,
        availability=schedule.availability,
        rules=schedule.rules,
        closure_rules=schedule.closure_rules + tuple(added),
        parse_status=schedule.parse_status,
        assumption_reason=schedule.assumption_reason,
        warnings=schedule.warnings,
    )


def _weekdays(value: object) -> list[int]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return []
    result: list[int] = []
    for item in value:
        index = _weekday_index(item)
        if index is not None and index not in result:
            result.append(index)
    return result


def _weekday_index(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    index = _WEEKDAY_INDEX.get(value.strip().replace("요일", ""))
    if index is None and value.strip():
        logger.warning("closure_extract.unknown_weekday value=%r", value)
    return index


def _ordinals(value: object) -> frozenset[int] | None:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return None
    result = {
        int(item)
        for item in value
        if isinstance(item, int) and 1 <= item <= _MAX_WEEK_ORDINAL
    }
    return frozenset(result) or None


__all__ = [
    "ClosureExtractor",
    "merge_extracted_closures",
    "needs_extraction",
]
