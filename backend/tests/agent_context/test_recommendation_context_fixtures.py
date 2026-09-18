"""D가 A 없이 사용할 수 있는 C 정규화 Context Fixture를 검증한다."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from app.agent_context.schemas import AgentContextResponse
from app.services.recommendation_pipeline import (
    _WEATHER_IGNORED_WARNING,
    _WEATHER_MISSING_WARNING,
    run_recommendation_pipeline_from_context,
)

_FIXTURE_DIRECTORY = Path(__file__).parents[1] / "fixtures" / "agent_context"
_VISIT_AT = datetime.fromisoformat("2026-08-15T11:00:00+09:00")

_D_FIXTURE_CASES = {
    "success.json": ("success", 2),
    "success_bad_weather.json": ("success", 2),
    "success_operating_schedule.json": ("success", 3),
    "partial_weather_unavailable.json": ("partial", 2),
    "partial_place_details.json": ("partial", 2),
    "missing_weather.json": ("success", 2),
    "missing_operating_hours.json": ("partial", 2),
    "insufficient_candidates.json": ("partial", 1),
    "no_place_candidates.json": ("no_data", 0),
}


def _load_response(filename: str) -> AgentContextResponse:
    with (_FIXTURE_DIRECTORY / filename).open(encoding="utf-8") as fixture_file:
        return AgentContextResponse.model_validate(json.load(fixture_file))


@pytest.mark.parametrize(("filename", "expected"), _D_FIXTURE_CASES.items())
def test_d_context_fixtures_match_c_contract(
    filename: str,
    expected: tuple[str, int],
) -> None:
    """모든 Fixture가 현재 C 응답 계약과 시나리오별 후보 수를 지키는지 확인한다."""

    expected_status, expected_place_count = expected
    response = _load_response(filename)

    assert response.status == expected_status
    assert response.context is not None
    assert response.context.places is not None
    assert len(response.context.places.data or []) == expected_place_count


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", _D_FIXTURE_CASES)
async def test_d_context_fixtures_can_run_recommendation_pipeline(filename: str) -> None:
    """정규화 Context만으로 D 공개 파이프라인을 실행할 수 있는지 확인한다."""

    response = _load_response(filename)
    result = await run_recommendation_pipeline_from_context(
        response.context,
        visit_at=_VISIT_AT,
        search_radius_km=2.0,
    )

    if filename == "no_place_candidates.json":
        assert result.recommendations == []
        assert result.unverified_recommendations == []
    else:
        assert result.recommendations or result.unverified_recommendations


@pytest.mark.asyncio
async def test_operating_schedule_fixture_excludes_closed_candidate() -> None:
    response = _load_response("success_operating_schedule.json")

    result = await run_recommendation_pipeline_from_context(
        response.context,
        visit_at=_VISIT_AT,
        search_radius_km=2.0,
    )

    returned_ids = {
        item.place_id
        for item in result.recommendations + result.unverified_recommendations
    }
    assert "schedule-open-1" in returned_ids
    assert "schedule-all-day-1" in returned_ids
    assert "schedule-closed-1" not in returned_ids


@pytest.mark.asyncio
async def test_missing_operating_hours_fixture_returns_unverified_candidates() -> None:
    response = _load_response("missing_operating_hours.json")

    result = await run_recommendation_pipeline_from_context(
        response.context,
        visit_at=_VISIT_AT,
        search_radius_km=2.0,
    )

    assert result.recommendations == []
    assert len(result.unverified_recommendations) == 2


@pytest.mark.asyncio
async def test_missing_weather_fixture_is_distinct_from_provider_failure() -> None:
    ignored_response = _load_response("missing_weather.json")
    failed_response = _load_response("partial_weather_unavailable.json")

    ignored_result = await run_recommendation_pipeline_from_context(
        ignored_response.context,
        visit_at=_VISIT_AT,
        search_radius_km=2.0,
    )
    failed_result = await run_recommendation_pipeline_from_context(
        failed_response.context,
        visit_at=_VISIT_AT,
        search_radius_km=2.0,
    )

    ignored_warnings = ignored_result.recommendations[0].warnings
    failed_warnings = failed_result.recommendations[0].warnings
    # 문구 자체가 아니라 "두 경고가 서로 다른 상수"라는 사실을 검증한다 —
    # 문구는 UX 논의로 바뀔 수 있고(D-038 결정 1), 그때마다 테스트가 깨지면 안 된다.
    assert _WEATHER_IGNORED_WARNING in ignored_warnings
    assert _WEATHER_MISSING_WARNING in failed_warnings


# _D_FIXTURE_CASES가 아니라 디렉터리 전체를 훑는다 — 나중에 추가되는 픽스처도
# 목록에 손대지 않고 자동으로 검사 대상이 된다.
@pytest.mark.parametrize(
    "filename",
    sorted(path.name for path in _FIXTURE_DIRECTORY.glob("*.json")),
)
def test_weather_fixtures_carry_the_facts_d_judges_on(filename: str) -> None:
    """날씨 데이터가 있는 픽스처는 판정 재료 3종을 모두 들고 있어야 한다.

    C 매퍼는 forecast가 있으면 PTY/SKY를 항상 함께 옮기고(mappers.py), 초단기예보는
    SKY·PTY·T1H를 한 묶음으로 준다 — 그래서 "success인데 기온만 있는 Context"는
    C가 만들 수 없는 모양이다. 그런 픽스처를 두면 D가 판정 로직 대신 "근거 전무"
    폴백을 타면서 조용히 NEUTRAL로 굳어, 테스트가 통과해도 아무것도 검증하지 못한다
    (precipitation/sky 추가 시 픽스처 3건을 갱신하지 않아 실제로 그랬다).

    condition은 곧 제거될 레거시라 여기서 검증하지 않는다 — 사실 3종만 본다.
    """
    # 되물음 응답(needs_location_clarification)은 context 자체가 없다.
    context = _load_response(filename).context
    if context is None:
        return

    weather = context.weather
    if weather is None or weather.status not in {"success", "partial"} or weather.data is None:
        return

    assert weather.data.precipitation is not None
    assert weather.data.sky is not None
    assert weather.data.temperature_celsius is not None
