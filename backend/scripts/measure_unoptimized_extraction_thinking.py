"""MODIFY·INFO·COMPARE·GENERAL 추출 4곳의 thinking_budget 미적용 여부를 실측한다.

역할: D-066(2026-08-20)이 분류·요약 계열에 thinking_budget=0을 적용하면서 "나머지
미최적화 추출 4곳"으로 남겨 둔 `extract_modify_conditions`/`extract_info_query`/
`extract_compare_request`/`extract_general_request`가 실제로 지금도 thinking_budget을
안 넘기는지, 그리고 켰을 때(현재 프로덕션)와 0으로 껐을 때 지연 차이가 얼마나 나는지를
classify_intent/extract_recommend_conditions 때와 같은 방식(
scripts/compare_classify_extract_thinking_budget.py)으로 잰다.

기존 프로덕션 코드는 수정하지 않는다 — RealGeminiProvider._call_structured()를 그대로
호출하고, 각 extract_*()가 내부적으로 만드는 것과 동일한 instruction/user_input을 이
스크립트가 직접 재구성해서 thinking_budget 값만 바꿔 넘긴다(공개 메서드는 그 인자를
안 받는다).

입력: 없음(하드코딩된 질문 목록). .env에 LLM_PROVIDER=real과 LLM_API_KEY 필요.
출력: 표준 출력 + backend/test_results/unoptimized_extraction_thinking_budget.csv
호출 시점: `python -m scripts.measure_unoptimized_extraction_thinking`으로 수동 실행
(1회성 측정 도구, pytest 스위트에는 포함하지 않는다 — 실제 API 호출 비용 때문).
"""

from __future__ import annotations

import asyncio
import csv
import statistics
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.agent_context.schemas import UserConditions
from app.config import settings
from app.providers import gemini_prompts
from app.providers.gemini import RealGeminiProvider
from app.schemas import LLMOutput

ROUNDS = 2

RESULTS_DIR = Path(__file__).resolve().parent.parent / "test_results"
RESULTS_CSV = RESULTS_DIR / "unoptimized_extraction_thinking_budget.csv"


@dataclass
class Case:
    operation: str
    label: str
    user_input: str
    instruction: str


def _cases() -> list[Case]:
    return [
        Case(
            "extract_modify_conditions",
            "location_with_history",
            "광화문으로",
            gemini_prompts.build_modify_extraction_instruction(
                UserConditions(search_center="경복궁"),
                pending_clarification=None,
                shown_place_count=3,
                shown_place_names=["가회민화박물관", "북촌한옥마을", "쌈지길"],
            ),
        ),
        Case(
            "extract_modify_conditions",
            "condition_change",
            "카페 말고 맛집으로",
            gemini_prompts.build_modify_extraction_instruction(
                UserConditions(search_center="경복궁", place_types=["카페"]),
                pending_clarification=None,
                shown_place_count=3,
            ),
        ),
        Case(
            "extract_info_query",
            "open_hours",
            "경복궁 오늘 열어?",
            gemini_prompts.build_info_extraction_instruction(
                has_previous_recommendation=False,
                reference_date=date(2026, 8, 27),
            ),
        ),
        Case(
            "extract_info_query",
            "current_congestion",
            "명동성당 지금 붐벼?",
            gemini_prompts.build_info_extraction_instruction(
                has_previous_recommendation=False,
                reference_date=date(2026, 8, 27),
            ),
        ),
        Case(
            "extract_compare_request",
            "two_names",
            "경복궁이랑 인사동 이동시간 비교해줘",
            gemini_prompts.build_compare_extraction_instruction(
                shown_place_count=0,
            ),
        ),
        Case(
            "extract_compare_request",
            "named_from_history",
            "백인제가옥이랑 가회민화박물관 비교해줘",
            gemini_prompts.build_compare_extraction_instruction(
                shown_place_count=3,
                shown_place_names=["백인제가옥", "가회민화박물관", "쌈지길"],
            ),
        ),
        Case(
            "extract_general_request",
            "identity",
            "넌 누구야?",
            gemini_prompts.build_general_extraction_instruction(),
        ),
        Case(
            "extract_general_request",
            "smalltalk",
            "오늘 날씨 어때?",
            gemini_prompts.build_general_extraction_instruction(),
        ),
    ]


