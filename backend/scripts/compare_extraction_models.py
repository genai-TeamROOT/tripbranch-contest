"""2단계 조건 추출 결과가 모델에 따라 갈리는지 대조한다.

역할: 같은 발화·같은 컨텍스트를 여러 모델의 추출 메서드에 넣고, **무엇이 어떻게
갈리는지**를 필드 단위로 남긴다. 계획서는
`test_results/model_tier_2026-09-08/plan.md`.

**기대값으로 채점하지 않는다** — `UserConditions` 18필드의 정답 세트가 없고, 그
정의는 프롬프트 소유라 임의로 만들 수 없다. 대신 세 가지를 기대값 없이 판정한다.

1. **페이로드 유실** — 조건을 나르는 턴(RECOMMEND·SCHEDULE)에 `recommend`가 통째로
   없는 것. 이건 정답이 있는 실패다(D-126). 페이로드가 없으면 조건 병합이 조용히
   건너뛰어지고 조건 0개로 추천·편성이 돈다.
2. **흔들림** — 같은 모델·같은 발화를 `--repeat`회 돌려 값이 갈리는지. 흔들림이
   있으면 모델 간 차이가 모델 차이인지 분산인지 못 가른다.
3. **모델 간 차이** — `A ≠ B`는 기대값 없이 판정할 수 있다. 인텐트 분류 실험에서
   68건 중 13건만 변별력이 있었던 것과 같은 접근이다
   (`test_results/intent_experiments_2026-08.md` §7).

**null을 버리지 않는다.** 예전에는 `model_dump(exclude_none=True)`로 덤프해 null
필드를 아예 잃었다 — 그러면 두 모델의 차이가 "값이 다르다"인지 "한쪽이 비웠다"인지
구분되지 않는다. 우리가 알고 싶은 것은 정확히 후자라, 필드별로 채워진 것과 비워진
것을 따로 남긴다.

**토큰을 함께 남긴다.** 비용 판단의 근거다. `thoughts_tokens`를 빼먹지 않는다 —
과금 대상인데 `output_tokens`에 안 잡혀서, 안 세면 비용이 과소 집계된다
(`schemas.py::LLMCallMetadata` 주석). 단가를 곱하는 것은 이 스크립트가 하지 않고
집계 쪽에 맡긴다 — 단가는 바뀌고, 실행 시점 단가로 굳혀 두면 다시 계산할 수 없다.

**SCHEDULE을 건너뛰지 않는다.** 예전 주석은 "SCHEDULE은 별도 편성 프롬프트를
쓴다"였는데 **틀렸다.** `services/interpret/orchestrator.py`가 SCHEDULE에도
`extract_recommend_conditions()`를 그대로 쓰고 intent만 SCHEDULE로 바꿔치기한다.
그래서 조건 유실 문제의 핵심 케이스 7건(51~56·59)이 통째로 빠져 있었다.
OUT_OF_SCOPE만 제외한다 — 그쪽은 2단계 추출이 아예 없다.

**폴백을 쓰지 않는다.** 단일 모델로 넘겨 어느 모델의 답인지 확정한다. 그래도
`served_model`을 기록한다 — 다르게 나오면 그 회차는 비교에서 빼야 한다.

thinking 예산은 이 스크립트가 건드리지 않는다 — 코드가 그 호출에 지정한 값이 그대로
나간다. **측정 시점의 코드 상태에 따라 결과가 달라지므로 결과 문서에 함께 남긴다.**

입력: `--cases` CSV(기본 intent_cot_2026-08-11의 68건). `.env`에 LLM_API_KEY 필요.
출력: 표준 출력 + `<--out-dir>/compare_extraction_<tag>.json`(원자료)
      + `<--out-dir>/compare_extraction_<tag>.md`(문서에 붙일 표)
호출 시점: 모델 교체를 검토할 때 수동 실행한다(1회성 측정 도구, 실제 API 호출 비용
때문에 pytest 스위트에는 넣지 않는다).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import time
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from app.config import settings
from app.providers.gemini import RealGeminiProvider
from app.schemas import UserConditions
from app.services.runtime.llm_execution import (
    get_llm_execution_metadata,
    reset_llm_execution_metadata,
)

RESULTS_DIR = Path(__file__).resolve().parent.parent / "test_results"
DEFAULT_CASES = RESULTS_DIR / "intent_cot_2026-08-11" / "intent_classification_cases_2026-08-11.csv"

# MODIFY 추출은 "무엇을 바꾸는가"라서 기존 조건이 있어야 성립한다. 케이스 CSV에는 그
# 값이 없으므로 모든 MODIFY 케이스에 같은 기준 조건을 준다 — 두 모델이 동일한 입력을
# 받는 것만 보장하면 대조 목적에는 충분하다.
BASELINE_CONDITIONS = UserConditions(search_center="경복궁")

# 날짜 의존 추출(INFO의 visit_time)이 실행 시각에 따라 흔들리지 않게 고정한다.
REFERENCE_DATE = date(2026, 8, 12)

# 2단계 추출이 아예 없는 인텐트만 뺀다. SCHEDULE을 빼지 않는 이유는 머리말 참고.
SKIPPED_INTENTS = {"OUT_OF_SCOPE"}

# 조건을 `llm_output.recommend`로 나르는 인텐트. 이 둘에서 페이로드가 없으면
# 정답이 있는 실패다(D-126) — state_transform이 조건 병합을 통째로 건너뛴다.
CONDITION_BEARING = {"RECOMMEND", "SCHEDULE"}


def _split_null_fields(conditions: dict[str, Any]) -> tuple[list[str], list[str]]:
    """조건 dict를 (채워진 필드, 비워진 필드)로 가른다.

    계약 1.3절이 미설정을 null 또는 빈 배열로 표현하기로 정했으므로 그것을 따른다.
    빈 문자열도 미설정으로 센다 — 프롬프트가 `""` 금지를 명시하지만 모델이 낼 수 있다.
    """

    filled: list[str] = []
    empty: list[str] = []
    for name, value in sorted(conditions.items()):
        if value is None or value == [] or value == "":
            empty.append(name)
        else:
            filled.append(name)
    return filled, empty


def _observe_call() -> dict[str, Any]:
    """직전 호출의 실제 응답 모델·토큰·재시도를 읽는다.

    `served_model`이 요청한 모델과 다르면 폴백이 낀 것이다 — 그 회차는 모델 비교에
    쓸 수 없다. `thoughts_tokens`는 과금 대상인데 output에 안 잡히므로 따로 남긴다.
    """

    metadata = get_llm_execution_metadata()
    if metadata is None or not metadata.calls:
        return {}
    call = metadata.calls[-1]
    return {
        "응답모델": call.served_model,
        "재시도": call.retry_count,
        "입력토큰": call.input_tokens,
        "출력토큰": call.output_tokens,
        "사고토큰": call.thoughts_tokens,
        "합계토큰": call.total_tokens,
    }


async def extract_one(
    provider: RealGeminiProvider, case: dict[str, str]
) -> dict[str, Any]:
    """케이스의 기대 intent에 맞는 2단계 메서드를 한 번 호출한다."""

    intent = case["기대_intent"]
    text = case["입력문장"]
    shown = int(case["shown_place_count"])
    pending = case["pending_clarification"] or None

    reset_llm_execution_metadata()
    started = time.perf_counter()
    try:
        if intent in CONDITION_BEARING:
            # SCHEDULE도 같은 메서드를 탄다 — orchestrator가 intent만 바꿔치기한다.
            result = await provider.extract_recommend_conditions(text)
        elif intent == "MODIFY":
            result = await provider.extract_modify_conditions(
                text,
                BASELINE_CONDITIONS,
                pending_clarification=pending,
                shown_place_count=shown,
            )
        elif intent == "INFO":
            result = await provider.extract_info_query(
                text,
                has_previous_recommendation=case["has_previous_recommendation"] == "True",
                reference_date=REFERENCE_DATE,
            )
        elif intent == "COMPARE":
            result = await provider.extract_compare_request(text, shown_place_count=shown)
        elif intent == "GENERAL":
            result = await provider.extract_general_request(text)
        else:
            raise ValueError(f"처리 대상이 아닌 intent: {intent}")

        output = result.data
        # exclude_none을 쓰지 않는다 — null 필드가 이 측정의 관심사다.
        payload = output.model_dump(mode="json")
        record: dict[str, Any] = {
            "오류": None,
            "지연ms": round((time.perf_counter() - started) * 1000),
            "intent": output.intent.value,
            "status": output.status.value,
            "payload": payload,
        }
        # 조건 페이로드가 왔는지, 왔으면 어느 필드가 비었는지.
        conditions = None
        if output.recommend is not None:
            conditions = output.recommend.conditions.model_dump(mode="json")
        elif output.modify is not None:
            changes = getattr(output.modify, "condition_changes", None)
            if changes is not None:
                conditions = changes.model_dump(mode="json")
        record["페이로드있음"] = conditions is not None
        if conditions is not None:
            filled, empty = _split_null_fields(conditions)
            record["채워진필드"] = filled
            record["빈필드"] = empty
            record["채워진수"] = len(filled)
        else:
            record["채워진필드"] = []
            record["빈필드"] = []
            record["채워진수"] = 0
        record.update(_observe_call())
        return record
    except Exception as exc:  # noqa: BLE001 — 한 건이 실패해도 대조를 계속한다
        detail = getattr(exc, "details", None) or str(exc)
        record = {
            "오류": f"{type(exc).__name__}: {detail}",
            "지연ms": round((time.perf_counter() - started) * 1000),
            "payload": None,
            "페이로드있음": False,
            "채워진필드": [],
            "빈필드": [],
            "채워진수": 0,
        }
        record.update(_observe_call())
        return record

# 연속 실패 중단용 카운터. 설정 오류로 전 케이스를 끝까지 도는 것을 막는다
# (2026-09-08: 분류 측정에서 400 조합으로 204호출을 버렸다. RULES 함정 46).
_CONSEC = {"n": 0}


async def run_model(model: str, cases: list[dict[str, str]], args: argparse.Namespace) -> dict:
    """한 모델로 전 케이스를 --repeat회 돈다."""

    provider = RealGeminiProvider(
        api_key=settings.llm_api_key,
        fast_model_names=[model],
        generation_model_names=[model],
        timeout_seconds=args.timeout,
    )
    out: dict[str, list[dict[str, Any]]] = {}
    for i, case in enumerate(cases, 1):
        runs: list[dict[str, Any]] = []
        for _ in range(args.repeat):
            runs.append(await extract_one(provider, case))
            print("." if runs[-1]["오류"] is None else "!", end="", flush=True)
            if runs[-1]["오류"] is None:
                _CONSEC["n"] = 0
            else:
                _CONSEC["n"] += 1
                if _CONSEC["n"] >= args.max_consecutive_errors:
                    raise SystemExit(
                        f"\n연속 {_CONSEC['n']}건 실패로 중단합니다. "
                        f"마지막 오류: {runs[-1]['오류']}"
                    )
            await asyncio.sleep(args.delay)
        out[case["번호"]] = runs
        if i % 10 == 0:
            print(f" {i}/{len(cases)}", flush=True)
    print(flush=True)
    return out


def _stable(runs: list[dict[str, Any]], key: str) -> bool:
    """같은 모델 반복에서 그 값이 고정인가."""
    seen = {json.dumps(r.get(key), ensure_ascii=False, sort_keys=True) for r in runs}
    return len(seen) == 1


def _token_totals(results: dict[str, dict[str, list[dict]]], model: str) -> dict[str, int]:
    """모델별 토큰 합계. 단가를 곱하는 것은 집계 쪽에 맡긴다."""
    totals = Counter()
    calls = 0
    for runs in results[model].values():
        for r in runs:
            calls += 1
            for k in ("입력토큰", "출력토큰", "사고토큰"):
                v = r.get(k)
                if isinstance(v, int):
                    totals[k] += v
    return {"호출수": calls, **dict(totals)}


def _null_frequency(
    results: dict[str, dict[str, list[dict]]], model: str
) -> dict[str, tuple[int, int]]:
    """필드별로 (비워진 회차, 페이로드가 온 회차). 분모를 함께 남긴다."""
    empty = Counter()
    seen = Counter()
    for runs in results[model].values():
        for r in runs:
            if not r.get("페이로드있음"):
                continue
            for name in r["채워진필드"]:
                seen[name] += 1
            for name in r["빈필드"]:
                seen[name] += 1
                empty[name] += 1
    return {name: (empty[name], seen[name]) for name in sorted(seen)}


def _markdown_report(
    models: list[str],
    cases: list[dict[str, str]],
    results: dict[str, dict[str, list[dict]]],
    diffs: list[dict],
    args: argparse.Namespace,
) -> str:
    """문서에 그대로 붙일 표. 판정은 쓰지 않는다 — 사람이 한다."""

    lines: list[str] = []
    lines.append(f"# 조건 추출 모델 대조 — {args.tag or 'extraction'}")
    lines.append("")
    lines.append(f"- 케이스 {len(cases)}건 × 반복 {args.repeat}회 × 모델 {len(models)}개")
    lines.append(f"- 기준일(INFO visit_time 고정) `{REFERENCE_DATE}`")
    lines.append("- thinking 예산은 코드가 지정한 값 그대로다(이 스크립트가 안 건드린다)")
    lines.append("")

    lines.append("## 1. 모델별 요약")
    lines.append("")
    lines.append("| 모델 | 페이로드 유실 | 흔들린 케이스 | 오류 | 지연 중앙값 | 호출 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for m in models:
        lost = 0
        unstable = 0
        errors = 0
        latencies: list[int] = []
        for case in cases:
            runs = results[m][case["번호"]]
            latencies.extend(r["지연ms"] for r in runs)
            if any(r["오류"] for r in runs):
                errors += 1
            if case["기대_intent"] in CONDITION_BEARING and any(
                not r["페이로드있음"] and r["오류"] is None for r in runs
            ):
                lost += 1
            if not _stable(runs, "payload"):
                unstable += 1
        latencies.sort()
        median = latencies[len(latencies) // 2] if latencies else 0
        lines.append(
            f"| `{m}` | {lost}/{sum(1 for c in cases if c['기대_intent'] in CONDITION_BEARING)} "
            f"| {unstable}/{len(cases)} | {errors} | {median}ms "
            f"| {_token_totals(results, m)['호출수']} |"
        )
    lines.append("")
    lines.append("페이로드 유실의 분모는 조건을 나르는 턴(RECOMMEND·SCHEDULE)이다.")
    lines.append("")

    lines.append("## 2. 토큰 — 단가는 곱하지 않았다")
    lines.append("")
    lines.append("| 모델 | 호출 | 입력 | 출력 | 사고 | 호출당 입력 | 호출당 출력+사고 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for m in models:
        t = _token_totals(results, m)
        calls = max(t["호출수"], 1)
        inp = t.get("입력토큰", 0)
        outp = t.get("출력토큰", 0)
        thought = t.get("사고토큰", 0)
        lines.append(
            f"| `{m}` | {t['호출수']} | {inp:,} | {outp:,} | {thought:,} "
            f"| {inp // calls:,} | {(outp + thought) // calls:,} |"
        )
    lines.append("")
    lines.append("**사고 토큰을 따로 세는 이유**는 과금 대상인데 출력 토큰에 안 잡히기")
    lines.append("때문이다 — 빼먹으면 비용이 과소 집계된다.")
    lines.append("")

    lines.append("## 3. 필드별 null 빈도")
    lines.append("")
    lines.append("분모는 **페이로드가 온 회차**다. 페이로드 자체가 없는 회차는 1절에서 따로 센다.")
    lines.append("")
    freqs = {m: _null_frequency(results, m) for m in models}
    names = sorted({n for f in freqs.values() for n in f})
    header = "| 필드 | " + " | ".join(f"`{m}`" for m in models) + " |"
    lines.append(header)
    lines.append("| --- | " + " | ".join("---" for _ in models) + " |")
    for name in names:
        cells = []
        for m in models:
            e, s = freqs[m].get(name, (0, 0))
            cells.append(f"{e}/{s}" if s else "—")
        lines.append(f"| `{name}` | " + " | ".join(cells) + " |")
    lines.append("")

    lines.append("## 4. 모델 간 답이 갈린 케이스")
    lines.append("")
    if not diffs:
        lines.append("없음.")
    else:
        lines.append(f"{len(diffs)}건.")
        lines.append("")
        for d in diffs:
            lines.append(f"### {d['번호']} \"{d['입력문장']}\" ({d['기대_intent']})")
            lines.append("")
            for m in models:
                runs = results[m][d["번호"]]
                first = runs[0]
                mark = "고정" if _stable(runs, "payload") else "**흔들림**"
                lines.append(
                    f"- `{m}` {mark} · 페이로드 "
                    f"{'있음' if first['페이로드있음'] else '**없음**'} · "
                    f"채워진 {first['채워진수']}개"
                )
                if first["빈필드"]:
                    lines.append(f"  - 빈 필드: {', '.join(f'`{n}`' for n in first['빈필드'])}")
            lines.append("")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", required=True, help="쉼표로 구분한 모델 2개 이상")
    parser.add_argument("--repeat", type=int, default=1,
                        help="케이스당 반복 횟수(기본 3). 1회 차이는 노이즈와 구분되지 않는다")
    parser.add_argument("--delay", type=float, default=1.0, help="호출 간 대기(초)")
    parser.add_argument("--timeout", type=float, default=30.0,
                        help="추출은 분류보다 길어 기본 10초로는 타임아웃이 섞인다")
    parser.add_argument("--only", default="", help="특정 번호만 (쉼표 구분)")
    parser.add_argument("--max-calls", type=int, default=150,
                        help="예정 호출 수가 이 값을 넘으면 실행을 거부한다")
    parser.add_argument("--max-consecutive-errors", type=int, default=3,
                        help="연속 실패가 이 횟수에 닿으면 중단한다")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--tag", default="")
    parser.add_argument("--out-dir", type=Path, default=RESULTS_DIR)
    args = parser.parse_args()

    if not settings.llm_api_key:
        raise SystemExit("LLM_API_KEY가 없습니다. backend/.env를 확인하세요.")
    if not args.cases.exists():
        raise SystemExit(f"케이스 파일이 없습니다: {args.cases}")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if len(models) < 2:
        raise SystemExit("--models에 모델 2개 이상이 필요합니다.")

    with args.cases.open(encoding="utf-8-sig") as fp:
        rows = list(csv.DictReader(fp))
    if args.only:
        wanted = {n.strip() for n in args.only.split(",")}
        rows = [r for r in rows if r["번호"] in wanted]
    cases = [r for r in rows if r["기대_intent"] not in SKIPPED_INTENTS]
    skipped = len(rows) - len(cases)
    if not cases:
        raise SystemExit("대조할 케이스가 없습니다.")

    bearing = sum(1 for c in cases if c["기대_intent"] in CONDITION_BEARING)
    print(
        f"대상 {len(cases)}건 (OUT_OF_SCOPE {skipped}건 제외, 그중 조건 나르는 턴 "
        f"{bearing}건) × 반복 {args.repeat}회 × 모델 {len(models)}개\n"
    )

    planned = len(models) * args.repeat * len(cases)
    print(f"예정 호출 {planned}건, 상한 {args.max_calls}건", flush=True)
    if planned > args.max_calls:
        raise SystemExit(
            f"예정 호출 {planned}건이 --max-calls({args.max_calls})를 넘습니다. "
            "먼저 --repeat 1로 쓸고 갈린 조합만 좁혀서 --repeat을 올리세요. "
            "통째로 돌려야 하면 --max-calls를 명시하세요."
        )
    results: dict[str, dict] = {}
    for model in models:
        print(f"[{model}]")
        results[model] = asyncio.run(run_model(model, cases, args))

    by_case = {c["번호"]: c for c in cases}
    diffs: list[dict] = []
    errors: list[str] = []
    fell_back: list[str] = []
    for number, case in by_case.items():
        runs_by_model = {m: results[m][number] for m in models}
        if any(r["오류"] for runs in runs_by_model.values() for r in runs):
            errors.append(number)
            continue
        for m, runs in runs_by_model.items():
            served = {r.get("응답모델") for r in runs}
            if served - {m, None}:
                fell_back.append(f"{number}({m}: {sorted(x for x in served if x)})")
        first = {
            m: json.dumps(runs[0]["payload"], ensure_ascii=False, sort_keys=True)
            for m, runs in runs_by_model.items()
        }
        if len(set(first.values())) > 1:
            diffs.append({
                "번호": number,
                "입력문장": case["입력문장"],
                "기대_intent": case["기대_intent"],
            })

    print("=" * 78)
    print(f"대조: {' vs '.join(models)}")
    for m in models:
        lost = [
            n for n, c in by_case.items()
            if c["기대_intent"] in CONDITION_BEARING
            and any(not r["페이로드있음"] and r["오류"] is None for r in results[m][n])
        ]
        unstable = [n for n in by_case if not _stable(results[m][n], "payload")]
        t = _token_totals(results, m)
        print(f"\n[{m}]")
        print(f"  페이로드 유실   {len(lost)}/{bearing}건  {lost if lost else ''}")
        print(f"  흔들린 케이스   {len(unstable)}/{len(by_case)}건")
        print(f"  토큰            입력 {t.get('입력토큰', 0):,} · 출력 "
              f"{t.get('출력토큰', 0):,} · 사고 {t.get('사고토큰', 0):,}")
    print(f"\n답이 갈린 케이스 {len(diffs)}건 / 오류 {len(errors)}건")
    if fell_back:
        print(f"\n⚠️  폴백이 낀 회차가 있다 — 그 케이스는 비교에서 빼야 한다:\n  {fell_back}")
    if errors:
        print(f"오류로 대조 못 한 케이스: {', '.join(errors)}")
    print("=" * 78)

    tag = args.tag or "extraction"
    out_json = args.out_dir / f"compare_extraction_{tag}.json"
    out_json.write_text(
        json.dumps(
            {
                "models": models,
                "reference_date": str(REFERENCE_DATE),
                "repeat": args.repeat,
                "대상": len(by_case),
                "조건_나르는_턴": bearing,
                "상이": len(diffs),
                "오류": errors,
                "폴백_낀_회차": fell_back,
                "diffs": diffs,
                "토큰합계": {m: _token_totals(results, m) for m in models},
                "필드별_null": {
                    m: {n: list(v) for n, v in _null_frequency(results, m).items()}
                    for m in models
                },
                "raw": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    out_md = args.out_dir / f"compare_extraction_{tag}.md"
    out_md.write_text(_markdown_report(models, cases, results, diffs, args), encoding="utf-8")
    print(f"원자료 저장: {out_json}")
    print(f"문서용 표 저장: {out_md}")


if __name__ == "__main__":
    main()
