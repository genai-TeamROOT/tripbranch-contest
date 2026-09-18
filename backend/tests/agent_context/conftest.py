from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

_FIXTURE_DIRECTORY = Path(__file__).parents[1] / "fixtures" / "agent_context"


def _load_agent_context_fixture(filename: str) -> dict[str, Any]:
    with (_FIXTURE_DIRECTORY / filename).open(encoding="utf-8") as fixture_file:
        return json.load(fixture_file)


@pytest.fixture
def success_agent_context_response() -> dict[str, Any]:
    return _load_agent_context_fixture("success.json")


@pytest.fixture
def partial_weather_unavailable_response() -> dict[str, Any]:
    return _load_agent_context_fixture("partial_weather_unavailable.json")


@pytest.fixture
def needs_location_clarification_response() -> dict[str, Any]:
    return _load_agent_context_fixture("needs_location_clarification.json")
