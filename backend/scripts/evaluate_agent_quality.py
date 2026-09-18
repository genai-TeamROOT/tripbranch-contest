"""TripBranch Agent 품질 골드셋을 실행하고 결과를 누적 저장한다.

입력:
    backend/test_results/agent_quality/evaluation_dev.csv
    backend/test_results/agent_quality/evaluation_final.csv

출력(실행마다 새로 생성):
    backend/test_results/agent_quality/runs/<run_id>/case_results.csv
    backend/test_results/agent_quality/runs/<run_id>/intent_metrics.csv
    backend/test_results/agent_quality/runs/<run_id>/confusion_matrix.csv
    backend/test_results/agent_quality/runs/<run_id>/summary.json
    backend/test_results/agent_quality/history.csv  (실행 요약 누적)

호출:
    backend/.venv/bin/python -m scripts.evaluate_agent_quality --split dev
    backend/.venv/bin/python -m scripts.evaluate_agent_quality --split final
    backend/.venv/bin/python -m scripts.evaluate_agent_quality --split all

실제 /api/chat과 Gemini/Tool을 호출하므로 pytest에는 포함하지 않는다. 평가셋의
정답은 자동 생성한 값이 아니라, 팀이 합의해 검토해야 하는 골드 라벨이다.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import statistics
import sys
import time
from collections import Counter

# `field`라는 이름은 이 파일에서 조건 필드를 가리키는 루프 변수로 이미 쓰인다.
# 별칭으로 들여와 가려지지 않게 한다.
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import httpx

from app.config import settings
from app.prompts.registry import operation_prompt_version
from app.providers.gemini_prompts import PROMPT_VERSION

ROOT_DIR = Path(__file__).resolve().parent.parent
QUALITY_DIR = ROOT_DIR / "test_results" / "agent_quality"
DATASET_PATHS = {
    "dev": QUALITY_DIR / "evaluation_dev.csv",
    "final": QUALITY_DIR / "evaluation_final.csv",
}
HISTORY_PATH = QUALITY_DIR / "history.csv"
DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_INTERVAL_SECONDS = 0.4

# operation이 어느 모델 스위치를 타는지. `providers/gemini.py`의 호출부에서 그대로 옮겼다.
#
# **이 표가 없으면 "모델을 바꿔 비교했다"가 성립하지 않는다.** 골드셋이 채점하는 것은
# intent와 `user_conditions` 둘뿐이고 **둘 다 FAST 산출물이다** — `LLM_GENERATION_MODEL_NAME`을
# 바꿔도 답변 문장은 완전히 달라지는데 이 스크립트의 점수는 한 자리도 안 움직인다.
# 그래서 "GENERATION을 바꿨는데 차이가 없다"로 읽히는 사고가 구조적으로 가능하다.
# 티어별로 **실제로 답한 모델**을 따로 기록해 그 오독을 막는다.
_FAST_OPERATIONS = frozenset(
    {
        "classify_intent",
        "extract_recommend_conditions",
        "extract_recommend_conditions_retry",
        "extract_modify_conditions",
        "extract_info_query",
        "extract_compare_request",
        "extract_general_request",
        "generate_follow_up_suggestions",
        "filter_review_evidence",
        "extract_closure_rules",
    }
)
_GENERATION_OPERATIONS = frozenset(
    {
        "generate_general_answer",
        "generate_recommendation_summary",
        "stream_recommendation_summary",
        "stream_general_answer",
        "stream_info_answer",
        "stream_review_answer",
        "generate_compare_summary",
        "judge_travel_modes",
        "generate_schedule_plan",
        "generate_schedule_fill",
    }
)
# `PLACE_REASON_MODEL_NAME`이라는 **세 번째 스위치**를 탄다. 두 티어 어디에도 넣지 않는다.
_PLACE_REASON_OPERATIONS = frozenset({"generate_place_reason"})


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    title: str
    turns: tuple[str, ...]
    expected_turn_intents: tuple[str, ...]
    expected_final_conditions: dict[str, Any]
    device_location: str | None
    note: str


@dataclass(frozen=True)
class CaseResult:
    case: EvaluationCase
    actual_turn_intents: tuple[str, ...]
    actual_final_conditions: dict[str, Any]
    intent_matches: tuple[bool, ...]
    condition_matches: dict[str, bool]
    client_elapsed_ms: float
    server_elapsed_ms: float | None
    # 턴마다 하나씩, 순서대로. 관측이 꺼져 있으면 빈 튜플이다.
    # 케이스 하나가 trace 여러 개에 대응하므로 단수가 아니다.
    langfuse_trace_ids: tuple[str, ...] = ()
    # 티어 → 이 케이스에서 실제로 답한 모델들. 폴백이 걸리면 둘 이상이 들어온다.
    served_models: dict[str, tuple[str, ...]] = dataclass_field(default_factory=dict)
    tokens: dict[str, int] = dataclass_field(default_factory=dict)
    error: str = ""

    @property
    def passed(self) -> bool:
        return not self.error and all(self.intent_matches) and all(self.condition_matches.values())


def _parse_json(value: str, *, column: str, case_id: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{case_id}의 {column} JSON이 올바르지 않습니다: {exc.msg}") from exc


def load_cases(split: Literal["dev", "final"]) -> list[EvaluationCase]:
    """CSV 골드셋을 읽고 기본 계약(턴 수·정답 Intent 수)을 검증한다."""

    path = DATASET_PATHS[split]
    if not path.exists():
        raise FileNotFoundError(f"평가셋을 찾을 수 없습니다: {path}")

    cases: list[EvaluationCase] = []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            case_id = (row.get("case_id") or "").strip()
            if not case_id:
                raise ValueError(f"{path}에 case_id가 비어 있는 행이 있습니다.")
            turns = _parse_json(row.get("turns") or "[]", column="turns", case_id=case_id)
            intents = _parse_json(
                row.get("expected_turn_intents") or "[]",
                column="expected_turn_intents",
                case_id=case_id,
            )
            conditions = _parse_json(
                row.get("expected_final_conditions") or "{}",
                column="expected_final_conditions",
                case_id=case_id,
            )
            if not isinstance(turns, list) or not all(
                isinstance(turn, str) and turn for turn in turns
            ):
                raise ValueError(f"{case_id}의 turns는 비어 있지 않은 문자열 배열이어야 합니다.")
            if not isinstance(intents, list) or not all(
                isinstance(intent, str) for intent in intents
            ):
                raise ValueError(f"{case_id}의 expected_turn_intents는 문자열 배열이어야 합니다.")
            if len(turns) != len(intents):
                raise ValueError(
                    f"{case_id}: turns {len(turns)}개와 expected_turn_intents "
                    f"{len(intents)}개가 다릅니다."
                )
            if not isinstance(conditions, dict):
                raise ValueError(f"{case_id}의 expected_final_conditions는 JSON 객체여야 합니다.")

            cases.append(
                EvaluationCase(
                    case_id=case_id,
                    title=(row.get("title") or case_id).strip(),
                    turns=tuple(turns),
                    expected_turn_intents=tuple(intents),
                    expected_final_conditions=conditions,
                    device_location=(row.get("device_location") or "").strip() or None,
                    note=(row.get("note") or "").strip(),
                )
            )
    return cases


def dataset_digest(cases: list[EvaluationCase]) -> str:
    """동일 골드셋끼리만 전 실행과 비교하도록 안정적인 해시를 만든다."""

    payload = [
        {
            "case_id": case.case_id,
            "turns": case.turns,
            "expected_turn_intents": case.expected_turn_intents,
            "expected_final_conditions": case.expected_final_conditions,
        }
        for case in cases
    ]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()[:12]


def _intent(body: dict[str, Any]) -> str:
    output = body.get("llm_output")
    return str(output.get("intent", "")) if isinstance(output, dict) else ""


def _conditions(body: dict[str, Any]) -> dict[str, Any]:
    state = body.get("state")
    if not isinstance(state, dict):
        return {}
    conditions = state.get("user_conditions")
    return conditions if isinstance(conditions, dict) else {}


def _session_id(body: dict[str, Any]) -> str | None:
    state = body.get("state")
    if not isinstance(state, dict):
        return None
    value = state.get("session_id")
    return value if isinstance(value, str) and value else None


def _langfuse_trace_id(body: dict[str, Any]) -> str | None:
    """이 턴을 기록한 Langfuse trace의 id. 관측이 꺼져 있으면 `None`이다.

    **session_id로 대신하지 않는다.** 세션은 LLM 단계 뒤에 발급돼서 첫 턴 trace에는
    session_id가 안 붙는다. 골드셋은 dev 35건 중 20건이 1턴짜리라, session_id
    역조회로는 그 20건이 통째로 빠진다.
    """
    value = body.get("langfuse_trace_id")
    return value if isinstance(value, str) and value else None


def _served_models(body: dict[str, Any]) -> dict[str, set[str]]:
    """이 턴에서 **실제로 답한** 모델을 티어별로 모은다.

    `.env`에 적어 둔 값이 아니라 응답이 말하는 값을 읽는다 — 서버를 재기동하지 않아
    옛 모델이 그대로 돌고 있는 것이 이 비교에서 가장 흔한 사고이고, 설정 파일을 읽는
    방식으로는 그것을 잡을 수 없다. 폴백이 걸려 2순위가 답한 경우도 여기 드러난다.
    """

    tiers: dict[str, set[str]] = {"fast": set(), "generation": set(), "place_reason": set()}
    execution = body.get("llm_execution")
    if not isinstance(execution, dict):
        return tiers
    for call in execution.get("calls") or []:
        if not isinstance(call, dict):
            continue
        served = call.get("served_model")
        operation = call.get("operation")
        if not isinstance(served, str) or not served:
            continue
        if operation in _FAST_OPERATIONS:
            tiers["fast"].add(served)
        elif operation in _GENERATION_OPERATIONS:
            tiers["generation"].add(served)
        elif operation in _PLACE_REASON_OPERATIONS:
            tiers["place_reason"].add(served)
    return tiers


def _call_tokens(body: dict[str, Any]) -> dict[str, int]:
    """이 턴의 토큰 합계. **사고 토큰을 따로 센다.**

    사고 토큰은 과금 대상인데 `output_tokens`에 안 잡혀서, 안 세면 새 모델의 비용을
    과소 집계한다. 지금까지 GENERATION 구간 비용은 프롬프트 크기로 계산한 추정치뿐이라
    (`test_results/model_tier_2026-09-08/비용과_시간.md` §3) 실측이 없었다 — 이 열이
    그 자리를 메운다. 실 API 호출은 늘지 않는다, 이미 받아 온 응답에서 읽을 뿐이다.
    """

    totals: dict[str, int] = {}
    execution = body.get("llm_execution")
    if not isinstance(execution, dict):
        return totals
    for call in execution.get("calls") or []:
        if not isinstance(call, dict):
            continue
        for key in ("input_tokens", "output_tokens", "thoughts_tokens", "cached_tokens"):
            value = call.get(key)
            if isinstance(value, int):
                totals[key] = totals.get(key, 0) + value
    return totals


def _server_elapsed_ms(body: dict[str, Any]) -> float | None:
    recommendations = body.get("recommendations")
    if isinstance(recommendations, dict) and isinstance(
        recommendations.get("elapsed_ms"), (int, float)
    ):
        return float(recommendations["elapsed_ms"])
    schedule = body.get("schedule")
    if isinstance(schedule, dict) and isinstance(schedule.get("elapsed_ms"), (int, float)):
        return float(schedule["elapsed_ms"])
    return None


def _post(
    client: httpx.Client, base_url: str, payload: dict[str, Any]
) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    response = client.post(f"{base_url}/api/chat", json=payload)
    elapsed_ms = (time.perf_counter() - started) * 1000
    if response.is_error:
        try:
            detail: Any = response.json()
        except ValueError:
            detail = response.text[:500]
        raise RuntimeError(f"HTTP {response.status_code}: {detail}")
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError("/api/chat 응답이 JSON 객체가 아닙니다.")
    return body, elapsed_ms


def _guard_expected_models(result: CaseResult, args: argparse.Namespace) -> None:
    """실제로 답한 모델이 기대와 다르면 즉시 중단한다.

    **모델은 이 스크립트가 못 정한다.** 서버가 시작할 때 `.env`로 고정하고
    `/api/chat`에는 오버라이드가 없다 — 그래서 `--fast-model` 같은 플래그를 두면
    "지정했으니 그 모델이겠지"라는 거짓 확신만 준다. 대신 응답이 말하는
    `served_model`을 기대값과 대조한다.

    관측이 꺼져 있으면(`llm_execution`이 없으면) 확인할 방법이 없으므로 통과시킨다 —
    없는 정보로 실행을 막지는 않는다.
    """

    for tier, expected in (
        ("fast", args.expect_fast_model),
        ("generation", args.expect_generation_model),
    ):
        if not expected:
            continue
        served = set(result.served_models.get(tier, ()))
        if not served:
            continue
        if served != {expected}:
            raise SystemExit(
                f"\n{tier.upper()} 티어 모델이 기대와 다릅니다 — 중단합니다.\n"
                f"  기대: {expected}\n"
                f"  실제: {', '.join(sorted(served))}\n"
                f"  ({result.case.case_id}에서 확인)\n\n"
                "서버가 시작할 때 .env로 모델을 고정하므로, .env를 고치고 "
                "백엔드를 재기동해야 반영됩니다."
            )


def evaluate_case(client: httpx.Client, case: EvaluationCase, base_url: str) -> CaseResult:
    """한 케이스의 턴을 같은 session_id로 순서대로 실행한다."""

    started = time.perf_counter()
    session_id: str | None = None
    response: dict[str, Any] | None = None
    actual_intents: list[str] = []
    trace_ids: list[str] = []
    # 케이스 전체에서 티어별로 답한 모델을 모은다. 턴마다 다를 수 있다(폴백).
    served: dict[str, set[str]] = {"fast": set(), "generation": set(), "place_reason": set()}
    tokens: dict[str, int] = {}
    try:
        for turn_index, user_input in enumerate(case.turns):
            payload: dict[str, Any] = {"user_input": user_input, "session_id": session_id}
            # 위치가 필요한 추천·일정 케이스도 재현 가능하게 첫 턴에만 고정 좌표를 넣는다.
            if turn_index == 0 and case.device_location:
                payload["device_location"] = case.device_location
            response, _ = _post(client, base_url, payload)
            for tier, models in _served_models(response).items():
                served[tier] |= models
            for token_key, token_value in _call_tokens(response).items():
                tokens[token_key] = tokens.get(token_key, 0) + token_value
            actual_intents.append(_intent(response))
            turn_trace_id = _langfuse_trace_id(response)
            if turn_trace_id is not None:
                trace_ids.append(turn_trace_id)
            session_id = _session_id(response)
            if session_id is None:
                raise ValueError(f"{turn_index + 1}턴 응답에 session_id가 없습니다.")

        assert response is not None
        final_conditions = _conditions(response)
        condition_matches = {
            field: final_conditions.get(field) == expected
            for field, expected in case.expected_final_conditions.items()
        }
        return CaseResult(
            case=case,
            actual_turn_intents=tuple(actual_intents),
            actual_final_conditions=final_conditions,
            intent_matches=tuple(
                actual == expected
                for actual, expected in zip(actual_intents, case.expected_turn_intents, strict=True)
            ),
            condition_matches=condition_matches,
            client_elapsed_ms=(time.perf_counter() - started) * 1000,
            server_elapsed_ms=_server_elapsed_ms(response),
            langfuse_trace_ids=tuple(trace_ids),
            served_models={tier: tuple(sorted(models)) for tier, models in served.items()},
            tokens=tokens,
        )
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        return CaseResult(
            case=case,
            actual_turn_intents=tuple(actual_intents),
            actual_final_conditions=_conditions(response) if response else {},
            intent_matches=tuple(False for _ in case.expected_turn_intents),
            condition_matches={field: False for field in case.expected_final_conditions},
            client_elapsed_ms=(time.perf_counter() - started) * 1000,
            server_elapsed_ms=_server_elapsed_ms(response) if response else None,
            # 실패한 케이스도 지금까지 열린 trace는 남는다 — 어디서 터졌는지
            # 보려면 오히려 이쪽이 필요하다.
            langfuse_trace_ids=tuple(trace_ids),
            error=str(exc),
        )


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def intent_metrics(results: list[CaseResult]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """모든 턴을 표본으로 Accuracy·Intent별 P/R/F1·Macro F1을 계산한다."""

    pairs = [
        (expected, actual)
        for result in results
        for expected, actual in zip(
            result.case.expected_turn_intents,
            result.actual_turn_intents
            + ("__ERROR__",)
            * (len(result.case.expected_turn_intents) - len(result.actual_turn_intents)),
            strict=True,
        )
    ]
    labels = sorted({expected for expected, _ in pairs} | {actual for _, actual in pairs})
    per_intent: list[dict[str, Any]] = []
    for label in labels:
        true_positive = sum(expected == label and actual == label for expected, actual in pairs)
        false_positive = sum(expected != label and actual == label for expected, actual in pairs)
        false_negative = sum(expected == label and actual != label for expected, actual in pairs)
        precision = _ratio(true_positive, true_positive + false_positive)
        recall = _ratio(true_positive, true_positive + false_negative)
        f1 = _ratio(2 * precision * recall, precision + recall)
        per_intent.append(
            {
                "intent": label,
                "support": sum(expected == label for expected, _ in pairs),
                "precision": precision,
                "recall": recall,
                "f1": f1,
            }
        )
    expected_labels = {expected for expected, _ in pairs}
    expected_metrics = [row for row in per_intent if row["intent"] in expected_labels]
    metric = {
        "turn_count": len(pairs),
        "intent_accuracy": _ratio(
            sum(expected == actual for expected, actual in pairs), len(pairs)
        ),
        "macro_precision": statistics.fmean(row["precision"] for row in expected_metrics)
        if expected_metrics
        else 0.0,
        "macro_recall": statistics.fmean(row["recall"] for row in expected_metrics)
        if expected_metrics
        else 0.0,
        "macro_f1": statistics.fmean(row["f1"] for row in expected_metrics)
        if expected_metrics
        else 0.0,
    }
    return metric, per_intent


def build_summary(results: list[CaseResult]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Intent와 조건·다중턴·지연시간 지표를 하나의 실행 요약으로 만든다."""

    intents, per_intent = intent_metrics(results)
    checks = [matched for result in results for matched in result.condition_matches.values()]
    exact_condition_cases = [result for result in results if result.case.expected_final_conditions]
    multi_turn = [result for result in results if len(result.case.turns) > 1]
    field_scores: dict[str, list[bool]] = {}
    for result in results:
        for field, matched in result.condition_matches.items():
            field_scores.setdefault(field, []).append(matched)
    elapsed = [result.client_elapsed_ms for result in results]
    summary = {
        **intents,
        "case_count": len(results),
        "case_pass_rate": _ratio(sum(result.passed for result in results), len(results)),
        "condition_field_accuracy": _ratio(sum(checks), len(checks)),
        "condition_exact_match_rate": _ratio(
            sum(all(result.condition_matches.values()) for result in exact_condition_cases),
            len(exact_condition_cases),
        ),
        "multi_turn_case_pass_rate": _ratio(
            sum(result.passed for result in multi_turn), len(multi_turn)
        ),
        "error_count": sum(bool(result.error) for result in results),
        "client_latency_p50_ms": percentile(elapsed, 0.50),
        "client_latency_p95_ms": percentile(elapsed, 0.95),
        "condition_accuracy_by_field": {
            field: _ratio(sum(values), len(values))
            for field, values in sorted(field_scores.items())
        },
        **_served_model_summary(results),
        **_token_summary(results),
    }
    return summary, per_intent


