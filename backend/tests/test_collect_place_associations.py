"""연관 관광지 원본 수집 스크립트의 페이지네이션·오류 처리·저장 형식을 고정한다.

areaBasedList1은 totalCount 기준으로 다음 페이지 필요 여부를 판단하고, NODATA 등
정상 실패(resultCode != "0000")는 예외 없이 빈 결과로 멈춰야 한다 — 구 하나가
실패해도 나머지 24개 구 수집이 계속돼야 하기 때문이다(로컬 25개 구 순회 특성상).
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings
from scripts.collect_place_associations import (
    SEOUL_DISTRICTS,
    DistrictResult,
    fetch_district_associations,
    write_jsonl,
)


def _settings() -> Settings:
    return Settings(tour_api_service_key="test-key")


def _item(tats_cd: str, rlte_cd: str, rank: int) -> dict[str, object]:
    return {
        "baseYm": "202508",
        "tAtsCd": tats_cd,
        "tAtsNm": "간현관광지",
        "areaCd": "11",
        "areaNm": "서울특별시",
        "signguCd": "11110",
        "signguNm": "종로구",
        "rlteTatsCd": rlte_cd,
        "rlteTatsNm": "연관장소",
        "rlteRegnCd": "11",
        "rlteRegnNm": "서울특별시",
        "rlteSignguCd": "11110",
        "rlteSignguNm": "종로구",
        "rlteCtgryLclsNm": "관광지",
        "rlteCtgryMclsNm": "문화관광",
        "rlteCtgrySclsNm": "전시시설",
        "rlteRank": rank,
    }


def _response_payload(
    items: list[dict[str, object]], *, total_count: int, result_code: str = "0000"
) -> dict[str, object]:
    return {
        "response": {
            "header": {"resultCode": result_code, "resultMsg": "OK"},
            "body": {
                "items": {"item": items},
                "numOfRows": len(items),
                "pageNo": 1,
                "totalCount": total_count,
            },
        }
    }


class TestFetchDistrictAssociations:
    @pytest.mark.asyncio
    async def test_totalCount를_넘길_때까지_페이지를_이어붙인다(self) -> None:
        pages = [
            _response_payload([_item("a", "b", 1)], total_count=2),
            _response_payload([_item("a", "c", 2)], total_count=2),
        ]
        calls = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            payload = pages[calls["count"]]
            calls["count"] += 1
            return httpx.Response(200, json=payload)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await fetch_district_associations(
                client,
                _settings(),
                area_code="11",
                signgu_cd="11110",
                signgu_nm="종로구",
                base_ym="202508",
                page_size=1,
                min_interval_seconds=0,
            )

        assert calls["count"] == 2
        assert len(result.items) == 2
        assert result.result_code == "0000"

    @pytest.mark.asyncio
    async def test_첫_페이지가_totalCount에_도달하면_한_번만_요청한다(self) -> None:
        payload = _response_payload(
            [_item("a", "b", 1), _item("a", "c", 2)], total_count=2
        )
        calls = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            return httpx.Response(200, json=payload)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await fetch_district_associations(
                client,
                _settings(),
                area_code="11",
                signgu_cd="11110",
                signgu_nm="종로구",
                base_ym="202508",
                page_size=100,
                min_interval_seconds=0,
            )

        assert calls["count"] == 1
        assert len(result.items) == 2

    @pytest.mark.asyncio
    async def test_NODATA면_예외_없이_빈_결과로_멈춘다(self) -> None:
        """구 하나에 그 달 데이터가 없어도 전체 수집이 죽으면 안 된다."""
        payload = {
            "response": {
                "header": {"resultCode": "03", "resultMsg": "NODATA_ERROR"},
                "body": {},
            }
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await fetch_district_associations(
                client,
                _settings(),
                area_code="11",
                signgu_cd="11740",
                signgu_nm="강동구",
                base_ym="202508",
                page_size=100,
                min_interval_seconds=0,
            )

        assert result.items == []
        assert result.result_code == "03"
        assert result.result_msg == "NODATA_ERROR"


class TestWriteJsonl:
    def test_구별_item을_한_줄씩_JSONL로_쓴다(self, tmp_path) -> None:
        results = [
            DistrictResult(
                signgu_cd="11110",
                signgu_nm="종로구",
                items=[_item("a", "b", 1), _item("a", "c", 2)],
                result_code="0000",
                result_msg="OK",
            ),
            DistrictResult(
                signgu_cd="11140",
                signgu_nm="중구",
                items=[_item("d", "e", 1)],
                result_code="0000",
                result_msg="OK",
            ),
        ]
        output = tmp_path / "out.jsonl"

        written = write_jsonl(results, output)

        assert written == 3
        lines = output.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3
        parsed = [json.loads(line) for line in lines]
        assert parsed[0]["tAtsCd"] == "a"
        assert parsed[2]["tAtsCd"] == "d"

    def test_결과가_없으면_빈_파일을_만든다(self, tmp_path) -> None:
        output = tmp_path / "empty.jsonl"

        written = write_jsonl([], output)

        assert written == 0
        assert output.read_text(encoding="utf-8") == ""


class TestSeoulDistricts:
    def test_25개_구가_모두_고유하다(self) -> None:
        codes = [code for code, _ in SEOUL_DISTRICTS]
        assert len(codes) == 25
        assert len(set(codes)) == 25

    def test_모든_코드가_서울_지역코드_11로_시작한다(self) -> None:
        for code, name in SEOUL_DISTRICTS:
            assert code.startswith("11"), f"{name}({code})는 11로 시작해야 함"
            assert len(code) == 5

    def test_종로구와_중구_코드가_알려진_값과_일치한다(self) -> None:
        by_name = dict((name, code) for code, name in SEOUL_DISTRICTS)
        assert by_name["종로구"] == "11110"
        assert by_name["중구"] == "11140"
