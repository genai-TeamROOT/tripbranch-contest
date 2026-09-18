"""적재된 place_embeddings로 대표 발화의 유사도 분포를 실측해 min_similarity 컷을 잡는다.

방법(package_D 계획 문서 §5-3과 동일): 대표 발화 여러 건을 실제로 임베딩해
search_place_evidence RPC(p_min_similarity=0.0, 필터 없음)를 돌리고, 장소별
top-3 평균 유사도의 분포(1위·중앙값·간격)를 본다. 컷은 중앙값 바로 위로 잡아
강한 발화는 넉넉히, 희소한 개념은 상위 소수만 통과시킨다.

sentence-transformers는 torch를 끌고 와 무거워 기본/dev 설치에 없다. 실행 전
`pip install -e ".[embeddings]"`가 필요하다. 모델(jhgan/ko-sroberta-multitask)은
첫 실행 시 Hugging Face Hub에서 자동 다운로드된다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

import httpx
from sentence_transformers import SentenceTransformer

from app.config import Settings

_MODEL_NAME = "jhgan/ko-sroberta-multitask"
_RPC_TIMEOUT_SECONDS = 60.0
_MATCH_COUNT = 3
# search_place_evidence의 후보 상한(202608180004)과 같은 값. 활성 장소가 이보다
# 많으면 고정 시드로 무작위 추출해 재현 가능한 부분집합으로 측정한다 — 분포의
# 근사치로는 충분하고, 상한을 넘겨 호출하면 함수가 즉시 에러를 낸다.
_MAX_CANDIDATES = 500
_SAMPLE_SEED = 42

# package_D §2.8 취향 개념 축(동반자·분위기·A가 제기한 역사/문화/시장/디저트 신규 축)을
# 골고루 덮는 자연 발화 11개. §7.12 검색 테스트와 같은 표현을 최대한 재사용해
# 결과를 비교할 수 있게 한다.
_QUERIES = [
    "혼자 조용히 산책하기 좋은 곳",
    "부모님 모시고 가기 좋은 곳",
    "아이랑 비 오는 날 갈 만한 실내 장소",
    "야경 보면서 데이트하기 좋은 곳",
    "친구랑 사진 찍기 좋은 이색적인 곳",
    "지친 마음 힐링할 수 있는 아늑한 곳",
    "역사적인 분위기를 느낄 수 있는 곳",
    "공연이나 문화생활 즐기기 좋은 곳",
    "시장 구경하면서 먹거리 즐기고 싶어",
    "디저트 먹으면서 동네 구경하기 좋은 데",
    "자연 속에서 여유롭게 쉬고 싶어",
]


@dataclass(frozen=True)
class QueryDistribution:
    query: str
    place_count: int
    top1_place: str
    top1_similarity: float
    median_similarity: float


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="place_embeddings 유사도 분포 실측")
    parser.add_argument(
        "--queries",
        nargs="+",
        default=None,
        help="측정할 발화 목록(기본값: 취향 개념 축을 덮는 11개 발화)",
    )
    return parser


async def _fetch_active_content_ids(client: httpx.AsyncClient) -> list[str]:
    response = await client.get(
        "/rest/v1/places",
        params={"select": "content_id", "is_active": "eq.true", "limit": "1000"},
    )
    response.raise_for_status()
    return [str(row["content_id"]) for row in response.json() if row.get("content_id")]


async def _search(
    client: httpx.AsyncClient,
    query_embedding: list[float],
    candidate_content_ids: list[str],
) -> list[dict[str, object]]:
    response = await client.post(
        "/rest/v1/rpc/search_place_evidence",
        json={
            "p_query_embedding": query_embedding,
            "p_candidate_content_ids": candidate_content_ids,
            "p_match_count": _MATCH_COUNT,
            "p_min_similarity": 0.0,
        },
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("RPC 응답이 목록이 아닙니다.")
    return payload


async def run(args: argparse.Namespace, settings: Settings) -> list[QueryDistribution]:
    if not settings.supabase_url:
        raise ValueError("SUPABASE_URL이 필요합니다.")
    if not settings.supabase_secret_key:
        raise ValueError("SUPABASE_SECRET_KEY가 필요합니다.")

    queries = args.queries or _QUERIES
    model = SentenceTransformer(_MODEL_NAME)
    embeddings = model.encode(queries, normalize_embeddings=True).tolist()

    headers = {
        "apikey": settings.supabase_secret_key,
        "Authorization": f"Bearer {settings.supabase_secret_key}",
    }
    results: list[QueryDistribution] = []
    async with httpx.AsyncClient(
        base_url=settings.supabase_url.rstrip("/"),
        headers=headers,
        timeout=_RPC_TIMEOUT_SECONDS,
    ) as client:
        candidate_content_ids = await _fetch_active_content_ids(client)
        if len(candidate_content_ids) > _MAX_CANDIDATES:
            candidate_content_ids = random.Random(_SAMPLE_SEED).sample(
                candidate_content_ids, k=_MAX_CANDIDATES
            )
        for query, embedding in zip(queries, embeddings, strict=True):
            rows = await _search(client, embedding, candidate_content_ids)
            similarities = [float(row["avg_similarity"]) for row in rows]
            if not similarities:
                results.append(QueryDistribution(query, 0, "(결과 없음)", 0.0, 0.0))
                continue
            top = rows[0]
            results.append(
                QueryDistribution(
                    query=query,
                    place_count=len(rows),
                    top1_place=str(top["place_title"]),
                    top1_similarity=float(top["avg_similarity"]),
                    median_similarity=statistics.median(similarities),
                )
            )
    return results


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    results = asyncio.run(run(args, Settings()))

    print(f"{'질의':<28} {'1위 장소':<20} {'1위 유사도':>10} {'중앙값':>8} {'장소수':>6}")
    for item in results:
        print(
            f"{item.query:<28} {item.top1_place:<20} "
            f"{item.top1_similarity:>10.3f} {item.median_similarity:>8.3f} "
            f"{item.place_count:>6}"
        )

    top1_values = [item.top1_similarity for item in results if item.place_count]
    median_values = [item.median_similarity for item in results if item.place_count]
    if top1_values and median_values:
        overall_median = statistics.median(median_values)
        print()
        print(
            json.dumps(
                {
                    "top1_mean": round(statistics.mean(top1_values), 3),
                    "top1_min": round(min(top1_values), 3),
                    "median_of_medians": round(overall_median, 3),
                    "suggested_min_similarity": round(overall_median + 0.02, 3),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
