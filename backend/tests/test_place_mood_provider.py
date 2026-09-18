"""장소 분위기 Provider의 호출 생략 조건과 인코더 부재 처리 테스트."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.domain.models import PlaceMoodMatch, PlaceMoodProfile
from app.providers.contracts import ProviderSource, ProviderStatus
from app.providers.place_mood import (
    DEFAULT_MATCH_COUNT,
    DEFAULT_MIN_SIMILARITY,
    PlaceMoodProvider,
)


class _RecordingEncoder:
    """torch 없이 도는 가짜 인코더 — 호출 여부만 센다."""

    def __init__(self) -> None:
        self.calls: list[int] = []

    def encode_image(self, image_bytes: bytes) -> Sequence[float]:
        self.calls.append(len(image_bytes))
        return [0.1] * 768


class _RecordingRepository:
    def __init__(
        self,
        profiles: dict[str, PlaceMoodProfile] | None = None,
        matches: tuple[PlaceMoodMatch, ...] = (),
    ) -> None:
        self.profile_calls: list[list[str]] = []
        self.search_calls: list[dict[str, object]] = []
        self._profiles = profiles or {}
        self._matches = matches

    async def find_mood_profiles(
        self,
        content_ids: Sequence[str],
    ) -> dict[str, PlaceMoodProfile]:
        self.profile_calls.append(list(content_ids))
        return dict(self._profiles)

    async def search_place_mood(
        self,
        query_embedding: Sequence[float],
        candidate_content_ids: Sequence[str] | None,
        *,
        match_count: int,
        min_similarity: float,
        latitude: float | None = None,
        longitude: float | None = None,
        radius_km: float | None = None,
        mean_center: bool = False,
        axis_weight: float = 1.0,
    ) -> tuple[PlaceMoodMatch, ...]:
        self.search_calls.append(
            {
                "embedding_len": len(query_embedding),
                "candidates": (
                    None
                    if candidate_content_ids is None
                    else list(candidate_content_ids)
                ),
                "match_count": match_count,
                "min_similarity": min_similarity,
                "latitude": latitude,
                "longitude": longitude,
                "radius_km": radius_km,
                "mean_center": mean_center,
                "axis_weight": axis_weight,
            }
        )
        return self._matches


def _profile(content_id: str, photo_count: int = 4) -> PlaceMoodProfile:
    return PlaceMoodProfile(
        content_id=content_id,
        axis_scores={"calm": 0.12, "traditional": -0.08},
        photo_count=photo_count,
    )


def _match(content_id: str, similarity: float) -> PlaceMoodMatch:
    return PlaceMoodMatch(
        content_id=content_id,
        similarity=similarity,
        profile=_profile(content_id),
    )


@pytest.mark.asyncio
async def test_describe_returns_profiles() -> None:
    repository = _RecordingRepository(profiles={"a": _profile("a")})
    provider = PlaceMoodProvider(repository)

    result = await provider.describe(["a", "b"])

    assert result.metadata.source is ProviderSource.SUPABASE_PLACE_MOOD
    assert result.metadata.status is ProviderStatus.SUCCESS
    assert set(result.data) == {"a"}
    assert repository.profile_calls == [["a", "b"]]


@pytest.mark.asyncio
async def test_describe_skips_query_when_no_candidates() -> None:
    """후보가 없으면 조회하지 않는다 — 빈 in 필터로 왕복만 낭비된다."""
    repository = _RecordingRepository()
    provider = PlaceMoodProvider(repository)

    result = await provider.describe([])

    assert result.metadata.status is ProviderStatus.NO_DATA
    assert repository.profile_calls == []


@pytest.mark.asyncio
async def test_describe_reports_no_data_when_nothing_loaded() -> None:
    """분위기 벡터가 없는 구의 후보만 들어오면 결측이 정상이다."""
    repository = _RecordingRepository(profiles={})
    provider = PlaceMoodProvider(repository)

    result = await provider.describe(["only-in-other-district"])

    assert result.metadata.status is ProviderStatus.NO_DATA
    assert result.data == {}


@pytest.mark.asyncio
async def test_photo_search_passes_defaults_to_repository() -> None:
    """컷값이나 개수를 빠뜨리면 RPC 기본값이 조용히 적용된다."""
    encoder = _RecordingEncoder()
    repository = _RecordingRepository(matches=(_match("a", 0.71),))
    provider = PlaceMoodProvider(repository, encoder)

    result = await provider.search_by_photo(b"fake-jpeg-bytes", ["a", "b"])

    assert result.metadata.status is ProviderStatus.SUCCESS
    assert encoder.calls == [len(b"fake-jpeg-bytes")]
    assert repository.search_calls == [
        {
            "embedding_len": 768,
            "candidates": ["a", "b"],
            "match_count": DEFAULT_MATCH_COUNT,
            "min_similarity": DEFAULT_MIN_SIMILARITY,
            "latitude": None,
            "longitude": None,
            "radius_km": None,
            "mean_center": False,
            "axis_weight": 1.0,
        }
    ]


@pytest.mark.asyncio
async def test_photo_search_without_candidates_passes_none() -> None:
    """후보를 안 넘기면 전체 검색이다 — 빈 배열로 바뀌면 결과가 0건이 된다."""
    repository = _RecordingRepository(matches=(_match("a", 0.71),))
    provider = PlaceMoodProvider(repository, _RecordingEncoder())

    await provider.search_by_photo(b"bytes")

    assert repository.search_calls[0]["candidates"] is None


@pytest.mark.asyncio
async def test_photo_search_without_encoder_does_not_query() -> None:
    """인코더가 없으면 조회하지 않는다.

    빈 벡터로 흉내내면 유사도가 전부 같아져 아무 장소나 순서대로 돌아오고,
    그게 추천으로 나가면 틀린 줄도 모른다(D-042).
    """
    repository = _RecordingRepository(matches=(_match("a", 0.71),))
    provider = PlaceMoodProvider(repository, encoder=None)

    result = await provider.search_by_photo(b"bytes")

    assert provider.photo_search_available is False
    assert result.metadata.status is ProviderStatus.NO_DATA
    assert result.data == ()
    assert repository.search_calls == []


@pytest.mark.asyncio
async def test_photo_search_skips_empty_image() -> None:
    encoder = _RecordingEncoder()
    repository = _RecordingRepository()
    provider = PlaceMoodProvider(repository, encoder)

    result = await provider.search_by_photo(b"")

    assert result.metadata.status is ProviderStatus.NO_DATA
    assert encoder.calls == []
    assert repository.search_calls == []


@pytest.mark.asyncio
async def test_describe_works_without_encoder() -> None:
    """SigLIP이 안 깔린 환경에서도 발화 경로는 돌아야 한다."""
    repository = _RecordingRepository(profiles={"a": _profile("a")})
    provider = PlaceMoodProvider(repository, encoder=None)

    result = await provider.describe(["a"])

    assert result.metadata.status is ProviderStatus.SUCCESS
    assert provider.photo_search_available is False


@pytest.mark.asyncio
async def test_radius_is_passed_through() -> None:
    """좌표와 반경으로 부르면 DB가 직접 좁힌다.

    후보 목록으로 부르면 그 목록을 만드는 데 TourAPI 상세 조회가 붙어 최대
    20곳이 된다 — 2,009곳을 적재해 두고 그 안에서만 고르는 셈이 된다.
    """
    repository = _RecordingRepository(matches=(_match("a", 0.9),))
    provider = PlaceMoodProvider(repository, _RecordingEncoder())

    await provider.search_by_photo(
        b"bytes", None, latitude=37.57, longitude=126.98, radius_km=2.0, match_count=40
    )

    call = repository.search_calls[0]
    assert call["candidates"] is None
    assert call["latitude"] == pytest.approx(37.57)
    assert call["radius_km"] == pytest.approx(2.0)
    assert call["match_count"] == 40


@pytest.mark.asyncio
async def test_mean_center_is_passed_through() -> None:
    """평균 빼기 설정이 저장소까지 내려간다(D-115).

    **여기서 못 박지 않으면 조용히 꺼진 채로 돈다.** 이 값은 응답 모양을 바꾸지
    않고 순위만 바꾸므로, 안 넘어가도 결과는 그럴듯하게 나오고 테스트도 통과한다.
    실제로는 47.1%짜리 옛 순위를 주면서 켠 줄 알게 된다.
    """
    repository = _RecordingRepository(matches=(_match("a", 0.9),))
    provider = PlaceMoodProvider(
        repository, _RecordingEncoder(), mean_center=True
    )

    await provider.search_by_photo(b"bytes", None)

    assert repository.search_calls[0]["mean_center"] is True


@pytest.mark.asyncio
async def test_mean_center_defaults_to_off_in_provider() -> None:
    """Provider 자체의 기본값은 꺼짐이다.

    켜고 끄는 판단은 설정이 한다(`place_mood_mean_center_enabled`). Provider가
    스스로 켜면 그 설정을 꺼도 안 꺼지는 경로가 생긴다.
    """
    repository = _RecordingRepository(matches=(_match("a", 0.9),))
    provider = PlaceMoodProvider(repository, _RecordingEncoder())

    await provider.search_by_photo(b"bytes", None)

    assert repository.search_calls[0]["mean_center"] is False


@pytest.mark.asyncio
async def test_axis_weight_is_passed_through() -> None:
    """축 섞는 비율이 저장소까지 내려간다(TP-206).

    `mean_center`와 같은 이유로 못 박는다 — 이 값은 응답 모양을 바꾸지 않고
    순위만 바꾸므로, 안 넘어가도 결과가 그럴듯하게 나오고 테스트도 통과한다.
    실제로는 축을 섞지 않은 옛 순위를 주면서 섞은 줄 알게 된다.
    """
    repository = _RecordingRepository(matches=(_match("a", 0.9),))
    provider = PlaceMoodProvider(repository, _RecordingEncoder(), axis_weight=0.7)

    await provider.search_by_photo(b"bytes", None)

    assert repository.search_calls[0]["axis_weight"] == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_axis_weight_defaults_to_one_in_provider() -> None:
    """Provider 자체의 기본값은 1.0(축을 쓰지 않음)이다.

    섞을 비율을 정하는 것은 설정이다(`place_mood_axis_weight`). Provider가
    스스로 섞으면 그 설정을 1.0으로 되돌려도 안 꺼지는 경로가 생긴다.
    """
    repository = _RecordingRepository(matches=(_match("a", 0.9),))
    provider = PlaceMoodProvider(repository, _RecordingEncoder())

    await provider.search_by_photo(b"bytes", None)

    assert repository.search_calls[0]["axis_weight"] == pytest.approx(1.0)
