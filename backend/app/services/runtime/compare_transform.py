"""COMPARE의 B 추천 이력 ↔ A–C 비교 계약 변환."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.schemas import ComparePayload, ComparisonResult
from app.services.runtime.compare_context_schemas import (
    CompareCandidate,
    CompareContextRequest,
    CompareContextResponse,
)
from app.state.schema import RecommendedItem


@dataclass(frozen=True)
class CompareRequestResolution:
    """targets 해석 결과. 오류는 사용자에게 바로 안내할 수 있는 문장으로 둔다."""

    request: CompareContextRequest | None
    message: str | None = None


def to_compare_context_request(
    request_id: str,
    payload: ComparePayload,
    shown_recommendations: Sequence[RecommendedItem],
) -> CompareRequestResolution:
    """1-indexed targets를 마지막 추천 이력의 Feature 스냅샷으로 푼다."""

    shown = sorted(shown_recommendations, key=lambda item: item.rank)
    if not shown:
        return CompareRequestResolution(
            request=None, message="먼저 장소를 추천해드릴까요?"
        )
    if len(shown) < 2:
        return CompareRequestResolution(
            request=None, message="비교할 장소가 더 필요해요. 다른 추천을 볼까요?"
        )

    by_rank = {item.rank: item for item in shown}
    if payload.targets == "all":
        targets = shown
    else:
        unknown = sorted({rank for rank in payload.targets if rank not in by_rank})
        if unknown:
            return CompareRequestResolution(
                request=None,
                message=f"추천 결과는 {len(shown)}개까지 있어요. 몇 번을 비교할까요?",
            )
        targets = [by_rank[rank] for rank in dict.fromkeys(payload.targets)]

    if len(targets) < 2:
        return CompareRequestResolution(
            request=None, message="비교하려면 서로 다른 장소를 두 곳 이상 골라주세요."
        )

    return CompareRequestResolution(
        request=CompareContextRequest(
            request_id=request_id,
            criteria=payload.criteria,
            candidates=[
                CompareCandidate(
                    place_id=item.place_id,
                    rank=item.rank,
                    distance_km=item.distance_km,
                    remaining_minutes=item.remaining_minutes,
                    environment_type=item.environment_type,
                )
                for item in targets
            ],
        )
    )


def to_comparison_result(response: CompareContextResponse) -> ComparisonResult | None:
    """C가 비교 가능한 두 곳 이상을 반환한 경우만 LLM 요약 입력으로 만든다."""

    if response.status not in {"success", "partial"} or len(response.items) < 2:
        return None
    return ComparisonResult(criteria=response.criteria, items=list(response.items))
