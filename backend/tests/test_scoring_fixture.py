"""Scoring v1 평가 Fixture v1 기반 테스트 (D-02).

역할: `fixtures/scoring_fixture_v1.py`의 고정 시나리오를 순회하며 정렬·제외·
미확인 처리를 검증하고, 동일 입력을 반복 실행해도 순서가 바뀌지 않는지
(결정적 정렬) 확인한다. 또한 `build_evidence_list()`가 `RankedCandidate`를
누락 없이 근거로 변환하는지 검증한다.
"""

from __future__ import annotations

import pytest
from fixtures.scoring_fixture_v1 import SCORING_FIXTURE_V1, ScoringFixtureCase

from app.domain.evidence import build_evidence_list
from app.domain.scoring import score_candidates


def _run(case: ScoringFixtureCase):
    return score_candidates(
        case.candidates,
        now=case.now,
        weather_condition=case.weather_condition,
        max_distance_km=case.max_distance_km,
        shown_place_ids=case.shown_place_ids,
        rejected_place_ids=case.rejected_place_ids,
        travel_routes=case.travel_routes,
    )


@pytest.mark.parametrize("case", SCORING_FIXTURE_V1, ids=lambda case: case.name)
def test_fixture_ranking_and_exclusion(case: ScoringFixtureCase) -> None:
    result = _run(case)

    place_ids = tuple(item.place_id for item in result.ranked)
    assert place_ids == case.expected_ranked_place_ids

    assert set(result.excluded_place_ids) == case.expected_excluded_place_ids

    unverified_ids = {item.place_id for item in result.ranked if item.is_unverified}
    assert unverified_ids == case.expected_unverified_place_ids
    for item in result.ranked:
        if item.place_id in case.expected_unverified_place_ids:
            assert item.warnings != ()
        else:
            assert item.warnings == ()


@pytest.mark.parametrize("case", SCORING_FIXTURE_V1, ids=lambda case: case.name)
def test_fixture_is_deterministic(case: ScoringFixtureCase) -> None:
    first = _run(case)
    second = _run(case)

    first_summary = [(item.place_id, item.rank, item.score) for item in first.ranked]
    second_summary = [(item.place_id, item.rank, item.score) for item in second.ranked]
    assert first_summary == second_summary
    assert first.excluded_place_ids == second.excluded_place_ids


@pytest.mark.parametrize("case", SCORING_FIXTURE_V1, ids=lambda case: case.name)
def test_fixture_evidence_matches_ranked_candidates(case: ScoringFixtureCase) -> None:
    result = _run(case)
    evidence_list = build_evidence_list(result)

    assert [evidence.place_id for evidence in evidence_list] == [
        item.place_id for item in result.ranked
    ]

    for evidence, ranked in zip(evidence_list, result.ranked, strict=True):
        assert evidence.score == ranked.score
        assert evidence.is_unverified == ranked.is_unverified
        assert evidence.warnings == ranked.warnings
        assert {c.feature for c in evidence.contributions} == set(ranked.feature_scores)
        for contribution in evidence.contributions:
            score = ranked.feature_scores[contribution.feature]
            weight = ranked.weights_used.get(contribution.feature)
            assert contribution.score == score
            assert contribution.weight == weight
            if score is not None and weight is not None:
                assert contribution.contribution == pytest.approx(score * weight)
            else:
                assert contribution.contribution is None
