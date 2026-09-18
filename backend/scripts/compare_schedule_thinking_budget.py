"""SCHEDULE Gemini 호출 소요시간 비교 — thinking_budget 켠 상태(before) vs 0(after).

역할: 동일한 SchedulePlanningRequest에 대해 thinking_budget만 바꿔가며
generate_schedule_plan()의 실제 Gemini 응답 소요시간을 잰다.
- before(thinking_budget=None): app/providers/gemini.py의 기존 9개 호출부와 같은
  동작 — GenerateContentConfig에 thinking_config를 아예 안 넣어 모델 기본값
  (gemini-2.5-flash 동적 thinking)을 그대로 쓴다.
- after(thinking_budget=0): SCHEDULE 두 호출부(generate_schedule_plan/
  generate_schedule_fill)에 실제로 적용된 값 — thinking을 완전히 끈다.

**기존 프로덕션 코드(app/ 이하)는 수정하지 않는다** — RealGeminiProvider의
_call_structured()를 그대로 호출하고, generate_schedule_plan()이 내부적으로
만드는 것과 동일한 instruction/context를 이 스크립트가 직접 재구성해서 두
thinking_budget 값을 각각 넘긴다(공개 메서드는 현재 0으로 고정돼 있어 이 값을
바꿔 부를 수 없기 때문).

입력: 없음(하드코딩된 후보 8곳·조건). .env에 LLM_PROVIDER=real과 LLM_API_KEY 필요
(샌드박스 환경은 외부 네트워크가 막혀 있어 이 스크립트는 실제 인터넷 접속이 되는
로컬 환경에서 돌려야 한다).
출력: 표준 출력 + backend/test_results/schedule_thinking_budget_latency.csv
호출 시점: `python -m scripts.compare_schedule_thinking_budget`로 수동 실행
(1회성 측정 도구, pytest 스위트에는 포함하지 않는다 — 실제 API 호출 비용 때문).
"""

from __future__ import annotations

import asyncio
import csv
import statistics
import time
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import settings
from app.providers import gemini_prompts
from app.providers.gemini import RealGeminiProvider
from app.schedule.schemas import ScheduleLLMPlan, SchedulePlanningRequest
from app.schemas import RecommendationItem, UserConditions

ROUNDS = 5
_KST = ZoneInfo("Asia/Seoul")

RESULTS_DIR = Path(__file__).resolve().parent.parent / "test_results"
RESULTS_CSV = RESULTS_DIR / "schedule_thinking_budget_latency.csv"

# 경복궁 주변 8곳 — compare_place_details_latency.py와 같은 구역을 예시로 쓴다.
# 실제 D 응답과 구조를 맞추되, 이 스크립트는 후보 검색 자체를 측정 대상에서
# 빼고(D/C 호출 없이) LLM 호출 하나만 고정 입력으로 반복한다.
_CANDIDATE_NAMES = [
    ("gyeongbokgung", "경복궁", "attraction"),
    ("bukchon", "북촌한옥마을", "attraction"),
    ("insadong", "인사동길", "attraction"),
    ("cafe-onion", "온천집 카페", "cafe"),
    ("gwanghwamun", "광화문광장", "attraction"),
    ("jogyesa", "조계사", "attraction"),
    ("cafe-blue", "블루보틀 삼청", "cafe"),
    ("national-museum", "국립고궁박물관", "attraction"),
]


def _build_candidates() -> list[RecommendationItem]:
    return [
        RecommendationItem(
            place_id=place_id,
            name=name,
            category=category,
            distance_km=round(0.3 + index * 0.4, 2),
            remaining_minutes=180,
            environment_type="outdoor" if category == "attraction" else "indoor",
            recommendation_reason=f"{name}은(는) 조건에 맞는 인기 장소예요.",
            explanations=["현재 위치에서 가까운 장소예요."],
            warnings=[],
            score=round(0.9 - index * 0.05, 2),
            feature_scores={"distance": round(0.9 - index * 0.05, 2)},
            weights_used={"distance": 1.0},
        )
        for index, (place_id, name, category) in enumerate(_CANDIDATE_NAMES)
    ]


def _build_pairwise_distances_km(
    candidates: list[RecommendationItem],
) -> dict[tuple[str, str], float]:
    # 실제 haversine 값 대신 순서 차이에 비례하는 고정값을 쓴다 — 이 스크립트의
    # 목적은 프롬프트 길이·구조를 실제와 비슷하게 맞추는 것이지, 거리 정확도가
    # 아니다(그건 이미 app.geo.haversine_km()로 별도 테스트됨).
    return {
        (a.place_id, b.place_id): round(abs(a.distance_km - b.distance_km) + 0.2, 2)
        for a, b in combinations(candidates, 2)
    }


