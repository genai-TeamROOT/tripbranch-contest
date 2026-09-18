"""답변·요약 계열(GENERAL/RECOMMEND/COMPARE/INFO 답변) thinking_budget 실측.

역할: SCHEDULE·classify_intent·extract_recommend_conditions에는 이미
thinking_budget=0(모델별 보정 후 thinking_level=MINIMAL)을 적용했지만, 문장 생성·요약류
(답변 계열)는 "품질 저하 리스크"로 의도적으로 손대지 않았었다(gemini.py의
_thinking_config_for() docstring 참고). 그런데 모델이 gemini-2.5-flash → gemini-3.5-flash로
바뀌면서 이 계열의 기본 thinking이 가벼움(경량)에서 MEDIUM(항상 켜짐)으로 바뀌어, 실사용에서
GENERAL 인사말 응답에도 6~7초 TTFT가 걸리는 문제가 실측됐다(2026-08-20).

이 스크립트로 실측한 결과(케이스 5개 × 3회) thinking_budget=0이 평균 3.9배 빠르면서
답변 품질(페르소나·자기소개·문장 수 규칙)은 그대로였다 — **이 결과에 따라 5개 호출부
(generate_general_answer/stream_general_answer/generate_recommendation_summary/
stream_recommendation_summary/stream_info_answer/generate_compare_summary) 모두
thinking_budget=0이 프로덕션 기본값으로 이미 적용됐다**(gemini.py). 공개 메서드는
이제 thinking_budget 인자를 받지 않으므로(SCHEDULE과 같은 이유로 고정값으로 내부에
박아뒀다), 이 스크립트는 대조군(thinking_budget=None)을 다시 재보기 위해 다른
스크립트들과 같은 방식으로 `_call_structured()`/`_stream_text()`를 직접 호출한다.

이 스크립트는 재현·회귀 확인용으로 남긴다(모델이 또 바뀌거나 품질 이슈가 제기되면
다시 돌려본다) — 응답이 여전히 프롬프트 규칙(길이·페르소나·자기소개 등)을 지키는지는
round마다 출력되는 텍스트를 눈으로 확인한다(자동 판정하지 않는다).

기존 프로덕션 코드(app/ 이하)는 이 스크립트가 수정하지 않는다 — RealGeminiProvider의
`_call_structured()`/`_stream_text()`를 그대로 호출하고, 각 공개 메서드가 내부적으로
만드는 것과 동일한 instruction/payload를 이 스크립트가 직접 재구성해서 thinking_budget
값만 바꿔 넘긴다(공개 메서드는 현재 0으로 고정돼 있어 이 값을 바꿔 부를 수 없기 때문 —
compare_schedule_thinking_budget.py와 같은 방식).

입력: 없음(하드코딩된 대표 케이스). .env에 LLM_PROVIDER=real과 LLM_API_KEY 필요(샌드박스
환경은 외부 네트워크가 막혀 있어 이 스크립트는 실제 인터넷 접속이 되는 로컬 환경에서
돌려야 한다).
출력: 표준 출력 + backend/test_results/answer_thinking_budget_latency.csv
호출 시점: `python -m scripts.compare_answer_thinking_budget`로 수동 실행
(1회성 측정 도구, pytest 스위트에는 포함하지 않는다 — 실제 API 호출 비용 때문).
"""

from __future__ import annotations

import asyncio
import csv
import json
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.config import settings
from app.providers import gemini_prompts
from app.providers.gemini import (
    RealGeminiProvider,
    _ComparisonSummary,
    _GeneralAnswer,
    _RecommendationSummary,
)
from app.schemas import (
    CompareCriteria,
    ComparisonItem,
    ComparisonResult,
    GeneralTopic,
    Intent,
    RecommendationItem,
    RecommendationResponse,
)

ROUNDS = 3

RESULTS_DIR = Path(__file__).resolve().parent.parent / "test_results"
RESULTS_CSV = RESULTS_DIR / "answer_thinking_budget_latency.csv"


