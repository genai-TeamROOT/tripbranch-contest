"""place_associations 조회 헬퍼가 올바른 필터/정렬로 요청하는지 고정한다."""

from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from scripts.query_place_associations import get_associated_places


class TestGetAssociatedPlaces:
    @pytest.mark.asyncio
    async def test_from_content_id로_필터하고_rank순으로_정렬한다(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json=[
                    {"to_content_id": "200", "category": "관광지", "rank": 1, "base_ym": "202607"},
                    {"to_content_id": "300", "category": "음식", "rank": 2, "base_ym": "202607"},
                ],
            )

        transport = httpx.MockTransport(handler)
        settings = Settings(
            supabase_url="https://project.supabase.co",
            supabase_secret_key="sb_secret_test",
        )

        async with httpx.AsyncClient(transport=transport) as client:
            places = await get_associated_places(settings, "100", client=client)

        assert len(requests) == 1
        params = requests[0].url.params
        assert params.get("from_content_id") == "eq.100"
        assert params.get("order") == "rank.asc"
        assert "category" not in params

        assert len(places) == 2
        assert places[0].content_id == "200"
        assert places[0].rank == 1
        assert places[1].category == "음식"

    @pytest.mark.asyncio
    async def test_category를_주면_필터_파라미터가_붙는다(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json=[])

        transport = httpx.MockTransport(handler)
        settings = Settings(
            supabase_url="https://project.supabase.co",
            supabase_secret_key="sb_secret_test",
        )

        async with httpx.AsyncClient(transport=transport) as client:
            await get_associated_places(settings, "100", client=client, category="음식")

        assert requests[0].url.params.get("category") == "eq.음식"
