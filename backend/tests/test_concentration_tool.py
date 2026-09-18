import pytest

from app.providers.concentration import FakeConcentrationProvider
from app.providers.contracts import ProviderSource, ProviderStatus
from app.tools.concentration import ConcentrationQuery, GetConcentrationTool
from app.tools.contracts import ToolStatus


@pytest.mark.asyncio
async def test_concentration_tool_returns_common_metadata() -> None:
    result = await GetConcentrationTool(FakeConcentrationProvider()).execute(
        ConcentrationQuery("11", "11110", "경복궁")
    )

    assert result.status is ToolStatus.SUCCESS
    assert result.concentration is not None
    assert result.provider_metadata[0].source is ProviderSource.FAKE_CONCENTRATION
    assert result.provider_metadata[0].status is ProviderStatus.SUCCESS
    assert result.provider_metadata[0].retrieved_at.tzinfo is not None
