"""PlaceDetails의 주차·요금·전화 정규화 필드를 생산 경로별로 검증한다.

**손으로 만든 PlaceDetails로는 이 검증이 안 된다.** provider가 필드를 안 채워도
규칙 테스트는 통과하기 때문이다 — 그래서 여기서는 반드시 provider를 태우고,
소비 측(extract_info_fields)이 실제로 받는 값을 단언한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import httpx
import pytest

from app.agent_context.info_field_rules import extract_info_fields
from app.domain.models import StoredPlaceDetail
from app.providers.real_place import RealPlaceProvider
from app.providers.stub import FakePlaceProvider
from app.providers.supabase_place_details import SupabasePlaceDetailsProvider

_FETCHED_AT = datetime(2026, 8, 10, 3, 0, tzinfo=UTC)


def _tour_api_client(common: dict, intro: dict) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        item = common if "detailCommon2" in request.url.path else intro
        return httpx.Response(
            200,
            json={"response": {"body": {"items": {"item": [item]}}}},
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class _Repository:
    def __init__(self, row: StoredPlaceDetail) -> None:
        self._row = row

    async def get_active_place_details(
        self, content_ids: Sequence[str]
    ) -> dict[str, StoredPlaceDetail]:
        return {
            content_id: self._row
            for content_id in content_ids
            if content_id == self._row.content_id
        }


def _row(**overrides: object) -> StoredPlaceDetail:
    base: dict[str, object] = {
        "content_id": "126508",
        "content_type_id": "14",
        "title": "경복궁",
        "address": "서울특별시 종로구 사직로 161",
        "operating_hours_raw": "09:00~18:00",
        "rest_date_raw": "매주 화요일",
        "detail_fetch_status": "success",
        "detail_fetched_at": _FETCHED_AT,
        "source_modified_at": None,
        "parking_info_raw": "가능 (승용차 240대)",
        "parking_fee_raw": "무료",
        "use_fee_raw": "어른 3,000원",
        "info_center_raw": "02-3700-3900",
        "baby_carriage_raw": "없음",
        "pet_raw": "불가",
        "credit_card_raw": "가능",
        "restroom_raw": "있음",
        "thumbnail_url": "https://example.test/thumb.jpg",
        "first_image_url": "https://example.test/first.jpg",
    }
    base.update(overrides)
    return StoredPlaceDetail(**base)  # type: ignore[arg-type]


class TestTourApiProvider:
    """유형별 키를 provider가 올바르게 고르는지 — 규칙이 여기로 옮겨왔다."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("content_type_id", "intro", "expected_parking"),
        [
            ("12", {"parking": "가능 (240대)"}, "가능 (240대)"),
            ("14", {"parkingculture": "주차 가능(무료)"}, "주차 가능(무료)"),
            ("32", {"parkinglodging": "가능(3대)"}, "가능(3대)"),
            ("38", {"parkingshopping": "불가능"}, "불가능"),
            ("39", {"parkingfood": "가능(10대)"}, "가능(10대)"),
            ("28", {"parkingleports": "가능"}, "가능"),
        ],
    )
    async def test_유형별_주차_키를_고른다(
        self, content_type_id: str, intro: dict, expected_parking: str
    ) -> None:
        async with _tour_api_client({"title": "장소"}, intro) as client:
            provider = RealPlaceProvider(api_key="k", client=client)
            details = (
                await provider.get_details("126508", content_type_id)
            ).data

        assert extract_info_fields("parking", details) == {
            "parking": expected_parking
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("content_type_id", "intro", "expected"),
        [
            ("12", {"chkbabycarriage": "없음"}, {"baby_carriage": "없음"}),
            ("14", {"chkpetculture": "불가"}, {"pet": "불가"}),
            ("38", {"chkcreditcardshopping": "가능"}, {"credit_card": "가능"}),
            ("39", {"chkcreditcardfood": "모든 카드 사용 가능"},
             {"credit_card": "모든 카드 사용 가능"}),
            ("38", {"restroom": "있음"}, {"restroom": "있음"}),
        ],
    )
    async def test_유형별_편의시설_키를_고른다(
        self, content_type_id: str, intro: dict, expected: dict
    ) -> None:
        async with _tour_api_client({"title": "장소"}, intro) as client:
            provider = RealPlaceProvider(api_key="k", client=client)
            details = (await provider.get_details("1", content_type_id)).data

        assert extract_info_fields("facility", details) == expected

    @pytest.mark.asyncio
    async def test_축제_요금은_usetimefestival에서_온다(self) -> None:
        """이름은 시간처럼 보이지만 내용은 요금이다(D-056).

        운영시간 자리에 요금이 새어 들어가지 않는 것까지 함께 본다.
        """
        async with _tour_api_client(
            {"title": "축제"},
            {"usetimefestival": "5,000원", "playtime": "19:10~21:00"},
        ) as client:
            provider = RealPlaceProvider(api_key="k", client=client)
            details = (await provider.get_details("1291408", "15")).data

        assert extract_info_fields("fee", details) == {"fee": "5,000원"}
        assert details.operating_hours == "19:10~21:00"

    @pytest.mark.asyncio
    async def test_전화번호는_유형별_안내처를_모두_훑는다(self) -> None:
        """예전에는 infocenter 하나만 봐서 음식점 전화번호가 비었다."""
        async with _tour_api_client(
            {"title": "식당"}, {"infocenterfood": "0507-1409-8780"}
        ) as client:
            provider = RealPlaceProvider(api_key="k", client=client)
            details = (await provider.get_details("1063408", "39")).data

        assert details.telephone == "0507-1409-8780"

    @pytest.mark.asyncio
    async def test_썸네일은_common의_firstimage2에서_온다(self) -> None:
        """TourAPI 직접 경로도 캐시 경로와 같은 키를 쓴다."""
        async with _tour_api_client(
            {"title": "장소", "firstimage2": "https://example.test/c-thumb.jpg"}, {}
        ) as client:
            provider = RealPlaceProvider(api_key="k", client=client)
            details = (await provider.get_details("126508", "12")).data

        assert details.thumbnail_url == "https://example.test/c-thumb.jpg"

    @pytest.mark.asyncio
    async def test_축제는_common의_tel을_쓴다(self) -> None:
        """축제(15)는 infocenter 계열이 없고 common의 tel만 채워진다."""
        async with _tour_api_client({"tel": "02-3210-1645"}, {}) as client:
            provider = RealPlaceProvider(api_key="k", client=client)
            details = (await provider.get_details("1291408", "15")).data

        assert details.telephone == "02-3210-1645"