def _place(place_id: str, name: str, *, distance_km: float) -> RecommendationItem:
    return RecommendationItem(
        place_id=place_id,
        name=name,
        category="cafe",
        distance_km=distance_km,
        remaining_minutes=90,
        environment_type="indoor",
        recommendation_reason=f"{name}은(는) 조건에 맞는 인기 장소예요.",
        explanations=["현재 위치에서 가까운 장소예요."],
        warnings=[],
        score=0.9,
        feature_scores={"distance": 0.9},
        weights_used={"distance": 1.0},
    )


_RECOMMENDATIONS = RecommendationResponse(
    recommendations=[
        _place("p1", "온천집 카페", distance_km=0.3),
        _place("p2", "블루보틀 삼청", distance_km=0.6),
    ],
    unverified_recommendations=[],
    elapsed_ms=0,
)

_COMPARISON = ComparisonResult(
    criteria=CompareCriteria.DISTANCE,
    items=[
        ComparisonItem(
            place_id="p1", place_name="온천집 카페", rank=1, distance_km=0.3,
            remaining_minutes=90, environment_type="indoor",
        ),
        ComparisonItem(
            place_id="p2", place_name="블루보틀 삼청", rank=2, distance_km=0.6,
            remaining_minutes=60, environment_type="indoor",
        ),
    ],
)


class _Invoker(Protocol):
    async def __call__(
        self, provider: RealGeminiProvider, *, thinking_budget: int | None
    ) -> str: ...


def _summary_payload(recommendations: RecommendationResponse) -> str:
    items = [
        RealGeminiProvider._recommendation_summary_item(item)  # noqa: SLF001
        for item in [*recommendations.recommendations, *recommendations.unverified_recommendations]
    ]
    return json.dumps({"recommendations": items}, ensure_ascii=False)


async def _general_identity(provider: RealGeminiProvider, *, thinking_budget: int | None) -> str:
    instruction = gemini_prompts.build_general_answer_instruction(GeneralTopic.SERVICE_IDENTITY)
    result = await provider._call_structured(  # noqa: SLF001 — 의도적으로 내부 메서드를 직접 호출
        instruction, "안녕", _GeneralAnswer,
        operation="generate_general_answer",
        model_names=provider._generation_model_names,  # noqa: SLF001
        thinking_budget=thinking_budget,
    )
    return result.answer


async def _general_knowledge(provider: RealGeminiProvider, *, thinking_budget: int | None) -> str:
    instruction = gemini_prompts.build_general_answer_instruction(GeneralTopic.SEASON_INFO)
    result = await provider._call_structured(  # noqa: SLF001
        instruction, "가을에 단풍 언제 절정이야?", _GeneralAnswer,
        operation="generate_general_answer",
        model_names=provider._generation_model_names,  # noqa: SLF001
        thinking_budget=thinking_budget,
    )
    return result.answer


async def _recommend_summary(provider: RealGeminiProvider, *, thinking_budget: int | None) -> str:
    instruction = gemini_prompts.build_recommendation_summary_instruction(Intent.RECOMMEND)
    result = await provider._call_structured(  # noqa: SLF001
        instruction, _summary_payload(_RECOMMENDATIONS), _RecommendationSummary,
        operation="generate_recommendation_summary",
        model_names=provider._generation_model_names,  # noqa: SLF001
        thinking_budget=thinking_budget,
    )
    return result.message


async def _compare_summary(provider: RealGeminiProvider, *, thinking_budget: int | None) -> str:
    instruction = gemini_prompts.build_compare_summary_instruction(_COMPARISON.criteria)
    result = await provider._call_structured(  # noqa: SLF001
        instruction, _COMPARISON.model_dump_json(exclude_none=True), _ComparisonSummary,
        operation="generate_compare_summary",
        model_names=provider._generation_model_names,  # noqa: SLF001
        thinking_budget=thinking_budget,
    )
    return "\n".join(result.lines)


