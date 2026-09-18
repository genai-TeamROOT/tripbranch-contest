"""추천 API 라우터.

역할: 확정된 조건으로 추천을 실행하고, 노출된 결과를 Agent State(B)에 기록한다.
입력: POST /api/recommendations JSON body의 RecommendationRequest.
출력: RecommendationResponse JSON 응답.
호출 시점: ConfirmPage에서 조건 확정 후 결과 화면으로 넘어갈 때 호출된다.
TODO: 조건 스키마가 UserConditions 14개 필드로 통합되면 변환 계층을 정리한다.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.auth.dependency import OptionalPrincipal
from app.schemas import (
    InterpretedConditions,
    RecommendationRequest,
    RecommendationResponse,
)
from app.services.recommendations import get_recommendations
from app.state import service as state_service

router = APIRouter(tags=["recommendations"])


@router.post("/recommendations", response_model=RecommendationResponse)
async def recommendations(
    request: RecommendationRequest, principal: OptionalPrincipal
) -> RecommendationResponse:
    conditions = InterpretedConditions(
        location_query=request.location_query,
        preferred_categories=request.preferred_categories,
        weather_condition=request.weather_condition,
        search_radius_km=request.search_radius_km,
    )

    # 1) 제외 목록은 B가 단일 기준이다. 세션이 있으면 호출자 값을 무시한다.
    #    누적 제외(excluded)를 쓴다. 이번 실행에서 다시 보여주지 않을 전체 목록이다.
    exclude_ids = request.shown_place_ids
    if request.session_id:
        context = state_service.get_session_context(request.session_id, principal=principal)
        if context.session_exists:
            exclude_ids = context.excluded_place_ids

    response = await get_recommendations(conditions, exclude_ids)

    # 2) 실제로 노출된 결과만 이력에 기록한다. (계약 6.4절)
    #    이 기록이 있어야 다음 턴에서 MODIFY / COMPARE 판정이 가능하다.
    if request.session_id and request.run_id:
        _record_shown(request.session_id, request.run_id, response)

    return response


def _record_shown(
    session_id: str, run_id: str, response: RecommendationResponse
) -> None:
    """노출된 추천 결과를 B에 기록한다.

    unverified_recommendations 도 화면에 함께 나가므로 이력에 포함한다.
    기록 실패는 사용자 응답 경로를 막지 않는다. (계약 6.6절)
    """
    shown = [*response.recommendations, *response.unverified_recommendations]
    if not shown:
        return

    state_service.record_recommendation(
        state_service.RecordRecommendationRequest(
            session_id=session_id,
            run_id=run_id,
            recommended=[
                state_service.RecommendedPlace(place_id=item.place_id, rank=rank)
                for rank, item in enumerate(shown, start=1)
            ],
        )
    )