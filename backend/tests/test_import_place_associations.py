"""원본 엣지(tAtsCd/rlteTatsCd) → content_id 엣지 변환 및 적재 규칙을 고정한다.

한쪽이라도 매핑 CSV에 없으면(build_place_association_mappings.py의 unmatched/
out_of_coverage) 그 엣지는 조용히 제외해야 한다 — content_id 없는 엣지가 들어가면
FK를 못 걸거나 조회 시 깨진 참조가 된다.
"""

from __future__ import annotations

import argparse
import csv
import json

import httpx
import pytest

from app.config import Settings
from scripts.import_place_associations import (
    AssociationEdge,
    load_code_to_content_id,
    resolve_edges,
    run,
    upsert_edges,
)


def _write_jsonl(path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8"
    )


def _write_mapping_csv(path, rows: list[dict]) -> None:
    fieldnames = [
        "tats_cd",
        "association_name",
        "district_code",
        "content_id",
        "place_title",
        "match_method",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _raw_row(
    *,
    t_ats_cd: str = "a1",
    rlte_tats_cd: str = "b1",
    base_ym: str = "202607",
    category: str = "관광지",
    rank: str = "1",
) -> dict:
    return {
        "baseYm": base_ym,
        "tAtsCd": t_ats_cd,
        "tAtsNm": "경복궁",
        "signguCd": "11110",
        "rlteTatsCd": rlte_tats_cd,
        "rlteTatsNm": "북촌한옥마을",
        "rlteSignguCd": "11110",
        "rlteCtgryLclsNm": category,
        "rlteRank": rank,
    }


class TestLoadCodeToContentId:
    def test_매칭된_행만_딕셔너리로_읽는다(self, tmp_path) -> None:
        path = tmp_path / "mapping.csv"
        _write_mapping_csv(
            path,
            [
                {
                    "tats_cd": "a1",
                    "association_name": "경복궁",
                    "district_code": "110",
                    "content_id": "100",
                    "place_title": "경복궁",
                    "match_method": "exact",
                }
            ],
        )

        mapping = load_code_to_content_id(path)

        assert mapping == {"a1": "100"}


class TestResolveEdges:
    def test_양쪽_다_매칭되면_엣지로_변환한다(self, tmp_path) -> None:
        path = tmp_path / "raw.jsonl"
        _write_jsonl(path, [_raw_row()])

        result = resolve_edges(path, {"a1": "100", "b1": "200"})

        assert result.raw_row_count == 1
        assert len(result.edges) == 1
        edge = result.edges[0]
        assert edge.from_content_id == "100"
        assert edge.to_content_id == "200"
        assert edge.category == "관광지"
        assert edge.rank == 1
        assert edge.base_ym == "202607"

    def test_한쪽만_매칭되면_제외한다(self, tmp_path) -> None:
        path = tmp_path / "raw.jsonl"
        _write_jsonl(path, [_raw_row()])

        result = resolve_edges(path, {"a1": "100"})  # b1 매핑 없음

        assert not result.edges
        assert result.unresolved_count == 1

    def test_양쪽_다_안_매칭되면_제외한다(self, tmp_path) -> None:
        path = tmp_path / "raw.jsonl"
        _write_jsonl(path, [_raw_row()])

        result = resolve_edges(path, {})

        assert not result.edges
        assert result.unresolved_count == 1

    def test_자기참조_엣지는_제외한다(self, tmp_path) -> None:
        path = tmp_path / "raw.jsonl"
        _write_jsonl(path, [_raw_row(t_ats_cd="a1", rlte_tats_cd="b1")])

        result = resolve_edges(path, {"a1": "100", "b1": "100"})  # 같은 content_id

        assert not result.edges
        assert result.self_loop_count == 1

    def test_같은_엣지가_반복되면_한_번만_남긴다(self, tmp_path) -> None:
        path = tmp_path / "raw.jsonl"
        _write_jsonl(path, [_raw_row(rank="1"), _raw_row(rank="1")])

        result = resolve_edges(path, {"a1": "100", "b1": "200"})

        assert len(result.edges) == 1
        assert result.duplicate_count == 1

    def test_알_수_없는_category는_예외를_던진다(self, tmp_path) -> None:
        path = tmp_path / "raw.jsonl"
        _write_jsonl(path, [_raw_row(category="쇼핑")])

        with pytest.raises(ValueError, match="category"):
            resolve_edges(path, {"a1": "100", "b1": "200"})

    def test_범위를_벗어난_rank는_예외를_던진다(self, tmp_path) -> None:
        path = tmp_path / "raw.jsonl"
        _write_jsonl(path, [_raw_row(rank="51")])

        with pytest.raises(ValueError, match="rlteRank"):
            resolve_edges(path, {"a1": "100", "b1": "200"})


class TestUpsertEdges:
    @pytest.mark.asyncio
    async def test_on_conflict과_merge_duplicates로_보낸다(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(201)

        transport = httpx.MockTransport(handler)
        settings = Settings(
            supabase_url="https://project.supabase.co",
            supabase_secret_key="sb_secret_test",
        )
        edges = [
            AssociationEdge(
                from_content_id="100",
                to_content_id="200",
                category="관광지",
                rank=1,
                base_ym="202607",
            )
        ]

        async with httpx.AsyncClient(transport=transport) as client:
            await upsert_edges(client, settings, edges)

        assert len(requests) == 1
        request = requests[0]
        assert request.url.params.get("on_conflict") == "from_content_id,to_content_id,base_ym"
        assert request.headers.get("Prefer") == "resolution=merge-duplicates,return=minimal"
        body = json.loads(request.content)
        assert body == [
            {
                "from_content_id": "100",
                "to_content_id": "200",
                "category": "관광지",
                "rank": 1,
                "base_ym": "202607",
            }
        ]
        assert "created_at" not in body[0]


class TestRun:
    @pytest.mark.asyncio
    async def test_활성_places에_없는_content_id가_있으면_예외를_던진다(self, tmp_path) -> None:
        raw_path = tmp_path / "raw.jsonl"
        _write_jsonl(raw_path, [_raw_row()])
        mapping_path = tmp_path / "mapping.csv"
        _write_mapping_csv(
            mapping_path,
            [
                {
                    "tats_cd": "a1",
                    "association_name": "경복궁",
                    "district_code": "110",
                    "content_id": "100",
                    "place_title": "경복궁",
                    "match_method": "exact",
                },
                {
                    "tats_cd": "b1",
                    "association_name": "북촌한옥마을",
                    "district_code": "110",
                    "content_id": "200",
                    "place_title": "북촌한옥마을",
                    "match_method": "exact",
                },
            ],
        )

        def handler(request: httpx.Request) -> httpx.Response:
            # places에는 100만 있고 200은 없다 — 200을 쓰는 엣지는 검증에서 걸려야 한다.
            return httpx.Response(
                200,
                json=[{"content_id": "100", "title": "경복궁", "district_code": "110"}],
            )

        transport = httpx.MockTransport(handler)
        settings = Settings(
            supabase_url="https://project.supabase.co",
            supabase_secret_key="sb_secret_test",
        )
        args = argparse.Namespace(raw_jsonl=raw_path, mapping_csv=mapping_path, dry_run=True)

        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="활성 places"):
                await run(args, settings, client)

    @pytest.mark.asyncio
    async def test_dry_run이면_적재_요청을_보내지_않는다(self, tmp_path) -> None:
        raw_path = tmp_path / "raw.jsonl"
        _write_jsonl(raw_path, [_raw_row()])
        mapping_path = tmp_path / "mapping.csv"
        _write_mapping_csv(
            mapping_path,
            [
                {
                    "tats_cd": "a1",
                    "association_name": "경복궁",
                    "district_code": "110",
                    "content_id": "100",
                    "place_title": "경복궁",
                    "match_method": "exact",
                },
                {
                    "tats_cd": "b1",
                    "association_name": "북촌한옥마을",
                    "district_code": "110",
                    "content_id": "200",
                    "place_title": "북촌한옥마을",
                    "match_method": "exact",
                },
            ],
        )

        post_calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                post_calls.append(request)
                return httpx.Response(201)
            return httpx.Response(
                200,
                json=[
                    {"content_id": "100", "title": "경복궁", "district_code": "110"},
                    {"content_id": "200", "title": "북촌한옥마을", "district_code": "110"},
                ],
            )

        transport = httpx.MockTransport(handler)
        settings = Settings(
            supabase_url="https://project.supabase.co",
            supabase_secret_key="sb_secret_test",
        )
        args = argparse_namespace(raw_jsonl=raw_path, mapping_csv=mapping_path, dry_run=True)

        async with httpx.AsyncClient(transport=transport) as client:
            result = await run(args, settings, client)

        assert len(result.edges) == 1
        assert not post_calls


def argparse_namespace(**kwargs):
    import argparse

    return argparse.Namespace(**kwargs)
