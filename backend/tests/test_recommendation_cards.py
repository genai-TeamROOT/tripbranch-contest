"""추천 카드 조립 Tool의 순서 보존·필드 매핑·실패 처리 테스트."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from app.domain.models import StoredPlaceDetail
from app.domain.parking import ParkingAvailability
from app.repositories.fake_places import FakePlaceDetailsRepository
from app.repositories.supabase_places import SupabaseRepositoryError
from app.tools.contracts import ToolStatus
from app.tools.recommendation_cards import RecommendationCardTool

_FETCHED_AT = datetime(2026, 8, 10, 3, 0, tzinfo=UTC)


def _row(
    content_id: str,
    *,
    title: str | None = None,
    lcls_systm1: str | None = "HS",
    lcls_systm2: str | None = "HS01",
    lcls_systm3: str | None = "HS010100",
    parking_info_raw: str | None = "가능",
    first_image_url: str | None = "https://example.test/first.jpg",
    thumbnail_url: str | None = "https://example.test/thumb.jpg",
) -> StoredPlaceDetail:
    return StoredPlaceDetail(
        content_id=content_id,
        content_type_id="12",
        title=title or f"장소 {content_id}",
        address="서울특별시 종로구",
        operating_hours_raw="09:00~18:00",
        rest_date_raw="매주 월요일",
        detail_fetch_status="success",
        detail_fetched_at=_FETCHED_AT,
        source_modified_at=None,
        lcls_systm1=lcls_systm1,
        lcls_systm2=lcls_systm2,
        lcls_systm3=lcls_systm3,
        parking_info_raw=parking_info_raw,
        parking_fee_raw=None,
        first_image_url=first_image_url,
        thumbnail_url=thumbnail_url,
    )


class FakeRepository:
    def __init__(
        self,
        rows: dict[str, StoredPlaceDetail],
        error: Exception | None = None,
    ) -> None:
        self.rows = rows
        self.error = error
        self.calls: list[list[str]] = []

    async def get_active_place_details(
        self, content_ids: Sequence[str]
    ) -> dict[str, StoredPlaceDetail]:
        self.calls.append(list(content_ids))
        if self.error is not None:
            raise self.error
        # 실제 저장소(PostgREST in.(...))는 입력 순서를 보장하지 않는다. 조회 순서에
        # 기대는 구현을 잡아내려고 일부러 뒤집어 돌려준다.
        return {
            content_id: self.rows[content_id]
            for content_id in reversed(list(content_ids))
            if content_id in self.rows
        }


@pytest.mark.asyncio
async def test_cards_follow_requested_order_not_repository_order() -> None:
    """A가 정한 순위가 저장소 응답 순서에 밀리면 안 된다."""
    rows = {content_id: _row(content_id) for content_id in ("c", "a", "b")}
    tool = RecommendationCardTool(FakeRepository(rows))

    result = await tool.get_cards(["c", "a", "b"])

    assert result.status is ToolStatus.SUCCESS
    assert [card.content_id for card in result.cards] == ["c", "a", "b"]


@pytest.mark.asyncio
async def test_missing_ids_are_reported_not_silently_dropped() -> None:
    rows = {"a": _row("a"), "c": _row("c")}
    tool = RecommendationCardTool(FakeRepository(rows))

    result = await tool.get_cards(["a", "b", "c"])

    assert result.status is ToolStatus.PARTIAL
    assert [card.content_id for card in result.cards] == ["a", "c"]
    assert result.missing_content_ids == ("b",)


@pytest.mark.asyncio
async def test_duplicate_ids_are_queried_and_returned_once() -> None:
    repository = FakeRepository({"a": _row("a"), "b": _row("b")})
    tool = RecommendationCardTool(repository)

    result = await tool.get_cards(["a", "b", "a"])

    assert [card.content_id for card in result.cards] == ["a", "b"]
    assert repository.calls == [["a", "b"]]


@pytest.mark.asyncio
async def test_empty_input_skips_repository_call() -> None:
    repository = FakeRepository({})
    tool = RecommendationCardTool(repository)

    result = await tool.get_cards([])

    assert result.status is ToolStatus.NO_DATA
    assert result.cards == ()
    assert repository.calls == []


@pytest.mark.asyncio
async def test_all_missing_is_no_data() -> None:
    tool = RecommendationCardTool(FakeRepository({}))

    result = await tool.get_cards(["a", "b"])

    assert result.status is ToolStatus.NO_DATA
    assert result.missing_content_ids == ("a", "b")


@pytest.mark.asyncio
async def test_category_label_uses_middle_classification() -> None:
    """대분류(역사관광)도 소분류(고궁)도 아닌 중분류(역사유적지)를 쓴다."""
    rows = {"a": _row("a", lcls_systm3="HS010100")}
    tool = RecommendationCardTool(FakeRepository(rows))

    result = await tool.get_cards(["a"])

    assert result.cards[0].category_label == "역사유적지"


@pytest.mark.asyncio
async def test_category_label_falls_back_to_middle_code() -> None:
    rows = {"a": _row("a", lcls_systm3=None, lcls_systm2="FD05")}
    tool = RecommendationCardTool(FakeRepository(rows))

    result = await tool.get_cards(["a"])

    assert result.cards[0].category_label == "카페/ 찻집"


@pytest.mark.asyncio
async def test_unknown_category_code_yields_none_not_crash() -> None:
    rows = {"a": _row("a", lcls_systm3="ZZ999999", lcls_systm2="ZZ99")}
    tool = RecommendationCardTool(FakeRepository(rows))

    result = await tool.get_cards(["a"])

    assert result.cards[0].category_label is None


@pytest.mark.asyncio
async def test_thumbnail_falls_back_to_first_image() -> None:
    rows = {"a": _row("a", thumbnail_url=None)}
    tool = RecommendationCardTool(FakeRepository(rows))

    result = await tool.get_cards(["a"])

    assert result.cards[0].thumbnail_url == "https://example.test/first.jpg"


@pytest.mark.asyncio
async def test_missing_images_leave_thumbnail_none() -> None:
    """이미지가 없는 장소가 실측 844건 중 169건(20%)이라 None이 정상 값이다."""
    rows = {"a": _row("a", thumbnail_url=None, first_image_url=None)}
    tool = RecommendationCardTool(FakeRepository(rows))

    result = await tool.get_cards(["a"])

    assert result.cards[0].thumbnail_url is None


@pytest.mark.asyncio
async def test_fallback_thumbnail_carries_the_other_url() -> None:
    """두 주소를 다 넘긴다 — 작은 썸네일만 죽은 장소가 있다.

    아현시장(2751432)은 firstimage2가 404인데 firstimage는 200이다(2026-09-05 실측).
    주소가 null이 아니라 살아 있는 척 죽어 있어서, thumbnail_url의 `or` 폴백으로는
    걸러지지 않는다. 프론트가 실패한 카드에서만 두 번째를 부른다.
    """
    rows = {"a": _row("a")}
    tool = RecommendationCardTool(FakeRepository(rows))

    result = await tool.get_cards(["a"])

    assert result.cards[0].thumbnail_url == "https://example.test/thumb.jpg"
    assert result.cards[0].fallback_thumbnail_url == "https://example.test/first.jpg"


@pytest.mark.asyncio
async def test_fallback_thumbnail_is_none_when_it_would_repeat_primary() -> None:
    """이미 primary로 나간 주소는 대안이 아니다.

    두 컬럼이 같은 파일을 가리키는 장소가 있고(실측), thumbnail_url이 비어 first_image_url이
    primary가 된 경우도 마찬가지다. 그대로 두면 프론트가 같은 404를 두 번 부른다.
    """
    same = {"a": _row("a", thumbnail_url="https://example.test/first.jpg")}
    tool = RecommendationCardTool(FakeRepository(same))
    result = await tool.get_cards(["a"])
    assert result.cards[0].fallback_thumbnail_url is None

    promoted = {"a": _row("a", thumbnail_url=None)}
    tool = RecommendationCardTool(FakeRepository(promoted))
    result = await tool.get_cards(["a"])
    assert result.cards[0].thumbnail_url == "https://example.test/first.jpg"
    assert result.cards[0].fallback_thumbnail_url is None

    none_at_all = {"a": _row("a", thumbnail_url=None, first_image_url=None)}
    tool = RecommendationCardTool(FakeRepository(none_at_all))
    result = await tool.get_cards(["a"])
    assert result.cards[0].fallback_thumbnail_url is None


@pytest.mark.asyncio
async def test_parking_is_normalized_onto_the_card() -> None:
    rows = {"a": _row("a", parking_info_raw="불가능")}
    tool = RecommendationCardTool(FakeRepository(rows))

    result = await tool.get_cards(["a"])

    card = result.cards[0]
    assert card.parking_status is ParkingAvailability.UNAVAILABLE
    assert card.parking_note == "불가능"


@pytest.mark.asyncio
async def test_operating_schedule_is_normalized_from_raw() -> None:
    tool = RecommendationCardTool(FakeRepository({"a": _row("a")}))

    result = await tool.get_cards(["a"])

    schedule = result.cards[0].operating_schedule
    assert schedule.cleaned_operating_hours == "09:00~18:00"
    assert schedule.rules


@pytest.mark.asyncio
async def test_repository_failure_is_reported_as_unavailable() -> None:
    repository = FakeRepository({}, error=SupabaseRepositoryError("boom"))
    tool = RecommendationCardTool(repository)

    result = await tool.get_cards(["a", "b"])

    assert result.status is ToolStatus.UNAVAILABLE
    assert result.cards == ()
    assert result.missing_content_ids == ("a", "b")
    assert result.error is not None
    assert result.error.retryable is True


@pytest.mark.asyncio
async def test_repository_failure_is_logged_not_swallowed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """실패를 로그로 남기는지 못박는다.

    부르는 쪽(A의 _with_thumbnails)은 이 결과를 상태로만 받고 추천을 그대로
    내보낸다. 그래서 화면에는 그 턴의 썸네일이 통째로 빠진 채 자리표시 칩만
    남는데, 여기서 남기지 않으면 "이 장소들에 원래 사진이 없다"와 구분할 수
    없다 — 같은 장소가 어떤 요청에는 사진이 나오고 어떤 요청에는 안 나오는
    이유를 서버 로그로 가리려면 이 줄이 있어야 한다.
    """
    repository = FakeRepository({}, error=SupabaseRepositoryError("boom"))
    tool = RecommendationCardTool(repository)

    with caplog.at_level(logging.WARNING, logger="app.tools.recommendation_cards"):
        await tool.get_cards(["a", "b"])

    assert "추천 카드 조회 실패" in caplog.text
    # 몇 건이 사진을 잃었는지가 있어야 전멸인지 일부인지 로그만으로 판단할 수 있다.
    assert "요청=2건" in caplog.text


@pytest.mark.asyncio
async def test_missing_ids_are_logged_apart_from_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """행이 없는 것은 조회 실패와 다른 사건이라 따로 남긴다."""
    repository = FakeRepository({"a": _row("a")})
    tool = RecommendationCardTool(repository)

    with caplog.at_level(logging.INFO, logger="app.tools.recommendation_cards"):
        await tool.get_cards(["a", "b"])

    assert "추천 카드 일부 없음" in caplog.text
    assert "없음=1건" in caplog.text
    # 채울 대상을 고르려면 어느 id인지가 필요하다.
    assert "id=b" in caplog.text
    assert "추천 카드 조회 실패" not in caplog.text


@pytest.mark.asyncio
async def test_fake_repository_exercises_parking_and_category_paths() -> None:
    """Fake가 소비 측 판정을 실제로 움직이는지 확인한다.

    주차·분류 코드를 비워둔 fake를 쓰면 카드 조립은 통과하는데 판정 로직은 한 줄도
    실행되지 않는다. 이 저장소에서 반복된 실패 유형이라 테스트로 못 박는다.
    """
    tool = RecommendationCardTool(FakePlaceDetailsRepository())

    result = await tool.get_cards(["fake-museum-1", "fake-cafe-1"])

    assert result.status is ToolStatus.SUCCESS
    museum, cafe = result.cards
    # `<br>` 정리와 note 보존이 실제로 돌아간다.
    assert museum.parking_status is ParkingAvailability.AVAILABLE
    assert museum.parking_note is not None
    assert "<br>" not in museum.parking_note
    assert museum.category_label == "전시시설"
    # 판정이 가장 쉽게 뒤집히는 값이 fake에도 들어 있다.
    assert cafe.parking_status is ParkingAvailability.UNAVAILABLE
    assert cafe.category_label == "카페/ 찻집"
    assert cafe.thumbnail_url is None
