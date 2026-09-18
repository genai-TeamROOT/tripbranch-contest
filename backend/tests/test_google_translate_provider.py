from __future__ import annotations

import httpx
import pytest

from app.errors import ProviderUnavailableError
from app.providers.google_translate import GoogleTranslateProvider


@pytest.mark.asyncio
async def test_translate_many_sends_one_ordered_request_and_unescapes_html() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        seen["json"] = request.content.decode()
        return httpx.Response(
            200,
            json={
                "data": {
                    "translations": [
                        {"translatedText": "Hello &amp; welcome"},
                        {"translatedText": "Open until 6 PM"},
                    ]
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = GoogleTranslateProvider(api_key="test-key", client=client, timeout_seconds=1)
        result = await provider.translate_many(
            ["안녕하세요", "18시까지 운영"], source_language="ko", target_language="en"
        )

    assert result == ["Hello & welcome", "Open until 6 PM"]
    assert seen["params"] == {"key": "test-key"}
    assert '"source":"ko"' in str(seen["json"])
    assert '"target":"en"' in str(seen["json"])


@pytest.mark.asyncio
async def test_translate_many_maps_api_error_to_provider_unavailable() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(403, json={"error": {"message": "API key invalid"}})
        )
    ) as client:
        provider = GoogleTranslateProvider(api_key="bad-key", client=client, timeout_seconds=1)
        with pytest.raises(ProviderUnavailableError, match="Google Cloud Translation"):
            await provider.translate_many(["hello"], source_language="en", target_language="ko")
