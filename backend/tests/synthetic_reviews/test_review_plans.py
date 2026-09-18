from collections import Counter
from dataclasses import replace

import pytest

from app.synthetic_reviews.personas import (
    PERSONA_COUNT_FLOOR,
    PlacePersonaInput,
    generate_personas,
)
from app.synthetic_reviews.review_plans import (
    _CONTEXT_TEMPLATES,
    MAX_REVIEWS_PER_PLACE,
    generate_review_plans,
)


def _personas(count: int):
    return generate_personas(
        PlacePersonaInput(
            content_id="126508",
            content_type_id="14",
            operating_hours_raw="09:00~18:00",
            rest_date_raw="매주 화요일",
            parking_info_raw="불가능",
            parking_fee_raw="무료",
            use_fee_raw="3,000원",
            pet_raw="불가",
            baby_carriage_raw="없음",
        ),
        max_count=count,
    )


@pytest.mark.parametrize("persona_count", [3, 4, 5])
def test_기본은_페르소나마다_리뷰를_한_건씩_만든다(persona_count: int) -> None:
    """리뷰 수가 곧 그 장소의 공식 근거 수가 되게 한다."""
    personas = _personas(persona_count)
    plans = generate_review_plans(personas)
    counts = Counter(plan.persona_id for plan in plans)

    assert len(plans) == persona_count
    assert [counts[persona.persona_id] for persona in personas] == [1] * persona_count
    assert [plan.review_index for plan in plans] == list(range(persona_count))


@pytest.mark.parametrize(
    ("persona_count", "expected_distribution"),
    [
        (3, [2, 2, 1]),
        (4, [2, 1, 1, 1]),
        (5, [1, 1, 1, 1, 1]),
    ],
)
def test_review_count를_주면_남는_리뷰를_앞에서부터_더_배분한다(
    persona_count: int, expected_distribution: list[int]
) -> None:
    personas = _personas(persona_count)
    plans = generate_review_plans(personas, review_count=5)
    counts = Counter(plan.persona_id for plan in plans)

    assert len(plans) == 5
    assert [counts[persona.persona_id] for persona in personas] == expected_distribution
    assert [plan.review_index for plan in plans] == list(range(5))


def test_같은_페르소나의_visit_context는_중복되지_않는다() -> None:
    # 페르소나 하나가 리뷰를 여러 건 맡아야 중복 여부를 실제로 확인할 수 있다.
    plans = generate_review_plans(_personas(3), review_count=5)

    contexts_by_persona: dict[str, list[str]] = {}
    for plan in plans:
        contexts_by_persona.setdefault(plan.persona_id, []).append(plan.visit_context)

    assert all(
        len(contexts) == len(set(contexts))
        for contexts in contexts_by_persona.values()
    )


def test_구체적인_공식_평가_축을_우선순서대로_배정한다() -> None:
    personas = _personas(4)
    # 축 순서는 같은 페르소나의 두 번째 리뷰에서 드러나므로 리뷰를 더 배분한다.
    plans = generate_review_plans(personas, review_count=5)
    schedule_plans = [plan for plan in plans if plan.persona_id == personas[0].persona_id]

    assert schedule_plans[0].focus_axes == ("OPERATING_HOURS",)
    assert schedule_plans[1].focus_axes == ("REST_DATE",)
    assert schedule_plans[0].evidence_fields == (
        "operating_hours_raw",
        "rest_date_raw",
    )


def test_계획은_페르소나가_허용한_평가_축과_근거만_사용한다() -> None:
    personas = _personas(4)
    plans = generate_review_plans(personas)
    by_id = {persona.persona_id: persona for persona in personas}

    for plan in plans:
        persona = by_id[plan.persona_id]
        assert set(plan.focus_axes) <= set(persona.allowed_evaluation_axes)
        assert plan.evidence_fields == persona.evidence_fields


def test_같은_입력은_항상_같은_계획을_만든다() -> None:
    personas = _personas(4)

    assert generate_review_plans(personas) == generate_review_plans(personas)


def test_중복된_persona_id는_거부한다() -> None:
    personas = _personas(3)
    duplicated = (personas[0], replace(personas[1], persona_id=personas[0].persona_id), personas[2])

    with pytest.raises(ValueError, match="중복"):
        generate_review_plans(duplicated)


@pytest.mark.parametrize("persona_count", [2, 6])
def test_페르소나는_3개에서_5개만_받는다(persona_count: int) -> None:
    source = _personas(5)
    personas = source[:persona_count] if persona_count == 2 else (*source, source[0])

    with pytest.raises(ValueError, match="3~5"):
        generate_review_plans(tuple(personas))


def test_리뷰_수는_페르소나_수보다_작을_수_없다() -> None:
    with pytest.raises(ValueError, match="페르소나 수 이상"):
        generate_review_plans(_personas(4), review_count=3)


def test_리뷰_수는_상한을_넘길_수_없다() -> None:
    with pytest.raises(ValueError, match="이하여야 합니다"):
        generate_review_plans(_personas(3), review_count=MAX_REVIEWS_PER_PLACE + 1)


def test_visit_context_템플릿은_상한을_감당한다() -> None:
    """페르소나가 최소 인원일 때도 리뷰 상한만큼의 고유 visitContext가 나온다.

    이 관계가 깨지면 generate_review_plans가 _CONTEXT_TEMPLATES 범위를 넘어선다.
    상한을 올리려면 템플릿을 먼저 늘려야 한다는 뜻이다.
    """
    assert PERSONA_COUNT_FLOOR * len(_CONTEXT_TEMPLATES) >= MAX_REVIEWS_PER_PLACE

    plans = generate_review_plans(
        _personas(PERSONA_COUNT_FLOOR), review_count=MAX_REVIEWS_PER_PLACE
    )
    contexts_by_persona: dict[str, list[str]] = {}
    for plan in plans:
        contexts_by_persona.setdefault(plan.persona_id, []).append(plan.visit_context)

    assert all(
        len(contexts) == len(set(contexts))
        for contexts in contexts_by_persona.values()
    )
