"""RealRecommendationProvider 단위 테스트.

D의 실제 recommendation_pipeline을 호출하되, Tool을 직접 호출하지 않는
run_recommendation_pipeline_from_context() 경로만 쓰므로 외부 API 없이 순수하게
테스트 가능하다.
"""

from __future__ import annotations

import pytest

from app.agent_context.enrichment_schemas import (
    CandidateEnrichmentResponse,
    CandidateEnrichmentResult,
)
from app.agent_context.schemas import ContextError, DistrictScope
from app.domain.models import WeatherCondition
from app.domain.travel_route import RouteSource, RouteStatus, TravelMode, TravelRoute
from app.errors import AppError
from app.place_search_policy import WALKING_SPEED_KM_PER_MINUTE
from app.schemas import (
    ConcentrationIntent,
    RecommendationResponse,
    StatedWeather,
    Transport,
    UserConditions,
    WeatherIntent,
)
from app.services.runtime import real_recommendation_provider as module
from app.services.runtime.context_schemas import (
    ContextValue,
    Coordinates,
    PlaceCandidate,
    RecommendationContext,
    ResolvedLocation,
)
from app.services.runtime.real_recommendation_provider import RealRecommendationProvider
from app.tools.recommendation_cards import PhotoAttribution


def _context(*, place_ids: list[str]) -> RecommendationContext:
    return RecommendationContext(
        location=ContextValue(
            status="success",
            data=ResolvedLocation(
                requested_query="경복궁",
                resolved_name="경복궁",
                source="query",
                location=Coordinates(latitude=37.5796, longitude=126.9770),
            ),
        ),
        places=ContextValue(
            status="success",
            data=[
                PlaceCandidate(
                    place_id=place_id,
                    name=f"장소-{place_id}",
                    category="cafe",
                    location=Coordinates(latitude=37.58, longitude=126.978),
                    operating_hours_raw="09:00~22:00",
                )
                for place_id in place_ids
            ],
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        (DistrictScope(district_code="680", district_name="강남구"), True),
        (None, False),
    ],
)
async def test_rerank_passes_district_scope_to_both_second_passes(
    monkeypatch: pytest.MonkeyPatch,
    scope: DistrictScope | None,
    expected: bool,
) -> None:
    """2차가 1차와 같은 가중치 규칙을 쓰려면 이 배선이 있어야 한다(1.9.0).

    파이프라인 함수에 직접 넘기는 테스트로는 이 줄이 지워져도 안 잡힌다 —
    되돌려 확인했다. 그래서 provider가 context에서 실제로 뽑아 넘기는지를
    여기서 따로 못 박는다. 날씨 판정·기준점 이름과 같은 자리다.
    """
    captured: dict[str, object] = {}

    async def _fake_concentration_rerank(
        first_pass,
        weather_condition,
        concentration,
        *,
        seek,
        weather_reason=None,
        origin_name=None,
        district_scoped=False,
    ):
        captured["concentration"] = district_scoped
        return first_pass

    async def _fake_co_visited_rerank(
        first_pass,
        co_visited_pairs,
        weather_condition,
        *,
        weather_reason=None,
        origin_name=None,
        district_scoped=False,
    ):
        captured["co_visited"] = district_scoped
        return first_pass

    monkeypatch.setattr(module, "rerank_with_concentration", _fake_concentration_rerank)
    monkeypatch.setattr(module, "rerank_with_co_visited", _fake_co_visited_rerank)

    provider = RealRecommendationProvider()
    conditions = UserConditions(concentration_intent=ConcentrationIntent.AVOID)
    context = _context(place_ids=["a"]).model_copy(update={"district_scope": scope})

    await provider.rerank_with_concentration(
        conditions, context, _empty_first_pass(), _unavailable_concentration()
    )
    await provider.rerank_with_co_visited(conditions, context, _empty_first_pass(), [])

    assert captured["concentration"] is expected
    assert captured["co_visited"] is expected


@pytest.mark.asyncio
async def test_recommend_returns_response_for_valid_context() -> None:
    provider = RealRecommendationProvider()
    conditions = UserConditions(max_travel_time=30)
    context = _context(place_ids=["a", "b"])

    result = await provider.recommend(conditions, context, excluded_place_ids=[])

    all_items = [*result.recommendations, *result.unverified_recommendations]
    assert {item.place_id for item in all_items} == {"a", "b"}


