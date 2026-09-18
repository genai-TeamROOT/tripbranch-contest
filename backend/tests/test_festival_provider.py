"""searchFestival2 응답 파싱을 실제 응답 모양으로 검증한다.

여기 쓰는 항목은 2026-08-07 실측 응답에서 필드 이름·값 형식을 그대로 가져왔다.
"""

import logging
from datetime import date

import httpx
import pytest

from app.errors import ProviderUnavailableError
from app.providers.contracts import ProviderStatus
from app.providers.festival import RealFestivalProvider, map_festival_items

# 실측 응답의 항목 한 건(필요 필드만 발췌).
REAL_ITEM = {
    "contentid": "3419040",
    "title": "서울썸머비치",
    "eventstartdate": "20260720",
    "eventenddate": "20260809",
    "addr1": "서울특별시 종로구 세종대로 175 (세종로)",
    "mapx": "126.9761682759",
    "mapy": "37.5718478585",
    "areacode": "",
    "sigungucode": "",
    "lDongRegnCd": "11",
    "lDongSignguCd": "110",
}


def _payload(items: list[dict]) -> dict:
    return {
        "response": {
            "header": {"resultCode": "0000", "resultMsg": "OK"},
            "body": {"items": {"item": items}, "totalCount": len(items)},
        }
    }


class TestMapping:
    def test_실제_응답_항목을_정규화한다(self) -> None:
        events = map_festival_items([REAL_ITEM])

        assert len(events) == 1
        event = events[0]
        assert event.content_id == "3419040"
        assert event.title == "서울썸머비치"
        assert event.start_date == date(2026, 7, 20)
        assert event.end_date == date(2026, 8, 9)
        assert event.address == "서울특별시 종로구 세종대로 175 (세종로)"
        # mapy가 위도, mapx가 경도다 — 뒤집으면 좌표가 종로 밖으로 나간다.
        assert event.latitude == pytest.approx(37.5718478585)
        assert event.longitude == pytest.approx(126.9761682759)

    def test_지원_구만_남긴다(self) -> None:
        mapo = {**REAL_ITEM, "contentid": "mapo-1", "lDongSignguCd": "440"}

        events = map_festival_items(
            [REAL_ITEM, mapo], allowed_district_codes=frozenset({"110"})
        )

        assert [event.content_id for event in events] == ["3419040"]

    def test_구_코드가_없으면_버린다(self) -> None:
        # 지원 구인지 알 수 없는 행사를 남기면 범위 밖 행사가 섞인다.
        events = map_festival_items(
            [{**REAL_ITEM, "lDongSignguCd": ""}],
            allowed_district_codes=frozenset({"110"}),
        )

        assert events == []

    def test_기간이_없으면_버린다(self) -> None:
        # 진행 중 판정을 못 하므로 event 질의에 쓸 수 없다.
        events = map_festival_items([{**REAL_ITEM, "eventenddate": ""}])

        assert events == []

    def test_기간_형식이_깨지면_버린다(self) -> None:
        events = map_festival_items([{**REAL_ITEM, "eventstartdate": "2026-07-20"}])

        assert events == []

    def test_종료일이_시작일보다_빠르면_버린다(self) -> None:
        events = map_festival_items(
            [{**REAL_ITEM, "eventstartdate": "20260809", "eventenddate": "20260720"}]
        )

        assert events == []

    def test_좌표가_없어도_남긴다(self) -> None:
        events = map_festival_items([{**REAL_ITEM, "mapx": "", "mapy": ""}])

        assert len(events) == 1
        assert events[0].latitude is None
        assert events[0].longitude is None


class TestIsOngoing:
    def test_기간_경계를_포함한다(self) -> None:
        event = map_festival_items([REAL_ITEM])[0]

        assert event.is_ongoing(date(2026, 7, 20)) is True
        assert event.is_ongoing(date(2026, 8, 9)) is True
        assert event.is_ongoing(date(2026, 7, 19)) is False
        assert event.is_ongoing(date(2026, 8, 10)) is False


