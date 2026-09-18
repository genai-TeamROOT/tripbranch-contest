"""연관 관광지 코드 ↔ content_id 매칭 규칙을 고정한다.

tAtsCd/rlteTatsCd는 TourAPI 표준 content_id와 다른 해시코드라 이름+구 매칭이
필요하다. 구가 다른 동명이인 장소를 잘못 붙이면 엉뚱한 곳의 연관관광지를
답하게 되므로(EXP-01 교훈), 구 필터가 실제로 매칭을 막는지를 우선 고정한다.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings
from scripts.build_place_association_mappings import (
    AssociationNode,
    PlaceRow,
    load_places_from_supabase,
    match_associations,
    parse_association_nodes,
    write_mapping_csv,
)


def _node(code: str, name: str, signgu_cd: str) -> AssociationNode:
    return AssociationNode(code=code, name=name, signgu_cd=signgu_cd)


def _place(content_id: str, title: str, district_code: str) -> PlaceRow:
    return PlaceRow(content_id=content_id, title=title, district_code=district_code)


class TestAssociationNodeDistrictCode:
    def test_signguCd_뒤_3자리가_district_code다(self) -> None:
        assert _node("a", "간현관광지", "11110").district_code == "110"
        assert _node("b", "명동성당", "11140").district_code == "140"


class TestMatchAssociations:
    def test_같은_구_정확일치는_매칭된다(self) -> None:
        nodes = [_node("a1", "경복궁", "11110")]
        places = [_place("100", "경복궁", "110")]

        matched, unmatched, out_of_coverage = match_associations(nodes, places)

        assert len(matched) == 1
        assert matched[0].content_id == "100"
        assert matched[0].match_method == "exact"
        assert not unmatched
        assert not out_of_coverage

    def test_괄호_부기가_달라도_정규화로_매칭된다(self) -> None:
        nodes = [_node("a1", "창덕궁과 후원", "11110")]
        places = [_place("100", "창덕궁과 후원 [유네스코 세계유산]", "110")]

        matched, unmatched, out_of_coverage = match_associations(nodes, places)

        assert len(matched) == 1
        assert matched[0].content_id == "100"
        assert matched[0].match_method == "normalized"

    def test_짧은_이름이_긴_장소명에_포함되면_부분일치로_매칭된다(self) -> None:
        """실측 사례 — '창덕궁'이 '창덕궁과 후원 [유네스코 세계유산]'에 포함된다.

        _variants()는 완전히 같아지는 경우만 잡아서(괄호 제거·서울 접두어 제거),
        '창덕궁'과 '창덕궁과 후원'처럼 진짜 다른 문자열인 포함 관계는 못 잡는다.
        """
        nodes = [_node("a1", "창덕궁", "11110")]
        places = [_place("100", "창덕궁과 후원 [유네스코 세계유산]", "110")]

        matched, unmatched, out_of_coverage = match_associations(nodes, places)

        assert len(matched) == 1
        assert matched[0].content_id == "100"
        assert matched[0].match_method == "substring"

    def test_부분일치_후보가_여러곳이면_자동매칭하지_않는다(self) -> None:
        """모호한 포함 관계는 사람 확인으로 남긴다 — 엉뚱한 곳에 붙이지 않는다."""
        nodes = [_node("a1", "종묘", "11110")]
        places = [
            _place("100", "종묘앞식당", "110"),
            _place("200", "종묘약속다방", "110"),
        ]

        matched, unmatched, out_of_coverage = match_associations(nodes, places)

        assert not matched
        assert len(unmatched) == 1
        assert not out_of_coverage

    def test_한글자짜리_토큰은_부분일치_대상에서_제외한다(self) -> None:
        """너무 짧은 키는 아무 데나 걸려 오매칭 위험이 크다."""
        nodes = [_node("a1", "역", "11110")]
        places = [_place("100", "서울역사박물관", "110")]

        matched, unmatched, out_of_coverage = match_associations(nodes, places)

        assert not matched
        assert len(unmatched) == 1

    def test_이름이_같아도_구가_다르면_매칭하지_않는다(self) -> None:
        """EXP-01 교훈 — 동명이인 장소를 이름만으로 붙이면 사고가 난다.

        중구(140) 노드 '중앙시장'과 이름이 같은 장소가 종로구(110)에만 있고,
        중구 자체에는 다른 이름의 장소만 있다 — 구 필터가 없으면 종로구 쪽으로
        잘못 붙을 위험이 있는 배치다.
        """
        nodes = [_node("a1", "중앙시장", "11140")]  # 중구(140)
        places = [
            _place("100", "중앙시장", "110"),  # 종로구 — 이름은 같지만 다른 구
            _place("200", "명동성당", "140"),  # 중구 — 이름이 다름
        ]

        matched, unmatched, out_of_coverage = match_associations(nodes, places)

        assert not matched
        assert not out_of_coverage
        assert len(unmatched) == 1
        assert unmatched[0].code == "a1"

    def test_같은_구인데_이름이_안_맞으면_unmatched로_분류한다(self) -> None:
        nodes = [_node("a1", "전혀다른이름", "11110")]
        places = [_place("100", "경복궁", "110")]

        matched, unmatched, out_of_coverage = match_associations(nodes, places)

        assert not matched
        assert len(unmatched) == 1
        assert not out_of_coverage

    def test_places에_없는_구는_out_of_coverage로_분류한다(self) -> None:
        nodes = [_node("a1", "아무개장소", "11740")]  # 강동구(740), places에 없음
        places = [_place("100", "경복궁", "110")]

        matched, unmatched, out_of_coverage = match_associations(nodes, places)

        assert not matched
        assert not unmatched
        assert len(out_of_coverage) == 1


class TestParseAssociationNodes:
    def test_기준과_연관_양쪽_모두_노드로_뽑는다(self, tmp_path) -> None:
        rows = [
            {
                "tAtsCd": "a1",
                "tAtsNm": "경복궁",
                "signguCd": "11110",
                "rlteTatsCd": "b1",
                "rlteTatsNm": "북촌한옥마을",
                "rlteSignguCd": "11110",
            }
        ]
        path = tmp_path / "in.jsonl"
        path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8"
        )

        nodes = parse_association_nodes(path)

        assert set(nodes) == {"a1", "b1"}
        assert nodes["a1"].name == "경복궁"
        assert nodes["b1"].name == "북촌한옥마을"

    def test_같은_코드가_반복돼도_한_번만_남는다(self, tmp_path) -> None:
        rows = [
            {
                "tAtsCd": "a1",
                "tAtsNm": "경복궁",
                "signguCd": "11110",
                "rlteTatsCd": "b1",
                "rlteTatsNm": "북촌한옥마을",
                "rlteSignguCd": "11110",
            },
            {
                "tAtsCd": "a1",
                "tAtsNm": "경복궁",
                "signguCd": "11110",
                "rlteTatsCd": "c1",
                "rlteTatsNm": "창덕궁",
                "rlteSignguCd": "11110",
            },
        ]
        path = tmp_path / "in.jsonl"
        path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8"
        )

        nodes = parse_association_nodes(path)

        assert len(nodes) == 3
        assert set(nodes) == {"a1", "b1", "c1"}


class TestLoadPlacesFromSupabase:
    @pytest.mark.asyncio
    async def test_1000행_넘는_경우_다음_페이지를_이어_받는다(self) -> None:
        """D-081과 같은 PostgREST 기본 1000행 상한 — limit을 줘도 여기서 다시 겪는다."""
        page1 = [
            {"content_id": str(i), "title": f"장소{i}", "district_code": "110"}
            for i in range(1000)
        ]
        page2 = [{"content_id": "1000", "title": "장소1000", "district_code": "140"}]
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            offset = request.url.params.get("offset")
            calls.append(offset or "0")
            if offset == "1000":
                return httpx.Response(200, json=page2)
            return httpx.Response(200, json=page1)

        transport = httpx.MockTransport(handler)
        settings = Settings(
            supabase_url="https://project.supabase.co",
            supabase_secret_key="sb_secret_test",
        )
        async with httpx.AsyncClient(transport=transport) as client:
            places = await load_places_from_supabase(settings, client)

        assert calls == ["0", "1000"]
        assert len(places) == 1001
        assert places[-1].content_id == "1000"
        assert places[-1].district_code == "140"

    @pytest.mark.asyncio
    async def test_1000행_미만이면_한_번만_요청한다(self) -> None:
        page = [{"content_id": "1", "title": "장소1", "district_code": "110"}]
        calls = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            return httpx.Response(200, json=page)

        transport = httpx.MockTransport(handler)
        settings = Settings(
            supabase_url="https://project.supabase.co",
            supabase_secret_key="sb_secret_test",
        )
        async with httpx.AsyncClient(transport=transport) as client:
            places = await load_places_from_supabase(settings, client)

        assert calls["count"] == 1
        assert len(places) == 1


class TestWriteMappingCsv:
    def test_매칭_결과를_CSV로_쓴다(self, tmp_path) -> None:
        from scripts.build_place_association_mappings import MatchedAssociation

        rows = [
            MatchedAssociation(
                code="a1",
                name="경복궁",
                district_code="110",
                content_id="100",
                place_title="경복궁",
                match_method="exact",
            )
        ]
        output = tmp_path / "out.csv"

        write_mapping_csv(rows, output)

        content = output.read_text(encoding="utf-8-sig")
        assert "tats_cd" in content.splitlines()[0]
        assert "a1,경복궁,110,100,경복궁,exact" in content