@pytest.mark.asyncio
async def test_recommend_excludes_given_place_ids() -> None:
    provider = RealRecommendationProvider()
    conditions = UserConditions()
    context = _context(place_ids=["a", "b", "c"])

    result = await provider.recommend(conditions, context, excluded_place_ids=["a", "b"])

    all_items = [*result.recommendations, *result.unverified_recommendations]
    assert {item.place_id for item in all_items} == {"c"}


@pytest.mark.asyncio
async def test_recommend_respects_limit_parameter() -> None:
    """SCHEDULE-03: limit을 넘기면 그 개수만큼만 반환돼야 한다."""
    provider = RealRecommendationProvider()
    conditions = UserConditions()
    context = _context(place_ids=[f"p{i}" for i in range(10)])

    result = await provider.recommend(conditions, context, excluded_place_ids=[], limit=10)

    all_items = [*result.recommendations, *result.unverified_recommendations]
    assert len(all_items) == 10


@pytest.mark.asyncio
async def test_recommend_defaults_to_five_when_limit_not_given() -> None:
    """limit을 안 넘기면 기존 RECOMMEND 흐름과 동일하게 5개로 제한돼야 한다."""
    provider = RealRecommendationProvider()
    conditions = UserConditions()
    context = _context(place_ids=[f"p{i}" for i in range(10)])

    result = await provider.recommend(conditions, context, excluded_place_ids=[])

    all_items = [*result.recommendations, *result.unverified_recommendations]
    assert len(all_items) == 5


@pytest.mark.asyncio
async def test_provider_merges_multiple_prepared_batches() -> None:
    provider = RealRecommendationProvider()
    conditions = UserConditions()
    visit_at = module.datetime.now(module._KST)
    first = await provider.prepare(
        conditions,
        _context(place_ids=["a"]),
        excluded_place_ids=[],
        visit_at=visit_at,
    )
    second = await provider.prepare(
        conditions,
        _context(place_ids=["b"]),
        excluded_place_ids=[],
        visit_at=visit_at,
    )

    merged = provider.merge_prepared([first, second])
    result = await provider.score_prepared(conditions, merged)

    assert {
        item.place_id for item in [*result.recommendations, *result.unverified_recommendations]
    } == {"a", "b"}


