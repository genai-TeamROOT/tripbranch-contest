"""LLM으로 구간 이동수단을 정하는 판정 구현. (TP-227)

역할: `ModeJudge` 프로토콜을 LLM Provider 위에 구현한다. 구간 표와 조건을 넘기고
      이동수단 목록을 받는다.
입력: 판정 입력 구간 표(`SegmentModeInput`)와 요청이 공유하는 조건.
출력: 구간 수와 같은 길이의 `TravelMode` 목록.
호출 시점: 구간 이동정보를 만들기 직전, 루프 밖에서 한 번(`select_modes_for_segments`).

**여기서 검증하지 않는다.** 개수와 어휘 검증은 `select_modes_for_segments()`가 한다 —
어느 구간의 답인지 아는 자리가 거기이고, 두 곳에서 검증하면 한쪽만 바뀌었을 때
결과가 갈린다.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from app.domain.schedule_travel import ModeJudgmentContext, SegmentModeInput
from app.domain.travel_route import TravelMode

logger = logging.getLogger(__name__)

# 이동수단 판정에 넘기는 무장애 어휘. 9개 중 셋뿐이다.
#
# 나머지 여섯(장애인 화장실·주차구역·수유실·휠체어 대여·좌석·점자 안내)은 **"그
# 장소가 어떤가"이지 "어떻게 갈까"가 아니다.** 넘겨봐야 판정과 무관한 정보로
# 판단을 흐린다. 무장애 검색이 그 값들로 후보를 이미 좁혔으므로 여기서 다시 볼
# 이유도 없다.
#
# `low_floor_transit`은 전환 근거가 아니라 반대 방향 정보다 — 사용자가 저상버스·역
# 엘리베이터를 요구했다는 건 대중교통을 쓸 생각이 있다는 뜻이라, 프롬프트가 그
# 값을 전환에 관대해지는 신호로 쓴다.
TRAVEL_RELEVANT_ACCESSIBILITY_NEEDS: frozenset[str] = frozenset(
    {"wheelchair_access", "stroller_access", "low_floor_transit"}
)


def narrow_accessibility_needs(needs: Sequence[str]) -> tuple[str, ...]:
    """이동 판정에 쓸 무장애 어휘만 남긴다. 순서는 들어온 대로 둔다."""

    return tuple(need for need in needs if need in TRAVEL_RELEVANT_ACCESSIBILITY_NEEDS)


class LlmModeJudge:
    """LLM Provider로 구간 이동수단을 정한다.

    Provider가 실패하면 예외를 올린다. 여기서 규칙으로 되돌리지 않는 이유는,
    폴백이 두 곳에 있으면 "규칙으로 돌아갔다"는 사실이 어디서 났는지 알 수 없기
    때문이다. 되돌리는 것과 그 사실을 남기는 것은 호출부가 함께 한다.
    """

    def __init__(self, llm: object) -> None:
        self._llm = llm

    async def judge(
        self,
        segments: Sequence[SegmentModeInput],
        context: ModeJudgmentContext,
    ) -> Sequence[TravelMode]:
        result = await self._llm.judge_travel_modes(segments, context)  # type: ignore[attr-defined]
        raw = result.data if getattr(result, "data", None) is not None else ()
        # 문자열을 여기서 TravelMode로 옮긴다. 옮기지 못하는 값은 그대로 흘려보내
        # 호출부의 어휘 검증에 걸리게 한다 — 여기서 조용히 버리면 개수가 줄어
        # "몇 번째 구간이 이상했나"가 사라진다.
        modes: list[TravelMode] = []
        for value in raw:
            try:
                modes.append(TravelMode(value))
            except ValueError:
                logger.warning("mode_judge.unknown_mode value=%r", value)
                modes.append(value)  # type: ignore[arg-type]
        return modes


__all__ = [
    "TRAVEL_RELEVANT_ACCESSIBILITY_NEEDS",
    "LlmModeJudge",
    "narrow_accessibility_needs",
]
