"""place_tag별로 질의 보강이 이득인지 취향 축 여러 개로 실측한다.

`measure_taste_query_enrichment.py`는 `카페` 한 태그와 `place_type` 라벨만
비교한다. 그런데 `_enrich_taste_query()`는 태그를 안 가리고 39개 전부에 붙인다
— 나머지 태그가 같은 효과를 내는지 확인할 수단이 없었다. 이 스크립트가 그
공백을 메운다(`_TASTE_QUERY_EXCLUDED_TAGS` 선정 근거).

**판정은 컷 통과 수 변화로만 한다.** 인용문에 취향 표현이 들어있는지 정규식으로
세는 프록시도 써봤는데 두 번 오판했다 — `궁궐`은 3/10으로 나왔지만 원문은 4/5가
진짜 조용함이었고("고즈넉", "절제된", "단정한"을 패턴이 못 잡는다), `미술관`은
"감성적인" 축에서 3/10인데 인용문이 "작품"·"전시"를 말하고 있었다. 한국어 표현을
정규식으로 다 덮을 수 없다. 프록시는 **원문을 열어볼 대상을 고르는 데만** 쓰고,
결론은 통과 수로 낸다.

**취향 축은 실 LLM이 실제로 뽑은 `taste_query` 값이어야 한다.** 처음엔
`"저렴한"`을 넣었다가 뺐다 — 그 말은 `taste_query`가 아니라 `budget` 필드로
간다(`_shared/rules/budget.md`). 시스템이 만들지 않는 질의로 재면 결론이 무의미
하다. 축을 추가할 때는 `verify_taste_query_extraction.py`나 실 호출로 먼저
확인한다.
"""

from __future__ import annotations

import asyncio
import csv
import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.agent_context.category_rules import PLACE_TAG_TO_SMALL_CODES
from app.config import Settings
from app.providers.place_evidence import DEFAULT_MIN_SIMILARITY, PlaceEvidenceProvider
from app.providers.place_evidence_encoder import get_shared_encoder
from app.repositories.supabase_places import SupabasePlaceRepository

_RPC_TIMEOUT_SECONDS = 120.0
_PAGE_SIZE = 1000
# RPC 후보 상한(`supabase_places._MAX_EVIDENCE_CANDIDATES`)과 같은 값.
_MAX_CANDIDATES = 500
# 이보다 매칭이 적으면 통과 수 비교가 잡음에 묻힌다.
_MIN_MATCHED = 5
# 상한을 넘는 태그를 자를 때 쓰는 기준점(결과를 결정적으로 만들기 위한 것일 뿐,
# 반경 필터가 아니다 — 태그별 후보는 활성 장소 전체에서 잡는다).
_ANCHOR = (37.579617, 126.977041)  # 경복궁

_GENERIC_SUFFIX = "곳"

# 전부 실 LLM(gemini-3.5-flash-lite)이 발화에서 실제로 뽑은 taste_query 문자열이다.
#   "조용한 카페 추천해줘"                 -> "조용한"
#   "감성적인 카페 추천해줘"               -> "감성적인"
#   "빈티지하고 레트로한 카페 추천해줘"     -> "빈티지하고 레트로한 분위기"
#   "분위기 좋은 카페 추천해줘"            -> "분위기 좋은"
#   "데이트하기 좋은 카페 추천해줘"         -> "데이트하기 좋은"
#   "혼자 가기 좋은 카페 추천해줘"          -> "혼자 가기 좋은"
_TASTE_AXES: tuple[str, ...] = (
    "조용한",
    "감성적인",
    "빈티지하고 레트로한 분위기",
    "분위기 좋은",
    "데이트하기 좋은",
    "혼자 가기 좋은",
)

RESULTS_DIR = Path(__file__).resolve().parents[1] / "test_results"
RESULTS_CSV = RESULTS_DIR / "taste_tag_enrichment.csv"


@dataclass(frozen=True)
class TagAxisRow:
    tag: str
    axis: str
    candidate_count: int
    matched: int
    passed_generic: int
    passed_tag: int
    avg_generic: float
    avg_tag: float
    tag_dominance: float
    """ρ(취향+태그 순위, 태그만 순위). 높을수록 취향 단어가 순위에 기여하지 못한다."""


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
        * math.sin(d_lng / 2) ** 2
    )
    return radius * 2 * math.asin(math.sqrt(a))


