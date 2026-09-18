"""저장소 → TourAPI 순으로 장소 사진을 채우는 경로의 판정 테스트.

역할: 사진을 못 얻는 세 경우(사진 없음·일일 한도 소진·일시 오류)를 서로 다르게
      다루는지, 그리고 그 차이가 다음 요청의 호출 여부를 실제로 바꾸는지 못 박는다.
입력: 저장소·API 대역이 돌려주는 값과 예외.
출력: 반환된 사진과 API 호출 횟수에 대한 assertion.
호출 시점: 로컬 테스트와 CI에서 pytest 실행 시.
"""

from __future__ import annotations

import pytest

from app.domain.models import PlacePhoto
from app.errors import ProviderTimeoutError, ProviderUnavailableError
from app.providers.contracts import ProviderSource, ProviderStatus, provider_result
from app.providers.hybrid_place_photos import (
    HybridPlacePhotoProvider,
    reset_photo_api_state,
)

_TTL_SECONDS = 6 * 60 * 60
_DISPLAY_LIMIT = 10


@pytest.fixture(autouse=True)
def _isolate_module_state() -> None:
    """캐시와 한도 래치가 모듈 수준이라 테스트마다 비운다."""
    reset_photo_api_state()


def _photo(content_id: str, order: int) -> PlacePhoto:
    return PlacePhoto(
        content_id=content_id,
        photo_order=order,
        url=f"https://tong.visitkorea.or.kr/{content_id}-{order}.jpg",
        image_name=None,
    )


class _StoredPhotos:
    def __init__(self, photos: dict[str, tuple[PlacePhoto, ...]]) -> None:
        self._photos = photos
        self.calls: list[list[str]] = []

    async def find_place_photos(self, content_ids):  # noqa: ANN001, ANN201
        self.calls.append(list(content_ids))
        return {
            content_id: self._photos[content_id]
            for content_id in content_ids
            if content_id in self._photos
        }


class _ApiPhotos:
    """detailImage2 대역. content_id마다 돌려줄 값이나 던질 예외를 미리 정한다."""

    def __init__(self, responses: dict[str, object]) -> None:
        self._responses = responses
        self.calls: list[str] = []

    async def get_place_images(self, content_id: str, limit: int):  # noqa: ANN201
        self.calls.append(content_id)
        response = self._responses.get(content_id, ())
        if isinstance(response, Exception):
            raise response
        photos = tuple(response)  # type: ignore[arg-type]
        return provider_result(
            photos,
            source=ProviderSource.TOUR_API_PLACE,
            status=ProviderStatus.SUCCESS if photos else ProviderStatus.NO_DATA,
        )


def _provider(stored: _StoredPhotos, api: _ApiPhotos) -> HybridPlacePhotoProvider:
    return HybridPlacePhotoProvider(
        photo_repository=stored,
        image_provider=api,
        display_limit=_DISPLAY_LIMIT,
        cache_ttl_seconds=_TTL_SECONDS,
    )


def _quota_exceeded() -> ProviderUnavailableError:
    """일일 한도 소진(코드 22). 초당 한도(23)와 같은 429라 코드로만 갈린다."""
    return ProviderUnavailableError(
        "TourAPI",
        'status=429 "returnReasonCode":"22" LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR',
    )


def _per_second_limited() -> ProviderUnavailableError:
    return ProviderUnavailableError(
        "TourAPI",
        'status=429 "returnReasonCode":"23" '
        "LIMITED_NUMBER_OF_SERVICE_REQUESTS_PER_SECOND_EXCEEDS_ERROR",
    )


@pytest.mark.asyncio
async def test_저장소에_있으면_api를_부르지_않는다() -> None:
    """적재된 5,465곳이 여기 해당한다. 여기서 API를 부르면 한도가 하루도 못 간다."""
    stored = _StoredPhotos({"126508": (_photo("126508", 1), _photo("126508", 2))})
    api = _ApiPhotos({})

    photos = await _provider(stored, api).find_place_photos(["126508"])

    assert [photo.photo_order for photo in photos["126508"]] == [1, 2]
    assert api.calls == []


@pytest.mark.asyncio
async def test_저장소에_없으면_api로_채운다() -> None:
    stored = _StoredPhotos({})
    api = _ApiPhotos({"3057945": (_photo("3057945", 1), _photo("3057945", 2))})

    photos = await _provider(stored, api).find_place_photos(["3057945"])

    assert [photo.url for photo in photos["3057945"]] == [
        "https://tong.visitkorea.or.kr/3057945-1.jpg",
        "https://tong.visitkorea.or.kr/3057945-2.jpg",
    ]
    assert api.calls == ["3057945"]


@pytest.mark.asyncio
async def test_적재된_곳과_아닌_곳이_섞여도_필요한_만큼만_부른다() -> None:
    stored = _StoredPhotos({"126508": (_photo("126508", 1),)})
    api = _ApiPhotos({"3057945": (_photo("3057945", 1),)})

    photos = await _provider(stored, api).find_place_photos(["126508", "3057945"])

    assert set(photos) == {"126508", "3057945"}
    assert api.calls == ["3057945"]


@pytest.mark.asyncio
async def test_api에도_사진이_없으면_다시_부르지_않는다() -> None:
    """빈 응답이 호출의 절반을 차지한다(적재분 5,465곳 중 2,749곳). 반복하면 한도만 태운다."""
    stored = _StoredPhotos({})
    api = _ApiPhotos({"3057945": ()})
    provider = _provider(stored, api)

    assert await provider.find_place_photos(["3057945"]) == {}
    assert await provider.find_place_photos(["3057945"]) == {}

    assert api.calls == ["3057945"]


