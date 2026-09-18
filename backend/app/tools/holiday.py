"""공휴일 Provider를 공통 Tool 계약으로 노출한다."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.domain.models import HolidayResult
from app.errors import AppError
from app.providers.contracts import ProviderMetadata, ProviderStatus
from app.providers.protocols import HolidayProvider
from app.tools.contracts import ToolError, ToolStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HolidayQuery:
    year: int
    month: int | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.year <= 9999:
            raise ValueError("year는 1~9999 범위여야 합니다.")
        if self.month is not None and not 1 <= self.month <= 12:
            raise ValueError("month는 1~12 범위여야 합니다.")


@dataclass(frozen=True)
class HolidayToolResult:
    status: ToolStatus
    holidays: HolidayResult | None
    error: ToolError | None
    warnings: tuple[str, ...] = ()
    provider_metadata: tuple[ProviderMetadata, ...] = ()


class GetHolidaysTool:
    def __init__(self, provider: HolidayProvider) -> None:
        self._provider = provider

    async def execute(self, query: HolidayQuery) -> HolidayToolResult:
        try:
            result = await self._provider.get_holidays(query.year, query.month)
        except AppError as exc:
            # 여기서 삼킨 오류는 200 응답으로 나가므로 로그가 유일한 흔적이다.
            logger.warning(
                "공휴일 정보 없이 진행 (code=%s, provider=%s, details=%s)",
                exc.code,
                exc.provider,
                exc.details,
            )
            return HolidayToolResult(
                status=ToolStatus.UNAVAILABLE,
                holidays=None,
                error=ToolError(
                    code="unavailable",
                    message="공휴일 정보를 가져오지 못했습니다.",
                    cause="timeout" if exc.code == "provider_timeout" else "upstream_error",
                    retryable=exc.retryable,
                ),
            )

        status = (
            ToolStatus.NO_DATA
            if result.metadata.status is ProviderStatus.NO_DATA
            else ToolStatus.SUCCESS
        )
        return HolidayToolResult(
            status=status,
            holidays=result.data,
            error=None,
            provider_metadata=(result.metadata,),
        )
