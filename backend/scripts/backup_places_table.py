"""동기화 전 places 테이블 전체를 CSV로 덤프한다(수동 실행).

역할: 강제 상세 재조회처럼 많은 행을 한 번에 갱신하기 전, 되돌릴 근거를 파일로
남긴다. supabase/data/backups/<날짜>_<사유>/ 관례를 따른다.
입력: --out(저장 경로). 생략하면 supabase/data/places_backup_<오늘>.csv.
출력: places 전체 행 CSV.
호출 시점: `python -m scripts.backup_places_table`

DB는 읽기만 한다.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from app.config import Settings

KST = ZoneInfo("Asia/Seoul")
_PAGE_SIZE = 500


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="places 테이블 CSV 백업")
    parser.add_argument("--out", type=Path, help="저장 경로")
    return parser


async def dump(settings: Settings, out_path: Path) -> int:
    """PostgREST의 CSV 응답을 그대로 이어붙인다.

    페이지를 나눠 받는다 — PostgREST 기본 상한이 있어 한 번에 전체가 오지 않을 수
    있고, 조용히 잘린 파일을 백업으로 남기면 백업이 없는 것보다 나쁘다.

    **행 수는 물리적 줄 수로 세지 않는다.** overview 같은 필드에 줄바꿈이 들어 있어
    한 레코드가 여러 줄을 차지한다. 페이지 종료 판정과 최종 건수 모두 csv 모듈이
    파싱한 레코드 수를 쓴다 — 줄 수로 세면 백업이 온전한데도 건수가 맞지 않아
    잘린 것처럼 보인다.
    """
    base = settings.supabase_url.rstrip("/")
    headers = {
        "apikey": settings.supabase_secret_key,
        "Authorization": f"Bearer {settings.supabase_secret_key}",
        "Accept": "text/csv",
    }
    chunks: list[str] = []
    total = 0
    async with httpx.AsyncClient(timeout=60.0) as client:
        offset = 0
        while True:
            response = await client.get(
                f"{base}/rest/v1/places",
                headers={
                    **headers,
                    "Range-Unit": "items",
                    "Range": f"{offset}-{offset + _PAGE_SIZE - 1}",
                },
                params={"select": "*", "order": "content_id"},
            )
            response.raise_for_status()
            text = response.text
            if not text.strip():
                break

            rows = len(list(csv.reader(io.StringIO(text)))) - 1
            if rows <= 0:
                break
            if offset == 0:
                chunks.append(text if text.endswith("\n") else text + "\n")
            else:
                # 2페이지부터는 헤더 줄을 뺀다. 헤더는 줄바꿈을 포함하지 않아 첫
                # 물리 줄로 잘라내도 안전하다.
                body = text.split("\n", 1)[1] if "\n" in text else ""
                if body.strip():
                    chunks.append(body if body.endswith("\n") else body + "\n")
            total += rows
            if rows < _PAGE_SIZE:
                break
            offset += _PAGE_SIZE

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("".join(chunks), encoding="utf-8")
    return total


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings()
    if not settings.supabase_url or not settings.supabase_secret_key:
        raise SystemExit("SUPABASE_URL / SUPABASE_SECRET_KEY가 필요합니다.")

    today = datetime.now(KST).strftime("%Y%m%d")
    out_path = args.out or (
        Path(__file__).resolve().parents[2]
        / "supabase"
        / "data"
        / f"places_backup_{today}.csv"
    )
    rows = asyncio.run(dump(settings, out_path))
    print(f"{rows}행을 {out_path}에 저장했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
