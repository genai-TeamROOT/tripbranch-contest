"""상황(SituationKind) → 실제로 실행 가능한 제안의 유일한 매핑.

역할: docs/design/conversational-layer.md 3단계 — "situation → 실행 가능한 action
매핑은 프롬프트가 아니라 코드에 둔다"의 실제 구현. LLM(general/extract.md)은
SituationKind 하나만 분류하고, 그 상황에서 무엇을 제안할지(추천 조건, 버튼 문구)는
전부 이 파일이 정한다.

이렇게 나누는 이유는 프롬프트가 "닫힌 목록"을 텍스트 규칙으로만 지키면 언젠가
코드와 어긋나기 때문이다 — 도구가 없어지거나 조건 필드가 바뀌어도 프롬프트 문서를
깜빡하고 안 고치면 LLM이 더 이상 못 하는 걸 계속 제안하게 된다. 매핑이 코드에
있으면 그 드리프트 자체가 불가능하다.

VAGUE(막연한 답답함)는 의도적으로 이 딕셔너리에 없다 — "선제 제안의 절제 규칙"
(문서 5장) 2번, 실제로 실행 가능한 도움이 없으면 제안하지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.schemas import Environment, SituationKind


@dataclass(frozen=True)
class OfferSpec:
    """제안 하나. content/button_label은 문장이 아니라 조립 재료다.

    content: 답변 단계(트리비 페르소나가 주입된 LLM 호출)가 "~을 찾아드릴까요?"
        같은 자연스러운 질문으로 마무리할 때 쓰는 짧은 명사구.
    button_label: 후속 질문 버튼(suggested_follow_ups)에 그대로 실리는 문장. 버튼을
        누르면 이 문구가 그대로 사용자 발화로 재전송되므로(response_composer가
        아니라 follow_up_suggester의 기존 계약) 그 자체로 완결된 요청이어야 한다.
    condition_overrides: 수락 시 현재 세션 조건에 덮어씌울 UserConditions 필드.
        app.schemas.UserConditions의 실제 필드명과 일치해야 한다(테스트로 검증).
    """

    action_id: str
    content: str
    button_label: str
    condition_overrides: Mapping[str, object]


# 같은 action_id를 공유하는 두 상황(FATIGUE/COMPANION_DIFFICULTY)은 의도적이다 —
# 실제로 제안하는 내용이 같으므로, 한쪽을 거절하면 다른 쪽도 다시 권하지 않는다
# (rejected_actions는 action_id로 중복을 없앤다).
_REST_PLACE_OFFER = OfferSpec(
    action_id="recommend_nearby_rest_place",
    content="이동이 짧고 쉬기 편한 곳",
    button_label="이동이 짧고 쉬기 편한 곳 찾아줘",
    condition_overrides={"max_travel_time": 15},
)

SITUATION_OFFERS: dict[SituationKind, OfferSpec] = {
    SituationKind.FATIGUE: _REST_PLACE_OFFER,
    SituationKind.COMPANION_DIFFICULTY: _REST_PLACE_OFFER,
    SituationKind.BAD_WEATHER: OfferSpec(
        action_id="recommend_indoor_place",
        content="실내 장소",
        button_label="실내 장소로 찾아줘",
        condition_overrides={"environment": Environment.INDOOR},
    ),
    SituationKind.CLOSED_OR_CROWDED: OfferSpec(
        action_id="recommend_alternative_place",
        content="지금 열려 있는 다른 곳",
        button_label="지금 열려 있는 다른 곳 찾아줘",
        # 폐점 후보를 빼는 건 추천의 기본 동작이라(ignore_operating_hours_until이
        # 켜져 있지 않은 한) 조건을 더 얹지 않고 그대로 재검색하는 것만으로 충분하다.
        condition_overrides={},
    ),
}


def offer_for(situation: SituationKind | None) -> OfferSpec | None:
    """상황에 맞는 제안을 찾는다. 없으면(VAGUE 포함) None — 제안하지 않는다."""

    if situation is None:
        return None
    return SITUATION_OFFERS.get(situation)


__all__ = ["OfferSpec", "SITUATION_OFFERS", "offer_for"]
