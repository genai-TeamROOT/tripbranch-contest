"""사진 검색 서비스의 순서·위치 우선순위·하드 필터 연결 테스트.

**순서가 이 파일의 주제다.** 사진 유사도로 먼저 줄을 세우고 상위 N곳만 상세를
확인한다 — 반대로 하면 후보가 20곳 상한에 걸려 어떤 사진을 올려도 같은 대여섯
곳이 나온다.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.domain.models import PlaceMoodMatch, PlaceMoodProfile
from app.errors import AppError
from app.providers.contracts import ProviderSource, ProviderStatus, provider_result
from app.providers.place_mood_encoder import UnreadableImageError
from app.services import photo_similar
from app.services.photo_similar import PhotoSimilarQuery, build_photo_similar_places


@dataclass
class _Detail:
    content_id: str
    title: str
    content_type_id: str = "39"
    address: str | None = "서울특별시 종로구"
    latitude: float | None = 37.57
    longitude: float | None = 126.98
    operating_hours_raw: str | None = "09:00~22:00"
    rest_date_raw: str | None = None


class _RecordingMood:
    """호출 인자만 기록하는 대역. SigLIP 없이 돈다."""

    def __init__(
        self,
        matches: tuple[PlaceMoodMatch, ...] = (),
        available: bool = True,
        photo_urls: dict[str, str] | None = None,
        raises: Exception | None = None,
    ):
        self._raises = raises
        self.calls: list[dict[str, object]] = []
        self.photo_url_calls: list[list[str]] = []
        self._matches = matches
        self._photo_urls = photo_urls or {}
        self.photo_search_available = available

    async def search_by_photo(
        self,
        image_bytes,
        candidate_content_ids=None,
        *,
        latitude=None,
        longitude=None,
        radius_km=None,
        match_count=None,
    ):
        if self._raises is not None:
            raise self._raises
        self.calls.append(
            {
                "candidates": candidate_content_ids,
                "latitude": latitude,
                "longitude": longitude,
                "radius_km": radius_km,
                "match_count": match_count,
            }
        )
        return provider_result(
            self._matches,
            source=ProviderSource.SUPABASE_PLACE_MOOD,
            status=ProviderStatus.SUCCESS if self._matches else ProviderStatus.NO_DATA,
        )

    async def first_photo_urls(self, content_ids):
        self.photo_url_calls.append(list(content_ids))
        return dict(self._photo_urls)


class _RecordingDetails:
    def __init__(self, details: dict[str, _Detail] | None = None):
        self.calls: list[list[str]] = []
        self._details = details or {}

    async def get_active_place_details(self, content_ids, **kwargs):
        self.calls.append(list(content_ids))
        return {k: v for k, v in self._details.items() if k in set(content_ids)}


def _match(content_id: str, similarity: float, distance_km: float = 0.5) -> PlaceMoodMatch:
    return PlaceMoodMatch(
        content_id=content_id,
        similarity=similarity,
        profile=PlaceMoodProfile(
            content_id=content_id, axis_scores={"calm": 0.02}, photo_count=4
        ),
        distance_km=distance_km,
    )


@pytest.fixture
def patched(monkeypatch):
    """하드 필터를 대역으로 바꿔 외부 호출 없이 흐름만 본다."""
    seen: dict[str, object] = {}

    async def _fake_prepare(context, **kwargs):
        seen["prepare_kwargs"] = kwargs
        places = (context.places.data or []) if context.places else []
        closed = seen.get("closed_ids", set())
        rows = [
            type("Item", (), {"candidate": type("C", (), {"place_id": p.place_id})()})()
            for p in places
            if p.place_id not in closed
        ]
        return type(
            "P", (), {"preparation": type("Prep", (), {"eligible_candidates": tuple(rows)})()}
        )()

    monkeypatch.setattr(photo_similar, "map_location_context", lambda r: None)
    monkeypatch.setattr(photo_similar, "prepare_recommendation_from_context", _fake_prepare)
    return seen


@pytest.mark.asyncio
async def test_ranks_by_radius_not_by_candidate_list(patched) -> None:
    """후보 목록이 아니라 좌표·반경으로 부른다.

    후보 목록으로 부르면 그 목록을 만드는 데 TourAPI 상세 조회가 붙어 최대
    20곳이 된다.
    """
    mood = _RecordingMood(matches=(_match("a", 0.9),))
    details = _RecordingDetails({"a": _Detail("a", "장소 a")})

    await build_photo_similar_places(
        PhotoSimilarQuery(image_bytes=b"jpeg", latitude=37.57, longitude=126.98, limit=10),
        geocoding_provider=object(),
        place_provider=object(),
        mood_provider=mood,
        details_repository=details,
    )

    call = mood.calls[0]
    assert call["candidates"] is None
    assert call["latitude"] == pytest.approx(37.57)
    assert call["radius_km"] is not None
    # 영업시간에 걸려 빠질 것을 감안해 넉넉히 받는다.
    assert call["match_count"] == 10 * photo_similar._OVERFETCH_FACTOR


@pytest.mark.asyncio
async def test_details_are_fetched_only_for_ranked_places(patched) -> None:
    """비싼 조회를 "어차피 보여줄 곳"에만 쓴다."""
    mood = _RecordingMood(matches=(_match("a", 0.9), _match("b", 0.8)))
    details = _RecordingDetails(
        {"a": _Detail("a", "장소 a"), "b": _Detail("b", "장소 b")}
    )

    await build_photo_similar_places(
        PhotoSimilarQuery(image_bytes=b"x", latitude=37.5, longitude=127.0),
        geocoding_provider=object(),
        place_provider=object(),
        mood_provider=mood,
        details_repository=details,
    )

    assert details.calls == [["a", "b"]]


@pytest.mark.asyncio
async def test_photo_order_survives_the_hard_filter(patched) -> None:
    """하드 필터를 지나도 사진 유사도 순서가 유지된다."""
    mood = _RecordingMood(
        matches=(_match("a", 0.9), _match("b", 0.8), _match("c", 0.7))
    )
    details = _RecordingDetails(
        {cid: _Detail(cid, f"장소 {cid}") for cid in ("a", "b", "c")}
    )

    result = await build_photo_similar_places(
        PhotoSimilarQuery(image_bytes=b"x", latitude=37.5, longitude=127.0),
        geocoding_provider=object(),
        place_provider=object(),
        mood_provider=mood,
        details_repository=details,
    )

    assert [row.content_id for row in result.places] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_closed_places_are_dropped_and_counted(patched) -> None:
    """닫힌 곳은 빼고 몇 곳이 빠졌는지 남긴다.

    결과가 적을 때 "닮은 곳이 없다"인지 "다 닫혔다"인지 갈릴 수 있어야 한다.
    """
    patched["closed_ids"] = {"b"}
    mood = _RecordingMood(matches=(_match("a", 0.9), _match("b", 0.8)))
    details = _RecordingDetails(
        {"a": _Detail("a", "장소 a"), "b": _Detail("b", "장소 b")}
    )

    result = await build_photo_similar_places(
        PhotoSimilarQuery(image_bytes=b"x", latitude=37.5, longitude=127.0),
        geocoding_provider=object(),
        place_provider=object(),
        mood_provider=mood,
        details_repository=details,
    )

    assert [row.content_id for row in result.places] == ["a"]
    assert result.truncated_count == 1


@pytest.mark.asyncio
async def test_hard_filter_reuses_the_recommendation_path(patched) -> None:
    """영업시간 판정을 직접 만들지 않는다.

    여기서 새로 만들면 같은 장소가 추천에서는 열렸는데 사진 검색에서는 닫힌
    것으로 갈릴 수 있다.
    """
    mood = _RecordingMood(matches=(_match("a", 0.9),))
    details = _RecordingDetails({"a": _Detail("a", "장소 a")})

    await build_photo_similar_places(
        PhotoSimilarQuery(
            image_bytes=b"x", latitude=37.5, longitude=127.0, ignore_operating_hours=True
        ),
        geocoding_provider=object(),
        place_provider=object(),
        mood_provider=mood,
        details_repository=details,
    )

    assert patched["prepare_kwargs"]["ignore_operating_hours"] is True


@pytest.mark.asyncio
async def test_place_without_details_is_skipped(patched) -> None:
    """상세가 없으면 영업 여부를 판정할 수 없다. 열려 있다고 단정하지 않는다."""
    mood = _RecordingMood(matches=(_match("a", 0.9), _match("ghost", 0.8)))
    details = _RecordingDetails({"a": _Detail("a", "장소 a")})

    result = await build_photo_similar_places(
        PhotoSimilarQuery(image_bytes=b"x", latitude=37.5, longitude=127.0),
        geocoding_provider=object(),
        place_provider=object(),
        mood_provider=mood,
        details_repository=details,
    )

    assert [row.content_id for row in result.places] == ["a"]


@pytest.mark.asyncio
async def test_limit_is_applied_after_filtering(patched) -> None:
    """상한은 걸러낸 뒤에 적용한다. 먼저 자르면 닫힌 곳이 자리를 차지한다."""
    patched["closed_ids"] = {"a"}
    mood = _RecordingMood(
        matches=(_match("a", 0.9), _match("b", 0.8), _match("c", 0.7))
    )
    details = _RecordingDetails(
        {cid: _Detail(cid, f"장소 {cid}") for cid in ("a", "b", "c")}
    )

    result = await build_photo_similar_places(
        PhotoSimilarQuery(image_bytes=b"x", latitude=37.5, longitude=127.0, limit=2),
        geocoding_provider=object(),
        place_provider=object(),
        mood_provider=mood,
        details_repository=details,
    )

    assert [row.content_id for row in result.places] == ["b", "c"]


@pytest.mark.asyncio
async def test_first_photo_url_is_attached_for_shown_places_only(patched) -> None:
    """비교에 쓴 사진을 붙이되 보여줄 곳만 조회한다."""
    mood = _RecordingMood(
        matches=(_match("a", 0.9), _match("b", 0.8)),
        photo_urls={"a": "https://tong.visitkorea.or.kr/x.jpg"},
    )
    details = _RecordingDetails(
        {"a": _Detail("a", "장소 a"), "b": _Detail("b", "장소 b")}
    )

    result = await build_photo_similar_places(
        PhotoSimilarQuery(image_bytes=b"x", latitude=37.5, longitude=127.0, limit=1),
        geocoding_provider=object(),
        place_provider=object(),
        mood_provider=mood,
        details_repository=details,
    )

    assert result.places[0].image_url == "https://tong.visitkorea.or.kr/x.jpg"
    assert mood.photo_url_calls == [["a"]]


@pytest.mark.asyncio
async def test_no_ranked_result_returns_zero_candidates(patched) -> None:
    """반경 안에 사진 벡터가 있는 장소 자체가 없었다는 뜻이다."""
    mood = _RecordingMood(matches=())
    details = _RecordingDetails()

    result = await build_photo_similar_places(
        PhotoSimilarQuery(image_bytes=b"x", latitude=37.5, longitude=127.0),
        geocoding_provider=object(),
        place_provider=object(),
        mood_provider=mood,
        details_repository=details,
    )

    assert result.places == ()
    assert result.candidate_count == 0
    assert details.calls == []


@pytest.mark.asyncio
async def test_missing_location_asks_again(patched) -> None:
    """지역명도 좌표도 없으면 되묻는다. 임의로 어딘가를 고르지 않는다."""
    with pytest.raises(AppError) as error:
        await build_photo_similar_places(
            PhotoSimilarQuery(image_bytes=b"jpeg"),
            geocoding_provider=object(),
            place_provider=object(),
            mood_provider=_RecordingMood(),
            details_repository=_RecordingDetails(),
        )

    assert error.value.code == "location_required"


@pytest.mark.asyncio
async def test_encoder_absent_is_an_error_not_empty_result(patched) -> None:
    """기능이 꺼진 것과 "닮은 곳이 없다"는 다르다(D-042와 같은 이유)."""
    with pytest.raises(AppError) as error:
        await build_photo_similar_places(
            PhotoSimilarQuery(image_bytes=b"jpeg", latitude=37.5, longitude=127.0),
            geocoding_provider=object(),
            place_provider=object(),
            mood_provider=_RecordingMood(available=False),
            details_repository=_RecordingDetails(),
        )

    assert error.value.code == "photo_search_unavailable"


@pytest.mark.asyncio
async def test_empty_image_is_rejected(patched) -> None:
    with pytest.raises(AppError) as error:
        await build_photo_similar_places(
            PhotoSimilarQuery(image_bytes=b"", latitude=37.5, longitude=127.0),
            geocoding_provider=object(),
            place_provider=object(),
            mood_provider=_RecordingMood(),
            details_repository=_RecordingDetails(),
        )

    assert error.value.code == "empty_image"


@pytest.mark.asyncio
async def test_unreadable_image_is_a_user_error_not_a_server_error(patched) -> None:
    """열 수 없는 사진은 422다.

    500으로 두면 서버 잘못이라는 뜻이 되어 로그·모니터링에서 진짜 장애와 섞이고,
    화면도 "예상치 못한 오류"만 보여줘 사용자가 무엇을 고쳐야 할지 모른다.
    확장자만 사진인 파일이나 잘린 사진에서 실제로 난다 — MIME 검사로는 못
    걸러진다(content-type은 브라우저가 파일 이름으로 붙이는 값이다).
    """
    mood = _RecordingMood(raises=UnreadableImageError("형식을 알아볼 수 없습니다."))

    with pytest.raises(AppError) as error:
        await build_photo_similar_places(
            PhotoSimilarQuery(image_bytes=b"not-an-image", latitude=37.5, longitude=127.0),
            geocoding_provider=object(),
            place_provider=object(),
            mood_provider=mood,
            details_repository=_RecordingDetails(),
        )

    assert error.value.code == "unreadable_image"
    assert error.value.status_code == 422
    assert error.value.retryable is True


class _RecordingReranker:
    """VLM 대역. 넘어온 후보를 기록하고 정해진 순서를 돌려준다."""

    def __init__(self, order: tuple[str, ...] | None = None) -> None:
        self.calls: list[list[str]] = []
        self._order = order

    async def rerank(self, *, query_image, candidates):
        self.calls.append([c.content_id for c in candidates])
        return self._order


def _three_places() -> tuple[_RecordingMood, _RecordingDetails]:
    matches = (_match("a", 0.80), _match("b", 0.70), _match("c", 0.60))
    mood = _RecordingMood(
        matches=matches,
        photo_urls={"a": "https://x/a.jpg", "b": "https://x/b.jpg", "c": "https://x/c.jpg"},
    )
    details = _RecordingDetails(
        {cid: _Detail(cid, f"장소 {cid}") for cid in ("a", "b", "c")}
    )
    return mood, details


@pytest.mark.asyncio
async def test_rerank_reorders_and_can_pull_up_a_lower_candidate(patched) -> None:
    """재랭커가 뒤쪽 후보를 앞으로 끌어올릴 수 있다.

    **자르기 전에 재랭킹해야 한다.** 보여줄 수만큼 먼저 자르면 VLM은 순서만
    바꾸고 어떤 곳이 나올지는 못 바꾼다 — 그러면 개선의 절반이 사라진다.
    """
    mood, details = _three_places()
    reranker = _RecordingReranker(order=("c", "a", "b"))

    result = await build_photo_similar_places(
        PhotoSimilarQuery(image_bytes=b"jpeg", latitude=37.57, longitude=126.98, limit=2),
        geocoding_provider=object(),
        place_provider=object(),
        mood_provider=mood,
        details_repository=details,
        reranker=reranker,
    )

    # limit이 2인데 3곳 전부가 재랭커에 넘어갔다.
    assert reranker.calls == [["a", "b", "c"]]
    # 임베딩 3위였던 c가 1위로 올라와 결과에 들어왔다.
    assert [row.content_id for row in result.places] == ["c", "a"]


@pytest.mark.asyncio
async def test_rerank_skipped_when_top_similarity_below_threshold(patched, monkeypatch) -> None:
    """1위 유사도가 문턱 미만이면 부르지 않는다.

    닮은 곳이 DB에 없다는 뜻이라 후보가 전부 안 맞는 곳이고, 순서를 바꿔봐야
    나아지지 않는다(TP-213 확인 22).
    """
    monkeypatch.setattr(
        photo_similar.settings, "place_mood_rerank_min_top_similarity", 0.90
    )
    mood, details = _three_places()  # 1위가 0.80이라 문턱 아래다
    reranker = _RecordingReranker(order=("c", "a", "b"))

    result = await build_photo_similar_places(
        PhotoSimilarQuery(image_bytes=b"jpeg", latitude=37.57, longitude=126.98, limit=3),
        geocoding_provider=object(),
        place_provider=object(),
        mood_provider=mood,
        details_repository=details,
        reranker=reranker,
    )

    assert reranker.calls == []
    assert [row.content_id for row in result.places] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_rerank_failure_keeps_embedding_order(patched) -> None:
    """재랭커가 None을 주면 임베딩 순서를 그대로 낸다.

    재랭킹은 보강이지 필수가 아니다. 실패가 검색 실패가 되면 안 된다.
    """
    mood, details = _three_places()
    reranker = _RecordingReranker(order=None)

    result = await build_photo_similar_places(
        PhotoSimilarQuery(image_bytes=b"jpeg", latitude=37.57, longitude=126.98, limit=3),
        geocoding_provider=object(),
        place_provider=object(),
        mood_provider=mood,
        details_repository=details,
        reranker=reranker,
    )

    assert reranker.calls == [["a", "b", "c"]]
    assert [row.content_id for row in result.places] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_without_reranker_only_shown_places_get_photo_urls(patched) -> None:
    """재랭커가 없으면 사진 주소를 보여줄 곳에만 붙인다.

    후보 전체에 붙이면 재랭킹을 안 쓰는 환경이 값을 더 치른다.
    """
    mood, details = _three_places()

    await build_photo_similar_places(
        PhotoSimilarQuery(image_bytes=b"jpeg", latitude=37.57, longitude=126.98, limit=1),
        geocoding_provider=object(),
        place_provider=object(),
        mood_provider=mood,
        details_repository=details,
    )

    assert mood.photo_url_calls == [["a"]]


@pytest.mark.asyncio
async def test_rerank_keeps_places_the_model_did_not_return(patched) -> None:
    """재랭커가 빠뜨린 곳은 원래 순서로 뒤에 붙는다.

    사진을 못 받아 후보에서 빠지는 일이 있다 — 그때 그 장소가 결과에서 통째로
    사라지면 안 된다.
    """
    mood, details = _three_places()
    reranker = _RecordingReranker(order=("c", "a"))  # b가 빠졌다

    result = await build_photo_similar_places(
        PhotoSimilarQuery(image_bytes=b"jpeg", latitude=37.57, longitude=126.98, limit=3),
        geocoding_provider=object(),
        place_provider=object(),
        mood_provider=mood,
        details_repository=details,
        reranker=reranker,
    )

    assert [row.content_id for row in result.places] == ["c", "a", "b"]


@pytest.mark.asyncio
async def test_좌표로만_찾으면_기준점_이름이_현재_위치다(patched) -> None:
    """`center_name`은 화면에 그대로 실린다.

    도메인 값("기기 GPS 위치")을 그대로 내면 화면이 "기기 GPS 위치 주변에서
    분위기가 닮은 곳이에요"가 된다. 저장소의 다른 곳도 좌표뿐인 기준점을
    "현재 위치"라고 부른다(domain/explanation.py).
    """
    mood = _RecordingMood(matches=(_match("a", 0.9),))
    details = _RecordingDetails({"a": _Detail("a", "장소 a")})

    result = await build_photo_similar_places(
        PhotoSimilarQuery(image_bytes=b"jpeg", latitude=37.57, longitude=126.98, limit=10),
        geocoding_provider=object(),
        place_provider=object(),
        mood_provider=mood,
        details_repository=details,
    )

    assert result.center_name == "현재 위치"