@dataclass
class RoundResult:
    operation: str
    tag: str
    label: str
    round_number: int
    elapsed_ms: float


async def _measure(
    provider: RealGeminiProvider, case: Case, *, tag: str, thinking_budget: int | None
) -> list[RoundResult]:
    results: list[RoundResult] = []
    for round_number in range(1, ROUNDS + 1):
        started = time.perf_counter()
        await provider._call_structured(  # noqa: SLF001 — 의도적으로 내부 메서드를 직접 호출
            case.instruction,
            case.user_input,
            LLMOutput,
            operation=case.operation,
            thinking_budget=thinking_budget,
            # model_names를 안 넘기면 _generate()가 self._generation_model_names로
            # 기본값을 삼는다 — 실제 extract_*()는 전부 self._fast_model_names를
            # 명시로 넘긴다(app/providers/gemini.py). 여기서 빠뜨리면 프로덕션과
            # 다른 모델 목록으로 재는 것이 되어 숫자가 왜곡된다(1차 실행에서 실제로
            # 이 버그로 12~40초가 나왔다가, 고친 뒤 1.1~1.6초로 정정됨).
            model_names=provider._fast_model_names,  # noqa: SLF001
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        results.append(
            RoundResult(case.operation, tag, case.label, round_number, elapsed_ms)
        )
        print(
            f"  [{tag:12}] {case.operation:26} {case.label:22} "
            f"{round_number}회차 {elapsed_ms:8.1f}ms"
        )
    return results


async def main() -> None:
    if settings.resolved_llm_provider != "real":
        print("LLM_PROVIDER(또는 PROVIDER_MODE)가 real이어야 합니다.")
        return
    if not settings.llm_api_key:
        print("LLM_API_KEY가 필요합니다.")
        return

    provider = RealGeminiProvider(
        api_key=settings.llm_api_key,
        fast_model_names=settings.resolved_llm_fast_models,
        generation_model_names=settings.resolved_llm_generation_models,
        timeout_seconds=settings.resolved_llm_timeout_seconds,
        max_retries=settings.external_api_retry_count,
    )

    cases = _cases()
    print(f"미최적화 추출 4곳, {len(cases)}케이스, 각 {ROUNDS}회씩 반복\n")

    all_results: list[RoundResult] = []
    for tag, budget in (("thinking_on", None), ("thinking_off", 0)):
        print(f"\n== {tag} (thinking_budget={budget}) ==")
        for case in cases:
            all_results.extend(
                await _measure(provider, case, tag=tag, thinking_budget=budget)
            )

    print("\n=== operation별 요약 ===")
    operations = sorted({c.operation for c in cases})
    for operation in operations:
        on_rows = [
            r.elapsed_ms for r in all_results if r.operation == operation and r.tag == "thinking_on"
        ]
        off_rows = [
            r.elapsed_ms
            for r in all_results
            if r.operation == operation and r.tag == "thinking_off"
        ]
        on_mean = statistics.mean(on_rows)
        off_mean = statistics.mean(off_rows)
        ratio = on_mean / off_mean if off_mean > 0 else float("nan")
        print(
            f"  {operation:26} thinking_on 평균 {on_mean:8.1f}ms  "
            f"thinking_off 평균 {off_mean:8.1f}ms  ({ratio:.1f}배)"
        )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with RESULTS_CSV.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.writer(fp)
        writer.writerow(["호출", "구분", "케이스", "회차", "소요시간_ms"])
        for r in all_results:
            writer.writerow(
                [r.operation, r.tag, r.label, r.round_number, f"{r.elapsed_ms:.1f}"]
            )
    print(f"\n결과 저장: {RESULTS_CSV}")


if __name__ == "__main__":
    asyncio.run(main())