class TestSupabaseProvider:
    """places 캐시 경로가 정규화 필드를 채우는지.

    이 가드가 없으면 _to_place_details가 필드를 빠뜨려도 아무도 모른 채
    INFO 주차·요금이 운영에서만 조용히 빈다 — D-054를 만든 사건이다.
    """

    @pytest.mark.asyncio
    async def test_주차와_요금이_소비_측까지_도달한다(self) -> None:
        provider = SupabasePlaceDetailsProvider(_Repository(_row()))

        details = (await provider.get_details("126508", "14")).data

        assert extract_info_fields("parking", details) == {
            "parking": "가능 (승용차 240대)",
            "parking_fee": "무료",
        }
        assert extract_info_fields("fee", details) == {"fee": "어른 3,000원"}

    @pytest.mark.asyncio
    async def test_편의시설이_소비_측까지_도달한다(self) -> None:
        provider = SupabasePlaceDetailsProvider(_Repository(_row()))

        details = (await provider.get_details("126508", "14")).data

        assert extract_info_fields("facility", details) == {
            "baby_carriage": "없음",
            "pet": "불가",
            "credit_card": "가능",
            "restroom": "있음",
        }

    @pytest.mark.asyncio
    async def test_썸네일이_실린다(self) -> None:
        """카드에 쓸 이미지가 캐시 경로에서 PlaceDetails까지 오는지 본다.

        firstimage2(작은 썸네일)보다 firstimage(원본 크기)를 우선한다
        (2026-08-13, hybrid_place_details.py와 동일한 이유).
        """
        provider = SupabasePlaceDetailsProvider(_Repository(_row()))

        details = (await provider.get_details("126508", "14")).data

        assert details.thumbnail_url == "https://example.test/first.jpg"

    @pytest.mark.asyncio
    async def test_썸네일이_없으면_목록_이미지로_대체한다(self) -> None:
        provider = SupabasePlaceDetailsProvider(
            _Repository(_row(thumbnail_url=None))
        )

        details = (await provider.get_details("126508", "14")).data

        assert details.thumbnail_url == "https://example.test/first.jpg"

    @pytest.mark.asyncio
    async def test_안내처가_전화번호로_실린다(self) -> None:
        provider = SupabasePlaceDetailsProvider(_Repository(_row()))

        details = (await provider.get_details("126508", "14")).data

        assert extract_info_fields("location_info", details) == {
            "address": "서울특별시 종로구 사직로 161",
            "telephone": "02-3700-3900",
        }

    @pytest.mark.asyncio
    async def test_적재_전에는_전화번호가_빈다(self) -> None:
        """info_center_raw가 아직 NULL인 현재 상태를 명시적으로 기록한다.

        값이 채워지기 전에는 no_data가 정상이다. 이 테스트가 깨지면 적재가 끝났거나
        엉뚱한 값이 들어온 것이다.
        """
        provider = SupabasePlaceDetailsProvider(_Repository(_row(info_center_raw=None)))

        details = (await provider.get_details("126508", "14")).data

        assert "telephone" not in extract_info_fields("location_info", details)


class TestFakeProvider:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("content_id", "content_type_id"),
        [("fake-museum-1", "14"), ("fake-cafe-1", "39")],
    )
    async def test_fake도_정규화_필드를_채운다(
        self, content_id: str, content_type_id: str
    ) -> None:
        """fake가 raw_intro만 채우고 정규화 필드를 비우면 fake 환경의 INFO 주차가
        조용히 빈다. 실 provider와 같은 키 목록을 쓰는지 함께 확인한다."""
        details = (
            await FakePlaceProvider().get_details(content_id, content_type_id)
        ).data

        assert extract_info_fields("parking", details) != {}
        assert extract_info_fields("facility", details) != {}
        assert details.parking is not None