class TestRequest:
    @pytest.mark.asyncio
    async def test_법정동_코드로_조회한다(self) -> None:
        # areaCode/sigunguCode를 쓰면 응답 다수가 서버 필터에서 탈락한다(D-055).
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(dict(request.url.params))
            return httpx.Response(200, json=_payload([REAL_ITEM]))

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = RealFestivalProvider("KEY", client, timeout_seconds=5.0)
            result = await provider.search_festivals("11", "110", date(2026, 8, 7))

        assert captured["lDongRegnCd"] == "11"
        assert captured["lDongSignguCd"] == "110"
        assert "areaCode" not in captured
        assert "sigunguCode" not in captured
        assert result.metadata.status is ProviderStatus.SUCCESS
        assert len(result.data) == 1

    @pytest.mark.asyncio
    async def test_구를_안_넘기면_시도_전체를_받는다(self) -> None:
        """지원 구가 여럿이어도 호출은 1회다 — 구마다 부르면 호출 수가 배로 는다."""
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(dict(request.url.params))
            return httpx.Response(200, json=_payload([REAL_ITEM]))

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = RealFestivalProvider("KEY", client, timeout_seconds=5.0)
            result = await provider.search_festivals("11", None, date(2026, 8, 7))

        assert captured["lDongRegnCd"] == "11"
        assert "lDongSignguCd" not in captured
        assert len(result.data) == 1

    @pytest.mark.asyncio
    async def test_지원하지_않는_구의_행사는_응답에서_버린다(self) -> None:
        """서울 전체를 받으므로 거르지 않으면 미지원 구 행사가 그대로 실린다.

        D-107으로 서울 25개 구 전체가 지원 구가 돼 실제 미지원 구 예시가 서울
        안에는 더 없다 — 존재하지 않는 코드("999")로 미지원 상태를 흉내낸다.
        """
        unsupported = {**REAL_ITEM, "contentid": "unsupported-1", "lDongSignguCd": "999"}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_payload([REAL_ITEM, unsupported]))

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = RealFestivalProvider("KEY", client, timeout_seconds=5.0)
            result = await provider.search_festivals("11", None, date(2026, 8, 7))

        assert [event.content_id for event in result.data] == ["3419040"]

    @pytest.mark.asyncio
    async def test_장기_행사가_시작일_필터에서_빠지지_않는다(self) -> None:
        # 20260101~20261231 같은 상설 행사가 있어 조회 시작일을 넉넉히 잡는다.
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(dict(request.url.params))
            return httpx.Response(200, json=_payload([]))

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = RealFestivalProvider("KEY", client, timeout_seconds=5.0)
            await provider.search_festivals("11", "110", date(2026, 8, 7))

        assert captured["eventStartDate"] == "20240807"

    @pytest.mark.asyncio
    async def test_결과가_없으면_no_data다(self) -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=_payload([]))
        )
        async with httpx.AsyncClient(transport=transport) as client:
            provider = RealFestivalProvider("KEY", client, timeout_seconds=5.0)
            result = await provider.search_festivals("11", "110", date(2026, 8, 7))

        assert result.metadata.status is ProviderStatus.NO_DATA
        assert result.data == []

    @pytest.mark.asyncio
    async def test_API_오류코드는_예외로_올린다(self) -> None:
        error_payload = {
            "response": {
                "header": {
                    "resultCode": "22",
                    "resultMsg": "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR",
                }
            }
        }
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=error_payload)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            provider = RealFestivalProvider("KEY", client, timeout_seconds=5.0)
            with pytest.raises(ProviderUnavailableError):
                await provider.search_festivals("11", "110", date(2026, 8, 7))

    @pytest.mark.asyncio
    async def test_서비스키가_로그나_예외에_남지_않는다(self) -> None:
        # backend/.env의 실제 키가 traceback에 실리면 안 된다.
        transport = httpx.MockTransport(
            lambda request: httpx.Response(500, text="boom")
        )
        async with httpx.AsyncClient(transport=transport) as client:
            provider = RealFestivalProvider("SECRET-KEY", client, timeout_seconds=5.0)
            with pytest.raises(ProviderUnavailableError) as exc_info:
                await provider.search_festivals("11", "110", date(2026, 8, 7))

        assert "SECRET-KEY" not in str(exc_info.value)
        assert "SECRET-KEY" not in repr(exc_info.value.details)


