"""일정 편성 모듈 스키마 검증 테스트.

계약 문서: docs/design/int-07-schedule.md 6.1~6.2절, 7절
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schedule.schemas import (
    ScheduleLLMItem,
    ScheduleLLMPlan,
    SchedulePlanningRequest,
)
from app.schemas import (
    AgentResponse,
    Intent,
    LLMOutput,
    OutputStatus,
    RecommendationItem,
    RecommendationResponse,
    ScheduleItem,
    ScheduleResult,
    UserConditions,
)
from app.state.schema import UserConditions as StateUserConditions
from app.state.service import ApiContextView, StateApplyResponse


def _llm_item(place_id: str, order: int) -> ScheduleLLMItem:
    """ScheduleLLMPlan에 실리는 항목 — 시각이 없다(TP-215)."""

    return ScheduleLLMItem(
        order=order,
        place_id=place_id,
        place_name=f"장소 {order}",
        estimated_duration_min=60,
        reason="테스트 이유",
    )


def _schedule_item(place_id: str, order: int) -> ScheduleItem:
    return ScheduleItem(
        order=order,
        place_id=place_id,
        place_name=f"장소 {place_id}",
        estimated_arrival="15:00",
        estimated_duration_min=60,
        travel_to_next_min=None,
        reason="테스트 이유",
    )


def _sample_recommendation_item(place_id: str = "place-1") -> RecommendationItem:
    return RecommendationItem(
        place_id=place_id,
        name="연남동 카페 A",
        category="cafe",
        distance_km=0.4,
        remaining_minutes=120,
        environment_type="indoor",
        recommendation_reason="근처라서 추천해요.",
        explanations=[],
        warnings=[],
        score=0.9,
        feature_scores={},
        weights_used={},
    )


def _sample_state_response() -> StateApplyResponse:
    return StateApplyResponse(
        session_id="sess_test",
        run_id="run_test",
        session_created=True,
        user_conditions=StateUserConditions(),
        api_context=ApiContextView(),
        condition_version=1,
        condition_changed=False,
    )


class TestScheduleResultSchema:
    """docs/design/int-07-schedule.md 6.2절 — 출력 스키마."""

    def test_basis_note_필드가_포함된다(self):
        result = ScheduleResult(
            items=[
                ScheduleItem(
                    order=1,
                    place_id="place-1",
                    place_name="연남동 카페 A",
                    estimated_arrival="15:00",
                    estimated_duration_min=60,
                    travel_to_next_min=15,
                    reason="도보 이동 시작점에 가까워요.",
                )
            ],
            total_duration_min=180,
            route_summary="연남동 순으로 이동 거리를 최소화했어요.",
            basis_note="이 정보는 15:00 기준으로 계산됐어요.",
            elapsed_ms=100.0,
        )
        assert result.basis_note == "이 정보는 15:00 기준으로 계산됐어요."
        assert result.items[0].order == 1
        assert result.items[0].travel_to_next_min == 15


class TestAgentResponseScheduleField:
    """AgentResponse.schedule이 SCHEDULE일 때만 채워지고 기존 흐름엔 영향 없어야 한다."""

    def test_schedule_기본값은_None이다(self):
        response = AgentResponse(
            llm_output=LLMOutput(intent=Intent.SCHEDULE, status=OutputStatus.COMPLETE),
            state=_sample_state_response(),
            message="일정 추천 기능은 아직 준비 중이에요.",
        )
        assert response.schedule is None

    def test_schedule_필드에_ScheduleResult를_담을_수_있다(self):
        result = ScheduleResult(
            items=[], total_duration_min=0, route_summary="", basis_note="", elapsed_ms=0.0
        )
        response = AgentResponse(
            llm_output=LLMOutput(intent=Intent.SCHEDULE, status=OutputStatus.COMPLETE),
            state=_sample_state_response(),
            schedule=result,
            message="일정 테스트",
        )
        assert response.schedule is result

    def test_recommendations만_있는_기존_응답도_그대로_생성된다(self):
        """RECOMMEND/MODIFY 흐름 회귀 없음 확인 — schedule 필드 추가가 기존
        recommendations 단독 구성을 깨지 않는지 본다."""
        response = AgentResponse(
            llm_output=LLMOutput(intent=Intent.RECOMMEND, status=OutputStatus.COMPLETE),
            state=_sample_state_response(),
            recommendations=RecommendationResponse(
                recommendations=[_sample_recommendation_item()],
                unverified_recommendations=[],
                elapsed_ms=10.0,
            ),
            message="추천 테스트",
        )
        assert response.schedule is None
        assert response.recommendations is not None
        assert len(response.recommendations.recommendations) == 1


class TestSchedulePlanningRequestSchema:
    """docs/design/int-07-schedule.md 6.1절 — 입력 스키마."""

    def test_candidates는_RecommendationItem_리스트다(self):
        request = SchedulePlanningRequest(
            candidates=[
                _sample_recommendation_item("place-1"),
                _sample_recommendation_item("place-2"),
            ],
            conditions=UserConditions(),
            visit_datetime=None,
            pairwise_distances_km={("place-1", "place-2"): 0.6},
        )
        assert len(request.candidates) == 2
        assert isinstance(request.candidates[0], RecommendationItem)
        assert request.pairwise_distances_km[("place-1", "place-2")] == 0.6

    def test_visit_datetime은_생략_가능하다(self):
        request = SchedulePlanningRequest(
            candidates=[],
            conditions=UserConditions(),
            pairwise_distances_km={},
        )
        assert request.visit_datetime is None


class TestScheduleLLMPlanItemsCountConstraint:
    """SCHEDULE-10: items의 구조적 제약은 min_length=1/max_length=5뿐이다.

    SCHEDULE-07 때는 항상 min_length=3을 걸었지만, 활동 가능 시간이 짧은
    요청("2시간 코스 짜줘")에서는 3개 고정 하한이 비현실적이라는 게 확인돼
    "이번 요청에 맞는" 목표 개수(1~5 사이)는 budget.derive_item_range()가 계산해
    프롬프트로만 지시하고, 이 모델은 "0개도 6개 이상도 아니다"라는 구조적
    최소한만 검증한다."""

    def test_1개면_통과한다(self):
        plan = ScheduleLLMPlan(
            items=[_llm_item("place-1", 1)],
            route_summary="테스트 동선",
        )
        assert len(plan.items) == 1

    def test_정확히_3개면_통과한다(self):
        plan = ScheduleLLMPlan(
            items=[_llm_item(f"place-{i}", i) for i in range(1, 4)],
            route_summary="테스트 동선",
        )
        assert len(plan.items) == 3

    def test_정확히_5개면_통과한다(self):
        plan = ScheduleLLMPlan(
            items=[_llm_item(f"place-{i}", i) for i in range(1, 6)],
            route_summary="테스트 동선",
        )
        assert len(plan.items) == 5

    def test_2개도_이제는_통과한다(self):
        """SCHEDULE-07 때는 검증 실패였지만, SCHEDULE-10부터는 구조적으로
        허용된다 — 2개가 적절한지는 budget.derive_item_range()/프롬프트가 판단할
        몫이지 이 스키마가 판단할 몫이 아니다."""
        plan = ScheduleLLMPlan(
            items=[_llm_item(f"place-{i}", i) for i in range(1, 3)],
            route_summary="테스트 동선",
        )
        assert len(plan.items) == 2

    def test_0개면_검증에_실패한다(self):
        with pytest.raises(ValidationError):
            ScheduleLLMPlan(items=[], total_duration_min=0, route_summary="")

    def test_6개면_검증에_실패한다(self):
        with pytest.raises(ValidationError):
            ScheduleLLMPlan(
                items=[_llm_item(f"place-{i}", i) for i in range(1, 7)],
                route_summary="테스트 동선",
            )
