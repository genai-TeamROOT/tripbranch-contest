"""취향 근거 Provider 조립과 D 진입점 배선 테스트."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.domain.models import PlaceEvidenceMatch
from app.providers.contracts import ProviderSource, ProviderStatus, provider_result
from app.schemas import PlaceTag, PlaceType, UserConditions
from app.services.runtime.real_recommendation_provider import (
    RealRecommendationProvider,
    _enrich_taste_query,
)


class _RecordingEvidenceProvider:
    def __init__(self, matches: dict[str, PlaceEvidenceMatch] | None = None) -> None:
        self.calls: list[tuple[str, list[str]]] = []
        self._matches = matches or {}

    async def search(self, query: str, candidate_content_ids: Sequence[str]):
        self.calls.append((query, list(candidate_content_ids)))
        return provider_result(
            self._matches,
            source=ProviderSource.SUPABASE_PLACE_EVIDENCE,
            status=ProviderStatus.SUCCESS if self._matches else ProviderStatus.NO_DATA,
        )


class _FailingEvidenceProvider:
    async def search(self, query: str, candidate_content_ids: Sequence[str]):
        raise RuntimeError("RPC 장애")


class _Prepared:
    """preparation.eligible_candidates만 보는 최소 대역."""

    def __init__(self, place_ids: Sequence[str]) -> None:
        self.preparation = type(
            "P",
            (),
            {
                "eligible_candidates": tuple(
                    type("C", (), {"candidate": type("X", (), {"place_id": pid})()})()
                    for pid in place_ids
                )
            },
        )()


def _match(place_id: str) -> PlaceEvidenceMatch:
    return PlaceEvidenceMatch(place_id, f"장소 {place_id}", 0.6, ())


@pytest.mark.asyncio
async def test_search_is_scoped_to_hard_filter_survivors() -> None:
    """후보를 안 좁히면 RPC가 전체를 훑어 6~9초가 걸린다(2026-08-18 실측)."""
    evidence = _RecordingEvidenceProvider({"a": _match("a")})
    provider = RealRecommendationProvider(evidence)

    matches = await provider._taste_matches_for(
        UserConditions(taste_query="조용한"), _Prepared(["a", "b"])
    )

    # place_tag가 없어 일반 접미어("곳")로 폴백해 붙는다 — _enrich_taste_query 참고.
    assert evidence.calls == [("조용한 곳", ["a", "b"])]
    assert matches is not None and set(matches) == {"a"}


def test_enrich_taste_query_appends_known_place_tag() -> None:
    """place_tag를 알면 그걸 붙인다 — 실측(2026-08-23, 경복궁 반경 3km 카페
    46곳 중 근거가 있는 45곳): "조용한" 컷 통과 2/45곳(평균 0.31) → "조용한
    카페" 38/45곳(평균 0.48). scripts/measure_taste_query_enrichment.py,
    종로 4개 지점에서 재현.
    """
    conditions = UserConditions(taste_query="조용한", place_tags=[PlaceTag.CAFE])

    assert _enrich_taste_query(conditions) == "조용한 카페"


def test_enrich_taste_query_appends_every_known_tag() -> None:
    conditions = UserConditions(
        taste_query="조용한", place_tags=[PlaceTag.CAFE, PlaceTag.PARK]
    )

    assert _enrich_taste_query(conditions) == "조용한 카페 공원"


def test_enrich_taste_query_uses_place_type_when_tag_is_missing() -> None:
    """"식당"/"레스토랑"처럼 넓은 유형을 말하면 place_tags가 비고 place_types만
    채워진다(태그는 한식·카페 같은 세분류뿐). 그때 일반 접미어로 떨어지면
    개선 효과를 거의 못 받는다 — 실측(경복궁 반경 3km 음식점 269곳 중 근거가
    있는 262곳, 질의 "조용한"): "조용한 곳" 17/262곳 → "조용한 맛집" 118/262곳.
    """
    conditions = UserConditions(
        taste_query="조용한", place_types=[PlaceType.RESTAURANT]
    )

    assert _enrich_taste_query(conditions) == "조용한 맛집"


def test_enrich_taste_query_prefers_the_narrower_place_tag() -> None:
    """태그와 유형이 함께 오면 좁은 쪽(태그)을 쓴다 — "카페"가 "맛집"보다
    후보를 정확히 가리킨다.
    """
    conditions = UserConditions(
        taste_query="조용한",
        place_tags=[PlaceTag.CAFE],
        place_types=[PlaceType.RESTAURANT],
    )

    assert _enrich_taste_query(conditions) == "조용한 카페"


def test_enrich_taste_query_skips_place_types_that_did_not_help() -> None:
    """festival·leisure는 라벨을 붙여도 효과가 없거나 나빠서 일부러 뺐다
    (실측: 축제 "조용한 곳" 1/32곳 → "조용한 축제" 0/32곳 · "조용한 행사"
    0/32곳). 표에 없는 유형은 일반 접미어로 떨어져야 한다.
    """
    conditions = UserConditions(
        taste_query="조용한", place_types=[PlaceType.FESTIVAL]
    )

    assert _enrich_taste_query(conditions) == "조용한 곳"


def test_enrich_taste_query_skips_excluded_place_tag() -> None:
    """"축제" 태그는 붙이면 오히려 손해라 걸러낸다.

    실측(2026-08-24, 활성 2,220곳, 취향 축 6종): "<취향> 곳" 대비 "<취향> 축제"의
    컷 통과 수가 **6축 전부** 줄었다(조용한 1→0, 감성적인 18→11, 빈티지 10→6,
    분위기 좋은 20→12, 데이트하기 좋은 19→13, 혼자 가기 좋은 4→1). 21개 태그 중
    유일하다. place_type 표에서 festival을 뺀 것과 대칭 처리다.
    """
    conditions = UserConditions(
        taste_query="조용한",
        place_tags=[PlaceTag.FESTIVAL],
        place_types=[PlaceType.FESTIVAL],
    )

    # place_type의 festival도 라벨 표에 없으므로 일반 접미어까지 내려간다.
    assert _enrich_taste_query(conditions) == "조용한 곳"


def test_enrich_taste_query_keeps_other_tags_when_one_is_excluded() -> None:
    """제외 태그가 섞여 와도 나머지는 그대로 쓴다 — "축제나 카페"처럼 복수
    유형을 말한 발화에서 카페까지 버리면 보강 효과를 통째로 잃는다.
    """
    conditions = UserConditions(
        taste_query="조용한", place_tags=[PlaceTag.FESTIVAL, PlaceTag.CAFE]
    )

    assert _enrich_taste_query(conditions) == "조용한 카페"


def test_enrich_taste_query_excluded_tag_falls_through_to_place_type() -> None:
    """제외 태그만 남으면 place_type 라벨로 내려간다 — 태그를 거른 것이
    하드 필터에는 영향을 주지 않으므로(후보는 이미 확정) 질의만 바뀐다.
    """
    conditions = UserConditions(
        taste_query="조용한",
        place_tags=[PlaceTag.FESTIVAL],
        place_types=[PlaceType.CULTURAL_FACILITY],
    )

    assert _enrich_taste_query(conditions) == "조용한 문화시설"


def test_enrich_taste_query_keeps_performance_hall_tag() -> None:
    """"공연장"은 제외 후보로 검토했다가 실측으로 철회했다.

    "조용한" 한 축의 인용문만 보면 좌석·동선 후기가 통과해 왜곡처럼 보였지만,
    6축으로 재니 0/6 손해였다(조용한 1→17, 혼자 가기 좋은 1→26, 데이트하기
    좋은 16→37). 한 축의 인용문은 태그를 빼는 근거로 부족하다 — 이 테스트는
    같은 판단이 다시 뒤집히지 않게 고정한다.
    """
    conditions = UserConditions(
        taste_query="조용한", place_tags=[PlaceTag.PERFORMANCE_HALL]
    )

    assert _enrich_taste_query(conditions) == "조용한 공연장"


def test_enrich_taste_query_falls_back_to_generic_suffix() -> None:
    """place_tag도 place_type도 없는 발화("조용한 곳 추천해줘")도 접미어를
    붙인다 — 실측(경복궁 카페 46곳): "조용한" 2곳 → "조용한 곳" 10곳 →
    "조용한 카페" 38곳. 특정 태그만큼은 아니지만 안 붙이는 것보다 낫다.
    """
    conditions = UserConditions(taste_query="조용한")

    assert _enrich_taste_query(conditions) == "조용한 곳"


@pytest.mark.asyncio
async def test_no_taste_query_skips_the_search_and_the_feature() -> None:
    """취향 미언급 요청의 가중치가 바뀌면 안 된다 — None이어야 Feature가 꺼진다."""
    evidence = _RecordingEvidenceProvider()
    provider = RealRecommendationProvider(evidence)

    matches = await provider._taste_matches_for(
        UserConditions(), _Prepared(["a"])
    )

    assert matches is None
    assert evidence.calls == []


@pytest.mark.asyncio
async def test_saved_taste_query_is_used_when_nothing_was_spoken() -> None:
    """발화에 취향이 없어도 계정에 저장해 둔 값이 있으면 그것으로 찾는다."""
    evidence = _RecordingEvidenceProvider({"a": _match("a")})
    provider = RealRecommendationProvider(evidence)

    matches = await provider._taste_matches_for(
        UserConditions(), _Prepared(["a", "b"]), saved_taste_query="아늑한 공간 전망 좋은"
    )

    assert evidence.calls == [("아늑한 공간 전망 좋은", ["a", "b"])]
    assert matches is not None and set(matches) == {"a"}


@pytest.mark.asyncio
async def test_saved_taste_query_skips_place_tag_enrichment() -> None:
    """저장값에는 장소 유형을 덧붙이지 않는다.

    그 보강은 "조용한" 같은 **한 단어 발화**가 임베딩에 안 걸리는 것을 고치려던
    것이고(2/45 → 38/45), 저장값은 칩 3~5개가 이어져 있어 이미 문맥이 있다.
    붙이면 발화가 없는데도 "카페"가 질의에 들어가 저장한 취향과 다른 말이 된다.
    """
    evidence = _RecordingEvidenceProvider({"a": _match("a")})
    provider = RealRecommendationProvider(evidence)

    await provider._taste_matches_for(
        UserConditions(place_tags=[PlaceTag.CAFE]),
        _Prepared(["a"]),
        saved_taste_query="아늑한 공간",
    )

    assert evidence.calls == [("아늑한 공간", ["a"])]


@pytest.mark.asyncio
async def test_saved_taste_query_is_appended_after_the_spoken_one() -> None:
    """발화가 앞이고 저장값이 뒤다.

    벡터 하나로 합쳐 검색하므로 순서가 점수를 가르지는 않지만, 로그에 남는
    질의를 읽을 때 사용자가 방금 한 말이 먼저 보인다.

    **발화 쪽만 장소 유형 보강(`_enrich_taste_query`)을 탄다** — "조용한"이
    "조용한 곳"이 되고, 저장값은 이미 칩이 여러 개라 문맥이 있어 그대로 붙는다.
    부딪히는 칩을 빼는 것은 D가 이 값을 만들 때 이미 끝났다.
    """
    evidence = _RecordingEvidenceProvider({"a": _match("a")})
    provider = RealRecommendationProvider(evidence)

    await provider._taste_matches_for(
        UserConditions(taste_query="조용한"),
        _Prepared(["a"]),
        saved_taste_query="사진 명소 아늑한 공간",
    )

    assert evidence.calls == [("조용한 곳 사진 명소 아늑한 공간", ["a"])]


@pytest.mark.asyncio
async def test_no_spoken_and_no_saved_disables_the_feature() -> None:
    """둘 다 없으면 지금까지와 같다 — 취향 축이 꺼진다."""
    evidence = _RecordingEvidenceProvider()
    provider = RealRecommendationProvider(evidence)

    matches = await provider._taste_matches_for(
        UserConditions(), _Prepared(["a"]), saved_taste_query=None
    )

    assert matches is None
    assert evidence.calls == []


@pytest.mark.asyncio
async def test_provider_absent_disables_the_feature() -> None:
    """모델을 올릴 수 없는 배포에서도 추천은 그대로 동작해야 한다."""
    provider = RealRecommendationProvider(None)

    assert await provider._taste_matches_for(
        UserConditions(taste_query="조용한 곳"), _Prepared(["a"])
    ) is None


@pytest.mark.asyncio
async def test_search_failure_falls_back_to_scoring_without_taste() -> None:
    """취향은 순위를 다듬는 축이지 후보를 만드는 축이 아니다 — 실패가 추천을 막으면 안 된다."""
    provider = RealRecommendationProvider(_FailingEvidenceProvider())

    assert await provider._taste_matches_for(
        UserConditions(taste_query="조용한 곳"), _Prepared(["a"])
    ) is None


@pytest.mark.asyncio
async def test_empty_candidates_skip_the_search() -> None:
    """하드 필터가 후보를 다 걸러낸 요청에서 빈 RPC를 부르지 않는다."""
    evidence = _RecordingEvidenceProvider()
    provider = RealRecommendationProvider(evidence)

    assert await provider._taste_matches_for(
        UserConditions(taste_query="조용한 곳"), _Prepared([])
    ) is None
    assert evidence.calls == []


def test_subclass_without_init_still_works() -> None:
    """A의 테스트 대역이 __init__을 부르지 않고 상속한다 — 클래스 기본값이 필요하다."""

    class _Sub(RealRecommendationProvider):
        def __init__(self) -> None:  # noqa: D107 - 의도적으로 super()를 부르지 않는다
            pass

    assert _Sub()._place_evidence is None
