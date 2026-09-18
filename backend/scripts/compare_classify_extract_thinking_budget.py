"""분류(classify_intent)·추출(extract_recommend_conditions) Gemini 호출 소요시간·정확도
비교 — thinking_budget 켠 상태(before) vs 0(after).

역할: SCHEDULE 두 호출(generate_schedule_plan/fill)에 이미 적용된 thinking_budget=0을
분류·추출 두 호출에도 확장 적용할 만한지 실측으로 검토한다(2026-08-13 논의).
- before(thinking_budget=None): 두 호출의 현재 프로덕션 동작 — GenerateContentConfig에
  thinking_config를 아예 안 넣어 모델 기본값(gemini-2.5-flash 동적 thinking)을 그대로 쓴다.
- after(thinking_budget=0): thinking을 완전히 껐을 때 같은 입력에 대해 속도와 분류/추출
  결과가 어떻게 달라지는지를 함께 잰다.

기존 프로덕션 코드(app/ 이하)는 수정하지 않는다 — RealGeminiProvider의 _call_structured()를
그대로 호출하고, classify_intent()/extract_recommend_conditions()가 내부적으로 만드는 것과
동일한 instruction/user_input을 이 스크립트가 직접 재구성해서 두 thinking_budget 값을 각각
넘긴다(공개 메서드는 thinking_budget 인자를 안 받기 때문).

테스트 케이스는 tests/test_llm_provider.py의 기존 회귀 케이스(FakeLLMProvider 검증용, 문서상
실 Gemini와 일치가 확인된 것들)에서 대표적인 것만 가져왔다 — "우리가 테스트해보던 질문들"
기준으로 실제 정확도가 유지되는지 확인하려는 목적.

입력: 없음(하드코딩된 질문 목록). .env에 LLM_PROVIDER=real과 LLM_API_KEY 필요(샌드박스
환경은 외부 네트워크가 막혀 있어 이 스크립트는 실제 인터넷 접속이 되는 로컬 환경에서 돌려야
한다).
출력: 표준 출력 + backend/test_results/classify_extract_thinking_budget.csv
호출 시점: `python -m scripts.compare_classify_extract_thinking_budget`로 수동 실행
(1회성 측정 도구, pytest 스위트에는 포함하지 않는다 — 실제 API 호출 비용 때문).
"""

from __future__ import annotations

import asyncio
import csv
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings
from app.providers import gemini_prompts
from app.providers.gemini import RealGeminiProvider
from app.schemas import Intent, IntentClassificationResult, LLMOutput

ROUNDS = 2  # 케이스당 반복 횟수(비용 때문에 SCHEDULE 스크립트보다 적게)

RESULTS_DIR = Path(__file__).resolve().parent.parent / "test_results"
RESULTS_CSV = RESULTS_DIR / "classify_extract_thinking_budget.csv"


@dataclass
class IntentCase:
    """tests/test_llm_provider.py에서 가져온 classify_intent 대표 케이스."""

    label: str
    user_input: str
    kwargs: dict[str, object] = field(default_factory=dict)
    expected: Intent = Intent.RECOMMEND


_INTENT_CASES: list[IntentCase] = [
    IntentCase(
        "tc01_recommend",
        "경복궁 근처 카페 추천해줘",
        {"has_previous_recommendation": False, "shown_place_count": 0},
        Intent.RECOMMEND,
    ),
    IntentCase(
        "schedule_explicit",
        "오늘 오후 종로 일정 짜줘",
        {"has_previous_recommendation": False, "shown_place_count": 0},
        Intent.SCHEDULE,
    ),
    IntentCase(
        "schedule_course",
        "반나절 코스 만들어줘",
        {"has_previous_recommendation": False, "shown_place_count": 0},
        Intent.SCHEDULE,
    ),
    IntentCase(
        "modify_location_with_history",
        "광화문으로",
        {"has_previous_recommendation": True, "shown_place_count": 3},
        Intent.MODIFY,
    ),
    IntentCase(
        "modify_show_more",
        "다른 곳 보여줘",
        {"has_previous_recommendation": True, "shown_place_count": 3},
        Intent.MODIFY,
    ),
    IntentCase(
        "schedule_clarification_continue",
        "광화문으로 알려줘",
        {
            "has_previous_recommendation": False,
            "shown_place_count": 0,
            "pending_clarification": "location_ambiguous",
            "last_intent": "SCHEDULE",
        },
        Intent.SCHEDULE,
    ),
    IntentCase(
        "info_open_hours",
        "경복궁 오늘 열어?",
        {"has_previous_recommendation": False, "shown_place_count": 0},
        Intent.INFO,
    ),
    IntentCase(
        "general_identity",
        "넌 누구야?",
        {"has_previous_recommendation": False, "shown_place_count": 0},
        Intent.GENERAL,
    ),
    IntentCase(
        "info_after_history",
        "경복궁 근처에 화장실 있어?",
        {"has_previous_recommendation": True, "shown_place_count": 3},
        Intent.INFO,
    ),
    IntentCase(
        "modify_condition_change_after_schedule",
        "경복궁 근처 카페 말고 맛집",
        {
            "has_previous_recommendation": False,
            "shown_place_count": 0,
            "pending_clarification": None,
            "last_intent": "SCHEDULE",
        },
        Intent.MODIFY,
    ),
]


