from __future__ import annotations

import httpx
import pytest

from app.providers.holiday import FakeHolidayProvider, RealHolidayProvider

pytestmark = pytest.mark.asyncio


async def test_fake_holiday_provider_filters_month_and_exposes_holidays() -> None:
    result = (await FakeHolidayProvider().get_holidays(2026, 3)).data

    assert [entry.name for entry in result.entries] == ["삼일절"]
    assert result.holidays == result.entries
    assert result.provider == "fake_holiday"


async def test_real_holiday_provider_maps_xml_and_request_parameters() -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <response><header><resultCode>00</resultCode><resultMsg>NORMAL SERVICE.</resultMsg></header>
    <body><items>
      <item><dateKind>02</dateKind><dateName>삼일절</dateName><isHoliday>Y</isHoliday><locdate>20260301</locdate><seq>1</seq></item>
      <item><dateKind>02</dateKind><dateName>어린이날</dateName><isHoliday>Y</isHoliday><locdate>20260505</locdate><seq>1</seq></item>
    </items><numOfRows>100</numOfRows><pageNo>1</pageNo><totalCount>2</totalCount></body></response>"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["serviceKey"] == "dummy"
        assert request.url.params["solYear"] == "2026"
        assert request.url.params["solMonth"] == "03"
        assert request.url.params["numOfRows"] == "100"
        return httpx.Response(200, text=xml)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = (await RealHolidayProvider("dummy", client).get_holidays(2026, 3)).data

    assert len(result.entries) == 2
    assert result.entries[0].name == "삼일절"
    assert result.entries[0].is_holiday is True
    assert result.entries[1].is_holiday is True
    assert result.holidays == result.entries


async def test_holiday_provider_rejects_invalid_month_before_request() -> None:
    async with httpx.AsyncClient() as client:
        provider = RealHolidayProvider("dummy", client)
        with pytest.raises(ValueError, match="month"):
            await provider.get_holidays(2026, 13)