async def _info_answer(provider: RealGeminiProvider, *, thinking_budget: int | None) -> str:
    instruction = gemini_prompts.build_info_answer_instruction("operating_hours")
    payload = json.dumps(
        {
            "place_name": "온천집 카페",
            "specific_question": "오늘 몇 시까지 해?",
            "fields": {"operating_hours": "10:00~21:00"},
        },
        ensure_ascii=False,
    )
    chunks = [
        chunk
        async for chunk in provider._stream_text(  # noqa: SLF001
            instruction=instruction,
            user_input=payload,
            operation="stream_info_answer",
            model_names=provider._generation_model_names,  # noqa: SLF001
            thinking_budget=thinking_budget,
        )
    ]
    return "".join(chunks)


@dataclass
class Case:
    label: str
    invoke: _Invoker


_CASES: list[Case] = [
    Case("general_identity", _general_identity),
    Case("general_knowledge", _general_knowledge),
    Case("recommend_summary", _recommend_summary),
    Case("compare_summary", _compare_summary),
    Case("info_answer", _info_answer),
]


@dataclass
class RoundResult:
    label: str
    round_number: int
    elapsed_ms: float
    text: str


async def _invoke(provider: RealGeminiProvider, case: Case, *, thinking_budget: int | None) -> str:
    return await case.invoke(provider, thinking_budget=thinking_budget)


async def _measure(
    tag: str, provider: RealGeminiProvider, case: Case, *, thinking_budget: int | None
) -> list[RoundResult]:
    results: list[RoundResult] = []
    for round_number in range(1, ROUNDS + 1):
        started = time.perf_counter()
        text = await _invoke(provider, case, thinking_budget=thinking_budget)
        elapsed_ms = (time.perf_counter() - started) * 1000
        results.append(RoundResult(f"{tag}:{case.label}", round_number, elapsed_ms, text))
        print(f"  [{tag:12}] {case.label:18} {round_number}회차 {elapsed_ms:8.1f}ms")
        print(f"      → {text[:120]}")
    return results


def _summarize(rows: list[float]) -> dict[str, float]:
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

    provider = RealGeminiProvider(
        api_key=settings.llm_api_key,
        fast_model_names=settings.resolved_llm_fast_models,
        generation_model_names=settings.resolved_llm_generation_models,
        timeout_seconds=settings.resolved_llm_timeout_seconds,
        max_retries=settings.external_api_retry_count,
    )

    print(
        f"케이스 {len(_CASES)}개, {ROUNDS}회씩 반복 "
        f"(생성 모델={settings.resolved_llm_generation_models})\n"
    )

    all_results: list[RoundResult] = []
    for tag, budget in (("thinking_on", None), ("thinking_off", 0)):
        print(f"\n== {tag} (thinking_budget={budget}) ==")
        for case in _CASES:
            all_results.extend(await _measure(tag, provider, case, thinking_budget=budget))

    print("\n=== 케이스별 요약 ===")
    for case in _CASES:
        for tag in ("thinking_on", "thinking_off"):
            rows = [r.elapsed_ms for r in all_results if r.label == f"{tag}:{case.label}"]
            stats = _summarize(rows)
            print(
                f"  {tag:12}[{case.label:18}] 평균 {stats['평균']:8.1f}ms "
                f"중앙값 {stats['중앙값']:8.1f}ms "
                f"(최소 {stats['최소']:.1f} / 최대 {stats['최대']:.1f})"
            )

    on_mean = statistics.mean(
        r.elapsed_ms for r in all_results if r.label.startswith("thinking_on:")
    )
    off_mean = statistics.mean(
        r.elapsed_ms for r in all_results if r.label.startswith("thinking_off:")
    )
    if off_mean > 0:
        print(f"\n  전체 평균: thinking_budget=0이 {on_mean / off_mean:.1f}배 빠름")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with RESULTS_CSV.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.writer(fp)
        writer.writerow(["구분", "케이스", "회차", "소요시간_ms", "응답"])
        for r in all_results:
            tag, _, case_label = r.label.partition(":")
            writer.writerow([tag, case_label, r.round_number, f"{r.elapsed_ms:.1f}", r.text])
    print(f"\n결과 저장: {RESULTS_CSV}")
    print("\n※ 응답 문구가 페르소나·길이·자기소개 규칙을 지키는지는 위 출력을 눈으로 확인하세요.")


if __name__ == "__main__":
    asyncio.run(main())
