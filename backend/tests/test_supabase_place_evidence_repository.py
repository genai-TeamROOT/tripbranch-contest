"""취향 근거 벡터 검색(search_place_evidence RPC)의 요청 구성과 응답 변환 테스트."""

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


def _match(content_id: str, avg: float) -> dict[str, object]:
    return {
        "content_id": content_id,
        "place_title": f"장소 {content_id}",
        "avg_similarity": avg,
        "evidence": [
            {
                "source_text": "혼자 조용히 앉아 있기 좋았다",
                "source_url": "https://blog.test/1",
                "similarity": avg + 0.02,
                "published_at": "2026-05-01T00:00:00+00:00",
            },
            {
                "source_text": "사람이 적어서 편했다",
                "source_url": None,
                "similarity": avg - 0.02,
                "published_at": None,
            },
        ],
    }


async def _repository(handler) -> tuple[SupabasePlaceRepository, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SupabasePlaceRepository(_URL, _SECRET, client), client


@pytest.mark.asyncio
async def test_rpc_receives_all_four_parameters() -> None:
    """컷값이나 후보를 빠뜨리면 RPC 기본값(0.0)으로 필터가 안 걸린다."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[_match("a", 0.51)])

    repository, client = await _repository(handler)
    async with client:
        await repository.search_place_evidence(
            [0.1] * 768,
            ["a", "b"],
            match_count=3,
            min_similarity=0.43,
        )

    assert seen[0].url.path.endswith("/rpc/search_place_evidence")
    assert seen[0].headers["apikey"] == _SECRET
    assert "Authorization" not in seen[0].headers
    body = json.loads(seen[0].content)
    assert len(body["p_query_embedding"]) == 768
    assert body["p_candidate_content_ids"] == ["a", "b"]
    assert body["p_match_count"] == 3
    assert body["p_min_similarity"] == 0.43


@pytest.mark.asyncio
async def test_response_is_mapped_to_domain_types() -> None:
    """jsonb evidence 배열이 스니펫으로 풀리지 않으면 근거 문장을 만들 수 없다."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_match("a", 0.61)])

    repository, client = await _repository(handler)
    async with client:
        matches = await repository.search_place_evidence(
            [0.1] * 768, ["a"], match_count=3, min_similarity=0.43
        )

    assert len(matches) == 1
    assert matches[0].content_id == "a"
    assert matches[0].avg_similarity == pytest.approx(0.61)
    assert len(matches[0].snippets) == 2
    assert matches[0].snippets[0].source_url == "https://blog.test/1"
    assert matches[0].snippets[1].source_url is None
    assert matches[0].snippets[1].published_at is None


@pytest.mark.asyncio
async def test_candidate_limit_is_blocked_before_the_request() -> None:
    """500건 초과는 RPC가 거부한다 — 왕복 전에 막아 느린 쿼리를 원천 차단한다."""
    called = False

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=[])

    repository, client = await _repository(handler)
    async with client:
        with pytest.raises(SupabaseRepositoryError) as exc_info:
            await repository.search_place_evidence(
                [0.1] * 768,
                [str(i) for i in range(501)],
                match_count=3,
                min_similarity=0.43,
            )

    assert called is False
    assert "500건 이하" in str(exc_info.value.details)


@pytest.mark.asyncio
async def test_duplicate_candidates_are_collapsed() -> None:
    """중복 content_id가 상한 계산과 쿼리 비용을 부풀리지 않아야 한다."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[])

    repository, client = await _repository(handler)
    async with client:
        await repository.search_place_evidence(
            [0.1] * 768, ["a", "a", "b"], match_count=3, min_similarity=0.43
        )

    assert json.loads(seen[0].content)["p_candidate_content_ids"] == ["a", "b"]


@pytest.mark.asyncio
async def test_empty_candidates_skip_the_request() -> None:
    """하드 필터가 후보를 다 걸러낸 요청에서 빈 RPC를 부르지 않는다."""
    called = False

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=[])

    repository, client = await _repository(handler)
    async with client:
        assert await repository.search_place_evidence(
            [0.1] * 768, [], match_count=3, min_similarity=0.43
        ) == ()

    assert called is False


@pytest.mark.asyncio
async def test_non_list_payload_is_rejected() -> None:
    """RPC 계약이 바뀌어 dict가 오면 조용히 통과시키지 않는다."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    repository, client = await _repository(handler)
    async with client:
        with pytest.raises(SupabaseRepositoryError):
            await repository.search_place_evidence(
                [0.1] * 768, ["a"], match_count=3, min_similarity=0.43
            )
