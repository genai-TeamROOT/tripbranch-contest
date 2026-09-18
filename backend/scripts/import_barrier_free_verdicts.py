"""무장애 문장 판정표를 읽어 place_barrier_free의 판정 컬럼을 채운다.

역할: `supabase/data/barrier_free_sentence_verdicts.csv`가 원본이고 DB 컬럼은
파생물이다. 컬럼을 손으로 고치지 않고 CSV를 고친 뒤 이 스크립트를 다시 돌린다.

판정은 문장마다 매겨져 있고 한 장소는 여러 문장을 갖는다. 그래서 장소 판정은
**그 장소가 가진 문장 중 가장 나쁜 것**으로 정한다. 접근로가 가능이어도
주출입구가 불가면 그 장소는 못 들어간다.

    impossible < partial < possible

어휘마다 읽는 컬럼이 다르다.

    wheelchair_access / stroller_access → 접근로·주출입구·엘리베이터
    visual_guide                        → 점자블록·점자안내·음성안내·안내견

원문이 하나도 없으면 판정을 null로 둔다. "판단할 근거가 없다"와 "가능하다"는
다르고, RPC는 null을 후보에서 뺀다.

**판정표에 없는 원문이 나오면 멈춘다.** 조용히 null로 두면 그 장소가 후보에서
소리 없이 사라지는데, 결과만 봐서는 그 사실이 드러나지 않는다. 적재가 늘어
새 문장이 생겼다는 신호이므로 라벨링을 먼저 해야 한다.

입력: supabase/data/barrier_free_sentence_verdicts.csv
출력: 없음 (public.place_barrier_free의 판정 컬럼 3개를 갱신한다)
호출 시점: 판정표를 고친 뒤 `python -m scripts.import_barrier_free_verdicts`로
      수동 실행한다. `--dry-run`은 쓰지 않고 무엇이 바뀌는지만 보여 준다.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import csv
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
VERDICTS_CSV = _REPO_ROOT / "supabase" / "data" / "barrier_free_sentence_verdicts.csv"

# 나쁜 쪽이 작다. 한 장소의 판정은 이 값의 최솟값이다.
_RANK = {"impossible": 0, "partial": 1, "possible": 2}
_BY_RANK = {rank: verdict for verdict, rank in _RANK.items()}

# 어휘 → 그 판정이 읽는 원문 컬럼. RPC(202609010001)의 판정 블록과 같은 묶음이라
# 한쪽만 바꾸면 후보 수와 안내 문구가 서로 다른 근거를 갖게 된다.
_SOURCE_COLUMNS = {
    "wheelchair_access": ("approach_route_raw", "entrance_access_raw", "elevator_raw"),
    "stroller_access": ("approach_route_raw", "entrance_access_raw", "elevator_raw"),
    "visual_guide": (
        "braille_block_raw",
        "braille_promotion_raw",
        "audio_guide_raw",
        "guide_dog_raw",
    ),
}
_VERDICT_COLUMNS = {need: f"{need}_verdict" for need in _SOURCE_COLUMNS}

# PostgREST는 한 번에 1000행까지만 돌려준다. 넘기면 조용히 잘려서, 적재하지 않은
# 장소가 "원문이 없는 장소"로 둔갑한다.
_PAGE_SIZE = 500


def load_verdicts() -> dict[str, dict[str, str]]:
    """판정표를 {원문: {어휘: 판정}}으로 읽는다."""
    if not VERDICTS_CSV.exists():
        raise FileNotFoundError(f"판정표가 없습니다: {VERDICTS_CSV}")
    table: dict[str, dict[str, str]] = collections.defaultdict(dict)
    with VERDICTS_CSV.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["verdict"] not in _RANK:
                raise ValueError(f"모르는 판정 값입니다: {row['verdict']!r}")
            table[row["sentence_text"]][row["need"]] = row["verdict"]
    return dict(table)


def resolve(row: dict, verdicts: dict[str, dict[str, str]]) -> dict[str, str | None]:
    """장소 한 곳의 어휘별 판정을 정한다. 원문이 없으면 None."""
    resolved: dict[str, str | None] = {}
    for need, columns in _SOURCE_COLUMNS.items():
        ranks = []
        for column in columns:
            text = row.get(column)
            if not text:
                continue
            verdict = verdicts.get(text, {}).get(need)
            if verdict is None:
                raise KeyError(f"판정표에 없는 원문입니다 [{column}]: {text[:60]!r}")
            ranks.append(_RANK[verdict])
        resolved[_VERDICT_COLUMNS[need]] = _BY_RANK[min(ranks)] if ranks else None
    return resolved


async def _fetch_all(client: httpx.AsyncClient, url: str, key: str) -> list[dict]:
    columns = sorted({c for group in _SOURCE_COLUMNS.values() for c in group})
    select = ",".join(["content_id", *columns, *_VERDICT_COLUMNS.values()])
    rows: list[dict] = []
    offset = 0
    while True:
        response = await client.get(
            f"{url}/rest/v1/place_barrier_free",
            params={
                "select": select,
                "order": "content_id",
                "limit": str(_PAGE_SIZE),
                "offset": str(offset),
            },
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
        )
        response.raise_for_status()
        page = response.json()
        rows += page
        if len(page) < _PAGE_SIZE:
            return rows
        offset += _PAGE_SIZE


async def _push(
    client: httpx.AsyncClient, url: str, key: str, changes: list[dict]
) -> None:
    """바뀐 장소만 갱신한다.

    upsert를 쓰지 않는다. place_barrier_free.fetched_at이 NOT NULL이고 기본값이
    없어서, upsert는 그 값을 함께 보내라고 요구한다. fetched_at은 TourAPI에서
    원문을 가져온 시각이라 판정을 넣는 지금 지어내면 안 된다.

    판정 조합이 같은 장소를 묶어 한 번에 갱신한다. 조합은 몇 가지뿐이라
    904행이 열 번 남짓의 호출로 끝난다.
    """
    grouped: dict[tuple, list[str]] = collections.defaultdict(list)
    for change in changes:
        key_ = tuple(change[column] for column in _VERDICT_COLUMNS.values())
        grouped[key_].append(change["content_id"])

    updated_at = datetime.now(UTC).isoformat()
    done = 0
    for verdicts, content_ids in grouped.items():
        payload = dict(zip(_VERDICT_COLUMNS.values(), verdicts, strict=True))
        # id를 한 줄에 다 실으면 URL이 길어져 잘린다. 잘리면 오류 없이 일부만
        # 갱신되고, 나머지는 옛 판정을 그대로 들고 남는다.
        for start in range(0, len(content_ids), 200):
            chunk = content_ids[start : start + 200]
            response = await client.patch(
                f"{url}/rest/v1/place_barrier_free",
                params={"content_id": f"in.({','.join(chunk)})"},
                json={**payload, "updated_at": updated_at},
                headers={
                    "apikey": key,
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal",
                },
            )
            if response.status_code >= 400:
                raise RuntimeError(
                    f"갱신 실패({response.status_code}): {response.text[:300]}"
                )
            done += len(chunk)
            print(f"  {done}/{len(changes)}행 · {payload}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="무장애 문장 판정표를 읽어 place_barrier_free의 판정 컬럼을 채운다"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="쓰지 않고 무엇이 바뀌는지만 보여 준다"
    )
    return parser


async def main() -> None:
    args = build_parser().parse_args()
    settings = Settings()
    if not settings.supabase_url.strip() or not settings.supabase_secret_key.strip():
        raise ValueError("SUPABASE_URL / SUPABASE_SECRET_KEY가 필요합니다.")

    verdicts = load_verdicts()
    print(f"판정표 문장 {len(verdicts)}개")

    async with httpx.AsyncClient(timeout=60) as client:
        rows = await _fetch_all(client, settings.supabase_url, settings.supabase_secret_key)
        print(f"무장애 행 {len(rows)}개")

        changes: list[dict] = []
        tally: collections.Counter[str] = collections.Counter()
        for row in rows:
            resolved = resolve(row, verdicts)
            for column, verdict in resolved.items():
                tally[f"{column}={verdict}"] += 1
            if any(row.get(column) != verdict for column, verdict in resolved.items()):
                changes.append({"content_id": row["content_id"], **resolved})

        for need, column in _VERDICT_COLUMNS.items():
            counts = {
                verdict: tally[f"{column}={verdict}"]
                for verdict in ("possible", "partial", "impossible", None)
            }
            print(
                f"  {need:<18} 가능 {counts['possible']:>4} · 부분 {counts['partial']:>3}"
                f" · 불가 {counts['impossible']:>2} · 원문없음 {counts[None]:>4}"
            )

        print(f"\n바꿀 행 {len(changes)}개")
        if not changes:
            print("바뀐 것이 없습니다.")
            return
        if args.dry_run:
            for change in changes[:10]:
                print(f"  {change}")
            print("--dry-run이라 쓰지 않았습니다.")
            return
        await _push(client, settings.supabase_url, settings.supabase_secret_key, changes)
    print("완료")


if __name__ == "__main__":
    asyncio.run(main())
