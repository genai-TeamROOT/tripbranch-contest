"""fast 경로(classify_intent)의 thinking 끄기가 실제로 걸리는지 실측한다.

역할: `_thinking_config_for()`가 0을 `thinking_level=MINIMAL`로 바꿔 보내게 된 뒤
(2026-08-18), `_resolve_thinking_budget()`에 남아 있던 `_REJECTS_ZERO_THINKING_BUDGET`
방어가 fast 모델(gemini-3.5-flash-lite)에서 그 최적화를 무효화하고 있었다. 방어를
지우는 것이 (a) 400을 유발하지 않고 (b) 실제로 더 빠른지를 확인한 스크립트다(D-076).

`--probe`는 각 모델에 세 가지 설정을 직접 보내 무엇이 400을 내는지 가른다.
옵션 없이 돌리면 실제 `classify_intent()`를 방어 켠/끈 상태로 **번갈아** 호출해
중앙값을 비교한다 — 한쪽을 몰아 돌리면 캐시 워밍이 한쪽에만 유리하고, 첫 실행이
항상 느려서 평균 대신 중앙값을 쓴다(measure_langgraph_overhead.py와 같은 방식).

측정 결과(2026-08-24) — **지연 이득은 없다.** 표본이 적으면 크게 흔들려서
반복을 늘려야 결론이 선다. 6회에서는 -17%, -7%까지 나왔지만 15회로 늘리면 사라진다.

  반복 15회  예전(방어 있음) 중앙값 958ms  →  현재(MINIMAL) 949ms   (-9ms, -0.9%)

  즉 gemini-3.5-flash-lite는 thinking 기본값이 이미 가벼워서 MINIMAL을 걸어도
  달라지지 않는다(eae832f가 gemini-2.5-flash-lite에 대해 관찰한 것과 같다).
  분류 결과는 30회 전부 RECOMMEND로 동일 — 동작도 바뀌지 않는다.

  **그러므로 방어를 지우는 근거는 속도가 아니라 두 가지다.**
  (1) 전제가 사라진 코드를 남겨두지 않는다 — 400은 "0"에만 나고 우리는 0을 숫자로
      보내지 않으므로, 그 분기는 지킬 것이 없으면서 의도만 무력화한다.
  (2) 지뢰를 제거한다 — 기본 thinking이 무거운 모델로 fast를 바꾸는 순간 그 분기가
      최적화를 조용히 삼킨다. `--probe`에서 gemini-3.6-flash는 설정 없음 3,518ms
      vs MINIMAL 1,416ms였다(각 1회, 참고값).

`--probe` 결과(같은 날):
  gemini-3.5-flash-lite / 3.6-flash  thinking_budget=0      → 400 INVALID_ARGUMENT
  gemini-3.5-flash-lite / 3.6-flash  thinking_budget=512    → 성공
  gemini-3.5-flash-lite / 3.6-flash  thinking_level=MINIMAL → 성공
  즉 400은 "0"에만 나고, 지금 코드는 0을 숫자로 보내지 않는다.

입력: 없음(하드코딩된 발화 1개). `.env`에 `LLM_API_KEY` 필요 — 실제 API를 호출하므로
      외부 네트워크가 되는 로컬에서 돌려야 한다.
출력: 표준 출력.
호출 시점: `python -m scripts.measure_fast_thinking_level [--probe]`로 수동 실행.
          실제 API 호출 비용이 있어 pytest 스위트에 넣지 않는다. 모델을 바꾸거나
          방어를 되살릴 논의가 나오면 다시 돌린다.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

from google import genai
from google.genai import types as genai_types

import app.providers.gemini as gemini_module
from app.config import settings
from app.providers.gemini import RealGeminiProvider


def _reps() -> int:
    """`--reps N`으로 반복 횟수를 올린다. 실측이 흔들려서 기본값을 12로 뒀다."""
    if "--reps" in sys.argv:
        return int(sys.argv[sys.argv.index("--reps") + 1])
    return 12


UTTERANCE = "경복궁 근처 조용한 카페 추천해줘"
PROBE_MODELS = ("gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-3.5-flash")

# `--matrix`가 도는 대상. 세대를 섞어 둔 것이 핵심이다 — "옛 방식이 통째로 없어졌나"는
# 2.5 세대와 3.x 세대를 같이 재봐야 답이 나온다.
MATRIX_MODELS = (
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
)
MATRIX_PROMPT = "경복궁 근처 카페 추천해줘 — 이 문장의 의도를 한 단어로만 답해."
RESULTS_DIR = Path(__file__).resolve().parent.parent / "test_results"


def _fast_models() -> list[str]:
    return settings.resolved_llm_fast_models


async def _probe() -> None:
    """어떤 thinking 설정이 400을 내는지 모델별로 가른다."""

    client = genai.Client(api_key=settings.llm_api_key)
    cases = (
        ("thinking_budget=0", genai_types.ThinkingConfig(thinking_budget=0)),
        ("thinking_budget=512", genai_types.ThinkingConfig(thinking_budget=512)),
        (
            "thinking_level=MINIMAL",
            genai_types.ThinkingConfig(thinking_level=genai_types.ThinkingLevel.MINIMAL),
        ),
    )
    print(f"{'모델':<26}{'보낸 설정':<26}결과")
    print("-" * 78)
    for model in PROBE_MODELS:
        for label, config in cases:
            try:
                await client.aio.models.generate_content(
                    model=model,
                    contents="한 단어로 답해: 안녕",
                    config=genai_types.GenerateContentConfig(thinking_config=config),
                )
                print(f"{model:<26}{label:<26}성공")
            except Exception as exc:  # noqa: BLE001 - 오류 문자열을 그대로 보려는 스크립트다
                message = " ".join(str(exc).split())
                print(f"{model:<26}{label:<26}실패 — {message[:60]}")
        print()


async def _measure() -> None:
    """방어를 켠/끈 상태로 실제 classify_intent를 번갈아 돌려 지연을 비교한다."""

    provider = RealGeminiProvider(
        api_key=settings.llm_api_key,
        fast_model_names=_fast_models(),
        generation_model_names=settings.resolved_llm_generation_models,
        timeout_seconds=settings.resolved_llm_timeout_seconds,
        max_retries=settings.external_api_retry_count,
    )

    async def one_turn() -> tuple[int, str]:
        started = time.perf_counter()
        result = await provider.classify_intent(
            UTTERANCE, has_previous_recommendation=False, shown_place_count=0
        )
        return int((time.perf_counter() - started) * 1000), result.data.intent.value

    # 방어가 이미 제거된 코드에서 "예전 동작"을 재현하려면 그 분기를 되살려야 한다.
    original_resolve = gemini_module._resolve_thinking_budget

    def resolve_with_old_guard(model_name: str, operation: str, requested: int | None):
        """제거 전 동작: 0을 거부하는 모델이면 thinking_config를 아예 안 싣는다."""
        if requested == 0 and model_name in {"gemini-3.5-flash-lite", "gemini-3.6-flash"}:
            return None
        return original_resolve(model_name, operation, requested)

    print(f"fast 모델 = {_fast_models()}")
    old_ms: list[int] = []
    new_ms: list[int] = []
    intents: set[str] = set()

    reps = _reps()
    for index in range(reps):
        gemini_module._resolve_thinking_budget = resolve_with_old_guard
        elapsed, intent = await one_turn()
        old_ms.append(elapsed)
        intents.add(intent)

        gemini_module._resolve_thinking_budget = original_resolve
        elapsed, intent = await one_turn()
        new_ms.append(elapsed)
        intents.add(intent)

        print(
            f"  {index + 1}회차  예전(방어 있음)={old_ms[-1]:>5}ms"
            f"   현재(MINIMAL)={new_ms[-1]:>5}ms"
        )

    gemini_module._resolve_thinking_budget = original_resolve

    old_median = statistics.median(old_ms)
    new_median = statistics.median(new_ms)
    print(f"\n중앙값  예전(방어 있음, thinking 켜짐)   = {old_median:.0f}ms")
    print(f"중앙값  현재(thinking_level=MINIMAL)     = {new_median:.0f}ms")
    delta = new_median - old_median
    print(f"차이   {delta:+.0f}ms ({delta / old_median * 100:+.1f}%)")
    print(f"분류 결과 집합: {intents}  (한 종류여야 동작이 같다는 뜻)")


async def _matrix() -> None:
    """모델 × thinking 설정 전수 측정.

    각 칸에서 두 가지를 본다 — (1) 그 설정을 받아주는가 (2) 받아준다면 실제로 생각에
    토큰을 몇 개 썼는가. (2)가 중요하다. "레벨=최소"가 이름만 최소인지 정말 생각을
    끄는지는 이 숫자로만 확인된다.
    """

    client = genai.Client(api_key=settings.llm_api_key)
    cases = (
        ("예산=0", genai_types.ThinkingConfig(thinking_budget=0)),
        ("예산=512", genai_types.ThinkingConfig(thinking_budget=512)),
        (
            "레벨=최소",
            genai_types.ThinkingConfig(thinking_level=genai_types.ThinkingLevel.MINIMAL),
        ),
        ("설정없음", None),
    )
    reps = _reps()
    results: dict[str, dict[str, dict[str, object]]] = {}

    for model in MATRIX_MODELS:
        results[model] = {}
        for label, config in cases:
            thinking_tokens: list[int] = []
            status = "성공"
            for _ in range(reps):
                try:
                    response = await client.aio.models.generate_content(
                        model=model,
                        contents=MATRIX_PROMPT,
                        config=genai_types.GenerateContentConfig(thinking_config=config),
                    )
                    used = getattr(response.usage_metadata, "thoughts_token_count", None)
                    thinking_tokens.append(used or 0)
                except Exception as exc:  # noqa: BLE001 - 거부 여부를 보려는 스크립트다
                    message = " ".join(str(exc).split())
                    status = "거부(400)" if "400" in message else f"오류: {message[:40]}"
                    break
            results[model][label] = {
                "status": status,
                "thinking_tokens": thinking_tokens,
                "median": statistics.median(thinking_tokens) if thinking_tokens else None,
            }

    header = f"{'모델':<24}" + "".join(f"{label:<18}" for label, _ in cases)
    print(f"반복 {reps}회, 생각 토큰은 중앙값\n")
    print(header)
    print("-" * len(header))
    for model in MATRIX_MODELS:
        cells = []
        for label, _ in cases:
            cell = results[model][label]
            if cell["status"] == "성공":
                cells.append(f"성공(생각 {cell['median']:.0f})")
            else:
                cells.append(str(cell["status"]))
        print(f"{model:<24}" + "".join(f"{c:<18}" for c in cells))

    out_dir = RESULTS_DIR / "gemini_thinking_matrix_2026-08-24"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "thinking_setting_matrix.json"
    out_path.write_text(
        json.dumps({"reps": reps, "prompt": MATRIX_PROMPT, "results": results},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n저장: {out_path.relative_to(RESULTS_DIR.parent)}")


def main() -> None:
    if not settings.llm_api_key:
        print("LLM_API_KEY가 없다. backend/.env를 확인한다.")
        raise SystemExit(1)
    if "--matrix" in sys.argv:
        asyncio.run(_matrix())
    elif "--probe" in sys.argv:
        asyncio.run(_probe())
    else:
        asyncio.run(_measure())


if __name__ == "__main__":
    main()
