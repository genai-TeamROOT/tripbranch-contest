"""서울시 실시간 상권 Provider를 INFO 경로의 Tool 계약으로 노출한다."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.domain.models import RealtimeCommercialResult
from app.errors import AppError
from app.providers.contracts import ProviderMetadata, ProviderStatus
from app.providers.protocols import RealtimeCommercialProvider
from app.tools.contracts import ToolError, ToolStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RealtimeCommercialQuery:
    area_name_or_code: str

    def __post_init__(self) -> None:
        if not self.area_name_or_code.strip():
            raise ValueError("서울시 상권 조회에는 지역명이 필요합니다.")


@dataclass(frozen=True)
class RealtimeCommercialToolResult:
    status: ToolStatus
    commercial: RealtimeCommercialResult | None
    error: ToolError | None
    provider_metadata: tuple[ProviderMetadata, ...] = ()


class GetRealtimeCommercialTool:
    def __init__(self, provider: RealtimeCommercialProvider) -> None:
        self._provider = provider

    async def execute(self, query: RealtimeCommercialQuery) -> RealtimeCommercialToolResult:
        try:
            result = await self._provider.get_area_commercial_status(
                query.area_name_or_code.strip()
            )
        except AppError as exc:
            logger.warning("실시간 상권 정보 없이 진행 (code=%s)", exc.code)
            return RealtimeCommercialToolResult(
                status=ToolStatus.UNAVAILABLE,
                commercial=None,
                error=ToolError(
                    code="unavailable",
                    message="실시간 상권 정보를 가져오지 못했습니다.",
                    cause="timeout" if exc.code == "provider_timeout" else "upstream_error",
                    retryable=exc.retryable,
                ),
            )
        return RealtimeCommercialToolResult(
            status=(
                ToolStatus.NO_DATA
                if result.metadata.status is ProviderStatus.NO_DATA
                else ToolStatus.SUCCESS
            ),
            commercial=result.data,
            error=None,
            provider_metadata=(result.metadata,),
        )


__all__ = [
    "GetRealtimeCommercialTool",
    "RealtimeCommercialQuery",
    "RealtimeCommercialToolResult",
]
