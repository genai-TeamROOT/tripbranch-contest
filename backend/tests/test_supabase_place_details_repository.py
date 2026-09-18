"""Supabase places 상세 배치 조회의 요청 구성과 오류 처리 테스트."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.providers.supabase_place_details import SupabasePlaceDetailsProvider
from app.repositories.supabase_places import (
    SupabasePlaceRepository,
    SupabaseRepositoryError,
)

_SECRET = "super-secret-service-key"
_URL = "https://example.supabase.co"


def _row(content_id: str) -> dict[str, object]:
    return {
        "content_id": content_id,
        "content_type_id": "12",
        "title": f"장소 {content_id}",
        "address": "서울특별시 종로구",
        "operating_hours_raw": "09:00~18:00",
        "rest_date_raw": None,
        "detail_fetch_status": "success",
        "detail_fetched_at": "2026-07-20T03:00:00+00:00",
        "source_modified_at": None,
        "lcls_systm1": "HS",
        "lcls_systm2": "HS01",
        "lcls_systm3": "HS010100",
        "parking_info_raw": "가능 (승용차 240대 / 버스 50대)",
        "parking_fee_raw": None,
        "first_image_url": "https://example.test/first.jpg",
        "thumbnail_url": "https://example.test/thumb.jpg",
    }


@pytest.mark.asyncio
async def test_card_columns_are_selected_and_mapped() -> None:
    """추천 카드용 컬럼이 select에서 빠지면 조립 측이 조용히 전부 None을 받는다."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[_row("a")])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        repository = SupabasePlaceRepository(_URL, _SECRET, client)
        rows = await repository.get_active_place_details(["a"])

    selected = seen[0].url.params["select"].split(",")
    for column in (
        "lcls_systm1",
        "lcls_systm2",
        "lcls_systm3",
        "parking_info_raw",
        "parking_fee_raw",
        "first_image_url",
        "thumbnail_url",
    ):
        assert column in selected

    row = rows["a"]
    assert row.lcls_systm3 == "HS010100"
    assert row.parking_info_raw == "가능 (승용차 240대 / 버스 50대)"
    assert row.thumbnail_url == "https://example.test/thumb.jpg"
    assert row.first_image_url == "https://example.test/first.jpg"


@pytest.mark.asyncio
async def test_batch_query_filters_active_places_by_content_id() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[_row("a"), _row("b")])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        repository = SupabasePlaceRepository(_URL, _SECRET, client)
        rows = await repository.get_active_place_details(["a", "b"])

    assert set(rows) == {"a", "b"}
    assert rows["a"].detail_fetched_at == datetime(2026, 7, 20, 3, 0, tzinfo=UTC)
    # 후보 여러 건을 개별 조회하지 않고 in.(...) 한 번으로 묶는다.
    assert len(seen) == 1
    query = seen[0].url.params
    assert query["content_id"] == 'in.("a","b")'
    assert query["is_active"] == "eq.true"


@pytest.mark.asyncio
async def test_empty_content_ids_skips_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("요청이 발생하면 안 됩니다.")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        repository = SupabasePlaceRepository(_URL, _SECRET, client)
        assert await repository.get_active_place_details([]) == {}


@pytest.mark.asyncio
async def test_error_message_does_not_leak_secret_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # 요청 헤더에는 키가 실리고, 오류 응답의 부가 필드에도 키가 섞여 온다.
        assert request.headers["apikey"] == _SECRET
        return httpx.Response(
            500,
            json={
                "code": "PGRST500",
                "message": "internal error",
                "hint": f"check apikey={_SECRET}",
                "details": _SECRET,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        repository = SupabasePlaceRepository(_URL, _SECRET, client)
        provider = SupabasePlaceDetailsProvider(repository)
        with pytest.raises(SupabaseRepositoryError) as exc_info:
            await provider.get_details_batch(["a"])

    error = exc_info.value
    # code/message만 추려 담으므로 hint·details를 통해 키가 새지 않는다.
    rendered = f"{error} {error.message} {error.details} {error.code}"
    assert _SECRET not in rendered
    assert "PGRST500" in rendered


@pytest.mark.asyncio
async def test_invalid_response_shape_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        repository = SupabasePlaceRepository(_URL, _SECRET, client)
        with pytest.raises(SupabaseRepositoryError):
            await repository.get_active_place_details(["a"])


@pytest.mark.asyncio
async def test_무장애_정보는_요청할_때만_함께_읽는다() -> None:
    """이 메서드를 부르는 세 곳 중 둘은 무장애 값을 읽지 않는다.

    추천 카드 조립과 추천 후보 상세가 그 둘이다. 임베드를 항상 붙이면 두 경로가
    쓰지도 않는 데이터를 매 요청마다 받아온다.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[_row("a")])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        repository = SupabasePlaceRepository(_URL, _SECRET, client)
        await repository.get_active_place_details(["a"])
        await repository.get_active_place_details(["a"], include_barrier_free=True)

    assert "place_barrier_free" not in seen[0].url.params["select"]
    select = seen[1].url.params["select"]
    assert "place_barrier_free(" in select
    for column in (
        "approach_route_raw",
        "entrance_access_raw",
        "elevator_raw",
        "accessible_restroom_raw",
        "wheelchair_rental_raw",
        "braille_promotion_raw",
        "disability_etc_raw",
    ):
        assert column in select


@pytest.mark.asyncio
async def test_임베드로_온_무장애_값이_행에_담긴다() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        row = _row("a")
        row["place_barrier_free"] = {
            "approach_route_raw": "출입구까지 턱이 없어 휠체어 접근 가능함",
            "entrance_access_raw": "주출입구는 경사로가 있어 휠체어 접근 가능함",
            "accessible_restroom_raw": "장애인 화장실 있음",
            "wheelchair_rental_raw": "대여가능",
        }
        return httpx.Response(200, json=[row])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        repository = SupabasePlaceRepository(_URL, _SECRET, client)
        rows = await repository.get_active_place_details(
            ["a"], include_barrier_free=True
        )

    detail = rows["a"]
    assert detail.approach_route_raw == "출입구까지 턱이 없어 휠체어 접근 가능함"
    assert detail.entrance_access_raw == "주출입구는 경사로가 있어 휠체어 접근 가능함"
    assert detail.accessible_restroom_raw == "장애인 화장실 있음"
    assert detail.wheelchair_rental_raw == "대여가능"
    # 응답에 없던 필드는 None이다. 빈 문자열이나 기본값을 지어내지 않는다.
    assert detail.elevator_raw is None
    assert detail.nursing_room_raw is None


@pytest.mark.asyncio
async def test_무장애_행이_없는_장소는_전부_None이다() -> None:
    """무장애 목록에 없는 장소는 place_barrier_free에 행이 없다(D-077).

    PostgREST는 그 자리를 null로 돌려준다. 이 경우가 대부분이라(4개 구 실측
    커버리지 19%) 여기서 터지면 INFO 상세 조회 전체가 멈춘다.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        row = _row("a")
        row["place_barrier_free"] = None
        return httpx.Response(200, json=[row])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        repository = SupabasePlaceRepository(_URL, _SECRET, client)
        rows = await repository.get_active_place_details(
            ["a"], include_barrier_free=True
        )

    detail = rows["a"]
    assert detail.approach_route_raw is None
    assert detail.accessible_restroom_raw is None
    assert detail.disability_etc_raw is None
    # 무장애 값과 무관한 필드는 그대로 채워진다.
    assert detail.title == "장소 a"
