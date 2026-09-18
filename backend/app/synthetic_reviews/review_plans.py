"""복합 페르소나에 장소별 합성 리뷰 작성 계획을 결정적으로 배분한다."""

from __future__ import annotations

from dataclasses import dataclass

from app.synthetic_reviews.personas import (
    PERSONA_COUNT_CEILING,
    PERSONA_COUNT_FLOOR,
    CompositePersona,
)

# 장소당 리뷰 수는 그 장소가 가진 공식 근거 수를 따라 3~5로 달라진다. 근거 없이
# 리뷰 수만 채우면 모델이 인용할 것이 없어 근거를 지어내기 때문이다(TP-152).
MIN_REVIEWS_PER_PLACE = PERSONA_COUNT_FLOOR
MAX_REVIEWS_PER_PLACE = PERSONA_COUNT_CEILING

_CONTEXT_TEMPLATES = (
    "공식 장소 정보를 확인하고 방문 여부를 계획하는 상황",
    "다른 일정과 함께 방문 순서를 조정하는 상황",
    "같은 목적의 방문 후보와 비교해 선택을 검토하는 상황",
)

# 주관적 시나리오 축은 뒤로 미룬다. 구체적인 공식 속성이 있으면 먼저 평가하고,
# 같은 페르소나의 다음 리뷰에서 취향과 일정 맥락을 다루게 한다.
_SECONDARY_AXES = frozenset({"PERSONAL_PREFERENCE", "ITINERARY_FIT"})


@dataclass(frozen=True)
class ReviewPlan:
    review_index: int
    persona_id: str
    persona_description: str
    visit_context: str
    focus_axes: tuple[str, ...]
    evidence_fields: tuple[str, ...]


def _ordered_axes(persona: CompositePersona) -> tuple[str, ...]:
    specific = tuple(
        axis for axis in persona.allowed_evaluation_axes if axis not in _SECONDARY_AXES
    )
    secondary = tuple(
        axis for axis in persona.allowed_evaluation_axes if axis in _SECONDARY_AXES
    )
    return (*specific, *secondary)


def generate_review_plans(
    personas: tuple[CompositePersona, ...],
    *,
    review_count: int | None = None,
) -> tuple[ReviewPlan, ...]:
    """3~5개 페르소나에 리뷰를 round-robin으로 배분한다.

    모든 페르소나에 한 건씩 먼저 배정하고 앞에서부터 추가 배정한다. review_count를
    생략하면 페르소나마다 한 건씩만 배정하므로, 리뷰 수가 곧 그 장소의 공식 근거
    수가 된다.
    """
    if not PERSONA_COUNT_FLOOR <= len(personas) <= PERSONA_COUNT_CEILING:
        raise ValueError(
            f"페르소나는 {PERSONA_COUNT_FLOOR}~{PERSONA_COUNT_CEILING}개여야 합니다."
        )
    if review_count is None:
        review_count = len(personas)
    if review_count < len(personas):
        raise ValueError("review_count는 페르소나 수 이상이어야 합니다.")
    # 상한을 넘기면 SyntheticReview.review_index가 받지 못하는 계획이 만들어져,
    # 생성은 되는데 검증에서 반드시 떨어지는 리뷰가 생긴다.
    #
    # 페르소나별 visitContext가 모자라는 경우는 따로 막지 않는다. 최소 페르소나 3명에
    # 템플릿 3개면 9건까지 감당하는데 리뷰는 MAX_REVIEWS_PER_PLACE(5)를 넘을 수 없어
    # 도달할 수 없는 조건이다. 이 관계는 test_visit_context_템플릿은_상한을_감당한다가
    # 못 박고 있어, 상한을 올리면 그 테스트가 먼저 깨진다.
    if review_count > MAX_REVIEWS_PER_PLACE:
        raise ValueError(
            f"review_count는 {MAX_REVIEWS_PER_PLACE} 이하여야 합니다: {review_count}"
        )
    persona_ids = [persona.persona_id for persona in personas]
    if len(set(persona_ids)) != len(persona_ids):
        raise ValueError("persona_id는 중복될 수 없습니다.")

    occurrence_counts = [0] * len(personas)
    plans: list[ReviewPlan] = []
    for review_index in range(review_count):
        persona_index = review_index % len(personas)
        persona = personas[persona_index]
        occurrence = occurrence_counts[persona_index]
        occurrence_counts[persona_index] += 1
        axes = _ordered_axes(persona)
        focus_axes = (axes[occurrence % len(axes)],) if axes else ()
        plans.append(
            ReviewPlan(
                review_index=review_index,
                persona_id=persona.persona_id,
                persona_description=persona.description,
                visit_context=_CONTEXT_TEMPLATES[occurrence],
                focus_axes=focus_axes,
                evidence_fields=persona.evidence_fields,
            )
        )
    return tuple(plans)


__all__ = [
    "MAX_REVIEWS_PER_PLACE",
    "MIN_REVIEWS_PER_PLACE",
    "ReviewPlan",
    "generate_review_plans",
]
