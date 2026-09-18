"""후기로 답하는 INFO 질의(question_type=review_opinion) 테스트.

이 기능의 핵심 위험은 "근거가 그 장소 이야기가 아닌 것"이다. 검색이 찾아온 문장에
근처 가게 후기가 섞여 있고(`docs/근거-장소연결-오염-점검-20260914.md`), 유사도로는
갈리지 않아 LLM 선별이 유일한 방어선이다. 그래서 선별이 비면 답변을 만들지 않는다는
계약을 여기서 잠근다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import pytest

from app.agent_context.info_schemas import (
    InfoContextRequest,
    InfoContextResponse,
    PlaceInfoResult,
    ReviewEvidenceItem,
)
from app.domain.models import PlaceEvidenceSnippet
from app.domain.review_evidence import usable_snippets
from app.providers.contracts import ProviderSource, provider_result
from app.schemas import QuestionType
from app.services.runtime.agent_runtime import _filter_review_evidence
from app.services.runtime.info_response_transform import _to_review_sources


def _snippet(text: str, *, source_type: str = "naver_post", url: str | None = "https://b/1"):
    return PlaceEvidenceSnippet(
        source_text=text,
        source_url=url,
        similarity=0.5,
        published_at=datetime(2026, 5, 1),
        source_type=source_type,
    )


class _FakeLLM:
    """filter_review_evidence만 흉내 내는 최소 대역."""

    def __init__(self, keep: Sequence[int] | Exception) -> None:
        self._keep = keep
        self.calls: list[tuple[str, str, list[str]]] = []

    async def filter_review_evidence(
        self, *, place_name: str, specific_question: str, snippets: Sequence[str]
    ):
        self.calls.append((place_name, specific_question, list(snippets)))
        if isinstance(self._keep, Exception):
            raise self._keep
        return provider_result(tuple(self._keep), source=ProviderSource.GEMINI)


def _response(evidence: tuple[ReviewEvidenceItem, ...]) -> InfoContextResponse:
    return InfoContextResponse(
        request_id="r-1",
        status="success" if evidence else "no_data",
        result=PlaceInfoResult(
            status="success" if evidence else "no_data",
            question_type="review_opinion",
            requested_place_name="경복궁",
            resolved_place_name="경복궁",
            place_id="126508",
            review_evidence=evidence,
        ),
    )


def _request() -> InfoContextRequest:
    return InfoContextRequest(
        request_id="r-1",
        place_name="경복궁",
        place_context="explicit",
        question_type="review_opinion",
        specific_question="아이와 가기 좋대?",
    )


def _evidence(*texts: str) -> tuple[ReviewEvidenceItem, ...]:
    return tuple(
        ReviewEvidenceItem(text=text, source_url=f"https://b/{index}", source_type="naver_post")
        for index, text in enumerate(texts, start=1)
    )


# --- 규칙 필터(검색 직후) ---


def test_usable_snippets_drops_tour_overview() -> None:
    """관광 안내문은 후기가 아니고 걸 링크도 없다."""
    snippets = (
        _snippet(
            "조선의 법궁으로 1395년에 창건되었으며 근정전이 중심이다",
            source_type="tour_overview",
        ),
        _snippet("아이랑 같이 갔는데 넓어서 뛰어놀기 좋았어요 유모차도 무리 없었고요"),
    )
    kept = usable_snippets(snippets, limit=8)
    assert [s.source_type for s in kept] == ["naver_post"]


def test_usable_snippets_drops_fragments_and_headings() -> None:
    """읽어도 뜻이 서지 않는 조각은 요약을 망친다(2026-09-08 점검)."""
    snippets = (
        _snippet("많이 넓다"),
        _snippet("넓음 / 여유"),
        _snippet("공간 & 분위기"),
        _snippet("내부가 생각보다 넓어서 아이랑 다니기에 부담이 없었습니다"),
    )
    kept = usable_snippets(snippets, limit=8)
    assert [s.source_text for s in kept] == [
        "내부가 생각보다 넓어서 아이랑 다니기에 부담이 없었습니다"
    ]


def test_usable_snippets_respects_limit() -> None:
    snippets = tuple(
        _snippet(f"아이와 함께 다녀왔는데 정말 좋았습니다 {i}번째 후기예요") for i in range(10)
    )
    assert len(usable_snippets(snippets, limit=8)) == 8


# --- LLM 선별(답변·출처 앞) ---


@pytest.mark.asyncio
async def test_filter_keeps_only_selected_evidence() -> None:
    response = _response(_evidence("근처 식당 이야기", "경복궁 자체 이야기", "또 다른 식당"))
    llm = _FakeLLM(keep=[2])

    filtered = await _filter_review_evidence(response, info_request=_request(), llm=llm)

    assert [item.text for item in filtered.result.review_evidence] == ["경복궁 자체 이야기"]
    assert filtered.result.status == "success"
    assert llm.calls[0][0] == "경복궁"
    assert llm.calls[0][1] == "아이와 가기 좋대?"


@pytest.mark.asyncio
async def test_filter_returns_no_data_when_everything_is_dropped() -> None:
    """전부 남 이야기면 답을 만들지 않는다 — 지어내는 것보다 침묵이 낫다."""
    response = _response(_evidence("근처 맛집 후기", "근처 카페 후기"))

    filtered = await _filter_review_evidence(
        response, info_request=_request(), llm=_FakeLLM(keep=[])
    )

    assert filtered.result.review_evidence == ()
    assert filtered.result.status == "no_data"
    assert filtered.status == "no_data"


@pytest.mark.asyncio
async def test_filter_drops_evidence_when_llm_fails() -> None:
    """선별이 실패하면 검토되지 않은 근거로 답하지 않는다."""
    response = _response(_evidence("검토되지 않은 근거"))

    filtered = await _filter_review_evidence(
        response, info_request=_request(), llm=_FakeLLM(keep=RuntimeError("LLM 장애"))
    )

    assert filtered.result.status == "no_data"


@pytest.mark.asyncio
async def test_filter_ignores_out_of_range_indexes() -> None:
    response = _response(_evidence("하나뿐인 근거"))

    filtered = await _filter_review_evidence(
        response, info_request=_request(), llm=_FakeLLM(keep=[0, 1, 99])
    )

    assert [item.text for item in filtered.result.review_evidence] == ["하나뿐인 근거"]


@pytest.mark.asyncio
async def test_filter_skips_other_question_types() -> None:
    """후기 질의가 아니면 LLM을 부르지 않는다."""
    response = InfoContextResponse(
        request_id="r-1",
        status="success",
        result=PlaceInfoResult(
            status="success",
            question_type="facility",
            resolved_place_name="경복궁",
            fields={"restroom": "있음"},
        ),
    )
    llm = _FakeLLM(keep=[1])

    filtered = await _filter_review_evidence(response, info_request=_request(), llm=llm)

    assert filtered is response
    assert llm.calls == []


# --- 출처 링크 ---


def test_review_sources_dedupe_and_cap() -> None:
    result = PlaceInfoResult(
        status="success",
        question_type="review_opinion",
        review_evidence=(
            ReviewEvidenceItem(text="a", source_url="https://b/1", source_type="naver_post"),
            ReviewEvidenceItem(text="b", source_url="https://b/1", source_type="naver_post"),
            ReviewEvidenceItem(text="c", source_url="https://b/2", source_type="google_review"),
            ReviewEvidenceItem(text="d", source_url="https://b/3"),
            ReviewEvidenceItem(text="e", source_url="https://b/4"),
        ),
    )

    sources = _to_review_sources(result)

    assert [source.url for source in sources] == ["https://b/1", "https://b/2", "https://b/3"]
    # 화면이 인용으로 그리므로 문장도 함께 실려야 한다.
    assert [source.text for source in sources] == ["a", "c", "d"]


def test_review_sources_keep_evidence_without_link() -> None:
    """링크가 없어도 인용은 근거다 — 링크는 더 읽고 싶을 때 쓰는 것이다."""
    result = PlaceInfoResult(
        status="success",
        question_type="review_opinion",
        review_evidence=(
            ReviewEvidenceItem(text="아이랑 가기 좋았어요", source_url=None),
            ReviewEvidenceItem(text="주차가 넉넉했어요", source_url="   "),
        ),
    )

    sources = _to_review_sources(result)

    assert [source.text for source in sources] == ["아이랑 가기 좋았어요", "주차가 넉넉했어요"]
    assert [source.url for source in sources] == [None, None]


def test_question_type_enum_has_review_opinion() -> None:
    """A의 enum과 C의 Literal이 같은 값을 써야 payload가 그대로 실린다."""
    assert QuestionType.REVIEW_OPINION.value == "review_opinion"
