"""COMPARE 비교 컨텍스트의 A–C 요청·응답 계약.

역할: A가 지시 표현(targets)을 이미 푼 뒤 넘기는 후보 목록을, C가 사람이 읽을 수
      있는 비교 사실로 채워 돌려주는 경계. INFO의 info_schemas.py와 같은 결로 둔다.
입력: CompareContextRequest — criteria + 후보별 place_id/rank와 B의 Feature 스냅샷.
출력: CompareContextResponse — place_name이 채워진 ComparisonItem 목록.
호출 시점: Agent Runtime이 COMPARE 인텐트를 확정한 뒤 C에 사실 조회를 위임할 때.

C는 비교 판정을 하지 않는다. 순위·우열은 A의 LLM 요약이 맡고, C는 place_id를
장소명으로 해석해 "무엇을 비교하는지"만 확정한다. 수치(거리·남은 운영시간·실내외)는
추천 시점에 계산된 스냅샷이라 재조회하지 않고 그대로 통과시킨다 — 사용자가 카드에서
본 값과 비교 답변의 값이 어긋나면 안 된다(D-050, int-04-compare.md §13).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.agent_context.schemas import ContextError, StrictModel
from app.schemas import CompareCriteria, ComparisonItem


class CompareCandidate(StrictModel):
    """A → C: 비교 대상 1건. B가 보관한 추천 시점 Feature 스냅샷을 그대로 싣는다.

    스냅샷 3개는 모두 선택이다 — 좌표가 없거나 운영시간을 확인하지 못한 채 추천된
    후보는 해당 값이 비어 있다.
    """

    place_id: str
    rank: int = Field(ge=1)
    distance_km: float | None = Field(default=None, ge=0)
    remaining_minutes: int | None = Field(default=None, ge=0)
    environment_type: str | None = None


class CompareContextRequest(StrictModel):
    """A → C: 비교 기준과 대상 목록.

    targets("all" / [1, 2] 같은 지시 표현)를 shown_place_ids로 푸는 일은 A가 이미
    마쳤다고 본다(agent-state-contract-v1.md의 shown_place_ids 용도). C는 풀린
    결과만 받는다.
    """

    request_id: str
    criteria: CompareCriteria
    candidates: list[CompareCandidate] = Field(min_length=1)


class CompareContextResponse(StrictModel):
    """C → A: 비교 사실 데이터.

    status는 4개만 둔다. needs_clarification/unsupported는 COMPARE에서 C가 만들
    일이 없다 — 대상 범위 초과 되묻기는 A가 targets를 풀면서 처리하고, criteria는
    enum이라 A 단계에서 이미 검증된다. 도달할 수 없는 상태를 계약에 남기면 소비
    측이 죽은 분기를 떠안는다.
    """

    request_id: str
    status: Literal["success", "partial", "no_data", "unavailable"]
    criteria: CompareCriteria
    items: list[ComparisonItem] = Field(default_factory=list)
    # 이름을 찾지 못해 비교에서 빠진 place_id. status=partial의 근거를 남긴다.
    missing_place_ids: list[str] = Field(default_factory=list)
    error: ContextError | None = None


__all__ = [
    "CompareCandidate",
    "CompareContextRequest",
    "CompareContextResponse",
]
