"""서울시 공영주차장 최신 현황 Tool."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.models import MunicipalParkingStatus
from app.errors import AppError
from app.providers.contracts import ProviderMetadata, ProviderStatus
from app.providers.protocols import MunicipalParkingProvider
from app.tools.contracts import ToolError, ToolStatus


@dataclass(frozen=True)
class MunicipalParkingQuery:
    district: str


@dataclass(frozen=True)
class MunicipalParkingToolResult:
    status: ToolStatus
    lots: tuple[MunicipalParkingStatus, ...]
    error: ToolError | None
    provider_metadata: tuple[ProviderMetadata, ...] = ()


class GetMunicipalParkingTool:
    def __init__(self, provider: MunicipalParkingProvider) -> None:
        self._provider = provider

    async def execute(self, query: MunicipalParkingQuery) -> MunicipalParkingToolResult:
        try:
            result = await self._provider.get_district_parking(query.district.strip())
        except AppError as exc:
            return MunicipalParkingToolResult(
                status=ToolStatus.UNAVAILABLE,
                lots=(),
                error=ToolError(
                    code="unavailable",
                    message="공영주차장 실시간 정보를 가져오지 못했습니다.",
                    cause="timeout" if exc.code == "provider_timeout" else "upstream_error",
                    retryable=exc.retryable,
                ),
            )
        return MunicipalParkingToolResult(
            status=(
                ToolStatus.NO_DATA
                if result.metadata.status is ProviderStatus.NO_DATA
                else ToolStatus.SUCCESS
            ),
            lots=result.data,
            error=None,
            provider_metadata=(result.metadata,),
        )


__all__ = ["GetMunicipalParkingTool", "MunicipalParkingQuery", "MunicipalParkingToolResult"]