def _served_model_summary(results: list[CaseResult]) -> dict[str, Any]:
    """티어별로 실제 답한 모델을 한 줄로 만든다. 둘 이상이면 `+`로 잇는다.

    값이 둘 이상이라는 것은 **폴백이 걸렸다**는 뜻이고, 그 실행의 점수는 한 모델의
    점수가 아니다 — 모델 비교에 쓰기 전에 이 칸을 먼저 봐야 한다.
    """

    merged: dict[str, set[str]] = {}
    for result in results:
        for tier, models in result.served_models.items():
            merged.setdefault(tier, set()).update(models)
    return {
        f"{tier}_models_served": "+".join(sorted(models)) if models else ""
        for tier, models in merged.items()
    }


def _token_summary(results: list[CaseResult]) -> dict[str, Any]:
    """실행 전체의 토큰 합계. 비용을 추정이 아니라 실측으로 내기 위한 것이다."""

    totals: dict[str, int] = {}
    for result in results:
        for key, value in result.tokens.items():
            totals[key] = totals.get(key, 0) + value
    return {f"total_{key}": value for key, value in sorted(totals.items())}


def percentile(values: list[float], quantile: float) -> float:
    """외부 의존성 없이 선형 보간 p50/p95를 계산한다."""

    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * quantile
    lower, upper = math.floor(index), math.ceil(index)
    if lower == upper:
        return round(ordered[lower], 2)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower), 2)


