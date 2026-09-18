""""~~에서 출발할건데"로 밝힌 출발지가 current_location에 담기는지 실 LLM으로 검증한다.

recommend.extract v2.10.0 배경: `location_rules.md`가 `current_location`을 "나 지금
~~야"처럼 **현재 있는 곳**을 밝힌 경우로만 한정해, "효창공원역앞에서 출발할건데
용문동에 아이랑 체험하기 좋은 곳 추천해줘"의 출발지가 통째로 버려졌다. 빈칸이 되면
위치 설정 화면 값이 대신 채워지므로(`_apply_selected_locations()`), 사용자가 말한
출발지 대신 화면에 잡힌 엉뚱한 역이 기준점이 됐다.

같은 표현인데 문장이 짧으면("~~에서 출발할건데 용문동에서도 추천해줘") 통과했다 —
규칙이 정한 동작이 아니라 모델이 눈치로 메우던 자리라 재현이 들쭉날쭉했다. 그래서
**같은 발화를 여러 번 돌려 흔들림까지** 본다.

골드셋(test_results/intent_classification_results.csv)에 "출발" 패턴 사례가 없어
회귀 기준이 없으므로, 이 스크립트가 신규 발화로 그 공백을 메운다.
`verify_travel_origin_extraction.py`와 같은 구조다.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from collections import Counter

from app.config import Settings
from app.providers.gemini import RealGeminiProvider

# (발화, 기대 current_location, 기대 search_center)
# 기대값이 None이면 null이어야 한다는 뜻이다.
CASES: tuple[tuple[str, str | None, str | None], ...] = (
    # --- ① 이번 수정의 대상 — 출발점 + 목적지가 한 발화에 같이 나온다 ---
    # 문장이 길어져도(취향·동행·장소 유형) 출발지가 탈락하면 안 된다. 이게 원 버그다.
    (
        "효창공원역앞에서 출발할건데 용문동에 아이랑 체험하기 좋은 곳 추천해줘",
        "효창공원역앞",
        "용문동",
    ),
    # 같은 뜻의 짧은 문장 — 수정 전에도 통과하던 대조군이다.
    ("효창공원역앞에서 출발할건데 용문동에서도 추천해줘", "효창공원역앞", "용문동"),
    ("안국역에서 출발해서 성수동 카페 추천해줘", "안국역", "성수동"),
    ("시청역에서 갈 건데 북촌 조용한 데 알려줘", "시청역", "북촌"),
    # --- ② 출발점만 말한 경우 — current_location만 채운다 ---
    ("혜화역에서 출발할건데 전시 볼 만한 곳 추천해줘", "혜화역", None),
    # --- ③ 현재 위치 표현 — 원래 규칙, 그대로 통과해야 한다 ---
    ("나 지금 강남역이야 근처 카페 추천해줘", "강남역", "강남역"),
    # --- ④ 회귀 대조군 — 출발 표현이 없으면 current_location은 비어 있어야 한다 ---
    # 비어야 화면에서 정한 출발지가 채워진다(발화가 없을 때만 화면이 이긴다).
    ("성수동 근처 카페 추천해줘", None, "성수동"),
    ("용문동에 아이랑 갈 만한 곳 추천해줘", None, "용문동"),
    ("경복궁 가려는데 조용한 카페 알려줘", None, "경복궁"),
)


async def _extract(
    provider: RealGeminiProvider, text: str
) -> tuple[str | None, str | None, str | None, int]:
    """(current_location, search_center, 오류, ms)를 반환한다."""
    started = time.perf_counter()
    try:
        result = await provider.extract_recommend_conditions(text)
        recommend = result.data.recommend
        conditions = recommend.conditions if recommend else None
        current_location = conditions.current_location if conditions else None
        search_center = conditions.search_center if conditions else None
        error = None
    except Exception as exc:  # noqa: BLE001 - 실 API 검증 스크립트
        current_location, search_center = None, None
        error = f"{type(exc).__name__}: {exc}"
    ms = round((time.perf_counter() - started) * 1000)
    return current_location, search_center, error, ms


def _matches(actual: str | None, expected: str | None) -> bool:
    """지명은 조사·수식이 붙어 올 수 있어 포함 관계로 본다(null은 엄격 비교)."""
    if expected is None:
        return actual is None
    if actual is None:
        return False
    return expected in actual or actual in expected


async def run(model: str | None, delay: float, repeat: int) -> list[dict[str, object]]:
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
    print(f"판단 모델: {fast[0]} · 케이스 {len(CASES)}건 × {repeat}회")

    rows: list[dict[str, object]] = []
    for text, expected_current, expected_search in CASES:
        observed: list[str] = []
        hits = 0
        errors: list[str] = []
        latencies: list[int] = []
        for _ in range(repeat):
            current, search, error, ms = await _extract(provider, text)
            observed.append(current or "null")
            latencies.append(ms)
            if error is not None:
                errors.append(error)
            elif _matches(current, expected_current) and _matches(search, expected_search):
                hits += 1
            print("." if error is None else "!", end="", flush=True)
            await asyncio.sleep(delay)
        rows.append(
            {
                "발화": text,
                "기대_current": expected_current or "null",
                "기대_search": expected_search or "null",
                "통과": hits,
                "시도": repeat,
                "관찰된_current": Counter(observed),
                "흔들림": len(set(observed)) > 1,
                "오류": errors[0] if errors else None,
                "평균ms": round(sum(latencies) / len(latencies)),
            }
        )

    print(flush=True)
    return rows


def _report(rows: list[dict[str, object]]) -> int:
    failures = 0
    unstable = 0
    print(f"\n{'판정':<5} {'통과':<7} {'기대 current':<16} {'관찰된 current'}")
    print("-" * 100)
    for r in rows:
        passed = r["통과"] == r["시도"]
        if not passed:
            failures += 1
        if r["흔들림"]:
            unstable += 1
        mark = "✅" if passed else "❌"
        observed = ", ".join(
            f"{value}×{count}" for value, count in r["관찰된_current"].most_common()
        )
        print(f"{mark:<4} {r['통과']}/{r['시도']:<5} {str(r['기대_current']):<16} {observed}")
        print(f"{'':>6} {r['발화']}  ({r['평균ms']}ms)")
        if r["오류"]:
            print(f"{'':>6} ⚠️  {r['오류']}")

    print(f"\n기대 불일치 {failures}/{len(rows)}건 · 흔들림 {unstable}/{len(rows)}건")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=None, help="모델 이름(기본: 설정값)")
    parser.add_argument("--delay", type=float, default=1.0, help="호출 간 대기(초)")
    parser.add_argument("--repeat", type=int, default=3, help="케이스당 반복 횟수")
    args = parser.parse_args()

    rows = asyncio.run(run(args.model, args.delay, args.repeat))
    raise SystemExit(1 if _report(rows) else 0)


if __name__ == "__main__":
    main()
