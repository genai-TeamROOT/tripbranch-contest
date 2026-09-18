"""종로구 장소 표본을 뽑아 Colab 생성용 입력 파일을 만든다.

LLM을 호출하지 않고 DB에 쓰지도 않는다. 저장소 코드도 고치지 않는다 —
`app.synthetic_reviews`의 공개 함수만 그대로 불러 쓴다.

만들어지는 `places_sample.json`은 Colab이 모델을 부르는 데 필요한 모든 것을 담는다.
Colab 쪽은 이 파일 하나만 있으면 되고, Supabase 자격증명은 전혀 알 필요가 없다.

실행:
    cd /Users/jinhyoungkim/Dev/TripBranch-synthetic-reviews/backend
    python <이 파일 경로> --out <출력 경로>/places_sample.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.repositories.supabase_places import SupabasePlaceRepository
from app.synthetic_reviews import (
    PlacePersonaInput,
    assess_sentiment,
    build_official_facts,
    generate_personas,
    generate_review_plans,
)
from app.synthetic_reviews.review_generator import (
    _SYSTEM_INSTRUCTION,
    PROMPT_VERSION,
    _prompt_payload,
    wire_schema_for,
)
from scripts.inspect_synthetic_review_plans import INSPECTION_COLUMNS

JONGNO_AREA_CODE = "11"
JONGNO_DISTRICT_CODE = "110"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="종로구 장소 표본으로 Colab 생성 입력 파일을 만든다 (LLM 호출 없음)"
    )
    parser.add_argument("--out", required=True, help="출력할 places_sample.json 경로")
    parser.add_argument("--count", type=int, default=10, help="표본 장소 수 (기본 10)")
    parser.add_argument(
        "--place-id",
        action="append",
        default=[],
        help="표본을 자동으로 고르지 않고 이 content_id만 쓴다 (반복 가능). "
        "프롬프트 버전 간 비교에는 같은 장소를 써야 한다",
    )
    parser.add_argument(
        "--area-code", default=JONGNO_AREA_CODE, help="TourAPI 광역 코드 (기본 11 서울)"
    )
    parser.add_argument(
        "--district-code",
        default=JONGNO_DISTRICT_CODE,
        help="TourAPI 시·군·구 코드 (기본 110 종로구)",
    )
    return parser


def _text(row: Mapping[str, object], field: str) -> str | None:
    value = row.get(field)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _place_input(row: Mapping[str, object]) -> PlacePersonaInput:
    values = {
        field: _text(row, field) for field in PlacePersonaInput.__dataclass_fields__
    }
    return PlacePersonaInput(
        content_id=values.pop("content_id") or "",
        content_type_id=values.pop("content_type_id") or "",
        **values,
    )


def _evidence_richness(row: Mapping[str, object]) -> int:
    """이 장소로 만들 수 있는 공식 근거 필드가 몇 개인가.

    페르소나는 원문 필드가 비어 있지 않은지로 만들어진다. 이 값이 크면 TOUR_API claim이
    많이 나오는 경로를, 0에 가까우면 SYNTHETIC_SCENARIO만 나오는 경로를 타므로 두 쪽을
    모두 표본에 넣어야 한다.
    """
    try:
        personas = generate_personas(_place_input(row))
    except ValueError:
        return -1
    return sum(len(persona.evidence_fields) for persona in personas)


def select_sample(
    rows: Sequence[Mapping[str, object]], *, count: int
) -> list[Mapping[str, object]]:
    """장소 유형을 흩뜨리고, 유형마다 근거가 풍부한 곳과 빈약한 곳을 함께 고른다."""
    by_type: dict[str, list[tuple[int, Mapping[str, object]]]] = {}
    for row in rows:
        content_type_id = _text(row, "content_type_id")
        if not content_type_id or not _text(row, "title"):
            continue
        richness = _evidence_richness(row)
        if richness < 0:
            continue
        by_type.setdefault(content_type_id, []).append((richness, row))

    for entries in by_type.values():
        # content_id를 2차 기준으로 둬 실행할 때마다 같은 표본이 나오게 한다.
        entries.sort(key=lambda item: (-item[0], str(item[1]["content_id"])))

    # 유형을 번갈아 돌면서 각 유형의 가장 풍부한 곳과 가장 빈약한 곳을 번갈아 집는다.
    selected: list[Mapping[str, object]] = []
    seen: set[str] = set()
    take_richest = True
    while len(selected) < count:
        progressed = False
        for content_type_id in sorted(by_type):
            entries = by_type[content_type_id]
            if not entries:
                continue
            richness, row = entries.pop(0 if take_richest else -1)
            content_id = str(row["content_id"])
            if content_id in seen:
                continue
            seen.add(content_id)
            selected.append(row)
            progressed = True
            if len(selected) >= count:
                break
        if not progressed:
            break
        take_richest = not take_richest
    return selected


def _dereference(schema: Mapping[str, Any]) -> dict[str, Any]:
    """$defs/$ref를 펼쳐 평평한 JSON Schema로 만든다.

    vLLM의 제약 디코딩 백엔드가 $ref를 다루긴 하지만 버전에 따라 까다롭다. 스키마가
    작으므로 미리 펼쳐 두면 실패 경로가 하나 줄어든다.
    """
    definitions = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, Mapping):
            if "$ref" in node:
                name = str(node["$ref"]).rsplit("/", 1)[-1]
                return walk(definitions[name])
            return {key: walk(value) for key, value in node.items() if key != "$defs"}
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    return walk(schema)


def build_entry(row: Mapping[str, object]) -> dict[str, Any]:
    facts = build_official_facts(row)
    place = _place_input(row)
    personas = generate_personas(place)
    plans = generate_review_plans(personas)
    sentiments = tuple(assess_sentiment(place, plan) for plan in plans)
    return {
        "contentId": facts["content_id"],
        "contentTypeId": facts["content_type_id"],
        "title": facts["title"],
        "reviewCount": len(plans),
        "evidenceRichness": sum(len(p.evidence_fields) for p in personas),
        # 리뷰 수가 장소마다 달라 응답 스키마도 장소마다 다르다.
        "responseJsonSchema": _dereference(
            wire_schema_for(len(plans)).model_json_schema()
        ),
        # facts만 있으면 채점 쪽에서 페르소나·계획·sentiment를 결정적으로 다시 만든다.
        "facts": facts,
        # 실제 생성기가 보내는 것과 글자 단위로 같은 사용자 메시지.
        "promptPayload": _prompt_payload(facts, plans, sentiments),
    }


async def load_rows(
    settings: Settings, area_code: str, district_code: str
) -> list[Mapping[str, object]]:
    if not settings.supabase_url.strip() or not settings.supabase_secret_key.strip():
        raise ValueError("SUPABASE_URL / SUPABASE_SECRET_KEY가 필요합니다.")
    async with httpx.AsyncClient() as client:
        repository = SupabasePlaceRepository(
            settings.supabase_url,
            settings.supabase_secret_key,
            client,
            timeout_seconds=max(settings.external_api_timeout_seconds, 30.0),
        )
        return await repository.list_region_place_rows(
            area_code, district_code, INSPECTION_COLUMNS
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rows = asyncio.run(load_rows(Settings(), args.area_code, args.district_code))
    if args.place_id:
        by_id = {str(row["content_id"]): row for row in rows}
        missing = [cid for cid in args.place_id if cid not in by_id]
        if missing:
            raise ValueError(f"활성 목록에 없는 content_id: {', '.join(missing)}")
        sample = [by_id[cid] for cid in args.place_id]
        print(f"지정된 {len(sample)}곳을 씁니다.")
    else:
        print(
            f"{args.district_code}구 활성 장소 {len(rows)}건에서 {args.count}곳을 고릅니다."
        )
        sample = select_sample(rows, count=args.count)
    entries = [build_entry(row) for row in sample]

    document = {
        "promptVersion": PROMPT_VERSION,
        "areaCode": args.area_code,
        "districtCode": args.district_code,
        "systemInstruction": _SYSTEM_INSTRUCTION,
        "places": entries,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n{out_path} 에 {len(entries)}곳을 저장했습니다.\n")
    print(f"{'contentId':<10} {'유형':<5} {'리뷰':<5} {'근거':<5} 제목")
    print("-" * 62)
    for entry in entries:
        print(
            f"{entry['contentId']:<10} {entry['contentTypeId']:<5} "
            f"{entry['reviewCount']:<5} {entry['evidenceRichness']:<5} {entry['title']}"
        )
    payload_chars = sum(len(e["promptPayload"]) for e in entries)
    print(
        f"\n시스템 지시문 {len(_SYSTEM_INSTRUCTION):,}자, "
        f"payload 합계 {payload_chars:,}자 (장소당 평균 {payload_chars // len(entries):,}자)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