async def _fetch_places(client: httpx.AsyncClient) -> list[dict[str, object]]:
    """활성 장소를 전부 읽는다(PostgREST가 한 번에 1000행까지만 준다)."""
    places: list[dict[str, object]] = []
    offset = 0
    while True:
        response = await client.get(
            "/rest/v1/places",
            params={
                "select": "content_id,latitude,longitude,lcls_systm3",
                "is_active": "eq.true",
                "limit": str(_PAGE_SIZE),
                "offset": str(offset),
            },
        )
        response.raise_for_status()
        batch = response.json()
        for row in batch:
            content_id = row.get("content_id")
            lat, lng = row.get("latitude"), row.get("longitude")
            if content_id and lat is not None and lng is not None:
                places.append(
                    {
                        "content_id": str(content_id),
                        "latitude": float(lat),
                        "longitude": float(lng),
                        "lcls_systm3": row.get("lcls_systm3"),
                    }
                )
        if len(batch) < _PAGE_SIZE:
            return places
        offset += _PAGE_SIZE


def _candidates_for_tag(
    places: Sequence[dict[str, object]], codes: Sequence[str]
) -> list[str]:
    wanted = set(codes)
    matched = [
        (
            _haversine_km(_ANCHOR[0], _ANCHOR[1], p["latitude"], p["longitude"]),
            str(p["content_id"]),
        )
        for p in places
        if p["lcls_systm3"] in wanted
    ]
    matched.sort(key=lambda entry: entry[0])
    return [content_id for _, content_id in matched[:_MAX_CANDIDATES]]


def _spearman(left: dict[str, float], right: dict[str, float]) -> float:
    """두 점수표가 만드는 순위의 상관. 공통 후보가 4곳 미만이면 NaN."""
    keys = sorted(set(left) & set(right))
    if len(keys) < 4:
        return float("nan")

    def ranks(scores: dict[str, float]) -> dict[str, int]:
        order = sorted(keys, key=lambda key: -scores[key])
        return {key: index for index, key in enumerate(order)}

    left_ranks, right_ranks = ranks(left), ranks(right)
    xs = [left_ranks[key] for key in keys]
    ys = [right_ranks[key] for key in keys]
    mean_x, mean_y = statistics.mean(xs), statistics.mean(ys)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    denominator = math.sqrt(
        sum((x - mean_x) ** 2 for x in xs) * sum((y - mean_y) ** 2 for y in ys)
    )
    return numerator / denominator if denominator else float("nan")


async def run() -> tuple[list[TagAxisRow], list[tuple[str, int]]]:
    settings = Settings()
    if not settings.supabase_url or not settings.supabase_secret_key:
        raise ValueError("SUPABASE_URL / SUPABASE_SECRET_KEY가 필요합니다.")

    encoder = get_shared_encoder()
    encoder.warmup()
    headers = {
        "apikey": settings.supabase_secret_key,
        "Authorization": f"Bearer {settings.supabase_secret_key}",
    }
    rows: list[TagAxisRow] = []
    skipped: list[tuple[str, int]] = []
    async with httpx.AsyncClient(
        base_url=settings.supabase_url.rstrip("/"),
        headers=headers,
        timeout=_RPC_TIMEOUT_SECONDS,
    ) as client:
        places = await _fetch_places(client)
        repository = SupabasePlaceRepository(
            supabase_url=settings.supabase_url,
            secret_key=settings.supabase_secret_key,
            client=client,
            timeout_seconds=settings.external_api_timeout_seconds,
        )
        # 컷 적용은 이쪽에서 판단한다 — 다른 컷을 실험할 때 RPC를 다시 안 부른다.
        provider = PlaceEvidenceProvider(encoder, repository, min_similarity=0.0)

        for tag, codes in PLACE_TAG_TO_SMALL_CODES.items():
            candidates = _candidates_for_tag(places, codes)
            if not candidates:
                skipped.append((tag, 0))
                continue
            tag_only = await provider.search(tag, candidates)
            if len(tag_only.data) < _MIN_MATCHED:
                skipped.append((tag, len(candidates)))
                continue
            tag_scores = {k: m.avg_similarity for k, m in tag_only.data.items()}

            for axis in _TASTE_AXES:
                generic = await provider.search(f"{axis} {_GENERIC_SUFFIX}", candidates)
                enriched = await provider.search(f"{axis} {tag}", candidates)
                enriched_scores = {
                    k: m.avg_similarity for k, m in enriched.data.items()
                }
                rows.append(
                    TagAxisRow(
                        tag=tag,
                        axis=axis,
                        candidate_count=len(candidates),
                        matched=len(enriched.data),
                        passed_generic=sum(
                            1
                            for m in generic.data.values()
                            if m.avg_similarity >= DEFAULT_MIN_SIMILARITY
                        ),
                        passed_tag=sum(
                            1
                            for m in enriched.data.values()
                            if m.avg_similarity >= DEFAULT_MIN_SIMILARITY
                        ),
                        avg_generic=(
                            statistics.mean(
                                m.avg_similarity for m in generic.data.values()
                            )
                            if generic.data
                            else 0.0
                        ),
                        avg_tag=(
                            statistics.mean(enriched_scores.values())
                            if enriched_scores
                            else 0.0
                        ),
                        tag_dominance=_spearman(enriched_scores, tag_scores),
                    )
                )
    return rows, skipped