@pytest.mark.asyncio
async def test_한도_소진이면_그날_남은_요청은_부르지_않는다() -> None:
    """소진 뒤에도 계속 던지면 매 요청이 실패한 호출의 지연을 먼저 기다린다."""
    stored = _StoredPhotos({})
    api = _ApiPhotos({"3057945": _quota_exceeded(), "1013079": _quota_exceeded()})
    provider = _provider(stored, api)

    assert await provider.find_place_photos(["3057945"]) == {}
    assert await provider.find_place_photos(["1013079"]) == {}

    # 첫 장소에서 소진을 확인했으므로 두 번째는 부르지 않는다.
    assert api.calls == ["3057945"]


@pytest.mark.asyncio
async def test_한도_소진은_사진_없음으로_캐시하지_않는다() -> None:
    """이 테스트가 이 설계의 핵심이다.

    "사진이 없다"와 "확인하지 못했다"는 다른 사실이다. 소진을 사진 없음으로
    캐시하면 그날 열린 장소들이 TTL(6시간) 동안 사진 없음으로 굳는다 — 한도가
    풀린 다음 날에도 캐시가 살아 있어 API를 다시 부르지 않는다.
    """
    stored = _StoredPhotos({})
    api = _ApiPhotos({"3057945": _quota_exceeded()})
    provider = _provider(stored, api)

    assert await provider.find_place_photos(["3057945"]) == {}

    # 날이 바뀌어 래치가 풀린 상황을 흉내 낸다. 캐시에 남아 있었다면 여기서도
    # 호출이 없고, 그러면 사진이 영영 안 나온다.
    from app.providers import hybrid_place_photos

    hybrid_place_photos._quota_exhausted_on = "2000-01-01"
    api._responses["3057945"] = (_photo("3057945", 1),)

    photos = await provider.find_place_photos(["3057945"])

    assert [photo.photo_order for photo in photos["3057945"]] == [1]
    assert api.calls == ["3057945", "3057945"]


@pytest.mark.asyncio
async def test_초당_한도는_다음_요청에_다시_부른다() -> None:
    """쉬었다 부르면 성공하는 오류다. 일일 한도(22)와 대응이 반대다."""
    stored = _StoredPhotos({})
    api = _ApiPhotos({"3057945": _per_second_limited()})
    provider = _provider(stored, api)

    assert await provider.find_place_photos(["3057945"]) == {}

    api._responses["3057945"] = (_photo("3057945", 1),)
    photos = await provider.find_place_photos(["3057945"])

    assert "3057945" in photos
    assert api.calls == ["3057945", "3057945"]


@pytest.mark.asyncio
async def test_타임아웃도_다음_요청에_다시_부른다() -> None:
    stored = _StoredPhotos({})
    api = _ApiPhotos({"3057945": ProviderTimeoutError("TourAPI")})
    provider = _provider(stored, api)

    assert await provider.find_place_photos(["3057945"]) == {}

    api._responses["3057945"] = (_photo("3057945", 1),)
    assert "3057945" in await provider.find_place_photos(["3057945"])
    assert api.calls == ["3057945", "3057945"]


@pytest.mark.asyncio
async def test_받은_사진은_ttl_동안_다시_부르지_않는다() -> None:
    stored = _StoredPhotos({})
    api = _ApiPhotos({"3057945": (_photo("3057945", 1),)})
    provider = _provider(stored, api)

    first = await provider.find_place_photos(["3057945"])
    second = await provider.find_place_photos(["3057945"])

    assert first == second
    assert api.calls == ["3057945"]


@pytest.mark.asyncio
async def test_ttl이_0이면_캐시하지_않는다() -> None:
    """설정으로 캐시를 끌 수 있어야 한다. 껐는데 캐시가 살아 있으면 진단이 막힌다."""
    stored = _StoredPhotos({})
    api = _ApiPhotos({"3057945": (_photo("3057945", 1),)})
    provider = HybridPlacePhotoProvider(
        photo_repository=stored,
        image_provider=api,
        display_limit=_DISPLAY_LIMIT,
        cache_ttl_seconds=0,
    )

    await provider.find_place_photos(["3057945"])
    await provider.find_place_photos(["3057945"])

    assert api.calls == ["3057945", "3057945"]


@pytest.mark.asyncio
async def test_표시_상한은_두_출처에_같게_적용된다() -> None:
    """출처에 따라 장수가 달라지면 사용자에게는 같은 화면이 이유 없이 달라 보인다."""
    stored = _StoredPhotos({"126508": tuple(_photo("126508", n) for n in range(1, 15))})
    api = _ApiPhotos({"3057945": tuple(_photo("3057945", n) for n in range(1, 15))})
    provider = HybridPlacePhotoProvider(
        photo_repository=stored,
        image_provider=api,
        display_limit=3,
        cache_ttl_seconds=_TTL_SECONDS,
    )

    photos = await provider.find_place_photos(["126508", "3057945"])

    assert len(photos["126508"]) == 3
    assert len(photos["3057945"]) == 3


@pytest.mark.asyncio
async def test_저장소_실패는_삼키지_않는다() -> None:
    """저장소를 못 읽은 것을 미적재로 오인하면 적재된 장소 전부에 API를 부른다."""

    class _BrokenRepository:
        async def find_place_photos(self, content_ids):  # noqa: ANN001, ANN201
            raise RuntimeError("supabase unreachable")

    api = _ApiPhotos({"126508": (_photo("126508", 1),)})
    provider = HybridPlacePhotoProvider(
        photo_repository=_BrokenRepository(),
        image_provider=api,
        display_limit=_DISPLAY_LIMIT,
        cache_ttl_seconds=_TTL_SECONDS,
    )

    with pytest.raises(RuntimeError):
        await provider.find_place_photos(["126508"])

    assert api.calls == []
