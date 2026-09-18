from __future__ import annotations

import pytest

from app.agent_context.schemas import (
    AgentContextRequest,
    AgentContextResponse,
    RecommendationContext,
)
from app.agent_context.schemas import UserConditions as ContextUserConditions
from app.schemas import UserConditions
from app.services.runtime import context_schemas as runtime_schemas
from app.services.runtime.context_transform import to_agent_context_request
from app.services.runtime.stubs import FakeToolProvider


def test_runtime_context_schema_path_reexports_canonical_c_types() -> None:
    """기존 A import 경로도 C가 소유한 동일 클래스 객체를 반환해야 한다."""

    assert runtime_schemas.AgentContextRequest is AgentContextRequest
    assert runtime_schemas.AgentContextResponse is AgentContextResponse
    assert runtime_schemas.RecommendationContext is RecommendationContext
    assert runtime_schemas.UserConditions is ContextUserConditions


def test_a_conditions_transform_to_canonical_context_request() -> None:
    """A의 Enum 기반 조건이 C의 엄격한 요청 계약으로 직접 변환되어야 한다."""

    request = to_agent_context_request(
        "request-1",
        UserConditions(
            search_center="경복궁",
            place_types=["restaurant"],
            place_tags=["카페"],
            weather="rain",
            weather_intent="AVOID",
            transport="walk",
            max_travel_time=20,
            time_available=120,
        ),
    )

    assert type(request) is AgentContextRequest
    assert type(request.conditions) is ContextUserConditions
    assert request.conditions.place_types == ["restaurant"]
    assert request.conditions.place_tags == ["카페"]
    assert request.conditions.weather == "rain"


@pytest.mark.asyncio
async def test_runtime_fake_tool_returns_canonical_c_response() -> None:
    """A의 임시 Fake도 실제 C와 같은 응답 클래스를 사용해야 한다."""

    request = to_agent_context_request(
        "request-1",
        UserConditions(search_center="경복궁"),
    )

    response = await FakeToolProvider().fetch_context(request)

    assert type(response) is AgentContextResponse
    assert response.status == "success"
    assert response.context is not None


@pytest.mark.asyncio
async def test_runtime_fake_clarification_obeys_c_state_rules() -> None:
    """위치 누락 Fake 응답도 C의 상태 교차 검증을 통과해야 한다."""

    request = to_agent_context_request("request-1", UserConditions())

    response = await FakeToolProvider().fetch_context(request)

    assert type(response) is AgentContextResponse
    assert response.status == "needs_clarification"
    assert response.context is None
    assert response.error is None
    assert response.clarification is not None
