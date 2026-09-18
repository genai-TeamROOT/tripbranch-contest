"""취향 근거 문장과 추천 이유 문구 테스트."""

from __future__ import annotations

import pytest

from app.domain.evidence import RecommendationEvidence, build_evidence
from app.domain.explanation import build_explanations
from app.domain.models import WeatherCondition
from app.domain.scoring import (
    DEFAULT_WEIGHTS,
    TASTE_WEIGHTS,
    RankedCandidate,
)
from app.services.recommendation_pipeline import _recommendation_reason

_QUOTE = "두 분의 미소, 눈가에 고인 기쁨의 눈물을 사진으로 전달하고 싶거든요."


def _ranked(*, taste: float, text: str | None = _QUOTE) -> RankedCandidate:
    scores = {
        "weather": 1.0,
        "remaining_operating_time": 1.0,
        "distance": 1.0,
        "taste": taste,
    }
    return RankedCandidate(
        place_id="a",
        name="장소",
        category="restaurant",
        rank=1,
        score=0.9,
        feature_scores=scores,
        weights_used=dict(TASTE_WEIGHTS),
        is_unverified=False,
        warnings=(),
        distance_km=0.3,
        remaining_minutes=120.0,
        weather_condition=WeatherCondition.GOOD,
        environment_type="indoor",
        taste_evidence_text=text,
    )


def _explain(candidate: RankedCandidate) -> tuple[str, ...]:
    return build_explanations(build_evidence(candidate))


def test_strong_taste_match_does_not_crash() -> None:
    """문장 생성기에 taste가 없으면 KeyError로 500이 난다 — 임계값 0.7 이상에서만 터진다."""
    sentences = _explain(_ranked(taste=0.95))

    assert any("방문 후기" in sentence for sentence in sentences)


def test_taste_sentence_quotes_the_evidence() -> None:
    """점수만으로는 "왜 내 취향이냐"에 답할 수 없다 — 실제 문장을 인용한다."""
    sentences = _explain(_ranked(taste=0.9))
    taste_sentence = next(s for s in sentences if "방문 후기" in s)

    assert "두 분의 미소" in taste_sentence


def test_long_quote_is_truncated() -> None:
    """블로그 문장이 길면 카드가 밀린다."""
    sentences = _explain(_ranked(taste=0.9, text="가" * 200))
    taste_sentence = next(s for s in sentences if "방문 후기" in s)

    assert taste_sentence.endswith('…"')
    assert len(taste_sentence) < 100


def test_missing_quote_falls_back_to_a_plain_sentence() -> None:
    """검색은 됐지만 조각이 비어 있어도 문장이 깨지면 안 된다."""
    sentences = _explain(_ranked(taste=0.9, text=None))

    assert "말씀하신 분위기와 잘 맞는 곳이에요." in sentences


def test_tag_scored_taste_uses_matching_tag_instead_of_unrelated_embedding_quote() -> None:
    candidate = _ranked(taste=0.9, text="가족 모임과 회식에 좋은 룸 식당이에요.")
    candidate = RankedCandidate(
        **{
            **candidate.__dict__,
            "taste_tag_label": "혼자 가기 좋은",
            "taste_tag_documents": 4,
        }
    )

    sentences = _explain(candidate)
    taste_sentence = next(sentence for sentence in sentences if "후기 4건" in sentence)

    assert '"혼자 가기 좋은"' in taste_sentence
    assert not any("가족 모임" in sentence for sentence in sentences)


def test_weak_taste_match_is_not_mentioned() -> None:
    """근거가 약한데 "취향에 맞다"고 말하면 거짓이 된다(임계값 0.7)."""
    sentences = _explain(_ranked(taste=0.3))

    assert not any("방문 후기" in sentence for sentence in sentences)


@pytest.mark.parametrize(
    ("weights", "expected"),
    [
        (DEFAULT_WEIGHTS, "날씨·운영시간·거리 조건을 종합한 1순위 추천이에요."),
        (TASTE_WEIGHTS, "날씨·운영시간·거리·취향 조건을 종합한 1순위 추천이에요."),
    ],
)
def test_reason_names_the_axes_actually_scored(weights, expected: str) -> None:
    """취향이 순위를 바꿔놓고 문장이 그 사실을 숨기면 응답이 계산과 어긋난다."""
    candidate = _ranked(taste=0.5)
    candidate = RankedCandidate(**{**candidate.__dict__, "weights_used": dict(weights)})

    assert _recommendation_reason(candidate) == expected


def test_evidence_carries_the_quote() -> None:
    """RankedCandidate → Evidence 전달이 끊기면 문장이 원문을 못 쓴다."""
    evidence: RecommendationEvidence = build_evidence(_ranked(taste=0.9))

    assert evidence.taste_evidence_text == _QUOTE
