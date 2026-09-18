"""app.schedule.associations.fetch_co_visited_hints() 회귀 테스트.

place_associations(D-088)에서 SCHEDULE 후보 집합 안의 co-visit 쌍만 골라오는
조회 함수다. 이 파일은 요청 파라미터 모양(양쪽 컬럼 in.() 필터)과, 후보가
2개 미만일 때 네트워크 호출 자체를 생략하는 단락 회로를 고정한다.
"""

from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.schedule.associations import CoVisitedHint, fetch_co_visited_hints


def _settings() -> Settings:
    return Settings(
        supabase_url="https://project.supabase.co", supabase_secret_key="sb_secret_test"
    )


class TestFetchCoVisitedHints:
    @pytest.mark.asyncio
    async def test_from_to_컬럼_둘_다_후보_집합으로_필터한다(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json=[
                    {"from_content_id": "A", "to_content_id": "B", "rank": 1},
                    {"from_content_id": "B", "to_content_id": "A", "rank": 2},
                ],
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            hints = await fetch_co_visited_hints(["A", "B", "C"], _settings(), client=client)

        assert len(requests) == 1
        params = requests[0].url.params
        assert params.get("from_content_id") == "in.(A,B,C)"
        assert params.get("to_content_id") == "in.(A,B,C)"
        assert params.get("order") == "rank.asc"

        assert hints == [
            CoVisitedHint(from_place_id="A", to_place_id="B", rank=1),
            CoVisitedHint(from_place_id="B", to_place_id="A", rank=2),
        ]

    @pytest.mark.asyncio
    async def test_후보가_2개_미만이면_네트워크_호출을_생략한다(self) -> None:
        call_count = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            return httpx.Response(200, json=[])

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            empty_result = await fetch_co_visited_hints([], _settings(), client=client)
            single_result = await fetch_co_visited_hints(["only-one"], _settings(), client=client)

        assert empty_result == []
        assert single_result == []
        assert call_count["n"] == 0

    @pytest.mark.asyncio
    async def test_supabase_url이_비어있으면_네트워크_호출을_생략한다(self) -> None:
        call_count = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            return httpx.Response(200, json=[])

        transport = httpx.MockTransport(handler)
        settings = Settings(supabase_url="", supabase_secret_key="")
        async with httpx.AsyncClient(transport=transport) as client:
            result = await fetch_co_visited_hints(["A", "B"], settings, client=client)

        assert result == []
        assert call_count["n"] == 0

    @pytest.mark.asyncio
    async def test_중복_id는_한_번만_필터에_실린다(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json=[])

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            await fetch_co_visited_hints(["A", "A", "B"], _settings(), client=client)

        # set()으로 중복 제거 후 정렬되므로 "A,B" 순서로 고정된다.
        assert requests[0].url.params.get("from_content_id") == "in.(A,B)"
