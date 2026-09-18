"""실제 장소 한 곳의 합성 리뷰를 생성해 표준 출력으로만 확인한다.

리뷰 수는 그 장소가 가진 공식 근거 수를 따라 3~5로 달라진다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict

import httpx

from app.config import Settings
from app.repositories.supabase_places import SupabasePlaceRepository
from app.synthetic_reviews import (
    PERSONA_COUNT_CEILING,
    GeminiSyntheticReviewGenerator,
    PlacePersonaInput,
    assess_sentiment,
    build_official_facts,
    generate_personas,
    generate_review_plans,
)
from scripts.inspect_synthetic_review_plans import INSPECTION_COLUMNS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="장소 한 곳의 합성 리뷰를 생성하며 DB에는 저장하지 않음"
    )
    parser.add_argument("--place-id", required=True, help="TourAPI content_id")
    parser.add_argument(
        "--model", help="생성 모델(생략 시 비용 절감용 LLM_FAST_MODEL_NAME)"
    )
    parser.add_argument(
        "--max-personas",
        type=int,
        default=PERSONA_COUNT_CEILING,
        choices=range(3, 6),
        help="복합 페르소나 수 상한 (기본 5). 실제 수는 그 장소의 공식 근거 수를 따른다",
    )
    return parser


def _place_input(facts: Mapping[str, str]) -> PlacePersonaInput:
    return PlacePersonaInput(
        **{
            field: facts.get(field)
            for field in PlacePersonaInput.__dataclass_fields__
        }
    )


async def run(args: argparse.Namespace, settings: Settings) -> dict[str, object]:
    if not settings.supabase_url.strip() or not settings.supabase_secret_key.strip():
        raise ValueError("SUPABASE_URL / SUPABASE_SECRET_KEY가 필요합니다.")
    if not settings.llm_api_key.strip():
        raise ValueError("LLM_API_KEY가 필요합니다.")
    async with httpx.AsyncClient() as client:
        repository = SupabasePlaceRepository(
            settings.supabase_url,
            settings.supabase_secret_key,
            client,
            timeout_seconds=max(settings.external_api_timeout_seconds, 30.0),
        )
        rows = await repository.list_active_place_rows_by_ids(
            [args.place_id], INSPECTION_COLUMNS
        )
    if not rows:
        raise ValueError(f"활성 places에서 찾지 못한 content_id: {args.place_id}")

    facts = build_official_facts(rows[0])
    place = _place_input(facts)
    personas = generate_personas(place, max_count=args.max_personas)
    plans = generate_review_plans(personas)
    sentiments = tuple(assess_sentiment(place, plan) for plan in plans)
    model_name = args.model or settings.llm_fast_model_name
    generator = GeminiSyntheticReviewGenerator(
        api_key=settings.llm_api_key,
        model_name=model_name,
    )
    batch = await generator.generate(
        facts=facts, plans=plans, sentiments=sentiments
    )
    return {
        "contentId": facts["content_id"],
        "title": facts["title"],
        "model": model_name,
        "usageMetadata": generator.usage_metadata,
        "personas": [asdict(persona) for persona in personas],
        "reviews": [
            review.model_dump(mode="json", by_alias=True) for review in batch.reviews
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = asyncio.run(run(args, Settings()))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
