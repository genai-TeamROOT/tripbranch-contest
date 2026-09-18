from __future__ import annotations

import logging
from collections.abc import Callable

import httpx
import pytest

from app.domain.models import PlaceCategoryFilter
from app.domain.operating_hours import (
    OperatingAvailability,
    OperatingParseStatus,
)
from app.errors import AppError, ProviderTimeoutError
from app.providers.real_place import RealPlaceProvider


def _payload(item: dict) -> dict:
    return {
        "response": {
            "header": {"resultCode": "0000", "resultMsg": "OK"},
            "body": {"items": {"item": item}},
        }
    }


@pytest.mark.asyncio
async def test_search_places_sends_tour_api_category_filters() -> None:
    seen_params: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/locationBasedList2")
        seen_params.update(dict(request.url.params))
        return httpx.Response(
            200,
            json=_payload(
                {
                    "contentid": "cafe-1",
                    "contenttypeid": "39",
                    "lDongRegnCd": "11",
                    "lDongSignguCd": "110",
                    "lclsSystm1": "FD",
                    "lclsSystm2": "FD05",
                    "lclsSystm3": "FD050100",
                    "title": "테스트 카페",
                    "mapx": "126.9770",
                    "mapy": "37.5788",
                }
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RealPlaceProvider(api_key="dummy", client=client)
        result = await provider.search_places(
            latitude=37.5788,
            longitude=126.9770,
            preferred_categories=["cafe"],
            search_radius_km=5.0,
            region_code="11",
            district_code="110",
            limit=10,
            category_filter=PlaceCategoryFilter(
                content_type_id="39",
                lcls_systm1="FD",
                lcls_systm2="FD05",
                lcls_systm3="FD050100",
            ),
        )
        result = result.data

    assert seen_params["contentTypeId"] == "39"
    assert seen_params["lDongRegnCd"] == "11"
    assert seen_params["lDongSignguCd"] == "110"
    assert seen_params["lclsSystm1"] == "FD"
    assert seen_params["lclsSystm2"] == "FD05"
    assert seen_params["lclsSystm3"] == "FD050100"
    assert seen_params["numOfRows"] == "10"
    assert result[0].content_type_id == "39"
    assert result[0].lcls_systm1 == "FD"
    assert result[0].lcls_systm2 == "FD05"
    assert result[0].lcls_systm3 == "FD050100"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"lcls_systm2": "FD05"}, "lcls_systm1"),
        (
            {"lcls_systm1": "FD", "lcls_systm3": "FD050100"},
            "lcls_systm1과 lcls_systm2",
        ),
    ],
)
def test_place_category_filter_requires_parent_codes(
    kwargs: dict[str, str], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        PlaceCategoryFilter(**kwargs)


@pytest.mark.asyncio
async def test_place_provider_timeout_does_not_chain_sensitive_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RealPlaceProvider(api_key="sensitive-key", client=client)
        with pytest.raises(ProviderTimeoutError) as exc_info:
            await provider.search_places(37.5788, 126.9770, [], 1.0)

    assert exc_info.value.__cause__ is None


@pytest.mark.asyncio
async def test_search_by_keyword_returns_content_identifiers() -> None:
    seen_params: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/searchKeyword2")
        seen_params.update(dict(request.url.params))
        return httpx.Response(
            200,
            json=_payload(
                {
                    "contentid": "126508",
                    "contenttypeid": "12",
                    "lDongRegnCd": "11",
                    "lDongSignguCd": "110",
                    "title": "경복궁",
                    "mapx": "126.9770",
                    "mapy": "37.5788",
                    "addr1": "서울특별시 종로구 사직로 161",
                }
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RealPlaceProvider(api_key="dummy", client=client)
        result = await provider.search_by_keyword(
            "경복궁", region_code="11", district_code="110"
        )
        result = result.data

    assert seen_params["keyword"] == "경복궁"
    assert seen_params["lDongRegnCd"] == "11"
    assert seen_params["lDongSignguCd"] == "110"
    assert result[0].place_id == "126508"
    assert result[0].content_type_id == "12"


@pytest.mark.asyncio
async def test_get_details_combines_common_and_intro_responses() -> None:
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path.endswith("/detailCommon2"):
            return httpx.Response(
                200,
                json=_payload(
                    {
                        "contentid": "126508",
                        "title": "경복궁",
                        "addr1": "서울특별시 종로구 사직로 161",
                        "overview": "조선 왕조의 법궁",
                        "homepage": "https://example.test",
                        "tel": "02-000-0000",
                    }
                ),
            )
        return httpx.Response(
            200,
            json=_payload(
                {
                    "contentid": "126508",
                    "contenttypeid": "12",
                    "usetime": "09:00~18:00",
                    "restdate": "매주 화요일",
                }
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RealPlaceProvider(api_key="dummy", client=client)
        result = await provider.get_details("126508", "12")
        result = result.data

    assert seen_paths[0].endswith("/detailCommon2")
    assert seen_paths[1].endswith("/detailIntro2")
    assert result.title == "경복궁"
    assert result.operating_hours == "09:00~18:00"
    assert result.rest_date == "매주 화요일"
    assert result.overview == "조선 왕조의 법궁"
    assert result.operating_schedule is not None
    assert (
        result.operating_schedule.availability
        is OperatingAvailability.SCHEDULED
    )
    assert result.operating_schedule.closure_rules[0].weekdays == frozenset({1})


@pytest.mark.asyncio
async def test_get_course_details_assumes_all_day_when_hours_are_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/detailCommon2"):
            return httpx.Response(
                200,
                json=_payload({"contentid": "course-1", "title": "테스트 여행코스"}),
            )
        return httpx.Response(
            200,
            json=_payload({"contentid": "course-1", "contenttypeid": "25"}),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await RealPlaceProvider(
            api_key="dummy",
            client=client,
        ).get_details("course-1", "25")
        result = result.data

    assert result.operating_hours is None
    assert result.operating_schedule is not None
    assert result.operating_schedule.availability is OperatingAvailability.ALL_DAY
    assert result.operating_schedule.parse_status is OperatingParseStatus.ASSUMED


@pytest.mark.asyncio
async def test_search_by_keyword_requires_region_for_district() -> None:
    async with httpx.AsyncClient() as client:
        provider = RealPlaceProvider(api_key="dummy", client=client)
        with pytest.raises(ValueError, match="region_code"):
            await provider.search_by_keyword("경복궁", district_code="110")


@pytest.mark.asyncio
async def test_find_details_by_name_searches_exact_match_then_gets_details() -> None:
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path.endswith("/searchKeyword2"):
            return httpx.Response(
                200,
                json=_payload(
                    {
                        "contentid": "126508",
                        "contenttypeid": "12",
                        "lDongRegnCd": "11",
                        "lDongSignguCd": "110",
                        "title": "경복궁",
                        "mapx": "126.9770",
                        "mapy": "37.5788",
                    }
                ),
            )
        if request.url.path.endswith("/detailCommon2"):
            return httpx.Response(
                200,
                json=_payload({"contentid": "126508", "title": "경복궁"}),
            )
        return httpx.Response(
            200,
            json=_payload(
                {
                    "contentid": "126508",
                    "contenttypeid": "12",
                    "usetime": "09:00~18:00",
                    "restdate": "매주 화요일",
                }
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RealPlaceProvider(api_key="dummy", client=client)
        result = await provider.find_details_by_name(
            "경복궁", region_code="11", district_code="110"
        )
        result = result.data

    assert seen_paths == [
        "/B551011/KorService2/searchKeyword2",
        "/B551011/KorService2/detailCommon2",
        "/B551011/KorService2/detailIntro2",
    ]
    assert result.title == "경복궁"
    assert result.operating_hours == "09:00~18:00"
    assert result.rest_date == "매주 화요일"


@pytest.mark.asyncio
async def test_find_details_by_name_does_not_guess_non_exact_candidate() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_payload(
                {
                    "contentid": "other",
                    "contenttypeid": "12",
                    "lDongRegnCd": "11",
                    "lDongSignguCd": "110",
                    "title": "경복궁역",
                    "mapx": "126.9770",
                    "mapy": "37.5788",
                }
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RealPlaceProvider(api_key="dummy", client=client)
        with pytest.raises(AppError) as exc_info:
            await provider.find_details_by_name("경복궁")

    assert exc_info.value.code == "place_not_found"


class TestSupportedDistrictFilter:
    """응답의 lDongSignguCd로 지원 구를 가린다(D-025).

    검색 요청에 구를 싣지 않고 반경으로만 받아 오므로, 거르지 않으면 지원하지 않는
    구의 장소가 후보에 섞인다.
    """

    @staticmethod
    def _handler(items: list[dict]) -> Callable[[httpx.Request], httpx.Response]:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "response": {
                        "header": {"resultCode": "0000", "resultMsg": "OK"},
                        "body": {"items": {"item": items}},
                    }
                },
            )

        return handler

    @staticmethod
    def _item(content_id: str, district_code: str, **overrides: str) -> dict:
        return {
            "contentid": content_id,
            "contenttypeid": "12",
            "title": f"장소 {content_id}",
            "mapx": "126.9770",
            "mapy": "37.5788",
            "lDongRegnCd": "11",
            "lDongSignguCd": district_code,
            **overrides,
        }

    async def _search(self, items: list[dict]) -> list[str]:
        transport = httpx.MockTransport(self._handler(items))
        async with httpx.AsyncClient(transport=transport) as client:
            provider = RealPlaceProvider(api_key="dummy", client=client)
            result = await provider.search_places(
                latitude=37.5788,
                longitude=126.9770,
                preferred_categories=[],
                search_radius_km=2.0,
                region_code="11",
            )
        return [candidate.place_id for candidate in result.data]

    @pytest.mark.asyncio
    async def test_구를_안_넘기면_요청에_lDongSignguCd가_없다(self) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(dict(request.url.params))
            return self._handler([self._item("a", "110")])(request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = RealPlaceProvider(api_key="dummy", client=client)
            await provider.search_places(
                latitude=37.5788,
                longitude=126.9770,
                preferred_categories=[],
                search_radius_km=2.0,
                region_code="11",
            )

        assert seen["lDongRegnCd"] == "11"
        assert "lDongSignguCd" not in seen

    @pytest.mark.asyncio
    async def test_지원_구는_남고_나머지는_버린다(self) -> None:
        """D-107으로 서울 25개 구 전체가 지원 구가 돼, 실제 미지원 구 예시가
        서울 안에는 더 없다 — 존재하지 않는 코드("999")로 미지원 상태를 흉내낸다."""
        place_ids = await self._search(
            [
                self._item("jongno", "110"),
                self._item("jung", "140"),
                self._item("yongsan", "170"),
                self._item("seongdong", "200"),
                self._item("unsupported", "999"),
            ]
        )

        assert place_ids == ["jongno", "jung", "yongsan", "seongdong"]

    @pytest.mark.asyncio
    async def test_좌표가_다른_구여도_응답의_구를_믿는다(self) -> None:
        """서울역 부속 시설 72건은 용산구로 등록돼 있지만 좌표는 중구 안이다.

        좌표로 구를 판정하면 이 장소들이 통째로 빠진다(2026-08-24 실측).
        """
        place_ids = await self._search(
            [self._item("seoul-station", "170", mapx="126.971733", mapy="37.554838")]
        )

        assert place_ids == ["seoul-station"]

    @pytest.mark.asyncio
    async def test_구_코드가_없으면_버리고_경고를_남긴다(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """전량이 조용히 사라지면 "이 근처에 장소가 없다"로 둔갑한다."""
        with caplog.at_level(logging.WARNING, logger="app.providers.mappers"):
            place_ids = await self._search([self._item("unknown", "")])

        assert place_ids == []
        assert "lDongSignguCd" in caplog.text
