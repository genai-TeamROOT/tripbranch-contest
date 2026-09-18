"""서울시 실시간 도시데이터를 INFO 경로에서 안전하게 호출한다."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.models import RealtimeCityDataResult
from app.errors import AppError
from app.providers.contracts import ProviderMetadata, ProviderStatus
from app.providers.protocols import RealtimeCityDataProvider
from app.tools.contracts import ToolError, ToolStatus


@dataclass(frozen=True)
class RealtimeCityDataQuery:
    area_name_or_code: str


@dataclass(frozen=True)
class RealtimeCityDataToolResult:
    status: ToolStatus
    citydata: RealtimeCityDataResult | None
    error: ToolError | None
    provider_metadata: tuple[ProviderMetadata, ...] = ()


class GetRealtimeCityDataTool:
    def __init__(self, provider: RealtimeCityDataProvider) -> None:
        self._provider = provider

    async def execute(self, query: RealtimeCityDataQuery) -> RealtimeCityDataToolResult:
        try:
            result = await self._provider.get_area_citydata(query.area_name_or_code.strip())
        except AppError as exc:
            return RealtimeCityDataToolResult(
                status=ToolStatus.UNAVAILABLE,
                citydata=None,
                error=ToolError(
                    code="unavailable",
                    message="실시간 도시데이터를 가져오지 못했습니다.",
                    cause="timeout" if exc.code == "provider_timeout" else "upstream_error",
                    retryable=exc.retryable,
                ),
            )
        return RealtimeCityDataToolResult(
            status=(
                ToolStatus.NO_DATA
                if result.metadata.status is ProviderStatus.NO_DATA
                else ToolStatus.SUCCESS
            ),
            citydata=result.data,
            error=None,
            provider_metadata=(result.metadata,),
        )