@dataclass
class ExtractCase:
    """tests/test_llm_provider.py에서 가져온 extract_recommend_conditions 대표 케이스."""

    label: str
    user_input: str
    check: str  # 사람이 읽을 기대값 설명(자동 검증은 search_center 등 핵심 필드만)
    expected_search_center: str | None = None


_EXTRACT_CASES: list[ExtractCase] = [
    ExtractCase(
        "cafe_near_gyeongbokgung",
        "경복궁 근처 카페 추천해줘",
        "search_center=경복궁",
        expected_search_center="경복궁",
    ),
    ExtractCase(
        "rainy_day",
        "비 오는데 갈 만한 곳 추천",
        "weather_intent 관련 조건 추출",
    ),
    ExtractCase(
        "museum_or_cafe",
        "박물관이나 카페 가고 싶어",
        "place_tags에 박물관/카페 계열 반영",
    ),
    ExtractCase(
        "bare_place_name",
        "경복궁",
        "search_center=경복궁(단순 지명)",
        expected_search_center="경복궁",
    ),
    ExtractCase(
        "no_condition",
        "추천해줘",
        "특별한 조건 없이 빈 조건 반환",
    ),
]


@dataclass
class IntentRoundResult:
    label: str
    round_number: int
    elapsed_ms: float
    correct: bool
    got_intent: str


@dataclass
class ExtractRoundResult:
    label: str
    round_number: int
    elapsed_ms: float
    search_center_ok: bool | None  # None이면 이 케이스는 자동 검증 대상이 아님
    got_search_center: str | None


