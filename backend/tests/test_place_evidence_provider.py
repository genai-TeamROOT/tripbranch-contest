"""취향 근거 Provider의 호출 생략 조건과 컷값 전달 테스트."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.domain.models import PlaceEvidenceMatch, PlaceEvidenceSnippet
from app.providers.contracts import ProviderSource, ProviderStatus
from app.providers.place_evidence import (
    DEFAULT_MATCH_COUNT,
    DEFAULT_MIN_SIMILARITY,
    PlaceEvidenceProvider,
)


class _RecordingEncoder:
    """torch 없이 도는 가짜 인코더 — 호출 여부만 센다."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def encode(self, text: str) -> Sequence[float]:
        self.calls.append(text)
        return [0.1] * 768


class _RecordingRepository:
    def __init__(self, matches: tuple[PlaceEvidenceMatch, ...] = ()) -> None:
        self.calls: list[dict[str, object]] = []
        self._matches = matches

    async def search_place_evidence(
        self,
        query_embedding: Sequence[float],
        candidate_content_ids: Sequence[str],
        *,
        match_count: int,
        min_similarity: float,
    ) -> tuple[PlaceEvidenceMatch, ...]:
        self.calls.append(
            {
                "embedding_len": len(query_embedding),
                "candidates": list(candidate_content_ids),
                "match_count": match_count,
                "min_similarity": min_similarity,
            }
        )
        return self._matches


def _match(content_id: str) -> PlaceEvidenceMatch:
    return PlaceEvidenceMatch(
        content_id=content_id,
        place_title=f"장소 {content_id}",
        avg_similarity=0.55,
        snippets=(
            PlaceEvidenceSnippet(
                source_text="혼자 조용히 있기 좋았다",
                source_url=None,
                similarity=0.55,
                published_at=None,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_cut_value_and_match_count_reach_the_repository() -> None:
    """컷값을 안 넘기면 RPC 기본값 0.0이 적용돼 필터가 조용히 사라진다."""
    encoder, repository = _RecordingEncoder(), _RecordingRepository((_match("a"),))
    provider = PlaceEvidenceProvider(encoder, repository)

    await provider.search("혼자 조용히 쉬고 싶어", ["a", "b"])

    assert repository.calls[0]["min_similarity"] == DEFAULT_MIN_SIMILARITY == 0.43
    assert repository.calls[0]["match_count"] == DEFAULT_MATCH_COUNT
    assert repository.calls[0]["embedding_len"] == 768


@pytest.mark.asyncio
async def test_result_is_keyed_by_content_id() -> None:
    """채점 측이 후보별로 바로 꺼내 쓸 수 있어야 한다."""
    provider = PlaceEvidenceProvider(
        _RecordingEncoder(), _RecordingRepository((_match("a"), _match("b")))
    )

    result = await provider.search("조용한 곳", ["a", "b"])

    assert set(result.data) == {"a", "b"}
    assert result.metadata.source is ProviderSource.SUPABASE_PLACE_EVIDENCE
    assert result.metadata.status is ProviderStatus.SUCCESS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "candidates"),
    [("", ["a"]), ("   ", ["a"]), ("조용한 곳", [])],
)
async def test_empty_input_skips_encoding_and_query(
    query: str, candidates: list[str]
) -> None:
    """취향을 말하지 않은 요청이 대부분이라, 여기서 안 걸러내면 모델 호출이 낭비된다."""
    encoder, repository = _RecordingEncoder(), _RecordingRepository()
    provider = PlaceEvidenceProvider(encoder, repository)

    result = await provider.search(query, candidates)

    assert encoder.calls == []
    assert repository.calls == []
    assert result.data == {}
    assert result.metadata.status is ProviderStatus.NO_DATA


@pytest.mark.asyncio
async def test_no_match_is_reported_as_no_data() -> None:
    """컷값을 넘긴 근거가 하나도 없으면 성공이 아니라 데이터 없음이다."""
    provider = PlaceEvidenceProvider(_RecordingEncoder(), _RecordingRepository(()))

    result = await provider.search("조용한 곳", ["a"])

    assert result.data == {}
    assert result.metadata.status is ProviderStatus.NO_DATA
