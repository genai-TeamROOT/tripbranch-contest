"""A Runtime의 COMPARE 비교 계약 재노출 모듈.

a-c-context-contract-draft.md §3이 스케치해둔 discriminated union
(RecommendContextRequest | InfoContextRequest | CompareContextRequest) 중
CompareContextRequest 자리의 실제 구현이다. info_context_schemas.py와 같은 역할이다.

실제 모델의 단일 원본은 ``app.agent_context.compare_schemas``다. A가 기존 import
관례를 그대로 쓸 수 있게 재노출만 한다. place_id → 장소명 해석은 C 내부 책임이며,
A는 구조화된 비교 사실만 받아 LLM 요약에 넘긴다.
"""

from app.agent_context.compare_schemas import (
    CompareCandidate,
    CompareContextRequest,
    CompareContextResponse,
)

__all__ = [
    "CompareCandidate",
    "CompareContextRequest",
    "CompareContextResponse",
]
