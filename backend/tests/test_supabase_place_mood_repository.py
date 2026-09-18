"""분위기 벡터 조회(search_place_mood RPC·place_mood_vectors)의 요청 구성과 응답 변환 테스트."""

from __future__ import annotations

import json

import httpx
import pytest

from app.repositories.supabase_places import (
    SupabasePlaceRepository,
    SupabaseRepositoryError,
)

_SECRET = "super-secret-service-key"
_URL = "https://example.supabase.co"


def _row(content_id: str, similarity: float) -> dict[str, object]:
    return {
        "content_id": content_id,
        "similarity": similarity,
        "axis_scores": {"calm": 0.12, "traditional": -0.08},
        "photo_count": 4,
    }


async def _repository(handler) -> tuple[SupabasePlaceRepository, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SupabasePlaceRepository(_URL, _SECRET, client), client


@pytest.mark.asyncio
async def test_rpc_receives_all_four_parameters() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[_row("a", 0.71)])

    repository, client = await _repository(handler)
    async with client:
        await repository.search_place_mood(
            [0.1] * 768,
            ["a", "b"],
            match_count=10,
            min_similarity=0.0,
        )

    body = json.loads(seen[0].content)
    assert seen[0].url.path.endswith("/rpc/search_place_mood")
    assert len(body["p_query_embedding"]) == 768
    assert body["p_candidate_content_ids"] == ["a", "b"]
    assert body["p_match_count"] == 10
    assert body["p_min_similarity"] == 0.0


@pytest.mark.asyncio
async def test_none_candidates_searches_everything() -> None:
    """None은 전체 검색이다. 빈 배열로 바뀌면 한 곳도 안 걸린다."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[_row("a", 0.71)])

    repository, client = await _repository(handler)
    async with client:
        matches = await repository.search_place_mood(
            [0.1] * 768, None, match_count=10, min_similarity=0.0
        )

    assert json.loads(seen[0].content)["p_candidate_content_ids"] is None
    assert len(matches) == 1


@pytest.mark.asyncio
async def test_empty_candidate_list_returns_nothing_without_calling() -> None:
    """빈 배열은 None과 다르다.

    후보를 좁히려다 전부 걸러진 호출이 전체 검색으로 둔갑하면, 지역 필터를
    통과하지 못한 장소가 추천에 섞인다.
    """
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=[])

    repository, client = await _repository(handler)
    async with client:
        matches = await repository.search_place_mood(
            [0.1] * 768, [], match_count=10, min_similarity=0.0
        )

    assert matches == ()
    assert called is False


@pytest.mark.asyncio
async def test_candidate_cap_fails_before_the_round_trip() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=[])

    repository, client = await _repository(handler)
    async with client:
        with pytest.raises(SupabaseRepositoryError):
            await repository.search_place_mood(
                [0.1] * 768,
                [str(index) for index in range(501)],
                match_count=10,
                min_similarity=0.0,
            )

    assert called is False


@pytest.mark.asyncio
async def test_duplicate_candidates_are_collapsed() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[])

    repository, client = await _repository(handler)
    async with client:
        await repository.search_place_mood(
            [0.1] * 768, ["a", "b", "a"], match_count=10, min_similarity=0.0
        )

    assert json.loads(seen[0].content)["p_candidate_content_ids"] == ["a", "b"]


@pytest.mark.asyncio
async def test_match_row_is_converted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_row("2946087", 0.83)])

    repository, client = await _repository(handler)
    async with client:
        matches = await repository.search_place_mood(
            [0.1] * 768, None, match_count=10, min_similarity=0.0
        )

    assert matches[0].content_id == "2946087"
    assert matches[0].similarity == pytest.approx(0.83)
    assert matches[0].profile.photo_count == 4
    assert matches[0].profile.axis_scores["calm"] == pytest.approx(0.12)


@pytest.mark.asyncio
async def test_profiles_request_omits_the_embedding_column() -> None:
    """embedding까지 읽으면 장소마다 768개 float이 실려 응답이 수 MB가 된다."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[_row("a", 0.0)])

    repository, client = await _repository(handler)
    async with client:
        await repository.find_mood_profiles(["a"])

    select = seen[0].url.params["select"]
    assert "embedding" not in select
    assert select == "content_id,axis_scores,photo_count"


@pytest.mark.asyncio
async def test_profiles_are_chunked() -> None:
    """in 필터가 URL에 실려서, 한 번에 다 넣으면 요청줄이 길어진다."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[])

    repository, client = await _repository(handler)
    async with client:
        await repository.find_mood_profiles([str(index) for index in range(450)])

    assert len(seen) == 3


@pytest.mark.asyncio
async def test_broken_axis_score_does_not_drop_the_place() -> None:
    """축 하나가 깨졌다고 장소를 버리지 않는다 — 남은 축으로도 정렬은 된다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "content_id": "a",
                    "axis_scores": {"calm": 0.12, "traditional": None},
                    "photo_count": 2,
                }
            ],
        )

    repository, client = await _repository(handler)
    async with client:
        profiles = await repository.find_mood_profiles(["a"])

    assert profiles["a"].axis_scores == {"calm": pytest.approx(0.12)}


@pytest.mark.asyncio
async def test_empty_content_ids_skips_the_request() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=[])

    repository, client = await _repository(handler)
    async with client:
        assert await repository.find_mood_profiles([]) == {}

    assert called is False
