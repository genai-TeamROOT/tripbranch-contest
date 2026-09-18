"""실시간 상권 Provider→Tool 상태 변환을 검증한다."""

from __future__ import annotations

import pytest

from app.domain.models import RealtimeCommercialResult
from app.errors import ProviderUnavailableError
from app.providers.contracts import ProviderSource, provider_result
from app.tools.contracts import ToolStatus
from app.tools.realtime_commercial import GetRealtimeCommercialTool, RealtimeCommercialQuery


class _SuccessProvider:
    async def get_area_commercial_status(self, area_name_or_code: str):
        return provider_result(
            RealtimeCommercialResult(
                area_name=area_name_or_code,
                area_code="POI076",
                area_activity_level="보통 시간대",
                observed_at=None,
                categories=(),
                provider="test",
            ),
            source=ProviderSource.FAKE_SEOUL_CITYDATA,
        )


class _UnavailableProvider:
    async def get_area_commercial_status(self, area_name_or_code: str):
        del area_name_or_code
        raise ProviderUnavailableError("서울시 실시간 상권")


@pytest.mark.asyncio
async def test_commercial_tool_returns_success_result() -> None:
    result = await GetRealtimeCommercialTool(_SuccessProvider()).execute(
        RealtimeCommercialQuery("POI076")
    )

    assert result.status is ToolStatus.SUCCESS
    assert result.commercial is not None
    assert result.commercial.area_code == "POI076"


@pytest.mark.asyncio
async def test_commercial_tool_normalizes_provider_failure() -> None:
    result = await GetRealtimeCommercialTool(_UnavailableProvider()).execute(
        RealtimeCommercialQuery("POI076")
    )

    assert result.status is ToolStatus.UNAVAILABLE
    assert result.error is not None
    assert result.error.code == "unavailable"
