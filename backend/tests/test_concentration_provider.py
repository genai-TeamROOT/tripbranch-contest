from __future__ import annotations

import httpx
import pytest

from app.errors import ProviderTimeoutError
from app.providers.concentration import (
    FakeConcentrationProvider,
    RealConcentrationProvider,
    map_concentration_response,
)


@pytest.mark.asyncio
async def test_fake_concentration_provider_uses_common_contract() -> None:
    result = (await FakeConcentrationProvider().get_forecast("11", "11110", "경복궁")).data

    assert result.area_code == "11"
    assert result.district_code == "11110"
    assert result.requested_place_name == "경복궁"
    assert result.forecasts[0].place_name == "경복궁"
    assert result.forecasts[0].concentration_rate == 42.0


def test_map_concentration_response_normalizes_known_fields() -> None:
    payload = {
        "response": {
            "body": {
                "items": {
                    "item": [
                        {"tAtsNm": "경복궁", "fcastYmd": "20260723", "cnctrRate": "47.5"}
                    ]
                }
            }
        }
    }

    result = map_concentration_response(
        payload,
        area_code="11",
        district_code="11110",
        requested_place_name="경복궁",
    )

    assert result.forecasts[0].forecast_date == "20260723"
    assert result.forecasts[0].concentration_rate == 47.5
    assert result.forecasts[0].raw_data["cnctrRate"] == "47.5"


@pytest.mark.asyncio
async def test_real_concentration_provider_sends_jongno_palace_query() -> None:
    seen_params: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.update(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "response": {
                    "header": {"resultCode": "0000", "resultMsg": "OK"},
                    "body": {
                        "items": {
                            "item": {
                                "tAtsNm": "경복궁",
                                "fcastYmd": "20260723",
                                "cnctrRate": 55,
                            }
                        }
                    },
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RealConcentrationProvider(api_key="dummy", client=client)
        result = (await provider.get_forecast("11", "11110", "경복궁")).data

    assert seen_params["areaCd"] == "11"
    assert seen_params["signguCd"] == "11110"
    assert seen_params["tAtsNm"] == "경복궁"
    assert seen_params["_type"] == "json"
    assert result.forecasts[0].concentration_rate == 55.0


@pytest.mark.asyncio
async def test_real_concentration_provider_returns_empty_forecasts_for_no_items() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "response": {
                    "header": {"resultCode": "0000", "resultMsg": "OK"},
                    "body": {"items": ""},
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RealConcentrationProvider(api_key="dummy", client=client)
        wrapped = await provider.get_forecast("11", "11110", "경복궁")

    assert wrapped.data.forecasts == ()
    assert wrapped.metadata.status.value == "no_data"


@pytest.mark.asyncio
async def test_real_concentration_provider_does_not_chain_sensitive_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RealConcentrationProvider(api_key="sensitive-key", client=client)
        with pytest.raises(ProviderTimeoutError) as exc_info:
            await provider.get_forecast("11", "11110", "경복궁")

    assert exc_info.value.__cause__ is None
