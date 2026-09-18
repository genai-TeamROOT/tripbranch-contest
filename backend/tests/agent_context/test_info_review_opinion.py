"""후기 질의가 C에서 벡터 검색으로 갈라지는지 잠근다.

여기서 확인하는 것은 세 가지다.

1. `review_opinion`이 TourAPI 상세 조회 대신 후기 검색으로 간다.
2. 저장소에서 해석되지 않아 `place_id`가 없는 장소는 검색을 아예 하지 않는다 —
   임베딩이 있을 리 없는데 RPC를 부르면 왕복만 버린다.
3. 검색 Provider가 없으면(기능 스위치 off, 인코더 미설치) 기존 상세 조회로 흘러간다.
   "후기를 못 찾았다"로 퇴보시키지 않는다는 뜻이다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.agent_context.info_schemas import InfoContextRequest
from app.agent_context.service import ContextService, ContextTools
from app.domain.models import (
    GeocodeResult,
    PlaceEvidenceMatch,
    PlaceEvidenceSnippet,
    StoredPlaceLocation,
)
from app.providers.contracts import ProviderSource, ProviderStatus, provider_result
from app.providers.holiday import FakeHolidayProvider
from app.providers.stub import FakePlaceProvider, FakeWeatherProvider
from app.repositories.fake_places import FakePlaceLocationRepository
from app.tools.holiday import GetHolidaysTool
from app.tools.nearby_place_details import NearbyPlaceDetailsTool
from app.tools.place_detail import GetPlaceDetailTool
from app.tools.resolve_location import ResolveLocationTool
from app.tools.weather_forecast import GetWeatherForecastTool

_STORED_PLACE = StoredPlaceLocation(
    content_id="126508",
    title="경복궁",
    address="서울특별시 종로구 사직로 161",
    latitude=37.5796,
    longitude=126.977,
    district_code="110",
)


class _Geocoding:
    async def geocode(self, location_query: str, *, use_alias: bool = True):
        del use_alias
        return provider_result(
            GeocodeResult(
                query=location_query,
                resolved_name=location_query,
                latitude=37.5796,
                longitude=126.977,
            ),
            source=ProviderSource.FAKE_GEOCODING,
        )


class _RecordingEvidenceProvider:
    def __init__(self, snippets: Sequence[PlaceEvidenceSnippet] = ()) -> None:
        self.calls: list[tuple[str, str, int, float]] = []
        self._snippets = tuple(snippets)

    async def search_one_place(
        self, query: str, content_id: str, *, match_count: int, min_similarity: float
    ):
        self.calls.append((query, content_id, match_count, min_similarity))
        match = (
            PlaceEvidenceMatch("126508", "경복궁", 0.5, self._snippets)
            if self._snippets
            else None
        )
        return provider_result(
            match,
            source=ProviderSource.SUPABASE_PLACE_EVIDENCE,
            status=ProviderStatus.SUCCESS if match else ProviderStatus.NO_DATA,
        )


def _snippet(text: str, *, source_type: str = "naver_post") -> PlaceEvidenceSnippet:
    return PlaceEvidenceSnippet(
        source_text=text,
        source_url="https://blog.example/1",
        similarity=0.52,
        published_at=datetime(2026, 5, 1),
        source_type=source_type,
    )


def _service(
    *,
    evidence: _RecordingEvidenceProvider | None,
    stored: bool = True,
) -> ContextService:
    place_provider = FakePlaceProvider()
    return ContextService(
        ContextTools(
            location=ResolveLocationTool(
                _Geocoding(),
                place_repository=FakePlaceLocationRepository(
                    (_STORED_PLACE,) if stored else ()
                ),
            ),
            places=NearbyPlaceDetailsTool(place_provider, place_provider),
            place_detail=GetPlaceDetailTool(place_provider),
            weather=GetWeatherForecastTool(FakeWeatherProvider()),
            holidays=GetHolidaysTool(FakeHolidayProvider()),
            place_evidence=evidence,
        ),
        candidate_limit=10,
        clock=lambda: datetime(2026, 9, 14, 14, 0, tzinfo=ZoneInfo("Asia/Seoul")),
    )


def _request() -> InfoContextRequest:
    return InfoContextRequest(
        request_id="review-opinion-1",
        place_name="경복궁",
        place_context="explicit",
        question_type="review_opinion",
        specific_question="아이와 가기 좋대?",
    )


@pytest.mark.asyncio
async def test_review_question_searches_evidence_for_that_place() -> None:
    evidence = _RecordingEvidenceProvider(
        [_snippet("아이랑 같이 갔는데 넓어서 뛰어놀기 좋았어요 유모차도 무리 없었습니다")]
    )

    response = await _service(evidence=evidence).fetch_info_context(_request())

    assert len(evidence.calls) == 1
    query, content_id, match_count, _ = evidence.calls[0]
    # 질의는 질문 원문 그대로다 — 정규화보다 나았다(2026-09-14 실측).
    assert query == "아이와 가기 좋대?"
    assert content_id == "126508"
    assert match_count == 8
    assert response.result.status == "success"
    assert len(response.result.review_evidence) == 1
    assert response.result.review_evidence[0].source_url == "https://blog.example/1"
    # 후기 경로는 TourAPI 필드를 채우지 않는다.
    assert response.result.fields == {}


@pytest.mark.asyncio
async def test_tour_overview_is_not_offered_as_review_evidence() -> None:
    """관광 안내문은 기존 경로가 쓰는 텍스트라 후기 근거로 싣지 않는다."""
    evidence = _RecordingEvidenceProvider(
        [_snippet("조선 왕조의 법궁으로 1395년에 창건되었다", source_type="tour_overview")]
    )

    response = await _service(evidence=evidence).fetch_info_context(_request())

    assert response.result.review_evidence == ()
    assert response.result.status == "no_data"


@pytest.mark.asyncio
async def test_no_evidence_ends_as_no_data() -> None:
    evidence = _RecordingEvidenceProvider([])

    response = await _service(evidence=evidence).fetch_info_context(_request())

    assert response.result.status == "no_data"
    assert response.result.review_evidence == ()


@pytest.mark.asyncio
async def test_place_without_stored_id_skips_the_search() -> None:
    """좌표로만 해석된 장소는 임베딩이 없다 — 부르지 않는다."""
    evidence = _RecordingEvidenceProvider([_snippet("아무 근거나")])

    response = await _service(evidence=evidence, stored=False).fetch_info_context(_request())

    assert evidence.calls == []
    # 기존 상세 조회 경로로 흘러가 그쪽 형식으로 답한다.
    assert response.result.review_evidence == ()


@pytest.mark.asyncio
async def test_without_provider_falls_back_to_place_detail() -> None:
    """기능이 꺼진 환경에서도 기존 소개글 답변은 그대로 나간다."""
    response = await _service(evidence=None).fetch_info_context(_request())

    assert response.result.review_evidence == ()
    assert response.result.question_type == "review_opinion"