class TestFullFetch:
    """100건에서 잘리던 것을 막는다(TP-253).

    전에는 `numOfRows=100`으로 한 번만 불러, 서울 전체가 100건을 넘긴 뒤로는
    나머지가 조용히 사라졌다. 2026-09-07 실측에서 전체 189건 중 진행 중 19건
    가운데 12건이 응답에 들어오지 않았다 — 거리순 정렬이라 실제로 가장 가까운
    행사가 후보에서 빠졌다.
    """

    def _many(self, count: int, *, start: int = 0) -> list[dict]:
        return [
            {**REAL_ITEM, "contentid": f"festival-{start + index}"} for index in range(count)
        ]

    def _paged(self, items: list[dict], total: int) -> dict:
        return {
            "response": {
                "header": {"resultCode": "0000", "resultMsg": "OK"},
                "body": {"items": {"item": items}, "totalCount": total},
            }
        }

    @pytest.mark.asyncio
    async def test_한_번에_100건보다_많이_요청한다(self) -> None:
        """TourAPI는 numOfRows에 100 상한을 걸지 않는다(2026-09-07 실측).

        넉넉히 요청하면 전량을 한 번에 주므로 평시 호출은 1회 그대로다.
        """
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(dict(request.url.params))
            return httpx.Response(200, json=_payload([REAL_ITEM]))

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = RealFestivalProvider("KEY", client, timeout_seconds=5.0)
            await provider.search_festivals("11", None, date(2026, 8, 7))

        assert int(str(captured["numOfRows"])) > 100

    @pytest.mark.asyncio
    async def test_totalCount만큼_받을_때까지_이어받는다(self) -> None:
        """한 장에 다 못 담길 만큼 커지면 다음 장을 받는다.

        평시에는 타지 않는 안전장치다 — 지금은 첫 장에서 끝난다.
        """
        pages: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            page = int(str(dict(request.url.params)["pageNo"]))
            pages.append(page)
            # 전체 900건을 500건씩 나눠 준다.
            items = self._many(500) if page == 1 else self._many(400, start=500)
            return httpx.Response(200, json=self._paged(items, 900))

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = RealFestivalProvider("KEY", client, timeout_seconds=5.0)
            result = await provider.search_festivals("11", None, date(2026, 8, 7))

        assert pages == [1, 2]
        assert len(result.data) == 900

    @pytest.mark.asyncio
    async def test_상한에_걸리면_경고를_남긴다(self, caplog) -> None:
        """지금 문제가 정확히 "잘렸는데 아무도 모른다"였다.

        상한을 두더라도 넘었을 때 조용하면 같은 일이 되풀이된다.
        """

        def handler(request: httpx.Request) -> httpx.Response:
            page = int(str(dict(request.url.params)["pageNo"]))
            return httpx.Response(
                200, json=self._paged(self._many(500, start=(page - 1) * 500), 99_999)
            )

        transport = httpx.MockTransport(handler)
        with caplog.at_level(logging.WARNING):
            async with httpx.AsyncClient(transport=transport) as client:
                provider = RealFestivalProvider("KEY", client, timeout_seconds=5.0)
                await provider.search_festivals("11", None, date(2026, 8, 7))

        assert "상한에서 잘렸습니다" in caplog.text

    @pytest.mark.asyncio
    async def test_한_장으로_끝나면_한_번만_부른다(self) -> None:
        """D-025가 호출 수를 이유로 구 단위 호출을 기각했다. 그 전제를 지킨다."""
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json=self._paged(self._many(189), 189))

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = RealFestivalProvider("KEY", client, timeout_seconds=5.0)
            result = await provider.search_festivals("11", None, date(2026, 8, 7))

        assert calls == 1
        assert len(result.data) == 189
