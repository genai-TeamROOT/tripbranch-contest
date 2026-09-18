from __future__ import annotations

import httpx
import pytest

from app.repositories.supabase_places import SupabasePlaceRepository


@pytest.mark.asyncio
async def test_find_preference_tags_groups_rows_in_display_order() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "content_id": "126499",
                    "preference_code": "nature",
                    "preference_label": "자연을 즐기기 좋은",
                    "display_rank": 1,
                    "mention_count": 16,
                },
                {
                    "content_id": "126499",
                    "preference_code": "walk",
                    "preference_label": "산책하기 좋은",
                    "display_rank": 2,
                    "mention_count": 13,
                },
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        repository = SupabasePlaceRepository("https://example.supabase.co", "service-key", client)
        result = await repository.find_preference_tags(["126499"])

    assert seen[0].url.path.endswith("/rest/v1/place_preference_tags")
    assert seen[0].url.params["limit"] == "33"
    assert [row["preference_code"] for row in result["126499"]] == ["nature", "walk"]
    assert result["126499"][0]["mention_count"] == 16
