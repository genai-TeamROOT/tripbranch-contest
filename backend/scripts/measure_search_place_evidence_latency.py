"""search_place_evidence RPC 소요시간 측정 — 후보 content_id 개수별 비교.

역할: 질의 임베딩 생성(로컬 모델)과 RPC 왕복(네트워크+DB 집계)을 나눠서 잰다.
후보 개수에 따라 DB 쪽 소요시간이 어떻게 변하는지 세 구간(소/중/대)으로 비교한다.
- large(500곳)는 search_place_evidence의 후보 상한(202608180004)에 걸리는
  최댓값이다. 예전에는 활성 전체(844곳, 후보를 하나도 안 좁힌 최악의 경우)를
  썼는데, 이제 그 경로는 함수가 아예 거부해 측정이 불가능하다 — 대신 상한
  바로 안쪽에서 지연시간이 여전히 실용적인지 확인한다.
- 500건을 넘기면 함수가 즉시 에러를 내는지도 별도로 확인한다(가드 동작 검증).
- 후보 목록은 활성 places에서 고정 시드로 무작위 추출해 회차마다 같은 후보로
  재도록 한다.
입력: 없음(고정된 질의 3개 × 후보 규모 3단계 × 3회 반복 + 상한 초과 가드 확인 1회).
출력: 표준 출력 + backend/test_results/search_place_evidence_latency.csv
호출 시점: `python -m scripts.measure_search_place_evidence_latency`로 수동 실행
(.env에 SUPABASE_URL/SUPABASE_SECRET_KEY 필요, sentence-transformers 설치 필요 —
`pip install -e ".[embeddings]"`).
"""

from __future__ import annotations

import asyncio
import csv
import random
import statistics
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import httpx
from sentence_transformers import SentenceTransformer

from app.config import Settings

_MODEL_NAME = "jhgan/ko-sroberta-multitask"
_RPC_TIMEOUT_SECONDS = 60.0
_MATCH_COUNT = 3
_MIN_SIMILARITY = 0.43  # 2026-08-18 실측 확정값(package_D 계획 문서 §5-3 방식)
_ROUNDS = 3
_RANDOM_SEED = 42

_CANDIDATE_SIZES = {"소(30)": 30, "중(150)": 150, "대(500, 상한)": 500}
_OVER_LIMIT_SIZE = 600  # 상한(500) 초과 시 즉시 거부되는지 확인용

_QUERIES = [
    "혼자 조용히 산책하기 좋은 곳",
    "야경 보면서 데이트하기 좋은 곳",
    "시장 구경하면서 먹거리 즐기고 싶어",
]

RESULTS_DIR = Path(__file__).resolve().parent.parent / "test_results"
RESULTS_CSV = RESULTS_DIR / "search_place_evidence_latency.csv"


@dataclass
class RoundResult:
    size_label: str
    candidate_count: int
    query: str
    round_number: int
    rpc_ms: float
    result_count: int


async def _fetch_active_content_ids(client: httpx.AsyncClient) -> list[str]:
    response = await client.get(
        "/rest/v1/places",
        params={"select": "content_id", "is_active": "eq.true", "limit": "1000"},
    )
    response.raise_for_status()
    return [str(row["content_id"]) for row in response.json() if row.get("content_id")]


async def _measure_rpc(
    client: httpx.AsyncClient,
    query_embedding: list[float],
    candidate_content_ids: list[str],
) -> tuple[float, int]:
    started = perf_counter()
    response = await client.post(
        "/rest/v1/rpc/search_place_evidence",
        json={
            "p_query_embedding": query_embedding,
            "p_candidate_content_ids": candidate_content_ids,
            "p_match_count": _MATCH_COUNT,
            "p_min_similarity": _MIN_SIMILARITY,
        },
    )
    elapsed_ms = (perf_counter() - started) * 1000
    response.raise_for_status()
    payload = response.json()
    return elapsed_ms, len(payload) if isinstance(payload, list) else 0


def _summarize(results: list[RoundResult]) -> dict[str, float]:
    values = [item.rpc_ms for item in results]
    return {
        "평균": statistics.mean(values),
        "중앙값": statistics.median(values),
        "최소": min(values),
        "최대": max(values),
    }


