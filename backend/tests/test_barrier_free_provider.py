"""무장애 여행 정보(KorWithService2) Provider의 응답 해석."""

from __future__ import annotations

import httpx
import pytest

from app.errors import ProviderUnavailableError
from app.providers.tour_barrier_free import RealBarrierFreeProvider

# 2026-08-25 종로구 광장시장 한복매장(1013527)의 실제 응답에서 값만 줄인 것이다.
_REAL_SHAPE_ITEM = {
    "contentid": "1013527",
    "parking": "장애인 주차장 있음(옥상 P층)_무장애 편의시설",
    "publictransport": (
        "대중교통 이용 가능 : 종로5가.광장시장(중) 정류장<br/>저상버스 운행 : 모든 버스"
    ),
    "route": "출입구까지 턱이 없어 휠체어 접근 가능함",
    "ticketoffice": "",
    "promotion": "",
    "wheelchair": "대여 가능(1대/안내데스크)",
    "exit": "주출입구는 턱이 없어 휠체어 접근 가능함",
    "elevator": "엘리베이터 있음",
    "restroom": "장애인 화장실 있음",
    "auditorium": "",
    "room": "",
    "handicapetc": "",
    "braileblock": "점자블록 있음(장애인화장실 앞)",
    "helpdog": "동반가능",
    "guidehuman": "",
    "audioguide": "",
    "bigprint": "",
    "brailepromotion": "",
    "guidesystem": "",
    "blindhandicapetc": "",
    "signguide": "",
    "videoguide": "",
    "hearingroom": "",
    "hearinghandicapetc": "",
    "stroller": "대여가능",
    "lactationroom": "수유실 있음(B동 5층)",
    "babysparechair": "",
    "infantsfamilyetc": "",
}


def _payload(item: object, *, total_count: object = 1) -> dict[str, object]:
    return {
        "response": {
            "header": {"resultCode": "0000", "resultMsg": "OK"},
            "body": {
                "items": item,
                "pageNo": 1,
                "numOfRows": 1000,
                "totalCount": total_count,
            },
        }
    }


