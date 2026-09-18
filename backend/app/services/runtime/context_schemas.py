"""A Runtime의 기존 import 경로를 위한 A–C Context 계약 재노출 모듈.

실제 스키마의 단일 원본은 ``app.agent_context.schemas``다. A와 C가 같은 이름의
Pydantic 모델을 별도로 정의해 검증 규칙이 달라지는 일을 막기 위해, 이 모듈에서는
모델을 다시 선언하지 않고 C가 소유한 계약 타입을 그대로 재노출한다.
"""

from app.agent_context.schemas import (
    AgentContextRequest,
    AgentContextResponse,
    Clarification,
    ContextError,
    ContextValue,
    ContextWarning,
    Coordinates,
    HolidayInfo,
    PlaceCandidate,
    ProviderMetadata,
    RecommendationContext,
    ResolvedLocation,
    ResponseMetadata,
    UserConditions,
    WeatherForecast,
)

__all__ = [
    "AgentContextRequest",
    "AgentContextResponse",
    "Clarification",
    "ContextError",
    "ContextValue",
    "ContextWarning",
    "Coordinates",
    "HolidayInfo",
    "PlaceCandidate",
    "ProviderMetadata",
    "RecommendationContext",
    "ResolvedLocation",
    "ResponseMetadata",
    "UserConditions",
    "WeatherForecast",
]
