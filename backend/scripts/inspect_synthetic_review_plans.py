"""Supabase 장소로 합성 리뷰 페르소나·계획·sentiment를 읽기 전용 검증한다."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import Protocol

import httpx

from app.config import Settings
from app.repositories.supabase_places import SupabasePlaceRepository
from app.synthetic_reviews import (
    PERSONA_COUNT_CEILING,
    PlacePersonaInput,
    assess_sentiment,
    generate_personas,
    generate_review_plans,
)

INSPECTION_COLUMNS = (
    "content_id",
    "content_type_id",
    "title",
    "address",
    "lcls_systm1",
    "lcls_systm2",
    "lcls_systm3",
    "operating_hours_raw",
    "rest_date_raw",
    "parking_info_raw",
    "parking_fee_raw",
    "use_fee_raw",
    "discount_info_raw",
    "info_center_raw",
    "baby_carriage_raw",
    "pet_raw",
    "credit_card_raw",
    "restroom_raw",
)
_INPUT_FIELDS = tuple(PlacePersonaInput.__dataclass_fields__)


class _PlaceReader(Protocol):
    async def list_region_place_rows(
        self,
        area_code: str,
        district_code: str,
        columns: Sequence[str],
        *,
        active_only: bool = True,
    ) -> list[Mapping[str, object]]: ...

    async def list_active_place_rows_by_ids(
        self, content_ids: Sequence[str], columns: Sequence[str]
    ) -> list[Mapping[str, object]]: ...


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="DB 장소의 합성 리뷰 계획을 생성하되 LLM 호출이나 DB 쓰기는 하지 않음"
    )
    parser.add_argument(
        "--place-id", action="append", default=[], help="검증할 content_id (반복 가능)"
    )
    parser.add_argument("--limit", type=int, default=5, help="장소 수 상한 (기본 5)")
    parser.add_argument("--area-code", help="TourAPI 광역 코드")
    parser.add_argument("--district-code", help="TourAPI 시·군·구 코드")
    parser.add_argument(
        "--max-personas",
        type=int,
        default=PERSONA_COUNT_CEILING,
        choices=range(3, 6),
        help="장소당 페르소나 수 상한 (기본 5). 실제 수는 그 장소의 공식 근거 수를 따른다",
    )
    parser.add_argument(
        "--reviews-per-place",
        type=int,
        help="장소당 리뷰 계획 수를 강제한다. 생략하면 페르소나 수를 따른다",
    )
    return parser


def _text(row: Mapping[str, object], field: str) -> str | None:
    value = row.get(field)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def build_place_report(
    row: Mapping[str, object], *, max_personas: int, review_count: int | None
) -> dict[str, object]:
    values = {field: _text(row, field) for field in _INPUT_FIELDS}
    place = PlacePersonaInput(
        content_id=values.pop("content_id") or "",
        content_type_id=values.pop("content_type_id") or "",
        **values,
    )
    personas = generate_personas(place, max_count=max_personas)
    plans = generate_review_plans(personas, review_count=review_count)
    reviews = []
    for plan in plans:
        reviews.append(
            {
                "plan": asdict(plan),
                "sentiment": asdict(assess_sentiment(place, plan)),
            }
        )
    return {
        "contentId": place.content_id,
        "title": _text(row, "title"),
        "personas": [asdict(persona) for persona in personas],
        "reviews": reviews,
    }


async def inspect(
    args: argparse.Namespace, settings: Settings, repository: _PlaceReader
) -> list[dict[str, object]]:
    if args.limit < 1:
        raise ValueError("--limit은 1 이상이어야 합니다.")
    if args.place_id:
        requested_ids = list(dict.fromkeys(args.place_id))[: args.limit]
        rows = await repository.list_active_place_rows_by_ids(
            requested_ids, INSPECTION_COLUMNS
        )
        found_ids = {str(row.get("content_id")) for row in rows}
        missing = [content_id for content_id in requested_ids if content_id not in found_ids]
        if missing:
            raise ValueError("활성 places에서 찾지 못한 content_id: " + ", ".join(missing))
    else:
        rows = await repository.list_region_place_rows(
            args.area_code or settings.place_sync_area_code,
            args.district_code or settings.place_sync_district_code,
            INSPECTION_COLUMNS,
        )
        rows = rows[: args.limit]
    return [
        build_place_report(
            row, max_personas=args.max_personas, review_count=args.reviews_per_place
        )
        for row in rows
    ]


async def run(args: argparse.Namespace, settings: Settings) -> list[dict[str, object]]:
    if not settings.supabase_url.strip() or not settings.supabase_secret_key.strip():
        raise ValueError("SUPABASE_URL / SUPABASE_SECRET_KEY가 필요합니다.")
    async with httpx.AsyncClient() as client:
        repository = SupabasePlaceRepository(
            settings.supabase_url,
            settings.supabase_secret_key,
            client,
            timeout_seconds=max(settings.external_api_timeout_seconds, 30.0),
        )
        return await inspect(args, settings, repository)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    reports = asyncio.run(run(args, Settings()))
    print(json.dumps(reports, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
