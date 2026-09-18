"""package_D의 place_embeddings.jsonl을 Supabase place_embeddings에 적재한다."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import Settings

_UPSERT_CHUNK_SIZE = 200
# 분산 임베딩 전체 적재 기간에는 HNSW 인덱스를 한 번 제거한 상태에서 200행씩
# 보낸다. 2026-09-09 서초구 v8 실측에서 HNSW가 있으면 200행 요청이 DB의
# statement_timeout(57014)으로 11.08초 만에 실패했지만, 인덱스 제거 후에는
# 같은 요청이 2.36초에 성공했다. 전체 적재가 끝난 뒤 HNSW를 한 번 재생성한다.
_UPSERT_TIMEOUT_SECONDS = 120.0
_UPSERT_MAX_ATTEMPTS = 5
_EMBEDDING_DIM = 768
# tour_overview는 202609070001 마이그레이션으로 허용됐다(TripBranch 분산
# 임베딩, 2026-09-07). published_at이 항상 비어 있어(overview는 작성일 개념이
# 없음) 아래 _parse_published_at은 raw가 없으면 그냥 None을 반환하는 기존
# 분기를 그대로 탄다 — source_type별 특별 처리가 필요 없다.
_VALID_SOURCE_TYPES = {"naver_post", "google_review", "tour_overview"}
_DEFAULT_JSONL_PATH = (
    Path(__file__).resolve().parents[2] / "package_D" / "place_embeddings.jsonl"
)

# naver_post: "YYYY. M. D. H:MM" 한국어 절대 표기(KST). 상대 표기("6시간 전")·
# 연월만("2026.08")·빈 문자열은 이 정규식에 매치되지 않아 NULL로 남는다 —
# 스크랩 기준 시각이 기록에 없어 절대 시각으로 되돌릴 근거가 없다(2026-08-18 실측).
_KOREAN_ABSOLUTE_RE = re.compile(
    r"^(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{1,2}):(\d{2})$"
)
# TripBranch_RAG_Distributed_Embedding(2026-09-07)의 naver_post published_at은
# prepare_rag_documents_prechunk.py의 normalize_naver_datetime()이 이미
# "YYYY-MM-DDTHH:MM:SS+09:00" ISO 8601로 정규화해 내보낸다. 위 한국어 절대
# 표기 정규식은 이 형식을 매치하지 못해 그대로 두면 새로 적재하는 naver_post
# 전체의 published_at이 조용히 NULL이 된다 — 두 형식을 모두 인식하도록
# ISO 8601(+HH:MM 오프셋) 형식도 함께 받는다. 기존 한국어 표기 데이터의 동작은
# 그대로 유지된다(이 정규식에 안 걸리면 기존 분기로 폴백).
_ISO_KST_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?[+-]\d{2}:\d{2}$"
)
# google_review: ISO 8601이되 끝맺음이 "Z"거나 "+00:00"이고, 나노초(9자리) 소수부가
# 있을 수도 없을 수도 있다(2026-08-18 실측, 두 형식이 3,988/1,667건). timestamptz는
# 마이크로초(6자리)까지만 받으므로 소수부가 있으면 뒤를 잘라낸다.
_ISO_UTC_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(?:Z|\+00:00)$"
)


@dataclass(frozen=True)
class PlaceEmbeddingImportResult:
    jsonl_row_count: int
    payload_count: int
    duplicate_keys: int
    published_at_nulled: int
    imported_count: int
    dry_run: bool
    conflict_key: str
    skipped_existing: int = 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="place_embeddings.jsonl 적재")
    parser.add_argument(
        "--jsonl",
        type=Path,
        default=_DEFAULT_JSONL_PATH,
        help="place_embeddings.jsonl 경로(기본값: package_D/place_embeddings.jsonl)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="jsonl과 장소 참조만 검증하고 테이블은 수정하지 않음",
    )
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help=(
            "적재 전에 동일 모델의 기존 임베딩을 지정 범위에서 삭제. "
            "청킹 버전 변경 시 사라진 옛 청크가 남는 것을 방지한다."
        ),
    )
    parser.add_argument(
        "--scope-content-ids",
        type=Path,
        help=(
            "--replace-existing 범위 CSV. content_id 컬럼이 있는 해당 구 "
            "place_catalog.csv를 지정해야 한다."
        ),
    )
    return parser


def _load_scope_content_ids(path: Path) -> list[str]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = csv.DictReader(stream)
        if not rows.fieldnames or "content_id" not in rows.fieldnames:
            raise ValueError(f"{path}: content_id 컬럼이 필요합니다.")
        ids = sorted({str(row.get("content_id") or "").strip() for row in rows})
    ids = [value for value in ids if value]
    if not ids:
        raise ValueError(f"{path}: 교체 범위 content_id가 없습니다.")
    return ids


def _parse_published_at(raw: object, *, source_type: str, content_id: str) -> str | None:
    if not raw:
        return None
    text = str(raw)
    if source_type == "google_review":
        match = _ISO_UTC_RE.match(text)
        if match is None:
            raise ValueError(
                f"{content_id}: 알 수 없는 google_review published_at 형식 {text!r}"
            )
        base, frac = match.groups()
        return f"{base}.{frac[:6]}Z" if frac else f"{base}Z"
    if _ISO_KST_RE.match(text):
        # 이미 완전한 ISO 8601이라 재조합 없이 그대로 timestamptz 리터럴로 쓴다.
        return text
    match = _KOREAN_ABSOLUTE_RE.match(text)
    if match is None:
        return None
    year, month, day, hour, minute = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}T{int(hour):02d}:{minute}:00+09:00"


def _optional_metadata_fields(
    row: dict[str, object], *, content_id: str, source_ref: str
) -> dict[str, object | None]:
    """rating/language/is_translated/chunk_* — 202609070002 마이그레이션으로 추가된
    컬럼. 전부 nullable이라 jsonl에 없으면 None으로 채운다(기존 jsonl 형식과도
    호환). 값이 있으면 타입을 검증해 잘못된 형식이 조용히 NULL로 들어가지
    않게 한다.
    """
    label = f"{content_id}/{source_ref}"
    fields: dict[str, object | None] = {}

    rating = row.get("rating")
    if rating in (None, ""):
        fields["rating"] = None
    elif isinstance(rating, (int, float)) and 1 <= rating <= 5:
        fields["rating"] = int(rating)
    else:
        raise ValueError(f"{label}: rating은 1~5 정수여야 합니다 ({rating!r})")

    language = row.get("language")
    fields["language"] = str(language).strip() or None if language else None

    is_translated = row.get("is_translated")
    if is_translated is None or is_translated == "":
        fields["is_translated"] = None
    elif isinstance(is_translated, bool):
        fields["is_translated"] = is_translated
    else:
        raise ValueError(f"{label}: is_translated는 boolean이어야 합니다 ({is_translated!r})")

    for key in ("chunk_index", "chunk_count", "token_count"):
        value = row.get(key)
        if value in (None, ""):
            fields[key] = None
        elif (
            isinstance(value, int)
            and not isinstance(value, bool)
            and (value >= 0 if key == "chunk_index" else value > 0)
        ):
            fields[key] = value
        else:
            condition = "0 이상" if key == "chunk_index" else "1 이상"
            raise ValueError(f"{label}: {key}는 {condition} 정수여야 합니다 ({value!r})")

    if fields.get("chunk_index") is not None and fields.get("chunk_count") is not None:
        if fields["chunk_index"] >= fields["chunk_count"]:
            raise ValueError(
                f"{label}: chunk_index({fields['chunk_index']})가 "
                f"chunk_count({fields['chunk_count']}) 범위를 벗어났습니다."
            )

    for key in (
        "document_id", "chunk_id", "content_hash", "preprocessing_version",
        "embedding_version",
    ):
        value = row.get(key)
        fields[key] = str(value).strip() or None if value else None

    return fields


def load_embedding_payloads(
    jsonl_path: Path,
) -> tuple[list[dict[str, object]], int, int, int]:
    """jsonl을 읽어 upsert 페이로드로 바꾼다. 형식 위반은 즉시 멈춘다.

    v2의 (content_id, chunk_id)가 같은 행이 또 나오면 나중 것을 버린다.
    같은 배치 안에서 동일 충돌 키가 반복되면 ON CONFLICT가 같은 행을 두 번
    갱신할 수 없기 때문이다. 202609070003 이후에는 chunk_id가 필수다.
    """
    payloads: list[dict[str, object]] = []
    seen_keys: set[tuple[str, str]] = set()
    duplicate_count = 0
    published_at_nulled = 0
    row_count = 0

    with jsonl_path.open(encoding="utf-8") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            line = raw_line.strip()
            if not line:
                continue
            row_count += 1
            row = json.loads(line)

            content_id = str(row.get("content_id") or "").strip()
            source_ref = str(row.get("source_ref") or "").strip()
            chunk_id = str(row.get("chunk_id") or "").strip()
            source_type = str(row.get("source_type") or "").strip()
            source_text = str(row.get("source_text") or "").strip()
            place_title = str(row.get("place_title") or "").strip()
            model_name = str(row.get("model_name") or "").strip()
            embedding = row.get("embedding")

            if not content_id:
                raise ValueError(f"{line_number}행: content_id가 없습니다.")
            if not source_ref:
                raise ValueError(f"{content_id}: source_ref가 없습니다.")
            if not chunk_id:
                raise ValueError(f"{content_id}/{source_ref}: chunk_id가 없습니다.")
            if source_type not in _VALID_SOURCE_TYPES:
                raise ValueError(f"{content_id}: 알 수 없는 source_type {source_type!r}")
            if not source_text:
                raise ValueError(f"{content_id}/{source_ref}: source_text가 비어 있습니다.")
            if not place_title:
                raise ValueError(f"{content_id}/{source_ref}: place_title이 비어 있습니다.")
            if not model_name:
                raise ValueError(f"{content_id}/{source_ref}: model_name이 없습니다.")
            if not isinstance(embedding, list) or len(embedding) != _EMBEDDING_DIM:
                raise ValueError(
                    f"{content_id}/{source_ref}: embedding이 {_EMBEDDING_DIM}차원이 아닙니다."
                )

            key = (content_id, chunk_id)
            if key in seen_keys:
                duplicate_count += 1
                continue
            seen_keys.add(key)

            raw_published_at = row.get("published_at")
            published_at = _parse_published_at(
                raw_published_at, source_type=source_type, content_id=content_id
            )
            if raw_published_at and published_at is None:
                published_at_nulled += 1

            source_url = str(row["source_url"]).strip() if row.get("source_url") else ""
            payloads.append(
                {
                    "content_id": content_id,
                    "place_title": place_title,
                    "source_type": source_type,
                    "source_text": source_text,
                    "source_url": source_url or None,
                    "source_ref": source_ref,
                    "published_at": published_at,
                    "embedding": embedding,
                    "model_name": model_name,
                    **_optional_metadata_fields(row, content_id=content_id, source_ref=source_ref),
                }
            )

    return payloads, row_count, duplicate_count, published_at_nulled


_VALIDATE_PAGE_SIZE = 1000


async def _validate_active_places(
    client: httpx.AsyncClient,
    payloads: Sequence[dict[str, object]],
) -> None:
    # limit만 걸고 페이지네이션이 없으면 활성 장소가 1000건을 넘는 순간(다른 구
    # 확장 등으로) 뒤쪽 행이 조용히 잘려나가 실제로 활성인 content_id를
    # "없다"고 오판한다(2026-08-20, 중구 확장 중 실측). order를 명시해야
    # offset 페이지네이션이 페이지 사이에서 행을 건너뛰거나 중복하지 않는다.
    active_ids: set[str] = set()
    offset = 0
    while True:
        response = await client.get(
            "/rest/v1/places",
            params={
                "select": "content_id",
                "is_active": "eq.true",
                "order": "content_id.asc",
                "limit": str(_VALIDATE_PAGE_SIZE),
                "offset": str(offset),
            },
        )
        response.raise_for_status()
        rows = response.json()
        active_ids.update(
            str(row["content_id"])
            for row in rows
            if isinstance(row, dict) and row.get("content_id")
        )
        if len(rows) < _VALIDATE_PAGE_SIZE:
            break
        offset += _VALIDATE_PAGE_SIZE
    missing_ids = sorted(
        {
            str(payload["content_id"])
            for payload in payloads
            if str(payload["content_id"]) not in active_ids
        }
    )
    if missing_ids:
        raise ValueError(
            "활성 places에 없는 content_id: " + ", ".join(missing_ids[:20])
            + (f" 외 {len(missing_ids) - 20}건" if len(missing_ids) > 20 else "")
        )


async def _delete_superseded_versions(
    client: httpx.AsyncClient,
    *,
    content_ids: Sequence[str],
    model_name: str,
    embedding_version: str,
) -> None:
    """새 적재 성공 뒤 해당 구·모델의 과거 버전만 작은 배치로 제거한다."""
    for start in range(0, len(content_ids), 100):
        chunk = content_ids[start : start + 100]
        quoted = ",".join(f'"{value}"' for value in chunk)
        response = await client.delete(
            "/rest/v1/place_embeddings",
            params={
                "content_id": f"in.({quoted})",
                "model_name": f"eq.{model_name}",
                "or": f"(embedding_version.is.null,embedding_version.neq.{embedding_version})",
            },
            headers={"Prefer": "return=minimal"},
        )
        response.raise_for_status()


async def run(
    args: argparse.Namespace,
    settings: Settings,
) -> PlaceEmbeddingImportResult:
    if not settings.supabase_url:
        raise ValueError("SUPABASE_URL이 필요합니다.")
    if not settings.supabase_secret_key:
        raise ValueError("SUPABASE_SECRET_KEY가 필요합니다.")

    payloads, row_count, duplicate_count, published_at_nulled = load_embedding_payloads(
        args.jsonl
    )
    headers = {
        "apikey": settings.supabase_secret_key,
        "Authorization": f"Bearer {settings.supabase_secret_key}",
    }
    async with httpx.AsyncClient(
        base_url=settings.supabase_url.rstrip("/"),
        headers=headers,
        timeout=_UPSERT_TIMEOUT_SECONDS,
    ) as client:
        await _validate_active_places(client, payloads)
        if not args.dry_run:
            conflict_key = "content_id,chunk_id"
            replace_scope_ids: list[str] | None = None
            replace_model_name: str | None = None
            replace_embedding_version: str | None = None
            if args.replace_existing:
                if args.scope_content_ids is None:
                    raise ValueError(
                        "--replace-existing에는 --scope-content-ids "
                        "place_catalog.csv가 필요합니다."
                    )
                model_names = {str(payload["model_name"]) for payload in payloads}
                if len(model_names) != 1:
                    raise ValueError("교체 적재 파일에는 model_name이 하나만 있어야 합니다.")
                embedding_versions = {
                    str(payload.get("embedding_version") or "") for payload in payloads
                }
                if len(embedding_versions) != 1 or not next(iter(embedding_versions)):
                    raise ValueError(
                        "교체 적재 파일에는 비어 있지 않은 "
                        "embedding_version 하나가 필요합니다."
                    )
                replace_scope_ids = _load_scope_content_ids(args.scope_content_ids)
                replace_model_name = next(iter(model_names))
                replace_embedding_version = next(iter(embedding_versions))
            total_chunks = (len(payloads) + _UPSERT_CHUNK_SIZE - 1) // _UPSERT_CHUNK_SIZE
            for chunk_index, start in enumerate(
                range(0, len(payloads), _UPSERT_CHUNK_SIZE), start=1
            ):
                chunk = payloads[start : start + _UPSERT_CHUNK_SIZE]
                for attempt in range(1, _UPSERT_MAX_ATTEMPTS + 1):
                    response = await client.post(
                        "/rest/v1/place_embeddings",
                        params={"on_conflict": conflict_key},
                        headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
                        json=chunk,
                    )
                    if response.status_code < 400:
                        break

                    # HNSW 인덱스 갱신 중 Postgres statement_timeout(57014)이
                    # 간헐적으로 발생할 수 있다. 같은 upsert는 chunk_id 기준으로
                    # 멱등이므로 짧게 기다린 뒤 해당 배치만 안전하게 재시도한다.
                    statement_timeout = (
                        response.status_code >= 500
                        and ("57014" in response.text or "statement timeout" in response.text)
                    )
                    if statement_timeout and attempt < _UPSERT_MAX_ATTEMPTS:
                        wait_seconds = min(2**attempt, 10)
                        print(
                            f"  배치 {chunk_index}/{total_chunks} statement timeout — "
                            f"{wait_seconds}초 후 재시도 "
                            f"({attempt}/{_UPSERT_MAX_ATTEMPTS})"
                        )
                        await asyncio.sleep(wait_seconds)
                        continue

                    # PostgREST 오류 본문에는 실제 원인(제약 이름, 타입 불일치 등)이
                    # 담겨 있는데 raise_for_status()만 부르면 그 내용이 사라진다.
                    print(
                        f"  배치 {chunk_index}/{total_chunks} 실패 "
                        f"(HTTP {response.status_code}, 시도 {attempt})"
                    )
                    print(f"  응답 본문: {response.text[:2000]}")
                    response.raise_for_status()
                else:  # pragma: no cover - 마지막 실패는 위에서 예외 처리된다.
                    raise RuntimeError(f"배치 {chunk_index}/{total_chunks} 적재 실패")
                if chunk_index % 20 == 0 or chunk_index == total_chunks:
                    print(f"  적재 {chunk_index}/{total_chunks} 배치 완료")
            # 새 버전 전체가 정상 적재된 뒤에만 과거 버전을 제거한다. 선삭제 후
            # 적재 실패로 검색 데이터가 비는 상황을 피한다.
            if replace_scope_ids is not None:
                await _delete_superseded_versions(
                    client,
                    content_ids=replace_scope_ids,
                    model_name=replace_model_name or "",
                    embedding_version=replace_embedding_version or "",
                )
                print(f"  과거 임베딩 버전 정리 완료: {len(replace_scope_ids)}개 장소")

    if args.dry_run:
        conflict_key = "content_id,chunk_id"

    return PlaceEmbeddingImportResult(
        jsonl_row_count=row_count,
        payload_count=len(payloads),
        duplicate_keys=duplicate_count,
        published_at_nulled=published_at_nulled,
        imported_count=0 if args.dry_run else len(payloads),
        dry_run=args.dry_run,
        conflict_key=conflict_key,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = asyncio.run(run(args, Settings()))
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
