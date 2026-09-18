import pytest

from app.providers.contracts import ProviderSource, ProviderStatus
from app.providers.holiday import FakeHolidayProvider
from app.tools.contracts import ToolStatus
from app.tools.holiday import GetHolidaysTool, HolidayQuery


@pytest.mark.asyncio
async def test_holiday_tool_distinguishes_success_and_no_data() -> None:
    tool = GetHolidaysTool(FakeHolidayProvider())

    success = await tool.execute(HolidayQuery(2026, 3))
    no_data = await tool.execute(HolidayQuery(2026, 7))

    assert success.status is ToolStatus.SUCCESS
    assert no_data.status is ToolStatus.NO_DATA
    assert no_data.holidays is not None
    assert no_data.provider_metadata[0].source is ProviderSource.FAKE_HOLIDAY
    assert no_data.provider_metadata[0].status is ProviderStatus.NO_DATA
    assert no_data.provider_metadata[0].retrieved_at.tzinfo is not None
