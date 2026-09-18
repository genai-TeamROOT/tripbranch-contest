"""D-07: C 정규화 Context Fixture로 돌린 D 파이프라인 실제 결과를 눈으로 확인한다.

역할: 기대값(순위·점수)을 손으로 미리 정하지 않고, 9개 Fixture를 실제로
돌려서 나온 결과를 먼저 사람이 검토하기 위한 진단용 테스트다. 여기서 확인한
값이 Scoring 규칙과 맞는지 검토한 뒤, 그 값을 기대값 Fixture로 확정한다.
`pytest -s`로 실행해야 출력이 보인다. assert는 최소한(예외 없이 완주)만 한다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from app.agent_context.schemas import AgentContextResponse
from app.schemas import RecommendationItem, RecommendationResponse
from app.services.recommendation_pipeline import run_recommendation_pipeline_from_context

_FIXTURE_DIRECTORY = Path(__file__).parents[1] / "fixtures" / "agent_context"
_VISIT_AT = datetime.fromisoformat("2026-08-15T11:00:00+09:00")
_SEARCH_RADIUS_KM = 2.0

_FIXTURE_FILES = (
    "success.json",
    "success_bad_weather.json",
    "success_operating_schedule.json",
    "partial_weather_unavailable.json",
    "partial_place_details.json",
    "missing_weather.json",
    "missing_operating_hours.json",
    "insufficient_candidates.json",
    "no_place_candidates.json",
)


def _load_response(filename: str) -> AgentContextResponse:
    with (_FIXTURE_DIRECTORY / filename).open(encoding="utf-8") as fixture_file:
        return AgentContextResponse.model_validate(json.load(fixture_file))


def _print_item(label: str, item: RecommendationItem) -> None:
    print(
        f"    [{label}] {item.place_id} ({item.name}) "
        f"score={item.score:.4f} distance_km={item.distance_km} "
        f"remaining_minutes={item.remaining_minutes} "
        f"environment_type={item.environment_type}"
    )
    print(f"        feature_scores={item.feature_scores}")
    print(f"        weights_used={item.weights_used}")
    print(f"        explanations={item.explanations}")
    if item.warnings:
        print(f"        warnings={item.warnings}")


def _print_result(filename: str, result: RecommendationResponse) -> None:
    print(f"\n=== {filename} (elapsed_ms={result.elapsed_ms:.2f}) ===")
    if not result.recommendations and not result.unverified_recommendations:
        print("    (추천 결과 없음)")
        return
    for item in result.recommendations:
        _print_item("recommendations", item)
    for item in result.unverified_recommendations:
        _print_item("unverified", item)


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", _FIXTURE_FILES)
async def test_inspect_recommendation_context_fixture(filename: str) -> None:
    response = _load_response(filename)

    result = await run_recommendation_pipeline_from_context(
        response.context,
        visit_at=_VISIT_AT,
        search_radius_km=_SEARCH_RADIUS_KM,
    )

    _print_result(filename, result)