@dataclass
class RoundResult:
    label: str
    round_number: int
    elapsed_ms: float
    item_count: int


async def _measure(
    label: str,
    provider: RealGeminiProvider,
    instruction: str,
    context: str,
    *,
    thinking_budget: int | None,
) -> list[RoundResult]:
    results: list[RoundResult] = []
    for round_number in range(1, ROUNDS + 1):
        started = time.perf_counter()
        plan = await provider._call_structured(  # noqa: SLF001 — 의도적으로 내부 메서드를 직접 호출
            instruction,
            context,
            ScheduleLLMPlan,
            operation="generate_schedule_plan",
            thinking_budget=thinking_budget,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        results.append(
            RoundResult(
                label=label,
                round_number=round_number,
                elapsed_ms=elapsed_ms,
                item_count=len(plan.items),
            )
        )
        print(f"  [{label:12}] {round_number}회차 {elapsed_ms:8.1f}ms (items={len(plan.items)})")
    return results


def _summarize(results: list[RoundResult]) -> dict[str, float]:
    values = [item.elapsed_ms for item in results]
    return {
        "평균": statistics.mean(values),
        "중앙값": statistics.median(values),
        "최소": min(values),
        "최대": max(values),
    }


async def main() -> None:
    if settings.resolved_llm_provider != "real":
        print("LLM_PROVIDER(또는 PROVIDER_MODE)가 real이어야 합니다.")
        return
    if not settings.llm_api_key:
        print("LLM_API_KEY가 필요합니다.")
        return

    # 앱과 같은 역할별 배선으로 잰다 — 여기서 재는 일정 편성 두 호출은
    # generation 묶음으로 나간다.
    provider = RealGeminiProvider(
        api_key=settings.llm_api_key,
        fast_model_names=settings.resolved_llm_fast_models,
        generation_model_names=settings.resolved_llm_generation_models,
        timeout_seconds=settings.resolved_llm_timeout_seconds,
        max_retries=settings.external_api_retry_count,
    )

    candidates = _build_candidates()
    request = SchedulePlanningRequest(
        candidates=candidates,
        conditions=UserConditions(time_available=240),
        visit_datetime=datetime(2026, 8, 13, 14, 0, tzinfo=_KST),
        pairwise_distances_km=_build_pairwise_distances_km(candidates),
    )
    instruction = gemini_prompts.build_schedule_planning_instruction(
        time_available_min=request.conditions.time_available
    )
    context = gemini_prompts.format_schedule_planning_context(request, "14:00")

    print(f"후보 {len(candidates)}곳, {ROUNDS}회씩 반복 (LLM_API_TIMEOUT_SECONDS="
          f"{settings.resolved_llm_timeout_seconds}, RETRY_COUNT="
          f"{settings.external_api_retry_count})\n")

    results: list[RoundResult] = []
    print("-- before: thinking_budget=None(모델 기본값, 기존 9개 호출부와 동일) --")
    results.extend(
        await _measure("thinking_on", provider, instruction, context, thinking_budget=None)
    )
    print("\n-- after: thinking_budget=0 (SCHEDULE에 실제 적용된 값) --")
    results.extend(
        await _measure("thinking_off", provider, instruction, context, thinking_budget=0)
    )

    print("\n=== 요약 ===")
    summaries: dict[str, dict[str, float]] = {}
    for label in ("thinking_on", "thinking_off"):
        rows = [item for item in results if item.label == label]
        summaries[label] = _summarize(rows)
        stats = summaries[label]
        print(
            f"  {label:12} 평균 {stats['평균']:8.1f}ms "
            f"중앙값 {stats['중앙값']:8.1f}ms "
            f"(최소 {stats['최소']:.1f} / 최대 {stats['최대']:.1f})"
        )
    on_mean = summaries["thinking_on"]["평균"]
    off_mean = summaries["thinking_off"]["평균"]
    if off_mean > 0:
        print(f"\n  thinking_budget=0이 평균 {on_mean / off_mean:.1f}배 빠름")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with RESULTS_CSV.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.writer(fp)
        writer.writerow(["구분", "회차", "소요시간_ms", "items_개수"])
        for item in results:
            writer.writerow(
                [item.label, item.round_number, f"{item.elapsed_ms:.1f}", item.item_count]
            )
        writer.writerow([])
        writer.writerow(["구분", "평균_ms", "중앙값_ms", "최소_ms", "최대_ms"])
        for label, stats in summaries.items():
            writer.writerow(
                [
                    label,
                    f"{stats['평균']:.1f}",
                    f"{stats['중앙값']:.1f}",
                    f"{stats['최소']:.1f}",
                    f"{stats['최대']:.1f}",
                ]
            )
    print(f"\n결과 저장: {RESULTS_CSV}")


if __name__ == "__main__":
    asyncio.run(main())