def _print(rows: Sequence[TagAxisRow], skipped: Sequence[tuple[str, int]]) -> None:
    by_tag: dict[str, list[TagAxisRow]] = {}
    for row in rows:
        by_tag.setdefault(row.tag, []).append(row)

    print(f"측정 대상 {len(by_tag)}개 / 전체 {len(PLACE_TAG_TO_SMALL_CODES)}개")
    if skipped:
        print(
            f"장소가 부족해 제외 {len(skipped)}개: "
            + ", ".join(f"{tag}({count})" for tag, count in skipped)
        )
    print("\n컷 통과 수: '취향 + 곳' → '취향 + 태그'   (↓ = 붙이면 손해)\n")

    labels = [axis[:4] for axis in _TASTE_AXES]
    print(f"{'태그':<8} " + " ".join(f"{label:>10}" for label in labels) + f" {'손해':>6}")
    print("-" * (9 + 11 * len(labels) + 7))
    harmful: list[str] = []
    for tag, tag_rows in by_tag.items():
        cells, worse = [], 0
        for row in tag_rows:
            mark = (
                "↓" if row.passed_tag < row.passed_generic
                else ("=" if row.passed_tag == row.passed_generic else " ")
            )
            if row.passed_tag < row.passed_generic:
                worse += 1
            cells.append(f"{row.passed_generic:>3}→{row.passed_tag:<3}{mark}")
        if worse == len(_TASTE_AXES):
            harmful.append(tag)
        body = " ".join(f"{c:>10}" for c in cells)
        print(f"{tag:<8} {body} {worse:>4}/{len(_TASTE_AXES)}")

    print(
        f"\n**{len(_TASTE_AXES)}개 축 전부에서 손해인 태그**"
        f"(`_TASTE_QUERY_EXCLUDED_TAGS` 후보): "
        + (", ".join(harmful) if harmful else "없음")
    )
    dominated = sorted(
        {
            row.tag
            for row in rows
            if not math.isnan(row.tag_dominance) and row.tag_dominance >= 0.90
        }
    )
    print(
        "\n한 축 이상에서 태그 지배도 ρ>=0.90 (취향 단어가 순위에 기여 못 함): "
        + (", ".join(dominated) if dominated else "없음")
        + "\n  → 후보가 적으면 ρ이 불안정하다. 바로 제외하지 말고 원문을 확인한다."
    )


def main() -> None:
    rows, skipped = asyncio.run(run())
    _print(rows, skipped)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with RESULTS_CSV.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.writer(fp)
        writer.writerow(
            [
                "tag",
                "taste_axis",
                "candidate_count",
                "matched",
                "passed_generic",
                "passed_tag",
                "avg_generic",
                "avg_tag",
                "tag_dominance",
            ]
        )
        for r in rows:
            writer.writerow(
                [
                    r.tag,
                    r.axis,
                    r.candidate_count,
                    r.matched,
                    r.passed_generic,
                    r.passed_tag,
                    f"{r.avg_generic:.4f}",
                    f"{r.avg_tag:.4f}",
                    "" if math.isnan(r.tag_dominance) else f"{r.tag_dominance:.4f}",
                ]
            )
    print(f"\n결과 저장: {RESULTS_CSV}")


if __name__ == "__main__":
    main()
