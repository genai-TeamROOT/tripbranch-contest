"""place_associations에서 content_id 기준 연관 장소를 조회하는 헬퍼.

역할: import_place_associations.py로 적재한 데이터가 실제로 rank/category와
함께 조회되는지 확인하는 용도다. SCHEDULE/RECOMMEND 파이프라인에 실제로
연결하는 배선 작업은 이 스크립트의 범위가 아니다(별도 작업) — 지금은 조회
함수와 수동 확인용 CLI만 둔다.

호출 시점: `python -m scripts.query_place_associations <content_id>`로 수동 실행한다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass

import httpx

from app.config import Settings

_DEFAULT_LIMIT = 50


@dataclass(frozen=True)
class AssociatedPlace:
    content_id: str
    category: str
    rank: int
    base_ym: str


async def get_associated_places(
    settings: Settings,
    content_id: str,
    *,
    client: httpx.AsyncClient | None = None,
    category: str | None = None,
    limit: int = _DEFAULT_LIMIT,
) -> list[AssociatedPlace]:
    """content_id를 from_content_id로 하는 연관 장소를 rank 오름차순으로 반환한다."""

    async def _query(active_client: httpx.AsyncClient) -> list[dict[str, object]]:
        params: dict[str, str] = {
            "select": "to_content_id,category,rank,base_ym",
            "from_content_id": f"eq.{content_id}",
            "order": "rank.asc",
            "limit": str(limit),
        }
        if category is not None:
            params["category"] = f"eq.{category}"
        response = await active_client.get(
            settings.supabase_url.rstrip("/") + "/rest/v1/place_associations",
            params=params,
            headers={"apikey": settings.supabase_secret_key},
            timeout=settings.external_api_timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    if client is not None:
        rows = await _query(client)
    else:
        async with httpx.AsyncClient() as owned_client:
            rows = await _query(owned_client)

    return [
        AssociatedPlace(
            content_id=str(row["to_content_id"]),
            category=str(row["category"]),
            rank=int(row["rank"]),
            base_ym=str(row["base_ym"]),
        )
        for row in rows
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="place_associations 조회")
    parser.add_argument("content_id", help="기준 장소 content_id")
    parser.add_argument("--category", help="전체/관광지/음식/숙박 중 하나로 필터링")
    parser.add_argument("--limit", type=int, default=_DEFAULT_LIMIT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings()
    if not settings.supabase_url or not settings.supabase_secret_key:
        raise ValueError("SUPABASE_URL / SUPABASE_SECRET_KEY가 필요합니다.")

    places = asyncio.run(
        get_associated_places(
            settings, args.content_id, category=args.category, limit=args.limit
        )
    )
    print(json.dumps([p.__dict__ for p in places], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
