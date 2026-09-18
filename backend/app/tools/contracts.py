"""Agent/Orchestrator가 공통으로 소비하는 Tool 결과 계약."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Generic, Protocol, TypeVar

from app.providers.contracts import ProviderMetadata


class ToolStatus(StrEnum):
    SUCCESS = "success"
    NO_DATA = "no_data"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ToolError:
    code: str
    message: str
    cause: str | None
    retryable: bool
    details: dict[str, str] = field(default_factory=dict)


T = TypeVar("T")


class ToolResult(Protocol, Generic[T]):
    """Tool별 payload와 무관하게 Orchestrator가 읽는 공통 필드."""

    status: ToolStatus
    error: ToolError | None
    warnings: tuple[str, ...]
    provider_metadata: tuple[ProviderMetadata, ...]
