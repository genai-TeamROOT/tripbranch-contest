from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import pytest

from app.agent_context.schemas import AgentContextResponse

_EXPECTED_TOP_LEVEL_KEYS = {
    "request_id",
    "intent",
    "contract_version",
    "status",
    "context",
    "clarification",
    "warnings",
    "error",
    "metadata",
}
_EXPECTED_CONTEXT_NAMES = {"location", "weather", "places", "holidays"}
_CONTEXT_VALUE_KEYS = {
    "status",
    "data",
    "error",
    "warnings",
    "provider_metadata",
}
_SNAKE_CASE_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


def _assert_timezone_aware(value: str) -> None:
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() is not None


def _assert_provider_metadata(metadata: list[dict[str, Any]]) -> None:
    assert metadata
    for item in metadata:
        assert set(item) == {"source", "status", "retrieved_at"}
        assert item["source"]
        assert item["status"] in {"success", "no_data", "unavailable"}
        _assert_timezone_aware(item["retrieved_at"])


def _assert_successful_context_value(value: dict[str, Any]) -> None:
    assert set(value) == _CONTEXT_VALUE_KEYS
    assert value["status"] == "success"
    assert value["data"] is not None
    assert value["error"] is None
    assert isinstance(value["warnings"], list)
    _assert_provider_metadata(value["provider_metadata"])


def _assert_snake_case_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested_value in value.items():
            assert _SNAKE_CASE_PATTERN.fullmatch(key), f"{key!r} is not snake_case"
            _assert_snake_case_keys(nested_value)
    elif isinstance(value, list):
        for item in value:
            _assert_snake_case_keys(item)


@pytest.mark.parametrize(
    "fixture_name",
    [
        "success_agent_context_response",
        "partial_weather_unavailable_response",
        "needs_location_clarification_response",
    ],
)
def test_agent_context_fixture_envelope_and_field_names(
    fixture_name: str,
    request: pytest.FixtureRequest,
) -> None:
    payload = request.getfixturevalue(fixture_name)

    assert set(payload) == _EXPECTED_TOP_LEVEL_KEYS
    assert payload["intent"] == "RECOMMEND"
    assert payload["contract_version"] == "draft-v0"
    assert isinstance(payload["metadata"]["rule_versions"], dict)
    assert isinstance(payload["metadata"]["provider_metadata"], list)
    _assert_snake_case_keys(payload)


@pytest.mark.parametrize(
    "fixture_name",
    [
        "success_agent_context_response",
        "partial_weather_unavailable_response",
        "needs_location_clarification_response",
    ],
)
def test_agent_context_fixture_matches_confirmed_pydantic_contract(
    fixture_name: str,
    request: pytest.FixtureRequest,
) -> None:
    payload = request.getfixturevalue(fixture_name)

    response = AgentContextResponse.model_validate(payload)

    assert response.request_id == payload["request_id"]
    assert response.status == payload["status"]


def test_success_fixture_contains_all_required_context(
    success_agent_context_response: dict[str, Any],
) -> None:
    payload = success_agent_context_response

    assert payload["status"] == "success"
    assert payload["clarification"] is None
    assert payload["error"] is None
    assert set(payload["context"]) == _EXPECTED_CONTEXT_NAMES
    assert "concentration" not in payload["context"]

    for context_value in payload["context"].values():
        _assert_successful_context_value(context_value)

    location = payload["context"]["location"]["data"]
    assert location["requested_query"] == "경복궁"
    assert location["location"] == {
        "latitude": 37.579617,
        "longitude": 126.977041,
    }

    weather = payload["context"]["weather"]["data"]
    # D-051: C는 판정(condition)을 싣지 않고 사실만 넘긴다. 판정 재료가 비면
    # D가 무조건 NEUTRAL로 굳으므로, 픽스처가 사실을 담고 있는지 못 박는다.
    assert "condition" not in weather
    assert weather["precipitation"] is not None
    assert weather["sky"] is not None
    _assert_timezone_aware(weather["forecast_for"])

    places = payload["context"]["places"]["data"]
    assert places
    for place in places:
        assert place["operating_hours_raw"]
        assert place["rest_date_raw"]
        assert place["operating_schedule"]["time_ranges"]

    assert payload["context"]["holidays"]["data"]
    assert payload["metadata"]["rule_versions"]
    _assert_provider_metadata(payload["metadata"]["provider_metadata"])


def test_partial_fixture_keeps_successful_context_when_weather_times_out(
    partial_weather_unavailable_response: dict[str, Any],
) -> None:
    payload = partial_weather_unavailable_response

    assert payload["status"] == "partial"
    assert set(payload["context"]) == _EXPECTED_CONTEXT_NAMES
    assert "concentration" not in payload["context"]
    for context_name in ("location", "places", "holidays"):
        _assert_successful_context_value(payload["context"][context_name])

    weather = payload["context"]["weather"]
    assert set(weather) == _CONTEXT_VALUE_KEYS
    assert weather["status"] == "unavailable"
    assert weather["data"] is None
    assert weather["error"]["code"] == "weather_provider_timeout"
    assert weather["error"]["retryable"] is True
    _assert_provider_metadata(weather["provider_metadata"])

    top_level_metadata = payload["metadata"]["provider_metadata"]
    successful_sources = {
        item["source"] for item in top_level_metadata if item["status"] == "success"
    }
    assert successful_sources == {
        "naver_geocoding",
        "tour_api_place",
        "kasi_holiday",
    }
    _assert_provider_metadata(top_level_metadata)


def test_clarification_fixture_contains_machine_readable_location_request_only(
    needs_location_clarification_response: dict[str, Any],
) -> None:
    payload = needs_location_clarification_response

    assert payload["status"] == "needs_clarification"
    assert payload["context"] is None
    assert payload["error"] is None
    assert payload["clarification"] == {
        "code": "location_required",
        "missing_fields": ["current_location"],
        "candidates": [],
    }
    assert "message" not in payload["clarification"]
    assert payload["metadata"] == {
        "rule_versions": {},
        "provider_metadata": [],
    }
