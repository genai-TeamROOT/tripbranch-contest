"""실제 추천 후보 범위에서 avg_similarity 분포를 재서 taste Feature 정규화를 정한다.

기존 measure_similarity_distribution.py와 무엇이 다른가: 그 스크립트는 활성
장소 844곳에서 무작위 500곳을 뽑아 컷값(0.43)을 정하려고 만들었다. 반면 실제
추천 흐름에서 채점 대상이 되는 건 **하드 필터를 통과한 근처 30~150곳**이다.
후보 범위가 좁아지면 같은 발화라도 유사도 분포가 달라질 수 있어, 점수 정규화를
그 좁은 범위 기준으로 정해야 한다.

방법: 검색 중심에서 가까운 순으로 N곳을 후보로 잡아(하드 필터의 거리 조건을
근사) search_place_evidence를 p_min_similarity=0.0으로 부르고, 장소별 top-3
평균 유사도(avg_similarity — 점수 Feature의 실제 입력값)의 분포를 본다.

sentence-transformers 필요: `pip install -e ".[embeddings]"`.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx
from sentence_transformers import SentenceTransformer

from app.config import Settings

_MODEL_NAME = "jhgan/ko-sroberta-multitask"
_RPC_TIMEOUT_SECONDS = 60.0
_MATCH_COUNT = 3
_MIN_SIMILARITY_CUT = 0.43

RESULTS_DIR = Path(__file__).resolve().parents[1] / "test_results"
RESULTS_CSV = RESULTS_DIR / "taste_score_distribution.csv"

# 경복궁 — 도보 실측(2026-08-19)에서 쓴 것과 같은 중심점이라 비교하기 좋다.
_DEFAULT_CENTER = (37.579617, 126.977041)

# 하드 필터 통과 후보 규모. RPC 지연 실측(2026-08-18)에서 30~150곳이 워밍업 후
# 70~120ms로 실서비스 범위였다.
_DEFAULT_CANDIDATE_COUNTS = (30, 80, 150)

_QUERIES = (
    "혼자 조용히 쉬고 싶어",
    "부모님 모시고 가기 좋은 곳",
    "아이랑 비 오는 날 실내",
    "야경 데이트",
    "친구랑 사진 찍기 이색적인 곳",
    "힐링되는 아늑한 곳",
    "역사적인 분위기",
    "시장 먹거리 구경",
)


@dataclass(frozen=True)
class Distribution:
    query: str
    candidate_count: int
    matched: int
    passed_cut: int
    top1: float
    p75: float
    median: float
    lowest: float


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


async def _fetch_places(client: httpx.AsyncClient) -> list[tuple[str, float, float]]:
    response = await client.get(
        "/rest/v1/places",
        params={
            "select": "content_id,latitude,longitude",
            "is_active": "eq.true",
            "limit": "1000",
        },
    )
    response.raise_for_status()
    places: list[tuple[str, float, float]] = []
    for row in response.json():
        content_id, lat, lng = (
            row.get("content_id"),
            row.get("latitude"),
            row.get("longitude"),
        )
        if content_id and lat is not None and lng is not None:
            places.append((str(content_id), float(lat), float(lng)))
    return places


def _nearest(
    places: Sequence[tuple[str, float, float]],
    center: tuple[float, float],
    count: int,
) -> list[str]:
    ranked = sorted(
        places, key=lambda p: _haversine_km(center[0], center[1], p[1], p[2])
    )
    return [content_id for content_id, _, _ in ranked[:count]]


async def _search(
    client: httpx.AsyncClient, embedding: list[float], candidates: list[str]
) -> list[float]:
    response = await client.post(
        "/rest/v1/rpc/search_place_evidence",
        json={
            "p_query_embedding": embedding,
            "p_candidate_content_ids": candidates,
            "p_match_count": _MATCH_COUNT,
            "p_min_similarity": 0.0,
        },
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("RPC 응답이 목록이 아닙니다.")
    return sorted(
        (float(row["avg_similarity"]) for row in payload), reverse=True
    )


def _summarize(query: str, count: int, scores: list[float]) -> Distribution:
    if not scores:
        return Distribution(query, count, 0, 0, 0.0, 0.0, 0.0, 0.0)
    return Distribution(
        query=query,
        candidate_count=count,
        matched=len(scores),
        passed_cut=sum(1 for s in scores if s >= _MIN_SIMILARITY_CUT),
        top1=scores[0],
        p75=scores[max(0, len(scores) // 4 - 1)],
        median=statistics.median(scores),
        lowest=scores[-1],
    )


async def run(
    center: tuple[float, float],
    counts: Sequence[int],
    queries: Sequence[str] = _QUERIES,
) -> list[Distribution]:
    settings = Settings()
    if not settings.supabase_url or not settings.supabase_secret_key:
        raise ValueError("SUPABASE_URL과 SUPABASE_SECRET_KEY가 필요합니다.")

    model = SentenceTransformer(_MODEL_NAME)
    embeddings = model.encode(list(queries), normalize_embeddings=True).tolist()

    headers = {
        "apikey": settings.supabase_secret_key,
        "Authorization": f"Bearer {settings.supabase_secret_key}",
    }
    results: list[Distribution] = []
    async with httpx.AsyncClient(
        base_url=settings.supabase_url.rstrip("/"),
        headers=headers,
        timeout=_RPC_TIMEOUT_SECONDS,
    ) as client:
        places = await _fetch_places(client)
        print(f"활성 장소 {len(places)}곳 중 중심에서 가까운 순으로 후보를 잡는다.\n")
        for count in counts:
            candidates = _nearest(places, center, count)
            for query, embedding in zip(queries, embeddings, strict=True):
                scores = await _search(client, embedding, candidates)
                results.append(_summarize(query, count, scores))
    return results


def _print(results: Sequence[Distribution]) -> None:
    header = (
        f"{'후보':>5} {'질의':<24} {'매칭':>5} {'컷통과':>6} "
        f"{'최고':>7} {'상위25%':>8} {'중앙':>7} {'최저':>7}"
    )
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r.candidate_count:>5} {r.query:<24} {r.matched:>5} {r.passed_cut:>6} "
            f"{r.top1:>7.3f} {r.p75:>8.3f} {r.median:>7.3f} {r.lowest:>7.3f}"
        )
    tops = [r.top1 for r in results if r.matched]
    if tops:
        print(
            f"\n최고 유사도: 평균 {statistics.mean(tops):.3f} / "
            f"최대 {max(tops):.3f} / 최소 {min(tops):.3f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lat", type=float, default=_DEFAULT_CENTER[0])
    parser.add_argument("--lng", type=float, default=_DEFAULT_CENTER[1])
    parser.add_argument(
        "--counts", type=int, nargs="+", default=list(_DEFAULT_CANDIDATE_COUNTS)
    )
    parser.add_argument(
        "--queries",
        nargs="+",
        default=None,
        help="측정할 발화 목록(기본값: 취향 개념 축을 덮는 8개 발화)",
    )
    args = parser.parse_args()

    results = asyncio.run(
        run((args.lat, args.lng), args.counts, tuple(args.queries or _QUERIES))
    )
    _print(results)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with RESULTS_CSV.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.writer(fp)
        writer.writerow(
            ["candidate_count", "query", "matched", "passed_cut",
             "top1", "p75", "median", "lowest"]
        )
        for r in results:
            writer.writerow(
                [r.candidate_count, r.query, r.matched, r.passed_cut,
                 f"{r.top1:.4f}", f"{r.p75:.4f}", f"{r.median:.4f}", f"{r.lowest:.4f}"]
            )
    print(f"\n결과 저장: {RESULTS_CSV}")


if __name__ == "__main__":
    main()
