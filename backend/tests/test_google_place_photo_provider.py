"""Google Places 사진 보강의 요청 구성과 실패 처리 테스트.

역할: 이미지가 없는 장소에 붙일 대표 사진을 찾는 Provider가 올바른 요청을
      보내고, 못 찾거나 실패했을 때 추천을 깨지 않는지 확인한다.
입력: MockTransport가 가로챈 Google Places 요청과 그 응답.
출력: 요청 헤더·본문·파라미터와 반환된 이미지 주소에 대한 assertion.
호출 시점: 로컬 테스트와 CI에서 pytest 실행 시.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from app.providers.google_place_photos import (
    FakeGooglePlacePhotoProvider,
    GooglePlacePhotoProvider,
    reset_google_photo_state,
)

_PHOTO_NAME = "places/ChIJtest/photos/AelY_photo_reference"
_PHOTO_URI = "https://lh3.googleusercontent.com/places/test-photo.jpg"


@pytest.fixture(autouse=True)
def _isolate_module_state() -> None:
    """캐시와 래치가 모듈 수준이라 테스트끼리 샌다. 매번 비운다."""
    reset_google_photo_state()


def _search_payload(*, photos: list[dict[str, str]] | None = None) -> dict[str, object]:
    if photos is None:
        photos = [{"name": _PHOTO_NAME}]
    return {"places": [{"photos": photos}]}


def _handler(
    *,
    search: httpx.Response | None = None,
    photo: httpx.Response | None = None,
    seen: list[httpx.Request] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.url.path.endswith(":searchText"):
            return search or httpx.Response(200, json=_search_payload())
        return photo or httpx.Response(200, json={"photoUri": _PHOTO_URI})

    return handle


def _provider(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    cache_ttl_seconds: int = 3600,
) -> tuple[GooglePlacePhotoProvider, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GooglePlacePhotoProvider(
        api_key="test-key",
        client=client,
        timeout_seconds=5.0,
        max_width_px=800,
        cache_ttl_seconds=cache_ttl_seconds,
    )
    return provider, client


@pytest.mark.asyncio
async def test_이름과_주소로_검색하고_사진_주소를_돌려준다() -> None:
    seen: list[httpx.Request] = []
    provider, client = _provider(_handler(seen=seen))

    async with client:
        result = await provider.find_cover_photo(
            name="안국역 카페",
            address="서울특별시 종로구 율곡로 1",
            latitude=37.5765,
            longitude=126.9860,
        )

    assert result == _PHOTO_URI
    assert len(seen) == 2

    search = seen[0]
    assert search.url.path.endswith("/places:searchText")
    # 키를 쿼리가 아니라 헤더로 보낸다. 실패 로그에 URL이 남는 경로가 있어
    # 쿼리로 실으면 키가 그대로 새어 나간다.
    assert search.headers["X-Goog-Api-Key"] == "test-key"
    assert "test-key" not in str(search.url)
    # FieldMask가 없으면 Google이 400으로 거절한다. 넓게 잡으면 비싼 SKU가 된다.
    assert search.headers["X-Goog-FieldMask"] == "places.photos.name"

    body = search.read().decode()
    assert "안국역 카페 서울특별시 종로구 율곡로 1" in body
    # 좌표를 주면 같은 이름의 다른 지점을 가져오지 않도록 반경으로 묶는다.
    assert "locationBias" in body

    media = seen[1]
    assert media.url.path.endswith(f"/{_PHOTO_NAME}/media")
    assert media.url.params["maxWidthPx"] == "800"
    # 이 값이 없으면 Google이 이미지 파일로 리다이렉트해, 서버가 이미지 바이트를
    # 받아 다시 내려보내야 한다.
    assert media.url.params["skipHttpRedirect"] == "true"


@pytest.mark.asyncio
async def test_좌표가_없으면_반경을_걸지_않는다() -> None:
    seen: list[httpx.Request] = []
    provider, client = _provider(_handler(seen=seen))

    async with client:
        await provider.find_cover_photo(name="이름만 아는 곳")

    assert "locationBias" not in seen[0].read().decode()


@pytest.mark.asyncio
async def test_검색_결과가_없으면_None이고_사진은_부르지_않는다() -> None:
    seen: list[httpx.Request] = []
    provider, client = _provider(
        _handler(search=httpx.Response(200, json={}), seen=seen)
    )

    async with client:
        result = await provider.find_cover_photo(name="없는 장소")

    assert result is None
    # 장소를 못 찾았는데 사진을 부르면 돈만 나간다.
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_장소는_찾았는데_사진이_없으면_None이다() -> None:
    provider, client = _provider(
        _handler(search=httpx.Response(200, json={"places": [{}]}))
    )

    async with client:
        result = await provider.find_cover_photo(name="사진 없는 장소")

    assert result is None


@pytest.mark.asyncio
async def test_사진_없음도_캐시해서_다시_부르지_않는다() -> None:
    """빈 결과를 캐시하지 않으면 사진 없는 장소가 카드에 뜰 때마다 계속 부른다."""
    seen: list[httpx.Request] = []
    provider, client = _provider(
        _handler(search=httpx.Response(200, json={}), seen=seen)
    )

    async with client:
        assert await provider.find_cover_photo(name="없는 장소") is None
        assert await provider.find_cover_photo(name="없는 장소") is None

    assert len(seen) == 1


@pytest.mark.asyncio
async def test_같은_장소를_두_번_물으면_호출은_한_번이다() -> None:
    seen: list[httpx.Request] = []
    provider, client = _provider(_handler(seen=seen))

    async with client:
        first = await provider.find_cover_photo(name="안국역 카페", latitude=37.5, longitude=127.0)
        second = await provider.find_cover_photo(name="안국역 카페", latitude=37.5, longitude=127.0)

    assert first == second == _PHOTO_URI
    assert len(seen) == 2  # 첫 요청의 검색 1 + 사진 1뿐이다.


@pytest.mark.asyncio
async def test_캐시를_끄면_매번_부른다() -> None:
    seen: list[httpx.Request] = []
    provider, client = _provider(_handler(seen=seen), cache_ttl_seconds=0)

    async with client:
        await provider.find_cover_photo(name="안국역 카페")
        await provider.find_cover_photo(name="안국역 카페")

    assert len(seen) == 4


@pytest.mark.asyncio
async def test_일시_오류는_None이되_다음_요청에_다시_시도한다() -> None:
    seen: list[httpx.Request] = []
    provider, client = _provider(
        _handler(search=httpx.Response(503, json={}), seen=seen)
    )

    async with client:
        assert await provider.find_cover_photo(name="흔들리는 곳") is None
        assert await provider.find_cover_photo(name="흔들리는 곳") is None

    # 쉬었다 부르면 성공할 수 있는 실패라 캐시하지 않는다.
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_한도를_소진하면_그날은_더_부르지_않는다() -> None:
    """한도가 끝난 뒤에도 계속 던지면 카드마다 실패한 호출의 지연만 쌓인다."""
    seen: list[httpx.Request] = []
    quota_error = httpx.Response(
        429,
        json={"error": {"status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded"}},
    )
    provider, client = _provider(_handler(search=quota_error, seen=seen))

    async with client:
        assert await provider.find_cover_photo(name="첫 장소") is None
        assert await provider.find_cover_photo(name="다른 장소") is None

    # 래치가 걸려 두 번째 장소는 아예 부르지 않는다.
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_타임아웃은_추천을_깨지_않는다() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    provider, client = _provider(handle)

    async with client:
        assert await provider.find_cover_photo(name="느린 곳") is None


@pytest.mark.asyncio
async def test_이름이_비면_부르지_않는다() -> None:
    seen: list[httpx.Request] = []
    provider, client = _provider(_handler(seen=seen))

    async with client:
        assert await provider.find_cover_photo(name="   ") is None

    assert seen == []


@pytest.mark.asyncio
async def test_Fake는_사진_없는_경우도_재현한다() -> None:
    """Fake가 항상 주소를 주면 '못 찾으면 그대로 둔다'는 분기가 안 돈다."""
    provider = FakeGooglePlacePhotoProvider()

    assert await provider.find_cover_photo(name="보통 장소") is not None
    assert await provider.find_cover_photo(name="사진없음 카페") is None
    assert await provider.find_cover_photo(name="") is None
