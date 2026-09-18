"""D-07: C 정규화 Context Fixture 기준 추천 품질 정밀 검증.

`test_recommendation_context_fixtures.py`(C 계약 검증, 순위·점수는 미검증)와
달리 이 테스트는 `recommendation_context_fixture_expectations.py`에 고정된
기대 순위·점수·가중치 재분배·제외 결과를 실제 파이프라인 산출값과 정확히
비교한다. `candidate_mapper.py`(거리 계산·environment_type 매핑·운영시간
파싱)를 포함한 D 전체 파이프라인을 대상으로 한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agent_context.schemas import AgentContextResponse, RecommendationContext, ResolvedLocation
from app.agent_context.schemas import ContextValue as AgentContextValue
from app.agent_context.schemas import Coordinates as AgentCoordinates
from app.agent_context.schemas import PlaceCandidate as AgentPlaceCandidate
from app.domain.candidate_mapper import map_context_to_scoring_candidates
from app.domain.evidence import build_evidence_list
from app.domain.scoring import score_candidates
from app.schemas import RecommendationItem, RecommendationResponse
from app.services.recommendation_pipeline import (
    resolve_weather_condition,
    run_recommendation_pipeline_from_context,
)
from tests.fixtures.recommendation_context_fixture_expectations import (
    CONTEXT_FIXTURE_EXPECTATIONS,
    ContextFixtureCase,
    ExpectedItem,
)

_FIXTURE_DIRECTORY = Path(__file__).parents[1] / "fixtures" / "agent_context"
_SCORE_TOLERANCE = 1e-3


def _load_response(filename: str) -> AgentContextResponse:
    with (_FIXTURE_DIRECTORY / filename).open(encoding="utf-8") as fixture_file:
        return AgentContextResponse.model_validate(json.load(fixture_file))


async def _run(case: ContextFixtureCase) -> RecommendationResponse:
    response = _load_response(case.filename)
    return await run_recommendation_pipeline_from_context(
        response.context,
        visit_at=case.visit_at,
        search_radius_km=case.search_radius_km,
        shown_place_ids=case.shown_place_ids,
        rejected_place_ids=case.rejected_place_ids,
    )


def _assert_item_matches(actual: RecommendationItem, expected: ExpectedItem) -> None:
    assert actual.place_id == expected.place_id
    assert actual.score == pytest.approx(expected.score, abs=_SCORE_TOLERANCE)

    assert set(actual.feature_scores) == set(expected.feature_scores)
    for feature, expected_value in expected.feature_scores.items():
        actual_value = actual.feature_scores[feature]
        if expected_value is None:
            assert actual_value is None, f"{expected.place_id}.{feature}는 결측이어야 한다"
        else:
            assert actual_value == pytest.approx(expected_value, abs=_SCORE_TOLERANCE), (
                f"{expected.place_id}.{feature} 불일치"
            )

    assert set(actual.weights_used) == set(expected.weights_used)
    for feature, expected_weight in expected.weights_used.items():
        actual_weight = actual.weights_used[feature]
        assert actual_weight == pytest.approx(expected_weight, abs=_SCORE_TOLERANCE), (
            f"{expected.place_id} weights_used.{feature} 불일치"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    CONTEXT_FIXTURE_EXPECTATIONS,
    ids=[case.name for case in CONTEXT_FIXTURE_EXPECTATIONS],
)
async def test_context_fixture_matches_expected_quality(case: ContextFixtureCase) -> None:
    result = await _run(case)

    actual_recommended_ids = tuple(item.place_id for item in result.recommendations)
    actual_unverified_ids = tuple(item.place_id for item in result.unverified_recommendations)

    assert actual_recommended_ids == tuple(item.place_id for item in case.expected_recommended)
    assert actual_unverified_ids == tuple(item.place_id for item in case.expected_unverified)

    for actual_item, expected_item in zip(
        result.recommendations, case.expected_recommended, strict=True
    ):
        _assert_item_matches(actual_item, expected_item)

    for actual_item, expected_item in zip(
        result.unverified_recommendations, case.expected_unverified, strict=True
    ):
        _assert_item_matches(actual_item, expected_item)

    returned_ids = set(actual_recommended_ids) | set(actual_unverified_ids)
    assert returned_ids.isdisjoint(case.expected_excluded_place_ids)


@pytest.mark.asyncio
async def test_shown_place_ids_are_excluded_from_results() -> None:
    """C Fixture엔 없는 필드라 D가 직접 값을 부여해 검증한다(README §실행 인자)."""

    response = _load_response("success.json")

    result = await run_recommendation_pipeline_from_context(
        response.context,
        visit_at=CONTEXT_FIXTURE_EXPECTATIONS[0].visit_at,
        search_radius_km=CONTEXT_FIXTURE_EXPECTATIONS[0].search_radius_km,
        shown_place_ids=frozenset({"126508"}),
    )

    returned_ids = {
        item.place_id for item in result.recommendations + result.unverified_recommendations
    }
    assert "126508" not in returned_ids
    assert "130100" in returned_ids


@pytest.mark.asyncio
async def test_rejected_place_ids_are_excluded_from_results() -> None:
    response = _load_response("success.json")

    result = await run_recommendation_pipeline_from_context(
        response.context,
        visit_at=CONTEXT_FIXTURE_EXPECTATIONS[0].visit_at,
        search_radius_km=CONTEXT_FIXTURE_EXPECTATIONS[0].search_radius_km,
        rejected_place_ids=frozenset({"130100"}),
    )

    returned_ids = {
        item.place_id for item in result.recommendations + result.unverified_recommendations
    }
    assert "130100" not in returned_ids
    assert "126508" in returned_ids


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    CONTEXT_FIXTURE_EXPECTATIONS,
    ids=[case.name for case in CONTEXT_FIXTURE_EXPECTATIONS],
)
async def test_context_fixture_is_deterministic_across_repeated_runs(
    case: ContextFixtureCase,
) -> None:
    """완료 기준: 동일 Fixture에서 점수와 순위가 일관되게 반환된다."""

    first_result = await _run(case)
    second_result = await _run(case)

    assert first_result.recommendations == second_result.recommendations
    assert first_result.unverified_recommendations == second_result.unverified_recommendations


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    CONTEXT_FIXTURE_EXPECTATIONS,
    ids=[case.name for case in CONTEXT_FIXTURE_EXPECTATIONS],
)
async def test_evidence_matches_final_score_and_ranking(case: ContextFixtureCase) -> None:
    """완료 기준: Evidence가 실제 점수와 Ranking 근거에 부합한다.

    공개 진입점(`run_recommendation_pipeline_from_context()`)이 반환한
    `RecommendationItem`과는 별도로, `candidate_mapper→score_candidates→
    build_evidence_list`를 직접 호출해 Evidence를 독립적으로 재구성한 뒤 서로
    일치하는지 비교한다. `_build_response()`가 `evidence.score`/
    `evidence.contributions`를 그대로 옮겨 담는다는 사실에만 기대지 않고,
    같은 입력으로 Evidence를 별도로 다시 만들어서 실제로 일치하는지 확인한다.
    """

    response = _load_response(case.filename)
    context = response.context
    if context is None:
        return

    candidates = map_context_to_scoring_candidates(context, visit_at=case.visit_at)
    weather_condition, weather_reason = resolve_weather_condition(context, None)
    scoring = score_candidates(
        candidates,
        now=case.visit_at,
        weather_condition=weather_condition,
        weather_reason=weather_reason,
        max_distance_km=case.search_radius_km,
        shown_place_ids=case.shown_place_ids,
        rejected_place_ids=case.rejected_place_ids,
    )
    evidence_by_place_id = {
        evidence.place_id: evidence for evidence in build_evidence_list(scoring)
    }

    result = await _run(case)

    assert set(evidence_by_place_id) == {
        item.place_id for item in [*result.recommendations, *result.unverified_recommendations]
    }

    for group in (result.recommendations, result.unverified_recommendations):
        ranks_in_order = [evidence_by_place_id[item.place_id].rank for item in group]
        assert ranks_in_order == sorted(ranks_in_order), (
            "노출 순서가 Evidence의 rank 오름차순과 어긋난다"
        )

        for item in group:
            evidence = evidence_by_place_id[item.place_id]

            assert evidence.score == pytest.approx(item.score, abs=_SCORE_TOLERANCE), (
                f"{item.place_id}: Evidence.score와 최종 score 불일치"
            )
            assert {c.feature for c in evidence.contributions} == set(item.feature_scores), (
                f"{item.place_id}: Evidence의 Feature 집합과 feature_scores 불일치"
            )
            for contribution in evidence.contributions:
                assert contribution.score == item.feature_scores[contribution.feature], (
                    f"{item.place_id}.{contribution.feature}: Evidence 기여 점수와 "
                    "최종 feature_scores 불일치"
                )


def _context_location() -> AgentContextValue:
    """test_recommendation_pipeline.py와 동일한 위치 Fixture를 재사용한다."""

    return AgentContextValue(
        status="success",
        data=ResolvedLocation(
            requested_query="경복궁",
            resolved_name="경복궁",
            source="query",
            location=AgentCoordinates(latitude=37.5796, longitude=126.9770),
        ),
    )


def _context_place(place_id: str, *, latitude: float, longitude: float) -> AgentPlaceCandidate:
    return AgentPlaceCandidate(
        place_id=place_id,
        name=place_id,
        category="cafe",
        location=AgentCoordinates(latitude=latitude, longitude=longitude),
        operating_schedule={"availability": "all_day", "time_ranges": [], "closure_rules": []},
    )


@pytest.mark.asyncio
async def test_tie_break_survives_candidate_mapper_through_full_pipeline() -> None:
    """완료 기준엔 없지만, candidate_mapper를 거쳐도 tie-break가 유지되는지 보강 검증한다.

    scoring_fixture_v1.py의 tie-break 테스트는 손으로 만든 ScoringCandidate를
    직접 넣어 candidate_mapper.py(거리 계산 등)를 거치지 않는다. 여기서는 C
    Context 형태의 입력을 candidate_mapper까지 통과시켜도 동일한 규칙(점수
    동점 시 거리 오름차순 → place_id 오름차순)이 유지되는지 확인한다.
    """

    same_coordinates = {"latitude": 37.5796, "longitude": 126.9770}
    context = RecommendationContext(
        location=_context_location(),
        places=AgentContextValue(
            status="success",
            data=[
                _context_place("zzz-place", **same_coordinates),
                _context_place("aaa-place", **same_coordinates),
            ],
        ),
    )

    result = await run_recommendation_pipeline_from_context(
        context,
        visit_at=CONTEXT_FIXTURE_EXPECTATIONS[0].visit_at,
        search_radius_km=2.0,
    )

    place_ids = [item.place_id for item in result.recommendations]
    assert place_ids == ["aaa-place", "zzz-place"], (
        "동점일 때 place_id 오름차순 tie-break가 candidate_mapper를 거친 뒤에도 유지돼야 한다"
    )


@pytest.mark.asyncio
async def test_recommendation_limit_truncates_to_top_scored_candidates() -> None:
    """완료 기준엔 없지만, recommendation_limit 초과 시 상위 점수만 남는지 보강 검증한다.

    지금까지의 D-07 Fixture는 전부 후보가 3개 이하라 recommendation_limit(기본값)을
    넘는 상황이 한 번도 발생하지 않았다. 여기서는 거리만 다른 후보 3개를 만들어
    limit=2일 때 가장 가까운(=점수 높은) 2개만 남는지 확인한다.
    """

    context = RecommendationContext(
        location=_context_location(),
        places=AgentContextValue(
            status="success",
            data=[
                _context_place("near", latitude=37.5796, longitude=126.9770),
                _context_place("mid", latitude=37.5896, longitude=126.9770),
                _context_place("far", latitude=37.5996, longitude=126.9770),
            ],
        ),
    )

    result = await run_recommendation_pipeline_from_context(
        context,
        visit_at=CONTEXT_FIXTURE_EXPECTATIONS[0].visit_at,
        search_radius_km=5.0,
        recommendation_limit=2,
    )

    all_ids = [item.place_id for item in result.recommendations + result.unverified_recommendations]
    assert all_ids == ["near", "mid"], "가장 가까운(점수 높은) 상위 2개만 남아야 한다"