@pytest.mark.asyncio
async def test_recommend_raises_app_error_when_context_is_none() -> None:
    provider = RealRecommendationProvider()
    conditions = UserConditions()

    with pytest.raises(AppError):
        await provider.recommend(conditions, None, excluded_place_ids=[])  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_search_radius_km_passed_to_pipeline_matches_to_search_radius_km(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D 호출에 실제로 넘어가는 search_radius_km이 to_search_radius_km() 값과 같은지 확인."""
    captured: dict[str, object] = {}
    original = module.score_prepared_recommendation

    async def _capture(prepared, **kwargs):
        captured["visit_at"] = prepared.visit_at
        captured.update(kwargs)
        return await original(prepared, **kwargs)

    monkeypatch.setattr(module, "score_prepared_recommendation", _capture)

    provider = RealRecommendationProvider()
    conditions = UserConditions(max_travel_time=30)
    context = _context(place_ids=["a"])

    await provider.recommend(conditions, context, excluded_place_ids=[])

    assert captured["search_radius_km"] == pytest.approx(module.to_search_radius_km(conditions))
    assert captured["visit_at"].tzinfo is not None


@pytest.mark.asyncio
async def test_conditions_are_passed_to_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A는 발화 조건을 줄이지 않고 그대로 D에 넘긴다.

    AVOID/ENJOY면 C가 날씨를 조회하지 않으므로 D는 conditions.weather로 날씨를
    판정한다. 여기서 A가 3단계로 미리 줄이면 의도와 발화 값이 함께 사라져 D가 다시
    판단할 수 없다(D-051).
    """
    captured: dict[str, object] = {}
    original = module.prepare_recommendation_from_context

    async def _capture(context, **kwargs):
        captured.update(kwargs)
        return await original(context, **kwargs)

    monkeypatch.setattr(module, "prepare_recommendation_from_context", _capture)

    provider = RealRecommendationProvider()
    conditions = UserConditions(weather_intent=WeatherIntent.AVOID, weather=StatedWeather.RAIN)

    await provider.recommend(conditions, _context(place_ids=["a"]), excluded_place_ids=[])

    assert captured["conditions"] is conditions


def _empty_first_pass() -> RecommendationResponse:
    return RecommendationResponse(recommendations=[], unverified_recommendations=[], elapsed_ms=0)


def _unavailable_concentration() -> CandidateEnrichmentResponse:
    return CandidateEnrichmentResponse(
        request_id="req-1",
        status="unavailable",
        candidates=[
            CandidateEnrichmentResult(
                place_id="a",
                name="장소-a",
                latitude=37.58,
                longitude=126.97,
                status="unavailable",
                error=ContextError(code="unavailable", message="실패", retryable=True),
            )
        ],
    )


@pytest.fixture
def taste_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """취향 스위치를 켠다. conftest가 끄는데, 꺼져 있으면 태그를 아예 읽지 않는다.

    켜지 않으면 태그 테스트가 저장소를 한 번도 부르지 않은 채 돌게 된다 —
    기대값이 빈 목록인 테스트는 엉뚱한 이유로 통과한다.
    """
    monkeypatch.setattr(module.settings, "taste_evidence_enabled", True)


class _FakePreferenceTagRepository:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def find_preference_tags(self, content_ids: list[str]):
        self.calls.append(list(content_ids))
        return {
            place_id: (
                {
                    "preference_code": "quiet",
                    "preference_label": "조용히 쉬기 좋은",
                    "display_rank": 1,
                    "mention_count": 12,
                    "positive_document_count": 12,
                    "negative_document_count": 0,
                    "confidence": 1.0,
                },
                {
                    "preference_code": "date",
                    "preference_label": "데이트하기 좋은",
                    "display_rank": 2,
                    "mention_count": 10,
                    "positive_document_count": 10,
                    "negative_document_count": 0,
                    "confidence": 1.0,
                },
                {
                    "preference_code": "cozy",
                    "preference_label": "아늑하게 머물기 좋은",
                    "display_rank": 3,
                    "mention_count": 8,
                    "positive_document_count": 8,
                    "negative_document_count": 0,
                    "confidence": 1.0,
                },
                {
                    "preference_code": "good_value",
                    "preference_label": "가성비가 좋은",
                    "display_rank": 4,
                    "mention_count": 7,
                    "positive_document_count": 7,
                    "negative_document_count": 0,
                    "confidence": 1.0,
                },
                {
                    "preference_code": "group_gathering",
                    "preference_label": "모임하기 좋은",
                    "display_rank": 5,
                    "mention_count": 6,
                    "positive_document_count": 6,
                    "negative_document_count": 0,
                    "confidence": 1.0,
                },
                {
                    "preference_code": "with_kids",
                    "preference_label": "아이와 함께하기 좋은",
                    "display_rank": 6,
                    "mention_count": 5,
                    "positive_document_count": 5,
                    "negative_document_count": 0,
                    "confidence": 0.85,
                },
            )
            for place_id in content_ids
        }


@pytest.mark.asyncio
async def test_requested_companion_tag_moves_to_front_and_is_marked(taste_on: None) -> None:
    """상위 5개 밖의 요청 태그도 가져와 첫 칩으로 노출한다."""
    provider = RealRecommendationProvider(
        preference_tags=_FakePreferenceTagRepository(),  # type: ignore[arg-type]
    )
    result = await provider.recommend(
        UserConditions(companion="child", taste_query="아이랑 가기 좋은"),
        _context(place_ids=["a"]),
        excluded_place_ids=[],
    )

    item = [*result.recommendations, *result.unverified_recommendations][0]
    assert [tag.code for tag in item.preference_tags] == [
        "with_kids",
        "quiet",
        "date",
        "cozy",
        "good_value",
    ]
    assert item.preference_tags[0].is_query_match is True
    assert all(tag.is_query_match is False for tag in item.preference_tags[1:])


@pytest.mark.asyncio
async def test_missing_requested_tag_does_not_create_a_fake_tag(taste_on: None) -> None:
    class _WithoutKids(_FakePreferenceTagRepository):
        async def find_preference_tags(self, content_ids: list[str]):
            rows = await super().find_preference_tags(content_ids)
            return {
                place_id: tuple(row for row in values if row["preference_code"] != "with_kids")
                for place_id, values in rows.items()
            }

    provider = RealRecommendationProvider(preference_tags=_WithoutKids())  # type: ignore[arg-type]
    result = await provider.recommend(
        UserConditions(companion="child", taste_query="아이와 가기 좋은"),
        _context(place_ids=["a"]),
        excluded_place_ids=[],
    )

    item = [*result.recommendations, *result.unverified_recommendations][0]
    assert [tag.code for tag in item.preference_tags] == [
        "quiet",
        "date",
        "cozy",
        "good_value",
        "group_gathering",
    ]
    assert all(tag.is_query_match is False for tag in item.preference_tags)


async def _recommend_for_child(
    repository: _FakePreferenceTagRepository | None,
) -> RecommendationResponse:
    """사전 코드(with_kids)가 잡히는 발화로 추천한다. 임베딩 Provider는 없다.

    임베딩을 빼 두면 taste Feature를 켤 수 있는 것은 태그 경로뿐이라, 스위치가
    그 경로를 실제로 막는지가 결과에 그대로 드러난다.
    """
    provider = RealRecommendationProvider(preference_tags=repository)  # type: ignore[arg-type]
    return await provider.recommend(
        UserConditions(companion="child", taste_query="아이랑 가기 좋은"),
        _context(place_ids=["a", "b"]),
        excluded_place_ids=[],
    )


def _items(response: RecommendationResponse):
    return [*response.recommendations, *response.unverified_recommendations]


@pytest.mark.asyncio
async def test_taste_tags_rank_and_reach_cards_when_taste_is_on(taste_on: None) -> None:
    """켜진 경우의 기준선 — 태그가 taste 축을 만들고 카드에 실린다(과잉 차단 방지)."""
    repository = _FakePreferenceTagRepository()

    result = await _recommend_for_child(repository)

    assert repository.calls, "태그 저장소를 불러야 한다"
    for item in _items(result):
        assert "taste" in item.feature_scores
        assert "taste" in item.weights_used
        assert item.preference_tags, "카드에 취향 태그가 실려야 한다"


@pytest.mark.asyncio
async def test_taste_off_drops_tag_ranking_and_card_tags() -> None:
    """취향 스위치가 꺼지면 태그 저장소가 있어도 순위가 "취향 없음"과 같다.

    conftest가 스위치를 끈 상태 그대로다. 같은 저장소로 켠 경우(위 테스트)에는
    taste 축과 태그가 생기므로, 여기서 사라지는 것이 스위치 때문임이 드러난다.
    """
    repository = _FakePreferenceTagRepository()

    result = await _recommend_for_child(repository)
    baseline = await _recommend_for_child(None)

    # 태그를 읽지도 않는다 — 채점용(후보 전원)도 카드용(노출분)도.
    assert repository.calls == []
    for item in _items(result):
        assert "taste" not in item.feature_scores
        assert "taste" not in item.weights_used
        assert item.taste_tag_score is None
        assert item.preference_tags == []
    # 저장소가 아예 없는 경우와 순위·점수가 같다.
    assert [(item.place_id, item.score, item.weights_used) for item in _items(result)] == [
        (item.place_id, item.score, item.weights_used) for item in _items(baseline)
    ]


@pytest.mark.asyncio
async def test_rerank_with_concentration_passes_origin_name_from_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2차 Scoring도 1차와 같은 기준점 이름으로 근거 문장을 다시 조립한다(TP-109).

    `rerank_with_concentration()`은 근거 문장을 처음부터 다시 만든다. 여기에 기준점
    이름을 안 넘기면 "현재 위치"로 폴백해, 같은 요청인데 1차 답변은 "경복궁에서",
    혼잡도 재정렬이 걸린 답변은 "현재 위치에서"라고 말하게 된다. 날씨 판정을
    context에서 다시 뽑는 것과 같은 이유다.
    """
    captured: dict[str, object] = {}

    async def _fake_rerank(
        first_pass,
        weather_condition,
        concentration,
        *,
        seek,
        weather_reason=None,
        origin_name=None,
        district_scoped=False,
    ):
        captured["origin_name"] = origin_name
        return first_pass

    monkeypatch.setattr(module, "rerank_with_concentration", _fake_rerank)

    provider = RealRecommendationProvider()
    conditions = UserConditions(concentration_intent=ConcentrationIntent.AVOID)

    await provider.rerank_with_concentration(
        conditions,
        _context(place_ids=["a"]),
        _empty_first_pass(),
        _unavailable_concentration(),
    )

    assert captured["origin_name"] == "경복궁"


@pytest.mark.asyncio
async def test_rerank_with_concentration_derives_seek_true_from_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-040: RealRecommendationProvider가 conditions.concentration_intent를
    올바르게 seek(bool)로 변환해 recommendation_pipeline.rerank_with_concentration()에
    넘기는지 확인한다(실제 재채점 로직은 test_recommendation_pipeline.py가 커버)."""
    captured: dict[str, object] = {}

    async def _fake_rerank(
        first_pass,
        weather_condition,
        concentration,
        *,
        seek,
        weather_reason=None,
        origin_name=None,
        district_scoped=False,
    ):
        captured["seek"] = seek
        return first_pass

    monkeypatch.setattr(module, "rerank_with_concentration", _fake_rerank)

    provider = RealRecommendationProvider()
    conditions = UserConditions(concentration_intent=ConcentrationIntent.SEEK)
    context = _context(place_ids=["a"])

    await provider.rerank_with_concentration(
        conditions, context, _empty_first_pass(), _unavailable_concentration()
    )

    assert captured["seek"] is True


@pytest.mark.asyncio
async def test_rerank_with_concentration_derives_seek_false_from_avoid_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def _fake_rerank(
        first_pass,
        weather_condition,
        concentration,
        *,
        seek,
        weather_reason=None,
        origin_name=None,
        district_scoped=False,
    ):
        captured["seek"] = seek
        return first_pass

    monkeypatch.setattr(module, "rerank_with_concentration", _fake_rerank)

    provider = RealRecommendationProvider()
    conditions = UserConditions(concentration_intent=ConcentrationIntent.AVOID)
    context = _context(place_ids=["a"])

    await provider.rerank_with_concentration(
        conditions, context, _empty_first_pass(), _unavailable_concentration()
    )

    assert captured["seek"] is False


@pytest.mark.asyncio
async def test_rerank_with_concentration_uses_resolve_weather_condition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-051: 2차 Scoring도 1차와 동일하게 resolve_weather_condition()으로 날씨를
    판정해야 한다. 옛 to_weather_condition()(C가 조회 당시 내린 3단계 condition을
    그대로 읽는 방식)은 weather_intent(AVOID/ENJOY) 재해석을 반영하지 못했다 —
    여기서는 ENJOY + 비 예보가 GOOD으로 반전되는지로 그 재해석이 실제로 적용됐는지
    확인한다(그대로 BAD가 나오면 옛 방식으로 되돌아간 것).
    """
    captured: dict[str, object] = {}

    async def _fake_rerank(
        first_pass,
        weather_condition,
        concentration,
        *,
        seek,
        weather_reason=None,
        origin_name=None,
        district_scoped=False,
    ):
        captured["weather_condition"] = weather_condition
        captured["weather_reason"] = weather_reason
        return first_pass

    monkeypatch.setattr(module, "rerank_with_concentration", _fake_rerank)

    provider = RealRecommendationProvider()
    # context.weather는 비워둔다 — AVOID/ENJOY라 C가 조회를 생략하고 발화 값을 쓴
    # 상황(tool_rules.py)을 재현한다. resolve_weather_condition()은 이때
    # conditions.weather로 대신 판정해야 한다.
    conditions = UserConditions(weather_intent=WeatherIntent.ENJOY, weather=StatedWeather.RAIN)
    context = _context(place_ids=["a"])

    await provider.rerank_with_concentration(
        conditions, context, _empty_first_pass(), _unavailable_concentration()
    )

    assert captured["weather_condition"] == WeatherCondition.GOOD
    assert captured["weather_reason"] == "rain"


# --- rerank_with_co_visited() 배선 (D-092) -----------------------------------


@pytest.mark.asyncio
async def test_rerank_with_co_visited_passes_origin_name_from_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """rerank_with_concentration()과 같은 이유로 2차도 1차와 같은 기준점 이름을
    써야 근거 문장이 갈리지 않는다(TP-109와 같은 유형)."""
    captured: dict[str, object] = {}

    async def _fake_rerank(
        first_pass,
        co_visited_pairs,
        weather_condition,
        *,
        weather_reason=None,
        origin_name=None,
        district_scoped=False,
    ):
        captured["origin_name"] = origin_name
        captured["co_visited_pairs"] = co_visited_pairs
        return first_pass

    monkeypatch.setattr(module, "rerank_with_co_visited", _fake_rerank)

    provider = RealRecommendationProvider()
    conditions = UserConditions()
    pairs = [("a", "b")]

    await provider.rerank_with_co_visited(
        conditions, _context(place_ids=["a", "b"]), _empty_first_pass(), pairs
    )

    assert captured["origin_name"] == "경복궁"
    assert captured["co_visited_pairs"] == pairs


@pytest.mark.asyncio
async def test_rerank_with_co_visited_uses_resolve_weather_condition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-051과 같은 이유로 co_visited 2차도 resolve_weather_condition()을 다시
    거쳐야 한다 — 1차와 판정이 갈리면 근거 문장이 서로 다른 날씨를 말하게 된다.
    """
    captured: dict[str, object] = {}

    async def _fake_rerank(
        first_pass,
        co_visited_pairs,
        weather_condition,
        *,
        weather_reason=None,
        origin_name=None,
        district_scoped=False,
    ):
        captured["weather_condition"] = weather_condition
        captured["weather_reason"] = weather_reason
        return first_pass

    monkeypatch.setattr(module, "rerank_with_co_visited", _fake_rerank)

    provider = RealRecommendationProvider()
    conditions = UserConditions(weather_intent=WeatherIntent.ENJOY, weather=StatedWeather.RAIN)
    context = _context(place_ids=["a"])

    await provider.rerank_with_co_visited(conditions, context, _empty_first_pass(), [])

    assert captured["weather_condition"] == WeatherCondition.GOOD
    assert captured["weather_reason"] == "rain"


# --- 도보 실측 전달 가드 (feat/walking-duration-scoring) --------------------

_WALKING_ROUTE = TravelRoute(
    place_id="a",
    mode=TravelMode.WALKING,
    status=RouteStatus.SUCCESS,
    source=RouteSource.KAKAO_WALKING,
    distance_m=400,
    duration_seconds=340,
)


_DRIVING_ROUTE = TravelRoute(
    place_id="a",
    mode=TravelMode.DRIVING,
    status=RouteStatus.SUCCESS,
    source=RouteSource.NAVER_DRIVING,
    distance_m=3054,
    duration_seconds=1245,
)


async def _captured_routes(
    monkeypatch: pytest.MonkeyPatch,
    conditions: UserConditions,
    route: TravelRoute = _WALKING_ROUTE,
) -> object:
    captured = await _captured_call(monkeypatch, conditions, route=route)
    return captured["travel_routes"]


async def _captured_call(
    monkeypatch: pytest.MonkeyPatch,
    conditions: UserConditions,
    route: TravelRoute = _WALKING_ROUTE,
) -> dict[str, object]:
    """D 채점 진입점에 실제로 넘어간 인자를 그대로 돌려준다."""
    captured: dict[str, object] = {}
    original = module.score_prepared_recommendation

    async def _capture(prepared, **kwargs):
        captured.update(kwargs)
        return await original(prepared, **kwargs)

    monkeypatch.setattr(module, "score_prepared_recommendation", _capture)

    provider = RealRecommendationProvider()
    prepared = await provider.prepare(
        conditions,
        _context(place_ids=["a"]),
        excluded_place_ids=[],
        visit_at=module.datetime.now(module._KST),
    )
    await provider.score_prepared(conditions, prepared, travel_routes=(route,))
    return captured


@pytest.mark.asyncio
async def test_walking_routes_are_used_when_transport_is_walk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = await _captured_routes(
        monkeypatch, UserConditions(transport=Transport.WALK, max_travel_time=30)
    )

    assert routes == (_WALKING_ROUTE,)


@pytest.mark.asyncio
async def test_walking_routes_are_used_when_travel_time_is_unspecified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """이동시간을 말하지 않으면 기본 반경(2.0km)이라 도보 시간으로 재도 맞는다."""
    routes = await _captured_routes(monkeypatch, UserConditions())

    assert routes == (_WALKING_ROUTE,)


@pytest.mark.asyncio
async def test_driving_routes_are_used_when_transport_is_car(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """자동차 실측은 채점까지 간다 — 반경과 예산이 같은 20km/h 가정이라 단위가 맞는다."""
    routes = await _captured_routes(
        monkeypatch,
        UserConditions(transport=Transport.CAR, max_travel_time=30),
        route=_DRIVING_ROUTE,
    )

    assert routes == (_DRIVING_ROUTE,)


@pytest.mark.asyncio
async def test_routes_are_kept_when_transport_is_unspecified_with_travel_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """이동시간만 말한 요청도 실측을 그대로 쓴다 (D-118).

    예전에는 버렸다 — 예산이 측정 수단의 속도로 나뉘던 시절에는 "반경을 만든
    속도와 실측한 수단이 같은 요청"에서만 쓸 수 있었기 때문이다. 예산이 더 이상
    측정 수단을 보지 않으므로 그 제약이 사라졌다.
    """
    routes = await _captured_routes(monkeypatch, UserConditions(max_travel_time=30))

    assert routes == (_WALKING_ROUTE,)


@pytest.mark.asyncio
async def test_budget_speed_follows_the_radius_not_the_measured_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """예산 속도는 반경을 만든 속도로 넘어간다 (D-118).

    도보 실측을 받았더라도, 반경이 20km/h로 만들어진 요청이면 예산도 20km/h다 —
    측정 수단이 아니라 요청이 자를 정한다.
    """
    captured = await _captured_call(
        monkeypatch, UserConditions(transport=Transport.PUBLIC, max_travel_time=30)
    )
    assert captured["travel_budget_speed_km_per_min"] == pytest.approx(20 / 60)

    captured = await _captured_call(monkeypatch, UserConditions())
    assert captured["travel_budget_speed_km_per_min"] == pytest.approx(WALKING_SPEED_KM_PER_MINUTE)


# --- 썸네일 병합(A가 C의 RecommendationCardTool을 빌려 D 결과에 붙인다) --------


class _FakeCard:
    def __init__(
        self,
        content_id: str,
        thumbnail_url: str | None,
        fallback_thumbnail_url: str | None = None,
        category_label: str | None = None,
        photo_attribution: PhotoAttribution | None = None,
    ) -> None:
        self.content_id = content_id
        self.thumbnail_url = thumbnail_url
        # Google 사진에만 붙는 출처. 소비 측이 사진과 함께 읽으므로 비워 두면
        # 출처 배선이 한 줄도 실행되지 않은 채 테스트만 통과한다.
        self.photo_attribution = photo_attribution
        # 소비 측이 실제로 읽는 필드다. 비워 두면 폴백 배선이 한 줄도 실행되지 않은 채
        # 테스트만 통과한다(이 저장소의 "조용한 fake" 유형).
        self.fallback_thumbnail_url = fallback_thumbnail_url
        # 같은 이유로 여기도 실제 필드명을 그대로 둔다 — 소비 측이 card.category_label을 읽는다.
        self.category_label = category_label


class _FakeCardResult:
    def __init__(self, cards: list[_FakeCard]) -> None:
        self.cards = cards
        self.missing_content_ids: tuple[str, ...] = ()


class _FakeRecommendationCardTool:
    """RecommendationCardTool을 흉내 낸다 — get_cards만 duck-typing으로 만족한다."""

    def __init__(
        self,
        thumbnails: dict[str, str | None],
        fallbacks: dict[str, str] | None = None,
        category_labels: dict[str, str] | None = None,
    ) -> None:
        self._thumbnails = thumbnails
        self._fallbacks = fallbacks or {}
        self._category_labels = category_labels or {}
        self.requested_place_ids: list[str] | None = None

    async def get_cards(self, content_ids: list[str]) -> _FakeCardResult:
        self.requested_place_ids = list(content_ids)
        return _FakeCardResult(
            [
                _FakeCard(
                    place_id,
                    self._thumbnails[place_id],
                    self._fallbacks.get(place_id),
                    self._category_labels.get(place_id),
                )
                for place_id in content_ids
                if place_id in self._thumbnails
            ]
        )


class _RaisingRecommendationCardTool:
    async def get_cards(self, content_ids: list[str]) -> _FakeCardResult:
        raise RuntimeError("thumbnail lookup boom")


@pytest.mark.asyncio
async def test_recommend_attaches_thumbnails_from_recommendation_card_tool() -> None:
    cards = _FakeRecommendationCardTool({"a": "https://img.test/a.jpg"})
    provider = RealRecommendationProvider(recommendation_cards=cards)
    conditions = UserConditions(max_travel_time=30)
    context = _context(place_ids=["a", "b"])

    result = await provider.recommend(conditions, context, excluded_place_ids=[])

    by_id = {
        item.place_id: item
        for item in [*result.recommendations, *result.unverified_recommendations]
    }
    assert by_id["a"].image_url == "https://img.test/a.jpg"
    # 썸네일이 없는 장소는 None으로 남는다 — 지어내지 않는다.
    assert by_id["b"].image_url is None
    assert set(cards.requested_place_ids or []) == {"a", "b"}


@pytest.mark.asyncio
async def test_recommend_attaches_thumbnail_fallback_url() -> None:
    """폴백 주소도 함께 붙인다.

    작은 썸네일(firstimage2)만 관광공사 서버에서 사라진 장소가 있다 — 아현시장이
    그렇다(2026-09-05 실측). 프론트가 그 카드에서만 원본으로 갈아타려면 두 주소가
    다 내려가야 한다. 서버가 미리 확인해 고르지 않는 이유는 추천 한 번에 확인
    요청이 5~10건 붙기 때문이다(schemas.RecommendationItem.image_url_fallback).
    """
    cards = _FakeRecommendationCardTool(
        {"a": "https://img.test/a_image3.jpg", "b": "https://img.test/b_image3.jpg"},
        fallbacks={"a": "https://img.test/a_image2.jpg"},
    )
    provider = RealRecommendationProvider(recommendation_cards=cards)
    conditions = UserConditions(max_travel_time=30)
    context = _context(place_ids=["a", "b"])

    result = await provider.recommend(conditions, context, excluded_place_ids=[])

    by_id = {
        item.place_id: item
        for item in [*result.recommendations, *result.unverified_recommendations]
    }
    assert by_id["a"].image_url_fallback == "https://img.test/a_image2.jpg"
    # 대안이 없는 장소는 None이다 — image_url을 복사해 두면 프론트가 같은 404를
    # 두 번 부른다.
    assert by_id["b"].image_url_fallback is None


@pytest.mark.asyncio
async def test_recommend_attaches_category_label() -> None:
    """분류 라벨도 같은 조회에서 붙인다.

    응답의 `category`는 대분류 코드라(`restaurant`·`shopping`) 화면에 영어가 그대로
    찍혔다. 카드가 이미 계산해 두는 중분류명(한식·전시시설)을 함께 내려보낸다 —
    이미 도는 get_cards()에서 필드 하나를 더 꺼낼 뿐이라 추가 조회는 없다.
    """
    cards = _FakeRecommendationCardTool(
        {"a": "https://img.test/a.jpg", "b": "https://img.test/b.jpg"},
        category_labels={"a": "한식"},
    )
    provider = RealRecommendationProvider(recommendation_cards=cards)
    conditions = UserConditions(max_travel_time=30)
    context = _context(place_ids=["a", "b"])

    result = await provider.recommend(conditions, context, excluded_place_ids=[])

    by_id = {
        item.place_id: item
        for item in [*result.recommendations, *result.unverified_recommendations]
    }
    assert by_id["a"].category_label == "한식"
    # 라벨이 없으면 지어내지 않는다. 화면은 그때 category로 되돌아간다.
    assert by_id["b"].category_label is None


@pytest.mark.asyncio
async def test_recommend_attaches_category_label_without_thumbnail() -> None:
    """사진이 없는 장소에도 라벨은 붙는다.

    둘을 한 조건에 묶으면 사진 없는 장소(실측 844건 중 169건, 20%)가 분류까지
    잃는다. 라벨과 썸네일은 서로 독립이다.
    """
    cards = _FakeRecommendationCardTool(
        {"a": None},
        category_labels={"a": "전시시설"},
    )
    provider = RealRecommendationProvider(recommendation_cards=cards)
    conditions = UserConditions(max_travel_time=30)
    context = _context(place_ids=["a"])

    result = await provider.recommend(conditions, context, excluded_place_ids=[])

    item = [*result.recommendations, *result.unverified_recommendations][0]
    assert item.image_url is None
    assert item.category_label == "전시시설"


@pytest.mark.asyncio
async def test_recommend_leaves_image_url_none_without_recommendation_card_tool() -> None:
    """recommendation_cards를 안 넘기면(None) 기존처럼 이미지 없이 추천한다."""
    provider = RealRecommendationProvider()
    conditions = UserConditions(max_travel_time=30)
    context = _context(place_ids=["a"])

    result = await provider.recommend(conditions, context, excluded_place_ids=[])

    all_items = [*result.recommendations, *result.unverified_recommendations]
    assert all_items[0].image_url is None


@pytest.mark.asyncio
async def test_recommend_survives_thumbnail_lookup_failure() -> None:
    """썸네일 조회가 실패해도(_with_preference_tags와 같은 원칙) 추천 자체는 유지된다."""
    provider = RealRecommendationProvider(recommendation_cards=_RaisingRecommendationCardTool())
    conditions = UserConditions(max_travel_time=30)
    context = _context(place_ids=["a"])

    result = await provider.recommend(conditions, context, excluded_place_ids=[])

    all_items = [*result.recommendations, *result.unverified_recommendations]
    assert all_items[0].place_id == "a"
    assert all_items[0].image_url is None