def confusion_rows(results: list[CaseResult]) -> tuple[list[str], list[dict[str, int]]]:
    pairs = [
        (expected, actual)
        for result in results
        for expected, actual in zip(
            result.case.expected_turn_intents,
            result.actual_turn_intents
            + ("__ERROR__",)
            * (len(result.case.expected_turn_intents) - len(result.actual_turn_intents)),
            strict=True,
        )
    ]
    labels = sorted({value for pair in pairs for value in pair})
    matrix = Counter(pairs)
    return labels, [
        {"expected": expected, **{actual: matrix[expected, actual] for actual in labels}}
        for expected in labels
    ]


def _write_csv(path: Path, header: list[str], rows: list[list[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        writer.writerows(rows)


def write_run(
    *,
    run_dir: Path,
    results: list[CaseResult],
    summary: dict[str, Any],
    per_intent: list[dict[str, Any]],
) -> None:
    """상세·혼동행렬·요약을 한 실행 폴더에 분리 저장한다."""

    _write_csv(
        run_dir / "case_results.csv",
        [
            "case_id",
            "title",
            "turns",
            "expected_turn_intents",
            "actual_turn_intents",
            "intent_match",
            "expected_final_conditions",
            "actual_final_conditions",
            "condition_matches",
            "case_pass",
            "client_elapsed_ms",
            "server_elapsed_ms",
            "langfuse_trace_ids",
            "note",
            "error",
        ],
        [
            [
                result.case.case_id,
                result.case.title,
                json.dumps(result.case.turns, ensure_ascii=False),
                json.dumps(result.case.expected_turn_intents, ensure_ascii=False),
                json.dumps(result.actual_turn_intents, ensure_ascii=False),
                json.dumps(result.intent_matches, ensure_ascii=False),
                json.dumps(result.case.expected_final_conditions, ensure_ascii=False),
                json.dumps(result.actual_final_conditions, ensure_ascii=False),
                json.dumps(result.condition_matches, ensure_ascii=False),
                result.passed,
                round(result.client_elapsed_ms, 2),
                result.server_elapsed_ms,
                json.dumps(result.langfuse_trace_ids, ensure_ascii=False),
                result.case.note,
                result.error,
            ]
            for result in results
        ],
    )
    _write_csv(
        run_dir / "intent_metrics.csv",
        ["intent", "support", "precision", "recall", "f1"],
        [
            [
                row["intent"],
                row["support"],
                round(row["precision"], 4),
                round(row["recall"], 4),
                round(row["f1"], 4),
            ]
            for row in per_intent
        ],
    )
    labels, matrix_rows = confusion_rows(results)
    _write_csv(
        run_dir / "confusion_matrix.csv",
        ["expected\\actual", *labels],
        [[row["expected"], *[row[label] for label in labels]] for row in matrix_rows],
    )
    with (run_dir / "summary.json").open("w", encoding="utf-8") as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=2)

    write_markdown_report(
        run_dir=run_dir,
        results=results,
        summary=summary,
        per_intent=per_intent,
        labels=labels,
        matrix_rows=matrix_rows,
    )


def _percent(value: float) -> str:
    return f"{value:.1%}"


def write_markdown_report(
    *,
    run_dir: Path,
    results: list[CaseResult],
    summary: dict[str, Any],
    per_intent: list[dict[str, Any]],
    labels: list[str],
    matrix_rows: list[dict[str, int]],
) -> None:
    """JSON/CSV 수치를 발표·리뷰에서 바로 읽을 수 있는 Markdown으로 요약한다."""

    lines = [
        "# Agent 품질 평가 결과",
        "",
        f"- 실행 ID: `{summary['run_id']}`",
        f"- 실행 시각: {summary['created_at']}",
        f"- 프롬프트 기준선: `{summary.get('prompt_variant', 'current')}`",
        f"- 프롬프트 버전: `{summary.get('extract_prompt_version', '?')}` · "
        f"`{summary.get('classify_prompt_version', '?')}` · "
        f"base `{summary.get('base_prompt_version', '?')}`"
        + (
            " — ⚠️ 레포 값이다. `LANGFUSE_PROMPTS_ENABLED=true`라 서버가 "
            "Langfuse의 다른 버전으로 돌았을 수 있다"
            if summary.get("prompt_source") == "langfuse"
            else ""
        ),
        f"- 평가셋: `{summary['split']}` · {summary['case_count']}건 / {summary['turn_count']}턴",
        f"- 골드셋 해시: `{summary['dataset_digest']}`",
        "",
        "## 핵심 결과",
        "",
        "| 지표 | 결과 | 의미 |",
        "| --- | ---: | --- |",
        (
            f"| Intent Accuracy | {_percent(summary['intent_accuracy'])} | "
            "전체 턴에서 Intent가 일치한 비율 |"
        ),
        (
            f"| Intent Macro F1 | {summary['macro_f1']:.3f} | "
            "Intent별 F1을 동등하게 평균낸 균형 점수 |"
        ),
        (
            f"| 조건 필드 정확도 | {_percent(summary['condition_field_accuracy'])} | "
            "기대 조건 필드 하나하나가 일치한 비율 |"
        ),
        (
            f"| 최종 조건 완전 일치율 | {_percent(summary['condition_exact_match_rate'])} | "
            "조건을 기대한 케이스에서 모든 필드가 맞은 비율 |"
        ),
        (
            f"| 멀티턴 통과율 | {_percent(summary['multi_turn_case_pass_rate'])} | "
            "2턴 이상 케이스가 Intent·조건을 모두 통과한 비율 |"
        ),
        (
            f"| 전체 케이스 통과율 | {_percent(summary['case_pass_rate'])} | "
            "케이스 단위로 모든 검증을 통과한 비율 |"
        ),
        f"| API 오류 | {summary['error_count']}건 | HTTP/Provider 오류로 평가하지 못한 케이스 수 |",
        "",
        "## 실행 성능",
        "",
        "| 지표 | 결과 |",
        "| --- | ---: |",
        f"| 클라이언트 지연시간 p50 | {summary['client_latency_p50_ms'] / 1000:.2f}초 |",
        f"| 클라이언트 지연시간 p95 | {summary['client_latency_p95_ms'] / 1000:.2f}초 |",
        "",
        "## Intent별 Precision / Recall / F1",
        "",
        "| Intent | 표본 수 | Precision | Recall | F1 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    lines.extend(
        f"| {row['intent']} | {row['support']} | {_percent(row['precision'])} | "
        f"{_percent(row['recall'])} | {row['f1']:.3f} |"
        for row in per_intent
    )

    lines.extend(
        [
            "",
            "## 혼동행렬",
            "",
            "행은 **기대 Intent**, 열은 **실제 Intent**입니다. 대각선 값은 정분류이고, "
            "대각선 밖 값은 어떤 Intent끼리 혼동했는지 보여줍니다.",
            "",
            "| 기대 \\ 실제 | " + " | ".join(labels) + " |",
            "| --- | " + " | ".join("---:" for _ in labels) + " |",
        ]
    )
    lines.extend(
        f"| {row['expected']} | " + " | ".join(str(row[label]) for label in labels) + " |"
        for row in matrix_rows
    )

    lines.extend(["", "## 조건 필드별 정확도", "", "| 필드 | 정확도 |", "| --- | ---: |"])
    lines.extend(
        f"| {field} | {_percent(score)} |"
        for field, score in summary["condition_accuracy_by_field"].items()
    )

    failed = [result for result in results if not result.passed]
    lines.extend(["", "## 불일치·오류 케이스", ""])
    if not failed:
        lines.append("모든 케이스가 Intent와 기대 조건을 통과했습니다.")
    else:
        for result in failed:
            mismatches = [
                f"`{field}` 기대 `{result.case.expected_final_conditions[field]!r}` / "
                f"실제 `{result.actual_final_conditions.get(field)!r}`"
                for field, matched in result.condition_matches.items()
                if not matched
            ]
            lines.extend(
                [
                    f"### {result.case.case_id} — {result.case.title}",
                    "",
                    f"- 기대 Intent: `{', '.join(result.case.expected_turn_intents)}`",
                    f"- 실제 Intent: `{', '.join(result.actual_turn_intents) or '__ERROR__'}`",
                    f"- 조건 불일치: {', '.join(mismatches) if mismatches else '없음'}",
                    f"- 오류: {result.error or '없음'}",
                    "",
                ]
            )

    if summary.get("previous_run_id"):
        lines.extend(
            [
                "## 직전 동일 골드셋 대비",
                "",
                f"비교 대상: `{summary['previous_run_id']}`",
                "",
            ]
        )
        for metric, delta in summary.get("delta_from_previous", {}).items():
            lines.append(f"- {metric}: {delta:+.4f}")

    lines.extend(
        [
            "",
            "## 원본 파일",
            "",
            "- `summary.json`: 기계 처리용 전체 요약",
            "- `case_results.csv`: 케이스별 기대값·실제값·조건 비교",
            "- `intent_metrics.csv`: Intent별 Precision / Recall / F1",
            "- `confusion_matrix.csv`: 혼동행렬 원본",
        ]
    )
    (run_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _prompt_versions() -> dict[str, str]:
    """이 회차가 어느 프롬프트로 돈 것인지 기록할 값을 모은다.

    **왜 필요한가**: 지금까지 남는 건 골드셋 해시(`dataset_digest`)뿐이라, 같은
    골드셋을 다른 프롬프트로 잰 회차들이 `history.csv`에서 구별되지 않았다.
    2026-08-28에 실제로 문제가 됐다 — `recommend.extract` 2.5.0이 D의 문안에서
    B의 환산표로 통째로 바뀌었는데, 그 전에 잰 4개 회차는 파일상 지금과 같은
    조건처럼 보인다.

    ⚠️ **`prompt_source`를 반드시 함께 읽어야 한다.** 여기 적히는 세 버전은
    **레포의 값**이다. `LANGFUSE_PROMPTS_ENABLED=true`면 서버는 Langfuse를 먼저
    읽으므로(`app/prompts/loader.py`) 실제로 돈 프롬프트가 이 값과 다를 수 있다 —
    디스크만 고치고 `sync_langfuse_prompts --push`를 안 한 상태가 그렇다.
    그때는 `prompt_source=langfuse`가 찍히므로 값을 그대로 믿지 말라는 표시가 된다.
    서버가 자기 버전을 응답에 실어주면 이 한계는 없어진다(미구현 — 라우트 계약 변경).
    """

    return {
        "extract_prompt_version": operation_prompt_version("extract_recommend_conditions") or "",
        "classify_prompt_version": operation_prompt_version("classify_intent") or "",
        "base_prompt_version": PROMPT_VERSION,
        "prompt_source": "langfuse" if settings.langfuse_prompts_enabled else "repo",
    }


def _read_history() -> list[dict[str, str]]:
    if not HISTORY_PATH.exists():
        return []
    with HISTORY_PATH.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _migrate_history_header(header: list[str]) -> None:
    """열이 늘어난 헤더로 기존 `history.csv`를 옮긴다.

    `csv.DictWriter`는 파일이 이미 있으면 헤더를 다시 쓰지 않는다. 그래서 열을
    추가하면 **헤더는 옛 열 수, 새 행은 새 열 수**가 되어 파일이 조용히 깨진다.
    옛 행의 새 열은 빈 값으로 둔다 — 그 회차가 어느 프롬프트로 돈 것인지는 이제
    복원할 수 없으므로 빈 값이 정직한 표기다.
    """

    if not HISTORY_PATH.exists():
        return
    with HISTORY_PATH.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames == header:
            return
        rows = list(reader)
    with HISTORY_PATH.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in header})


def append_history(summary: dict[str, Any]) -> dict[str, str] | None:
    """동일 split·골드셋 해시의 직전 실행과 비교할 수 있도록 요약을 누적한다."""

    previous = next(
        (
            row
            for row in reversed(_read_history())
            if row.get("split") == summary["split"]
            and row.get("dataset_digest") == summary["dataset_digest"]
            and row.get("prompt_variant", "current") == summary["prompt_variant"]
        ),
        None,
    )
    header = [
        "run_id",
        "created_at",
        "split",
        "dataset_digest",
        "prompt_variant",
        "extract_prompt_version",
        "classify_prompt_version",
        "base_prompt_version",
        "prompt_source",
        "case_count",
        "turn_count",
        "intent_accuracy",
        "macro_precision",
        "macro_recall",
        "macro_f1",
        "condition_field_accuracy",
        "condition_exact_match_rate",
        "multi_turn_case_pass_rate",
        "case_pass_rate",
        "error_count",
        "client_latency_p50_ms",
        "client_latency_p95_ms",
        # 어느 모델의 점수인지 이력에 남는다. 이 열이 없으면 몇 달 뒤에 history.csv를
        # 보고 "이 행은 어떤 모델이었나"를 .env 이력으로 역추적해야 한다.
        "fast_models_served",
        "generation_models_served",
        "place_reason_models_served",
        "total_input_tokens",
        "total_output_tokens",
        "total_thoughts_tokens",
        "total_cached_tokens",
    ]
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _migrate_history_header(header)
    exists = HISTORY_PATH.exists()
    with HISTORY_PATH.open("a", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=header)
        if not exists:
            writer.writeheader()
        writer.writerow({key: summary.get(key, "") for key in header})
    return previous


def _previous_delta(summary: dict[str, Any], previous: dict[str, str] | None) -> dict[str, float]:
    if previous is None:
        return {}
    delta: dict[str, float] = {}
    for key in ("intent_accuracy", "macro_f1", "condition_field_accuracy", "case_pass_rate"):
        try:
            delta[key] = round(float(summary[key]) - float(previous[key]), 4)
        except (KeyError, TypeError, ValueError):
            continue
    return delta


def _check_server(client: httpx.Client, base_url: str) -> None:
    for path in ("/health", "/api/health"):
        try:
            response = client.get(f"{base_url}{path}", timeout=5.0)
            if response.is_success:
                return
        except httpx.HTTPError:
            continue
    raise RuntimeError(f"서버({base_url})에 연결할 수 없습니다. backend 서버를 먼저 실행해주세요.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TripBranch Agent 골드셋 평가")
    parser.add_argument("--split", choices=("dev", "final", "all"), default="dev")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--prompt-variant",
        default="current",
        help=(
            "평가 중인 서버의 TRIPBRANCH_PROMPT_VARIANT 값. 서버 설정을 바꾸지는 않고 "
            "결과 식별·비교에만 사용한다."
        ),
    )
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument("--interval-seconds", type=float, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--limit", type=int, default=None, help="점검용으로 앞 N개 케이스만 실행")
    parser.add_argument(
        "--expect-fast-model",
        default=None,
        help="FAST 티어가 이 모델이 아니면 첫 케이스에서 중단한다"
        " (모델을 바꾸는 것이 아니라 대조하는 것 — 모델은 서버 .env가 정한다)",
    )
    parser.add_argument(
        "--expect-generation-model",
        default=None,
        help="GENERATION 티어가 이 모델이 아니면 첫 케이스에서 중단한다."
        " **이 티어를 바꿔도 이 스크립트의 점수는 안 움직인다** —"
        " 채점 축(intent·user_conditions)이 둘 다 FAST 산출물이기 때문이다."
        " 답변 품질·이동수단 판정은 measure_mode_judge.py 등으로 따로 재야 한다",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="API를 호출하지 않고 CSV 계약·건수만 검증"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    splits: tuple[Literal["dev", "final"], ...] = (
        ("dev", "final") if args.split == "all" else (args.split,)
    )
    for split in splits:
        cases = load_cases(split)
        if args.limit is not None:
            cases = cases[: args.limit]
        digest = dataset_digest(cases)
        print(f"[{split}] 골드셋 {len(cases)}건 · digest={digest}")
        if args.dry_run:
            continue

        started_at = datetime.now().astimezone()
        variant_slug = re.sub(r"[^A-Za-z0-9._-]+", "-", args.prompt_variant).strip("-")
        run_id = (
            f"{started_at.strftime('%Y-%m-%d_%H%M')}_{split}_{variant_slug}_"
            f"{len(cases)}cases_{digest}"
        )
        run_dir = QUALITY_DIR / "runs" / run_id
        results: list[CaseResult] = []
        with httpx.Client(timeout=args.timeout_seconds) as client:
            _check_server(client, args.base_url.rstrip("/"))
            for index, case in enumerate(cases, start=1):
                result = evaluate_case(client, case, args.base_url.rstrip("/"))
                results.append(result)
                # **첫 케이스에서 모델을 확인하고 틀리면 즉시 멈춘다.** 서버를 재기동하지
                # 않아 옛 모델이 그대로 도는 것이 이 비교에서 가장 흔한 사고이고,
                # 끝까지 돌고 나서 알면 1시간과 그 실행을 통째로 버린다.
                _guard_expected_models(result, args)
                print(
                    f"[{split} {index:>2}/{len(cases)}] {'PASS' if result.passed else 'FAIL'} "
                    f"{case.case_id} · {result.client_elapsed_ms / 1000:.1f}s"
                )
                if result.error:
                    print(f"  오류: {result.error}")
                if index < len(cases):
                    time.sleep(args.interval_seconds)

        summary, per_intent = build_summary(results)
        summary.update(
            {
                "run_id": run_id,
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "split": split,
                "dataset_digest": digest,
                "prompt_variant": args.prompt_variant,
                **_prompt_versions(),
            }
        )
        previous = append_history(summary)
        summary["previous_run_id"] = previous.get("run_id") if previous else None
        summary["delta_from_previous"] = _previous_delta(summary, previous)
        write_run(run_dir=run_dir, results=results, summary=summary, per_intent=per_intent)
        print(
            f"  Intent Accuracy={summary['intent_accuracy']:.1%} · "
            f"Macro F1={summary['macro_f1']:.3f} · "
            f"조건 필드 정확도={summary['condition_field_accuracy']:.1%}"
        )
        if summary["delta_from_previous"]:
            print(f"  직전 동일 골드셋 대비: {summary['delta_from_previous']}")
        print(f"  결과: {run_dir}")


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"평가를 실행하지 못했습니다: {exc}", file=sys.stderr)
        sys.exit(1)
