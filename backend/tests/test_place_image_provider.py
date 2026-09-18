"""TourAPI detailImage2 조회의 요청 구성과 응답 변환 테스트.

역할: 저장소에 사진이 없는 장소를 채우는 경로가 어떤 파라미터로 나가고, 응답을
      어떤 순서로 옮기는지 못 박는다.
입력: MockTransport가 가로챈 TourAPI 요청과 그 응답.
출력: 요청 파라미터와 변환된 PlacePhoto에 대한 assertion.
호출 시점: 로컬 테스트와 CI에서 pytest 실행 시.
"""

from __future__ import annotations

import httpx
import pytest

from app.providers.real_place import RealPlaceProvider


def _payload(items: object) -> dict[str, object]:
    return {
        "response": {
            "header": {"resultCode": "0000", "resultMsg": "OK"},
            "body": {"items": items, "pageNo": 1, "numOfRows": 10, "totalCount": 1},
        }
    }


def _image(serial: str, index: int) -> dict[str, object]:
    return {
        "contentid": "3057945",
        "originimgurl": f"https://tong.visitkorea.or.kr/cms/resource/{index}_image2_1.jpg",
        "imgname": f"김희수아트센터 ({index})",
        "smallimageurl": f"https://tong.visitkorea.or.kr/cms/resource/{index}_image3_1.jpg",
        "serialnum": serial,
    }


def _provider(handler) -> tuple[RealPlaceProvider, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return RealPlaceProvider("test-key", client), client


@pytest.mark.asyncio
async def test_요청은_장소_사진만_한도만큼_가져온다() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_payload({"item": [_image("3487594", 1)]}))

    provider, client = _provider(handler)
    async with client:
        await provider.get_place_images("3057945", limit=10)

    params = seen[0].url.params
    assert seen[0].url.path.endswith("/detailImage2")
    assert params["contentId"] == "3057945"
    # N으로 주면 음식점 메뉴판 같은 다른 분류가 돌아온다.
    assert params["imageYN"] == "Y"
    assert params["numOfRows"] == "10"


@pytest.mark.asyncio
async def test_photo_order는_응답_순서다() -> None:
    """serialnum은 관광공사의 사진 식별자라 장소 안의 순번이 아니다.

    적재분(place_image_embeddings)도 응답 순서로 번호를 매겼기 때문에, 두 출처의
    순서가 같은 뜻을 가지려면 여기서도 순서로 세야 한다.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_payload(
                {"item": [_image("3487599", 4), _image("3487594", 1), _image("3487597", 3)]}
            ),
        )

    provider, client = _provider(handler)
    async with client:
        result = await provider.get_place_images("3057945", limit=10)

    assert [photo.photo_order for photo in result.data] == [1, 2, 3]
    # 응답 첫 항목이 1번이다. serialnum(3487599)으로 매겼다면 순서가 달라진다.
    assert result.data[0].url.endswith("4_image2_1.jpg")
    assert result.data[0].image_name == "김희수아트센터 (4)"


@pytest.mark.asyncio
async def test_원본_주소가_없는_항목은_뺀다() -> None:
    """축소본으로 대체하면 같은 갤러리 안에서 화질이 들쭉날쭉해진다."""
    broken = _image("3487594", 1)
    broken["originimgurl"] = ""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_payload({"item": [broken, _image("3487595", 2)]}))

    provider, client = _provider(handler)
    async with client:
        result = await provider.get_place_images("3057945", limit=10)

    assert len(result.data) == 1
    assert result.data[0].photo_order == 1
    assert result.data[0].url.endswith("2_image2_1.jpg")


@pytest.mark.asyncio
async def test_사진이_없으면_no_data다() -> None:
    """결과가 0건이면 items가 빈 문자열로 온다. 적재분에서도 절반이 이 경우였다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_payload(""))

    provider, client = _provider(handler)
    async with client:
        result = await provider.get_place_images("3057945", limit=10)

    assert result.data == ()
    assert result.metadata.status.value == "no_data"


@pytest.mark.asyncio
async def test_빈_content_id와_잘못된_limit은_거절한다() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("호출되면 안 된다")

    provider, client = _provider(handler)
    async with client:
        with pytest.raises(ValueError):
            await provider.get_place_images("  ", limit=10)
        with pytest.raises(ValueError):
            await provider.get_place_images("3057945", limit=0)
