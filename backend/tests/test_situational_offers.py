"""situational_offers.SITUATION_OFFERS의 안전망.

역할: 대화층 3단계 — "situation → 실행 가능한 action 매핑은 프롬프트가 아니라
코드에 둔다"는 결정이 실제로 지켜지는지 확인한다. 특히 condition_overrides의
필드명이 UserConditions(app.schemas)와 어긋나면, 수락 시 조용히 무시되는 조건이
생긴다 — 여기서 잡지 않으면 실사용에서만 드러난다.
"""

from __future__ import annotations

from app.schemas import SituationKind, UserConditions
from app.services.interpret.situational_offers import SITUATION_OFFERS, offer_for


def test_every_offer_condition_override_field_exists_on_user_conditions() -> None:
    valid_fields = set(UserConditions.model_fields)
    for situation, offer in SITUATION_OFFERS.items():
        for field in offer.condition_overrides:
            assert field in valid_fields, (
                f"{situation}의 offer({offer.action_id})가 존재하지 않는 "
                f"UserConditions 필드 '{field}'를 가리킨다"
            )


def test_condition_overrides_actually_apply_to_user_conditions() -> None:
    """model_copy(update=...)가 실제로 통과하는지까지 확인한다(타입 불일치 방지)."""
    base = UserConditions(search_center="경복궁")
    for offer in SITUATION_OFFERS.values():
        merged = base.model_copy(update=dict(offer.condition_overrides))
        for field, value in offer.condition_overrides.items():
            assert getattr(merged, field) == value
        # override 대상이 아닌 필드는 그대로 유지되어야 한다.
        assert merged.search_center == "경복궁"


def test_vague_has_no_offer() -> None:
    """막연한 답답함은 실행 가능한 도움이 없다 — 제안하지 않는다(절제 규칙 2번)."""
    assert offer_for(SituationKind.VAGUE) is None


def test_none_situation_has_no_offer() -> None:
    assert offer_for(None) is None


def test_fatigue_and_companion_difficulty_share_the_same_action() -> None:
    """같은 도움을 두 상황이 제안하면, 한쪽을 거절하면 다른 쪽도 다시 권하지 않아야
    한다 — 그러려면 action_id가 같아야 한다(rejected_actions는 action_id로 dedup)."""
    fatigue = offer_for(SituationKind.FATIGUE)
    companion = offer_for(SituationKind.COMPANION_DIFFICULTY)
    assert fatigue is not None
    assert companion is not None
    assert fatigue.action_id == companion.action_id


def test_every_offer_has_a_non_empty_button_label_and_content() -> None:
    for offer in SITUATION_OFFERS.values():
        assert offer.content.strip()
        assert offer.button_label.strip()
        assert offer.action_id.strip()
