"""연관 관광지 원본(collect_place_associations.py) + 매핑 CSV
(build_place_association_mappings.py)를 content_id 엣지로 변환해
place_associations 테이블에 적재한다.

역할: 원본 JSONL은 tAtsCd/rlteTatsCd(32자리 해시코드)로 장소를 가리켜 우리
places.content_id와 바로 못 붙는다. 매핑 CSV는 그 코드를 content_id로 바꾼
표(코드가 매칭된 건만 존재)이므로, 두 파일을 코드 기준으로 조인해 양쪽 다
매칭된 엣지만 골라 적재한다. 한쪽이라도 매칭 실패면(build_place_association_
mappings.py의 unmatched/out_of_coverage) 그 엣지는 조용히 제외한다 — content_id가
없는 엣지를 억지로 넣으면 FK를 못 걸거나 조회 시 깨진 참조가 되므로, 매칭
자체는 그 스크립트의 재작업 대상으로 남긴다.

적재 정책: base_ym 단위로 이력을 보존한다(같은 base_ym을 재수집해 다시 실행하면
upsert로 rank/category만 덮어쓰고 created_at은 최초 적재 시각을 유지한다). 다른
base_ym으로 재수집하면 새 스냅샷 행이 별도로 쌓인다.

입력: --raw-jsonl(생략하면 supabase/data의 최신 place_associations_raw_*.jsonl)
      --mapping-csv(생략하면 supabase/data의 최신 place_association_mapping_*.csv)
호출 시점: `python -m scripts.import_place_associations`로 수동 실행한다.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import Settings
from scripts.build_place_association_mappings import load_places_from_supabase

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "supabase" / "data"
_UPSERT_CHUNK_SIZE = 100
_VALID_CATEGORIES = {"전체", "관광지", "음식", "숙박"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="연관 관광지 엣지를 place_associations에 적재")
    parser.add_argument(
        "--raw-jsonl",
        type=Path,
        help="collect_place_associations.py 출력 JSONL. 생략하면 최신 파일 자동 탐색.",
    )
    parser.add_argument(
        "--mapping-csv",
        type=Path,
        help="build_place_association_mappings.py 출력 CSV. 생략하면 최신 파일 자동 탐색.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="변환·검증만 하고 place_associations 테이블은 수정하지 않음",
    )
    return parser


def find_latest(data_dir: Path, pattern: str) -> Path | None:
    candidates = sorted(data_dir.glob(pattern))
    return candidates[-1] if candidates else None


@dataclass(frozen=True)
class AssociationEdge:
    from_content_id: str
    to_content_id: str
    category: str
    rank: int
    base_ym: str


@dataclass(frozen=True)
class ResolveResult:
    edges: list[AssociationEdge]
    raw_row_count: int
    unresolved_count: int
    self_loop_count: int
    duplicate_count: int


def load_code_to_content_id(mapping_csv: Path) -> dict[str, str]:
    with mapping_csv.open(encoding="utf-8-sig", newline="") as fp:
        return {
            row["tats_cd"]: row["content_id"]
            for row in csv.DictReader(fp)
            if row.get("tats_cd") and row.get("content_id")
        }


def resolve_edges(raw_jsonl: Path, code_to_content_id: dict[str, str]) -> ResolveResult:
    """원본 엣지(tAtsCd → rlteTatsCd)를 매핑 딕셔너리로 content_id 엣지로 바꾼다."""
    edges: list[AssociationEdge] = []
    seen: set[tuple[str, str, str]] = set()
    raw_row_count = 0
    unresolved_count = 0
    self_loop_count = 0
    duplicate_count = 0

    with raw_jsonl.open(encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            raw_row_count += 1
            item = json.loads(line)

            from_id = code_to_content_id.get(str(item.get("tAtsCd") or ""))
            to_id = code_to_content_id.get(str(item.get("rlteTatsCd") or ""))
            if not from_id or not to_id:
                unresolved_count += 1
                continue
            if from_id == to_id:
                self_loop_count += 1
                continue

            base_ym = str(item.get("baseYm") or "")
            key = (from_id, to_id, base_ym)
            if key in seen:
                duplicate_count += 1
                continue
            seen.add(key)

            category = str(item.get("rlteCtgryLclsNm") or "")
            if category not in _VALID_CATEGORIES:
                raise ValueError(
                    f"알 수 없는 category입니다: {category!r} (rlteTatsCd={item.get('rlteTatsCd')})"
                )
            rank_raw = str(item.get("rlteRank") or "")
            if not rank_raw.isdigit():
                raise ValueError(f"rlteRank가 숫자가 아닙니다: {rank_raw!r}")
            rank = int(rank_raw)
            if not 1 <= rank <= 50:
                raise ValueError(f"rlteRank 범위를 벗어났습니다: {rank}")
            if not base_ym:
                raise ValueError(f"baseYm이 비어 있습니다(tAtsCd={item.get('tAtsCd')}).")

            edges.append(
                AssociationEdge(
                    from_content_id=from_id,
                    to_content_id=to_id,
                    category=category,
                    rank=rank,
                    base_ym=base_ym,
                )
            )

    return ResolveResult(
        edges=edges,
        raw_row_count=raw_row_count,
        unresolved_count=unresolved_count,
        self_loop_count=self_loop_count,
        duplicate_count=duplicate_count,
    )


def _edge_payload(edge: AssociationEdge) -> dict[str, object]:
    # created_at은 일부러 안 보낸다 — merge-duplicates upsert가 페이로드에 있는
    # 컬럼만 덮어쓰므로, 안 보내면 재수집 때도 최초 적재 시각이 유지된다.
    return {
        "from_content_id": edge.from_content_id,
        "to_content_id": edge.to_content_id,
        "category": edge.category,
        "rank": edge.rank,
        "base_ym": edge.base_ym,
    }


async def _validate_active_endpoints(
    client: httpx.AsyncClient, settings: Settings, edges: Sequence[AssociationEdge]
) -> None:
    active_places = await load_places_from_supabase(settings, client)
    active_ids = {p.content_id for p in active_places}
    missing_ids = sorted(
        {edge.from_content_id for edge in edges if edge.from_content_id not in active_ids}
        | {edge.to_content_id for edge in edges if edge.to_content_id not in active_ids}
    )
    if missing_ids:
        raise ValueError(
            "활성 places에 없는 content_id가 엣지에 포함돼 있습니다: " + ", ".join(missing_ids)
        )


async def upsert_edges(
    client: httpx.AsyncClient, settings: Settings, edges: Sequence[AssociationEdge]
) -> None:
    payloads = [_edge_payload(edge) for edge in edges]
    for start in range(0, len(payloads), _UPSERT_CHUNK_SIZE):
        response = await client.post(
            settings.supabase_url.rstrip("/") + "/rest/v1/place_associations",
            params={"on_conflict": "from_content_id,to_content_id,base_ym"},
            headers={
                "apikey": settings.supabase_secret_key,
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
            json=payloads[start : start + _UPSERT_CHUNK_SIZE],
            timeout=settings.external_api_timeout_seconds,
        )
        response.raise_for_status()


async def run(
    args: argparse.Namespace,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> ResolveResult:
    raw_path = args.raw_jsonl or find_latest(_DATA_DIR, "place_associations_raw_*.jsonl")
    if raw_path is None or not raw_path.exists():
        raise ValueError(
            "원본 JSONL을 찾을 수 없습니다. --raw-jsonl로 지정하거나 먼저 "
            "collect_place_associations.py를 실행하세요."
        )
    mapping_path = args.mapping_csv or find_latest(_DATA_DIR, "place_association_mapping_*.csv")
    if mapping_path is None or not mapping_path.exists():
        raise ValueError(
            "매핑 CSV를 찾을 수 없습니다. --mapping-csv로 지정하거나 먼저 "
            "build_place_association_mappings.py를 실행하세요."
        )

    code_to_content_id = load_code_to_content_id(mapping_path)
    print(f"매핑 CSV {len(code_to_content_id)}건 로드: {mapping_path.name}")

    result = resolve_edges(raw_path, code_to_content_id)
    print(
        f"원본 {result.raw_row_count}건 → 엣지 {len(result.edges)}건 변환 "
        f"(미매칭 제외 {result.unresolved_count} / 자기참조 제외 {result.self_loop_count} / "
        f"중복 제외 {result.duplicate_count})"
    )

    if not settings.supabase_url or not settings.supabase_secret_key:
        raise ValueError("SUPABASE_URL / SUPABASE_SECRET_KEY가 필요합니다.")

    async def _with_client(active_client: httpx.AsyncClient) -> None:
        await _validate_active_endpoints(active_client, settings, result.edges)
        if not args.dry_run:
            await upsert_edges(active_client, settings, result.edges)

    if client is not None:
        await _with_client(client)
    else:
        async with httpx.AsyncClient() as owned_client:
            await _with_client(owned_client)

    print(f"\n적재 {'생략(dry-run)' if args.dry_run else '완료'}: {len(result.edges)}건")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    asyncio.run(run(args, Settings()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