async def _measure_intent(
    tag: str, provider: RealGeminiProvider, case: IntentCase, *, thinking_budget: int | None
) -> list[IntentRoundResult]:
    instruction = gemini_prompts.build_intent_classification_instruction(**case.kwargs)
    results: list[IntentRoundResult] = []
    for round_number in range(1, ROUNDS + 1):
        started = time.perf_counter()
        parsed = await provider._call_structured(  # noqa: SLF001 — 의도적으로 내부 메서드를 직접 호출
            instruction,
            case.user_input,
            IntentClassificationResult,
            operation="classify_intent",
            thinking_budget=thinking_budget,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        correct = parsed.intent == case.expected
        results.append(
            IntentRoundResult(
                label=f"{tag}:{case.label}",
                round_number=round_number,
                elapsed_ms=elapsed_ms,
                correct=correct,
                got_intent=parsed.intent.value,
            )
        )
        mark = "OK" if correct else "MISS"
        print(
            f"  [{tag:12}] {case.label:38} {round_number}회차 {elapsed_ms:8.1f}ms "
            f"{mark} (기대={case.expected.value}, 실제={parsed.intent.value})"
        )
    return results


async def _measure_extract(
    tag: str, provider: RealGeminiProvider, case: ExtractCase, *, thinking_budget: int | None
) -> list[ExtractRoundResult]:
    instruction = gemini_prompts.build_recommend_extraction_instruction()
    results: list[ExtractRoundResult] = []
    for round_number in range(1, ROUNDS + 1):
        started = time.perf_counter()
        parsed = await provider._call_structured(  # noqa: SLF001 — 의도적으로 내부 메서드를 직접 호출
            instruction,
            case.user_input,
            LLMOutput,
            operation="extract_recommend_conditions",
            thinking_budget=thinking_budget,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        got_center = (
            parsed.recommend.conditions.search_center
            if parsed.recommend is not None
            else None
        )
        search_center_ok: bool | None = None
        if case.expected_search_center is not None:
            search_center_ok = got_center == case.expected_search_center
        results.append(
            ExtractRoundResult(
                label=f"{tag}:{case.label}",
                round_number=round_number,
                elapsed_ms=elapsed_ms,
                search_center_ok=search_center_ok,
                got_search_center=got_center,
            )
        )
        note = (
            f"search_center={got_center!r}"
            if search_center_ok is None
            else ("OK" if search_center_ok else f"MISS(실제={got_center!r})")
        )
        print(
            f"  [{tag:12}] {case.label:24} {round_number}회차 {elapsed_ms:8.1f}ms {note}"
            f" -- {case.check}"
        )
    return results


def _summarize_ms(rows: list[float]) -> dict[str, float]:
    return {
        "평균": statistics.mean(rows),
        "중앙값": statistics.median(rows),
        "최소": min(rows),
        "최대": max(rows),
    }


async def main() -> None:
    if settings.resolved_llm_provider != "real":
        print("LLM_PROVIDER(또는 PROVIDER_MODE)가 real이어야 합니다.")
        return
    if not settings.llm_api_key:
        print("LLM_API_KEY가 필요합니다.")
        return

    # 앱과 같은 역할별 배선으로 잰다 — 여기서 재는 classify_intent·
    # extract_recommend_conditions는 fast 묶음으로 나간다.
    provider = RealGeminiProvider(
        api_key=settings.llm_api_key,
        fast_model_names=settings.resolved_llm_fast_models,
        generation_model_names=settings.resolved_llm_generation_models,
        timeout_seconds=settings.resolved_llm_timeout_seconds,
        max_retries=settings.external_api_retry_count,
    )

    print(
        f"classify_intent {len(_INTENT_CASES)}건, extract_recommend_conditions "
        f"{len(_EXTRACT_CASES)}건, 각 {ROUNDS}회씩 반복\n"
    )

    intent_results: list[IntentRoundResult] = []
    extract_results: list[ExtractRoundResult] = []

    for tag, budget in (("thinking_on", None), ("thinking_off", 0)):
        print(f"\n== classify_intent -- {tag} (thinking_budget={budget}) ==")
        for case in _INTENT_CASES:
            intent_results.extend(
                await _measure_intent(tag, provider, case, thinking_budget=budget)
            )

    for tag, budget in (("thinking_on", None), ("thinking_off", 0)):
        print(f"\n== extract_recommend_conditions -- {tag} (thinking_budget={budget}) ==")
        for case in _EXTRACT_CASES:
            extract_results.extend(
                await _measure_extract(tag, provider, case, thinking_budget=budget)
            )

    print("\n=== classify_intent 요약 ===")
    for tag in ("thinking_on", "thinking_off"):
        rows = [r for r in intent_results if r.label.startswith(f"{tag}:")]
        stats = _summarize_ms([r.elapsed_ms for r in rows])
        accuracy = sum(1 for r in rows if r.correct) / len(rows)
        print(
            f"  {tag:12} 평균 {stats['평균']:8.1f}ms 정확도 {accuracy * 100:5.1f}% "
            f"({sum(1 for r in rows if r.correct)}/{len(rows)})"
        )
        misses = [r for r in rows if not r.correct]
        for miss in misses:
            print(f"    ! 틀림: {miss.label} 실제={miss.got_intent}")

    print("\n=== extract_recommend_conditions 요약(정확도는 search_center 있는 케이스만) ===")
    for tag in ("thinking_on", "thinking_off"):
        rows = [r for r in extract_results if r.label.startswith(f"{tag}:")]
        stats = _summarize_ms([r.elapsed_ms for r in rows])
        checked = [r for r in rows if r.search_center_ok is not None]
        accuracy_note = (
            f"search_center 정확도 {sum(1 for r in checked if r.search_center_ok)}/{len(checked)}"
            if checked
            else "search_center 검증 케이스 없음"
        )
        print(f"  {tag:12} 평균 {stats['평균']:8.1f}ms {accuracy_note}")

    on_intent_mean = statistics.mean(
        r.elapsed_ms for r in intent_results if r.label.startswith("thinking_on:")
    )
    off_intent_mean = statistics.mean(
        r.elapsed_ms for r in intent_results if r.label.startswith("thinking_off:")
    )
    on_extract_mean = statistics.mean(
        r.elapsed_ms for r in extract_results if r.label.startswith("thinking_on:")
    )
    off_extract_mean = statistics.mean(
        r.elapsed_ms for r in extract_results if r.label.startswith("thinking_off:")
    )
    if off_intent_mean > 0:
        ratio = on_intent_mean / off_intent_mean
        print(f"\n  classify_intent: thinking_budget=0이 평균 {ratio:.1f}배 빠름")
    if off_extract_mean > 0:
        print(
            f"  extract_recommend_conditions: thinking_budget=0이 평균 "
            f"{on_extract_mean / off_extract_mean:.1f}배 빠름"
        )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with RESULTS_CSV.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.writer(fp)
        writer.writerow(["호출", "구분", "케이스", "회차", "소요시간_ms", "정확", "실제값"])
        for r in intent_results:
            tag, _, case_label = r.label.partition(":")
            writer.writerow(
                [
                    "classify_intent",
                    tag,
                    case_label,
                    r.round_number,
                    f"{r.elapsed_ms:.1f}",
                    r.correct,
                    r.got_intent,
                ]
            )
        for r in extract_results:
            tag, _, case_label = r.label.partition(":")
            writer.writerow(
                [
                    "extract_recommend_conditions",
                    tag,
                    case_label,
                    r.round_number,
                    f"{r.elapsed_ms:.1f}",
                    r.search_center_ok,
                    r.got_search_center,
                ]
            )
    print(f"\n결과 저장: {RESULTS_CSV}")


if __name__ == "__main__":
    asyncio.run(main())
