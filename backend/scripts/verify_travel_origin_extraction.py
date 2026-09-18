""""~~에서 10분"과 "~~ 근처에 10분"이 travel_origin을 다르게 채우는지 실 LLM으로 검증한다.

D-071 배경: 이 둘은 이동시간을 재는 기준점이 다른데(전자는 그 장소, 후자는 사용자
위치), 골드셋(test_results/intent_classification_results.csv)에 "~~에서" 패턴
사례가 없어 이 규칙이 실사용에서 얼마나 자주 발동하는지 사전에 검증할 회귀 기준이
없었다. 이 스크립트가 그 공백을 신규 발화로 메운다.
"""

from __future__ import annotations

import argparse
import asyncio
import time

from app.config import Settings
from app.providers.gemini import RealGeminiProvider

# (발화, 기대 search_center 포함 여부는 보지 않음 — 장소명이 그대로 옮겨지는지는
# 참고만 하고, 핵심은 travel_origin이다), 기대 travel_origin
CASES: tuple[tuple[str, str | None], ...] = (
    # --- ① 조사로 출발점이 확정 — search_center여야 한다 ---
    ("안국역에서 10분 안에 갈 수 있는 카페", "search_center"),
    ("안국역까지 도보 10분 거리인 곳", "search_center"),
    ("혜화역에서 20분 이내 갈 수 있는 맛집", "search_center"),
    # --- ② 근처/주변 — null이어야 한다(D-067 기본값이 적용됨) ---
    ("안국역 근처에 10분 안에 갈 수 있는 카페", None),
    ("안국역 주변에서 10분 거리인 곳", None),
    ("경복궁 가려는데 10분 안에 갈 수 있는 데", None),
    # --- ③ 조사 없음 — 애매하므로 null이 안전하다 ---
    ("안국역 10분 거리에 있는 카페", None),
    # --- max_travel_time 미언급 — 채우지 않아야 한다 ---
    ("안국역에서 갈만한 조용한 카페", None),
)


async def _extract(
    provider: RealGeminiProvider, text: str
) -> tuple[str | None, str | None, int | None, str | None, int]:
    """(search_center, travel_origin, max_travel_time, 오류, ms)를 반환한다."""
    started = time.perf_counter()
    try:
        result = await provider.extract_recommend_conditions(text)
        recommend = result.data.recommend
        conditions = recommend.conditions if recommend else None
        search_center = conditions.search_center if conditions else None
        travel_origin = conditions.travel_origin if conditions else None
        travel_origin_str = travel_origin.value if travel_origin is not None else None
        max_travel_time = conditions.max_travel_time if conditions else None
        error = None
    except Exception as exc:  # noqa: BLE001 - 실 API 검증 스크립트
        search_center, travel_origin_str, max_travel_time = None, None, None
        error = f"{type(exc).__name__}: {exc}"
    ms = round((time.perf_counter() - started) * 1000)
    return search_center, travel_origin_str, max_travel_time, error, ms


async def run(model: str | None, delay: float) -> list[dict[str, object]]:
    settings = Settings()
    if not settings.llm_api_key:
        raise ValueError("LLM_API_KEY가 필요합니다.")

    fast = [model] if model else settings.resolved_llm_fast_models
    provider = RealGeminiProvider(
        api_key=settings.llm_api_key,
        fast_model_names=fast,
        generation_model_names=settings.resolved_llm_generation_models,
        timeout_seconds=60.0,
    )
    print(f"판단 모델: {fast[0]}")

    rows: list[dict[str, object]] = []
    for text, expected in CASES:
        search_center, travel_origin, max_travel_time, error, ms = await _extract(
            provider, text
        )
        rows.append(
            {
                "발화": text,
                "기대": expected or "null",
                "search_center": search_center or "null",
                "travel_origin": travel_origin or "null",
                "max_travel_time": max_travel_time,
                "일치": error is None and travel_origin == expected,
                "오류": error,
                "ms": ms,
            }
        )
        print("." if error is None else "!", end="", flush=True)
        await asyncio.sleep(delay)

    print(flush=True)
    return rows


def _report(rows: list[dict[str, object]]) -> int:
    failures = 0
    print(f"\n{'일치':<5} {'기대':<14} {'travel_origin':<14} {'search_center':<10} {'발화'}")
    print("-" * 100)
    for r in rows:
        ok = "✅" if r["일치"] else "❌"
        if ok == "❌":
            failures += 1
        print(
            f"{ok:<4} {str(r['기대']):<14} {str(r['travel_origin']):<14} "
            f"{str(r['search_center']):<10} {r['발화']}"
        )
        if r["오류"]:
            print(f"{'':>11} ⚠️  {r['오류']}")

    print(f"\n실패 {failures}/{len(rows)}건")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=None, help="모델 이름(기본: 설정값)")
    parser.add_argument("--delay", type=float, default=1.0, help="호출 간 대기(초)")
    args = parser.parse_args()

    rows = asyncio.run(run(args.model, args.delay))
    raise SystemExit(1 if _report(rows) else 0)


if __name__ == "__main__":
    main()