@pytest.mark.asyncio
async def test_상세_응답을_의미대로_옮긴다() -> None:
    """응답 키와 저장 필드의 이름이 어긋나는 두 필드가 이 테스트의 핵심이다.

    `wheelchair`는 휠체어 출입이 아니라 대여이고, `exit`는 출구가 아니라
    주출입구다. 키 이름을 그대로 믿고 옮기면 "휠체어로 들어갈 수 있다"는 답이
    대여 여부에서 나오게 된다.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/detailWithTour2")
        assert request.url.params["contentId"] == "1013527"
        return httpx.Response(200, json=_payload({"item": [_REAL_SHAPE_ITEM]}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RealBarrierFreeProvider(api_key="k", client=client)
        details = await provider.get_barrier_free_details("1013527")

    assert details is not None
    assert details.wheelchair_rental_raw == "대여 가능(1대/안내데스크)"
    assert details.entrance_access_raw == "주출입구는 턱이 없어 휠체어 접근 가능함"
    assert details.approach_route_raw == "출입구까지 턱이 없어 휠체어 접근 가능함"
    assert details.accessible_restroom_raw == "장애인 화장실 있음"
    assert details.accessible_parking_raw == "장애인 주차장 있음(옥상 P층)_무장애 편의시설"
    assert details.stroller_rental_raw == "대여가능"
    assert details.nursing_room_raw == "수유실 있음(B동 5층)"
    assert details.guide_dog_raw == "동반가능"
    assert details.braille_block_raw == "점자블록 있음(장애인화장실 앞)"
    # 빈 문자열은 값이 없는 것으로 본다.
    assert details.audio_guide_raw is None
    assert details.disability_etc_raw is None


@pytest.mark.asyncio
async def test_원문을_고치지_않는다() -> None:
    """`<br/>`도 `_무장애 편의시설` 접미사도 지우지 않는다.

    places의 `_raw` 컬럼들과 같은 규칙이다 — 여기서 손대면 원문으로 되돌릴 수 없다.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_payload({"item": [_REAL_SHAPE_ITEM]}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RealBarrierFreeProvider(api_key="k", client=client)
        details = await provider.get_barrier_free_details("1013527")

    assert details is not None
    assert "<br/>" in (details.public_transport_raw or "")
    assert (details.accessible_parking_raw or "").endswith("_무장애 편의시설")


@pytest.mark.asyncio
async def test_등록되지_않은_장소는_None이다() -> None:
    """무장애 목록에 없는 장소는 `totalCount: 0`으로 온다.

    값이 전부 빈 결과와 구분해야 한다 — 목록에 있는데도 필드가 모두 빈 장소가
    따로 있어서, 둘을 뭉개면 그 장소들을 매번 다시 부르게 된다.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_payload("", total_count=0))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RealBarrierFreeProvider(api_key="k", client=client)
        assert await provider.get_barrier_free_details("1018469") is None


@pytest.mark.asyncio
async def test_목록은_유형과_함께_돌려준다() -> None:
    """대상 거르기(숙박 제외)를 places 없이도 할 수 있어야 한다.

    유형을 places에서 다시 읽으면, 아직 places에 없는 신규 장소에서 값이 비어
    거르지 못한다.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/areaBasedList2")
        assert request.url.params["lDongRegnCd"] == "11"
        assert request.url.params["lDongSignguCd"] == "110"
        return httpx.Response(
            200,
            json=_payload(
                {
                    "item": [
                        {"contentid": "126508", "contenttypeid": "12"},
                        {"contentid": "2653363", "contenttypeid": "32"},
                    ]
                },
                total_count=2,
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RealBarrierFreeProvider(api_key="k", client=client)
        listed = await provider.list_barrier_free_content_ids("11", "110")

    assert listed == {"126508": "12", "2653363": "32"}


@pytest.mark.asyncio
async def test_목록이_여러_쪽이면_모두_모은다() -> None:
    pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page_no = int(request.url.params["pageNo"])
        pages.append(page_no)
        if page_no == 1:
            return httpx.Response(
                200,
                json=_payload(
                    {"item": [{"contentid": "1", "contenttypeid": "12"}]},
                    total_count=2,
                ),
            )
        return httpx.Response(
            200,
            json=_payload(
                {"item": [{"contentid": "2", "contenttypeid": "14"}]}, total_count=2
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RealBarrierFreeProvider(api_key="k", client=client)
        listed = await provider.list_barrier_free_content_ids("11", "110")

    assert pages == [1, 2]
    assert listed == {"1": "12", "2": "14"}


@pytest.mark.asyncio
async def test_빈_쪽이_오면_멈춘다() -> None:
    """totalCount가 실제보다 크게 와도 끝없이 다음 쪽을 부르지 않는다."""
    pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        pages.append(int(request.url.params["pageNo"]))
        if len(pages) == 1:
            return httpx.Response(
                200,
                json=_payload(
                    {"item": [{"contentid": "1", "contenttypeid": "12"}]},
                    total_count=999,
                ),
            )
        return httpx.Response(200, json=_payload("", total_count=999))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RealBarrierFreeProvider(api_key="k", client=client)
        listed = await provider.list_barrier_free_content_ids("11", "110")

    assert pages == [1, 2]
    assert listed == {"1": "12"}


@pytest.mark.asyncio
async def test_오류_응답은_무장애_서비스_이름으로_올린다() -> None:
    """어느 서비스가 실패했는지 로그와 오류에서 갈라 보여야 한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "response": {
                    "header": {
                        "resultCode": "22",
                        "resultMsg": "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR",
                    }
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RealBarrierFreeProvider(api_key="k", client=client)
        with pytest.raises(ProviderUnavailableError) as error:
            await provider.get_barrier_free_details("126508")

    assert "무장애" in str(error.value)
