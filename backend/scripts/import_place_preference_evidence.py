"""상세 카드용 대표 취향 근거 문장을 Supabase에 upsert한다."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

import httpx

from app.config import Settings

CHUNK_SIZE = 100
MAX_EVIDENCE_PER_POLARITY = 2
MAX_EVIDENCE_LENGTH = 500
NAVER_MAP_BLOCK_PATTERN = re.compile(r"©\s*NAVER\s*Corp\.(.{0,500})", re.DOTALL)


def _text(value: object) -> str:
    return str(value or "").strip()


def _compact(value: object) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", _text(value).lower())


def load_active_tag_keys(cards_path: Path) -> set[tuple[str, str]]:
    """현재 place_preference_tags에 올린 장소×태그 조합만 고른다."""
    with cards_path.open(encoding="utf-8-sig", newline="") as handle:
        keys: set[tuple[str, str]] = set()
        for row in csv.DictReader(handle):
            content_id = _text(row.get("content_id"))
            if not content_id:
                continue
            details = json.loads(_text(row.get("preference_details_json")) or "[]")
            for detail in details:
                if isinstance(detail, dict) and _text(detail.get("code")):
                    keys.add((content_id, _text(detail["code"])))
    return keys


def load_document_contexts(documents_path: Path) -> dict[str, dict[str, str]]:
    """장소를 직접 다룬 정상 문서만 대표 근거 후보로 읽는다."""
    csv.field_size_limit(10_000_000)
    with documents_path.open(encoding="utf-8-sig", newline="") as handle:
        contexts: dict[str, dict[str, str]] = {}
        for row in csv.DictReader(handle):
            document_id = _text(row.get("document_id"))
            directness = _text(row.get("directness"))
            quality_status = _text(row.get("quality_status"))
            if (
                not document_id
                or (directness and directness != "direct")
                or (quality_status and quality_status != "ok")
            ):
                continue
            contexts[document_id] = {
                "content_id": _text(row.get("content_id")),
                "place_title": _text(row.get("place_title")),
                "source_type": _text(row.get("source_type")) or "naver_post",
                "source_text": _text(row.get("source_text")) or _text(row.get("clean_text")),
            }
    return contexts


def has_matching_naver_map_block(context: dict[str, str]) -> bool:
    """음식점 블로그는 네이버 지도·주소 블록 안에 대상 상호가 있어야 통과한다."""
    title = _compact(context["place_title"])
    if len(title) < 2:
        return False
    for matched in NAVER_MAP_BLOCK_PATTERN.finditer(context["source_text"]):
        if title in _compact(matched.group(1)):
            return True
    return False


async def fetch_restaurant_content_ids(
    client: httpx.AsyncClient, contexts: dict[str, dict[str, str]]
) -> set[str]:
    """TourAPI content_type_id=39(음식점)인 현재 후보 장소만 가져온다."""
    content_ids = sorted({item["content_id"] for item in contexts.values() if item["content_id"]})
    restaurant_ids: set[str] = set()
    for start in range(0, len(content_ids), 200):
        group = content_ids[start : start + 200]
        response = await client.get(
            "/rest/v1/places",
            params={
                "select": "content_id",
                "content_id": f"in.({','.join(group)})",
                "content_type_id": "eq.39",
            },
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"음식점 목록 조회 실패 HTTP {response.status_code}: {response.text[:500]}"
            )
        rows = response.json()
        if not isinstance(rows, list):
            raise RuntimeError("음식점 목록 응답 형식이 올바르지 않습니다.")
        restaurant_ids.update(_text(row.get("content_id")) for row in rows if isinstance(row, dict))
    return restaurant_ids


def _candidate_rows(
    evidence_path: Path,
    active_keys: set[tuple[str, str]],
    contexts: dict[str, dict[str, str]],
    restaurant_content_ids: set[str],
) -> Iterable[dict[str, object]]:
    with evidence_path.open(encoding="utf-8-sig", newline="") as handle:
        for row_number, row in enumerate(csv.DictReader(handle), start=2):
            content_id = _text(row.get("content_id"))
            preference_code = _text(row.get("preference_code"))
            polarity = _text(row.get("polarity"))
            document_id = _text(row.get("document_id"))
            context = contexts.get(document_id)
            text = " ".join(_text(row.get("evidence_text")).split())
            if (content_id, preference_code) not in active_keys or context is None:
                continue
            if context["source_type"] == "naver_post" and content_id in restaurant_content_ids:
                if not has_matching_naver_map_block(context):
                    continue
            if polarity not in {"positive", "mixed", "negative"}:
                raise ValueError(f"{row_number}행: 알 수 없는 polarity {polarity}")
            if not text:
                continue
            yield {
                "content_id": content_id,
                "preference_code": preference_code,
                "polarity": polarity,
                "document_id": document_id,
                "source_evidence_id": _text(row.get("evidence_id")),
                "evidence_text": text[:MAX_EVIDENCE_LENGTH],
                "source_type": _text(row.get("source_type")),
                "source_url": _text(row.get("source_url")) or None,
                "match_strength": int(_text(row.get("match_strength")) or "0"),
                "extraction_version": _text(row.get("extraction_version")),
            }


def load_payloads(
    evidence_path: Path,
    cards_path: Path,
    contexts: dict[str, dict[str, str]],
    restaurant_content_ids: set[str],
) -> list[dict[str, object]]:
    active_keys = load_active_tag_keys(cards_path)
    grouped: dict[tuple[str, str, str], list[dict[str, object]]] = defaultdict(list)
    for row in _candidate_rows(evidence_path, active_keys, contexts, restaurant_content_ids):
        grouped[
            (str(row["content_id"]), str(row["preference_code"]), str(row["polarity"]))
        ].append(row)

    payloads: list[dict[str, object]] = []
    for rows in grouped.values():
        rows.sort(
            key=lambda row: (
                -int(row["match_strength"]),
                -len(str(row["evidence_text"])),
                str(row["source_evidence_id"]),
            )
        )
        seen_documents: set[str] = set()
        rank = 0
        for row in rows:
            document_id = str(row["document_id"])
            if document_id in seen_documents:
                continue
            seen_documents.add(document_id)
            rank += 1
            payloads.append({**row, "evidence_rank": rank})
            if rank == MAX_EVIDENCE_PER_POLARITY:
                break
    return payloads


async def replace_existing_rows(client: httpx.AsyncClient) -> None:
    count_response = await client.get(
        "/rest/v1/place_preference_evidence",
        params={"select": "content_id", "limit": "1"},
        headers={"Prefer": "count=exact"},
    )
    if count_response.status_code >= 400:
        raise RuntimeError(
            f"기존 건수 조회 실패 HTTP {count_response.status_code}: {count_response.text[:500]}"
        )
    print(f"기존 대표 근거 {count_response.headers.get('content-range', '알 수 없음')} 교체")
    response = await client.delete(
        "/rest/v1/place_preference_evidence",
        params={"content_id": "not.is.null"},
        headers={"Prefer": "return=minimal"},
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"기존 대표 근거 삭제 실패 HTTP {response.status_code}: {response.text[:500]}"
        )


async def run(args: argparse.Namespace) -> None:
    settings = Settings()
    if not settings.supabase_url or not settings.supabase_secret_key:
        raise ValueError("SUPABASE_URL / SUPABASE_SECRET_KEY가 필요합니다.")
    contexts = load_document_contexts(args.documents_csv)
    headers = {
        "apikey": settings.supabase_secret_key,
        "Authorization": f"Bearer {settings.supabase_secret_key}",
    }
    async with httpx.AsyncClient(
        base_url=settings.supabase_url.rstrip("/"), headers=headers, timeout=60
    ) as client:
        restaurant_ids = await fetch_restaurant_content_ids(client, contexts)
        payloads = load_payloads(args.evidence_csv, args.cards_csv, contexts, restaurant_ids)
        print(
            f"직접 관련 문서 {len(contexts):,}건 · 음식점 {len(restaurant_ids):,}곳 "
            f"· 대표 근거 {len(payloads):,}건 준비 완료"
        )
        if args.dry_run:
            return
        if args.replace:
            await replace_existing_rows(client)
        for start in range(0, len(payloads), CHUNK_SIZE):
            chunk = payloads[start : start + CHUNK_SIZE]
            response = await client.post(
                "/rest/v1/place_preference_evidence",
                params={"on_conflict": "content_id,preference_code,polarity,evidence_rank"},
                headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
                json=chunk,
            )
            if response.status_code >= 400:
                raise RuntimeError(f"적재 실패 HTTP {response.status_code}: {response.text[:1000]}")
            print(f"적재 {min(start + CHUNK_SIZE, len(payloads)):,}/{len(payloads):,}")


def main() -> int:
    parser = argparse.ArgumentParser(description="상세 카드용 취향 근거 Supabase 적재")
    parser.add_argument("--evidence-csv", type=Path, required=True)
    parser.add_argument("--cards-csv", type=Path, required=True)
    parser.add_argument("--documents-csv", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--replace", action="store_true", help="현재 대표 근거만 모두 교체")
    asyncio.run(run(parser.parse_args()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
