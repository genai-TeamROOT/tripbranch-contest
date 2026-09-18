"""사진 검색 VLM 재랭커의 실패 처리와 응답 형식 테스트.

**이 파일의 주제는 "실패해도 예외를 올리지 않는다"이다.** 재랭킹은 보강이지
필수가 아니므로, 어떤 실패든 `None`으로 끝나고 호출 측이 임베딩 순서를 그대로
쓸 수 있어야 한다. 예외가 새어 나가면 사진 검색 전체가 죽는다.
"""

from __future__ import annotations

import httpx
import pytest
from google.genai import errors as genai_errors

from app.providers.gemini_vlm_rerank import GeminiPhotoReranker, RerankCandidate


class _FakeModels:
    """`client.aio.models`를 흉내 낸다."""

    def __init__(self, *, text: str | None = None, raises: Exception | None = None):
        self._text = text
        self._raises = raises
        self.configs: list[object] = []

    async def generate_content(self, *, model, contents, config):
        self.configs.append(config)
        if self._raises is not None:
            raise self._raises
        return type("R", (), {"text": self._text})()


def _reranker(monkeypatch, *, text=None, raises=None) -> tuple[GeminiPhotoReranker, _FakeModels]:
    models = _FakeModels(text=text, raises=raises)

    def _fake_client(**kwargs):
        aio = type("Aio", (), {"models": models})()
        return type("C", (), {"aio": aio})()

    monkeypatch.setattr(
        "app.providers.gemini_vlm_rerank.genai.Client", lambda **kw: _fake_client()
    )
    reranker = GeminiPhotoReranker(
        api_key="k", model_name="gemini-3.5-flash", timeout_seconds=10.0
    )
    return reranker, models


def _photo_client(status: int = 200) -> httpx.AsyncClient:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=b"\xff\xd8jpeg")

    return httpx.AsyncClient(transport=httpx.MockTransport(_handler))


def _candidates(*ids: str) -> list[RerankCandidate]:
    return [RerankCandidate(content_id=i, photo_url=f"https://x/{i}.jpg") for i in ids]


@pytest.mark.asyncio
async def test_reorders_by_score_and_keeps_embedding_order_on_ties(monkeypatch) -> None:
    """점수로 다시 세우고, 동점이면 임베딩 순서를 유지한다.

    사람이 매기는 값도 3단계뿐이라 동점이 흔하다. 그때 임의로 섞으면 이미
    확인된 순서를 근거 없이 버리게 된다.
    """
    reranker, _ = _reranker(monkeypatch, text="[1, 2, 1]")
    async with _photo_client() as client:
        reranker._http = client
        order = await reranker.rerank(
            query_image=b"jpeg", candidates=_candidates("a", "b", "c")
        )

    # b가 2점으로 1위, a와 c는 동점이라 원래 순서(a → c)를 지킨다.
    assert order == ("b", "a", "c")


@pytest.mark.asyncio
async def test_schema_pins_the_number_of_scores(monkeypatch) -> None:
    """응답 스키마로 칸 수를 고정한다.

    프롬프트로 부탁하면 모델이 한 칸 모자란 답을 낸다(TP-213). 형식으로 박아야
    실패가 사라진다.
    """
    reranker, models = _reranker(monkeypatch, text="[0, 1, 2]")
    async with _photo_client() as client:
        reranker._http = client
        await reranker.rerank(query_image=b"jpeg", candidates=_candidates("a", "b", "c"))

    schema = models.configs[0].response_schema
    assert schema.min_items == 3
    assert schema.max_items == 3


@pytest.mark.asyncio
async def test_timeout_returns_none_instead_of_raising(monkeypatch) -> None:
    reranker, _ = _reranker(monkeypatch, raises=httpx.TimeoutException("느림"))
    async with _photo_client() as client:
        reranker._http = client
        order = await reranker.rerank(
            query_image=b"jpeg", candidates=_candidates("a", "b")
        )
    assert order is None


@pytest.mark.asyncio
async def test_api_error_returns_none_instead_of_raising(monkeypatch) -> None:
    error = genai_errors.APIError(503, {"message": "unavailable"})
    reranker, _ = _reranker(monkeypatch, raises=error)
    async with _photo_client() as client:
        reranker._http = client
        order = await reranker.rerank(
            query_image=b"jpeg", candidates=_candidates("a", "b")
        )
    assert order is None


@pytest.mark.asyncio
async def test_unparsable_reply_returns_none(monkeypatch) -> None:
    reranker, _ = _reranker(monkeypatch, text="점수는 잘 모르겠어요")
    async with _photo_client() as client:
        reranker._http = client
        order = await reranker.rerank(
            query_image=b"jpeg", candidates=_candidates("a", "b")
        )
    assert order is None


@pytest.mark.asyncio
async def test_wrong_score_count_returns_none(monkeypatch) -> None:
    """칸 수가 어긋나면 조용히 잘못된 순서를 내지 않고 포기한다.

    스키마를 걸었으므로 여기 오면 안 되지만, 모델이 못 지켰을 때 엉뚱한 순서를
    내는 것이 가장 나쁘다 — 오류가 없어 알아챌 수가 없다.
    """
    reranker, _ = _reranker(monkeypatch, text="[1, 2]")
    async with _photo_client() as client:
        reranker._http = client
        order = await reranker.rerank(
            query_image=b"jpeg", candidates=_candidates("a", "b", "c")
        )
    assert order is None


@pytest.mark.asyncio
async def test_single_candidate_does_not_call_the_model(monkeypatch) -> None:
    """후보가 하나면 부르지 않는다. 바꿀 것이 없는데 돈만 쓴다."""
    reranker, models = _reranker(monkeypatch, text="[2]")
    async with _photo_client() as client:
        reranker._http = client
        order = await reranker.rerank(query_image=b"jpeg", candidates=_candidates("a"))
    assert order is None
    assert models.configs == []


@pytest.mark.asyncio
async def test_unfetchable_photos_drop_candidates_not_the_whole_call(monkeypatch) -> None:
    """사진을 못 받은 후보만 빠지고 나머지로 진행한다."""
    reranker, models = _reranker(monkeypatch, text="[2, 1]")

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("b.jpg"):
            return httpx.Response(404)
        return httpx.Response(200, content=b"\xff\xd8jpeg")

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
        reranker._http = client
        order = await reranker.rerank(
            query_image=b"jpeg", candidates=_candidates("a", "b", "c")
        )

    assert order == ("a", "c")
    assert models.configs[0].response_schema.min_items == 2


@pytest.mark.asyncio
async def test_too_few_fetchable_photos_returns_none(monkeypatch) -> None:
    """사진을 받은 후보가 두 곳 미만이면 부르지 않는다."""
    reranker, models = _reranker(monkeypatch, text="[2]")
    async with _photo_client(status=500) as client:
        reranker._http = client
        order = await reranker.rerank(
            query_image=b"jpeg", candidates=_candidates("a", "b", "c")
        )
    assert order is None
    assert models.configs == []
