"""후기 질의가 C에서 벡터 검색으로 갈라지는지 잠근다.

여기서 확인하는 것은 세 가지다.

1. `review_opinion`이 TourAPI 상세 조회 대신 후기 검색으로 간다.
2. 저장소에서 해석되지 않아 `place_id`가 없는 장소는 검색을 아예 하지 않는다 —
   임베딩이 있을 리 없는데 RPC를 부르면 왕복만 버린다.
3. 검색 Provider가 없으면(기능 스위치 off, 인코더 미설치) 기존 상세 조회로 흘러간다.
   "후기를 못 찾았다"로 퇴보시키지 않는다는 뜻이다 — 개요 질문(general_info)으로
   바꿔 답하므로 A도 후기를 봤다고 말하지 않는다.
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
    PlaceDetails,
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


_OVERVIEW = "조선 왕조의 법궁으로 1395년에 창건되었다."


class _OverviewDetailProvider:
    """경복궁 상세를 개요와 함께 돌려주는 대역.

    FakePlaceProvider는 경복궁을 모른다 — 그대로 쓰면 상세 조회가 no_data로 끝나,
    "개요로 답한다"를 확인하려는 테스트가 개요 없이 통과한다(조용한 fake).
    """

    async def find_details_by_name(
        self, name: str, region_code: str | None = None, district_code: str | None = None
    ):
        del region_code, district_code
        return provider_result(
            PlaceDetails(
                content_id="126508",
                content_type_id="12",
                title=name,
                address="서울특별시 종로구 사직로 161",
                overview=_OVERVIEW,
                homepage=None,
                telephone=None,
                operating_hours=None,
                rest_date=None,
                raw_common={},
                raw_intro={},
                provider="fake_place",
            ),
            source=ProviderSource.FAKE_PLACE,
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
            place_detail=GetPlaceDetailTool(_OverviewDetailProvider()),
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
    assert response.result.question_type == "general_info"


@pytest.mark.asyncio
async def test_without_provider_falls_back_to_place_detail() -> None:
    """기능이 꺼진 환경에서도 기존 소개글 답변은 그대로 나간다."""
    response = await _service(evidence=None).fetch_info_context(_request())

    assert response.result.review_evidence == ()
    # 개요 질문으로 바뀌어 소개글이 답이 된다. 예전에는 review_opinion으로 남아
    # 필드 추출에 그 분기가 없어 fields가 비고 no_data가 됐다.
    assert response.result.question_type == "general_info"
    assert response.status == "success"
    assert response.result.fields["overview"] == _OVERVIEW


@pytest.mark.asyncio
async def test_without_provider_reply_does_not_claim_reviews_were_checked() -> None:
    """검색이 돌지 않았는데 "후기에서 확인하지 못했어요"라고 말하지 않는다.

    취향 스위치(`taste_evidence_enabled`)나 `review_answer_enabled`가 꺼지면
    factory가 Provider를 None으로 준다. 같은 질문을 Provider가 있고 근거가
    없는 경우(아래 대조)에는 그 문장이 나오므로, 차이가 폴백 때문임이 드러난다.
    """
    from app.services.runtime.response_composer import compose_place_info_message

    fallback = await _service(evidence=None).fetch_info_context(_request())
    searched = await _service(evidence=_RecordingEvidenceProvider([])).fetch_info_context(
        _request()
    )

    fallback_message = compose_place_info_message(
        fallback, specific_question="아이와 가기 좋대?"
    )
    assert "후기" not in fallback_message
    assert _OVERVIEW in fallback_message
    # 대조: 실제로 검색했는데 못 찾은 경우에만 후기 문장이 나온다.
    assert "후기에서" in compose_place_info_message(
        searched, specific_question="아이와 가기 좋대?"
    )
