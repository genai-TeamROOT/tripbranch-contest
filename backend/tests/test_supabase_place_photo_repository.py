"""장소 사진 목록 조회(place_image_embeddings)의 요청 구성과 응답 변환 테스트.

역할: 상세 화면이 여러 장을 보여주기 위해 읽는 find_place_photos()가 순서·상한·
      빠진 장소를 어떻게 다루는지 못 박는다.
입력: MockTransport가 가로챈 PostgREST 요청과 그 응답.
출력: 요청 파라미터와 변환된 PlacePhoto에 대한 assertion.
호출 시점: 로컬 테스트와 CI에서 pytest 실행 시.
"""

from __future__ import annotations

import httpx
import pytest

from app.repositories.supabase_places import (
    SupabasePlaceRepository,
    SupabaseRepositoryError,
)

_SECRET = "super-secret-service-key"
_URL = "https://example.supabase.co"


def _row(content_id: str, photo_order: int, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "content_id": content_id,
        "photo_order": photo_order,
        "origin_url": f"https://tong.visitkorea.or.kr/{content_id}-{photo_order}.jpg",
        "image_name": f"테스트 장소 ({photo_order})",
    }
    row.update(overrides)
    return row


async def _repository(handler) -> tuple[SupabasePlaceRepository, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SupabasePlaceRepository(_URL, _SECRET, client), client


@pytest.mark.asyncio
async def test_request_orders_by_photo_order_and_asks_for_display_columns() -> None:
    """번호로 거르지 않는다 — 자르는 일은 정렬한 뒤 파이썬에서 한다."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[_row("1013076", 1)])

    repository, client = await _repository(handler)
    async with client:
        await repository.find_place_photos(["1013076"])

    params = seen[0].url.params
    assert seen[0].url.path.endswith("/place_image_embeddings")
    assert params["content_id"] == "in.(1013076)"
    assert params["order"] == "content_id.asc,photo_order.asc"
    # 번호 필터를 걸면 빈 번호가 있는 장소에서 앞쪽 사진이 빠진다.
    assert "photo_order" not in params
    # 임베딩 벡터는 읽지 않는다. 768차원을 실어 오면 응답만 커지고 쓰임이 없다.
    assert "embedding" not in params["select"]
    assert params["select"] == "content_id,photo_order,origin_url,image_name"


@pytest.mark.asyncio
async def test_gap_in_photo_order_does_not_drop_photos() -> None:
    """빈 번호가 있는 장소도 가진 사진을 다 돌려준다.

    김희수아트센터가 [1,2,3,4,5,6,8,12]로 8장인데 번호는 12까지 간다(5,465곳 중
    7곳이 이런 모양이다). 번호로 잘랐을 때 12번이 빠지던 자리다.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[_row("3057945", order) for order in (1, 2, 3, 4, 5, 6, 8, 12)],
        )

    repository, client = await _repository(handler)
    async with client:
        photos = await repository.find_place_photos(["3057945"])

    assert [photo.photo_order for photo in photos["3057945"]] == [1, 2, 3, 4, 5, 6, 8, 12]


@pytest.mark.asyncio
async def test_keeps_only_the_first_ten_photos() -> None:
    """상한은 순서로 센다 — 번호가 얼마인지와 무관하게 앞에서 열 장이다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[_row("1013076", order) for order in range(1, 15)],
        )

    repository, client = await _repository(handler)
    async with client:
        photos = await repository.find_place_photos(["1013076"])

    assert [photo.photo_order for photo in photos["1013076"]] == list(range(1, 11))


@pytest.mark.asyncio
async def test_photos_come_back_in_photo_order() -> None:
    """응답이 뒤섞여 와도 photo_order 순서로 돌려준다 — 1번이 대표 사진이다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[_row("1013076", 3), _row("1013076", 1), _row("1013076", 2)],
        )

    repository, client = await _repository(handler)
    async with client:
        photos = await repository.find_place_photos(["1013076"])

    assert [photo.photo_order for photo in photos["1013076"]] == [1, 2, 3]
    assert photos["1013076"][0].url.endswith("1013076-1.jpg")
    assert photos["1013076"][0].image_name == "테스트 장소 (1)"


@pytest.mark.asyncio
async def test_places_without_photos_are_absent_not_empty() -> None:
    """사진이 없는 장소는 키가 없다. 빈 튜플로 채우면 미조회와 구분되지 않는다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_row("1013076", 1)])

    repository, client = await _repository(handler)
    async with client:
        photos = await repository.find_place_photos(["1013076", "9999999"])

    assert "9999999" not in photos
    assert set(photos) == {"1013076"}


@pytest.mark.asyncio
async def test_row_without_url_is_dropped_but_others_survive() -> None:
    """주소가 빈 행은 뺀다 — 화면에 깨진 이미지를 그리는 것보다 한 장 적은 편이 낫다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[_row("1013076", 1, origin_url=""), _row("1013076", 2)],
        )

    repository, client = await _repository(handler)
    async with client:
        photos = await repository.find_place_photos(["1013076"])

    assert [photo.photo_order for photo in photos["1013076"]] == [2]


@pytest.mark.asyncio
async def test_empty_input_does_not_call_supabase() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=[])

    repository, client = await _repository(handler)
    async with client:
        assert await repository.find_place_photos([]) == {}

    assert calls == []


@pytest.mark.asyncio
async def test_non_list_payload_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": "unexpected"})

    repository, client = await _repository(handler)
    async with client:
        with pytest.raises(SupabaseRepositoryError):
            await repository.find_place_photos(["1013076"])