async def main() -> None:
    settings = Settings()
    if not settings.supabase_url or not settings.supabase_secret_key:
        print("SUPABASE_URL / SUPABASE_SECRET_KEY가 필요합니다.")
        return

    print("질의 임베딩 생성 중...")
    embed_started = perf_counter()
    model = SentenceTransformer(_MODEL_NAME)
    embeddings = model.encode(_QUERIES, normalize_embeddings=True).tolist()
    embed_ms = (perf_counter() - embed_started) * 1000
    print(f"  질의 {len(_QUERIES)}건 임베딩: {embed_ms:.1f}ms (전체 합계, 1회성)\n")

    headers = {
        "apikey": settings.supabase_secret_key,
        "Authorization": f"Bearer {settings.supabase_secret_key}",
    }
    results: list[RoundResult] = []
    async with httpx.AsyncClient(
        base_url=settings.supabase_url.rstrip("/"),
        headers=headers,
        timeout=_RPC_TIMEOUT_SECONDS,
    ) as client:
        active_ids = await _fetch_active_content_ids(client)
        rng = random.Random(_RANDOM_SEED)

        for size_label, size in _CANDIDATE_SIZES.items():
            candidate_ids = rng.sample(active_ids, k=size)
            print(f"[{size_label}] 후보 {len(candidate_ids)}곳")
            for query, embedding in zip(_QUERIES, embeddings, strict=True):
                for round_number in range(1, _ROUNDS + 1):
                    rpc_ms, result_count = await _measure_rpc(
                        client, embedding, candidate_ids
                    )
                    results.append(
                        RoundResult(
                            size_label=size_label,
                            candidate_count=len(candidate_ids),
                            query=query,
                            round_number=round_number,
                            rpc_ms=rpc_ms,
                            result_count=result_count,
                        )
                    )
                    print(
                        f"  [{query[:16]:16}] {round_number}회차 "
                        f"{rpc_ms:8.1f}ms (결과 {result_count}곳)"
                    )
            print()

        print(f"[상한 초과 확인] 후보 {_OVER_LIMIT_SIZE}곳으로 호출")
        over_limit_ids = rng.sample(active_ids, k=min(_OVER_LIMIT_SIZE, len(active_ids)))
        try:
            rpc_ms, _ = await _measure_rpc(client, embeddings[0], over_limit_ids)
        except httpx.HTTPStatusError as exc:
            elapsed_ms = exc.response.elapsed.total_seconds() * 1000
            print(
                f"  즉시 거부됨 (status={exc.response.status_code}, "
                f"{elapsed_ms:.1f}ms) — 가드 정상 동작\n"
            )
        else:
            print(f"  ⚠️ 거부되지 않고 {rpc_ms:.1f}ms만에 응답함 — 가드가 안 걸렸다\n")

    print("=== 요약 (후보 규모별 RPC 왕복 시간) ===")
    summaries: dict[str, dict[str, float]] = {}
    for size_label in _CANDIDATE_SIZES:
        rows = [item for item in results if item.size_label == size_label]
        summaries[size_label] = _summarize(rows)
        stats = summaries[size_label]
        print(
            f"  {size_label:10} 평균 {stats['평균']:8.1f}ms "
            f"중앙값 {stats['중앙값']:8.1f}ms "
            f"(최소 {stats['최소']:.1f} / 최대 {stats['최대']:.1f})"
        )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with RESULTS_CSV.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.writer(fp)
        writer.writerow(
            ["후보규모", "후보수", "질의", "회차", "RPC_ms", "결과_장소수"]
        )
        for item in results:
            writer.writerow(
                [
                    item.size_label,
                    item.candidate_count,
                    item.query,
                    item.round_number,
                    f"{item.rpc_ms:.1f}",
                    item.result_count,
                ]
            )
        writer.writerow([])
        writer.writerow(["후보규모", "평균_ms", "중앙값_ms", "최소_ms", "최대_ms"])
        for size_label, stats in summaries.items():
            writer.writerow(
                [
                    size_label,
                    f"{stats['평균']:.1f}",
                    f"{stats['중앙값']:.1f}",
                    f"{stats['최소']:.1f}",
                    f"{stats['최대']:.1f}",
                ]
            )
    print(f"\n결과 저장: {RESULTS_CSV}")


if __name__ == "__main__":
    asyncio.run(main())
