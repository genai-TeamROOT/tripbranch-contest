"""A → C 변환: 1차 추천 결과의 혼잡도 보강 조회 전용 (concentration_intent AVOID/SEEK).

context_transform.py(A→C 초기 Context)/info_context_transform.py(A→C INFO)와
같은 원칙 — 이 모듈은 A↔C 후보 보강 변환만 전담한다. 안 B(post-ranking 보강
재사용, concentration-conditions.md §2.2.3, agent-runtime-contract.md §6.5.2)의
A쪽 조각이며, D의 2차 Scoring 신규 인터페이스가 확정되기 전까지는 A가 이
함수로 만든 요청을 C에 보내는 데까지만 쓰인다.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.agent_context.enrichment_schemas import (
    CandidateEnrichmentRequest,
    CandidateEnrichmentTarget,
)
from app.agent_context.schemas import PlaceCandidate
from app.schemas import RecommendationResponse


def to_candidate_enrichment_request(
    request_id: str,
    recommendations: RecommendationResponse,
    places: Sequence[PlaceCandidate],
) -> CandidateEnrichmentRequest | None:
    """1차 추천 결과(위경도 없음)를 원본 후보 목록(위경도 있음)과 place_id로
    재조인해 CandidateEnrichmentRequest를 만든다.

    1차 결과는 이미 recommendations + unverified_recommendations 합쳐서
    최대 5개다(real_recommendation_provider.py의 recommendation_limit=5) —
    여기서 추가로 자르지 않는다.

    원본 places에서 place_id를 못 찾은 후보는 조용히 제외한다(있을 수 없는
    경우지만 크래시보다 안전). 재조인 후 대상이 하나도 없으면 None을 반환해
    호출부가 보강 조회 자체를 건너뛰게 한다.
    """

    location_by_place_id = {place.place_id: place.location for place in places}

    items = [*recommendations.recommendations, *recommendations.unverified_recommendations]
    targets = [
        CandidateEnrichmentTarget(
            place_id=item.place_id,
            name=item.name,
            latitude=location_by_place_id[item.place_id].latitude,
            longitude=location_by_place_id[item.place_id].longitude,
        )
        for item in items
        if item.place_id in location_by_place_id
    ]

    if not targets:
        return None

    return CandidateEnrichmentRequest(
        request_id=request_id,
        candidates=targets,
        features=["concentration"],
    )


__all__ = ["to_candidate_enrichment_request"]
