"""구간 이동수단 판정을 기대표로 채점하고, 모델·추론 단계별로 비교한다.

역할: `judge_travel_modes()`를 34개 입력(거리표 3 × 조건 × 일정/추천)으로 반복 호출해
      정답이 확실한 칸만 채점한다. `measure_mode_judge.py`가 방향(전환이 늘었나)만
      보던 것을 칸 단위 판정으로 바꾼 것이다.

      기대값과 판정 방식의 근거는
      `test_results/mode_judge_thinking_2026-09-14/기대표.md`에 있다. 요약하면:

      - 조건 없음·맑음은 모든 칸이 거리 규칙(직선 850m 초과면 transit)과 같아야 한다
      - 조건이 있으면 850m 초과 칸은 transit이어야 한다("앞당길 뿐 미루지 않는다")
      - 비가 든 조건은 0.8km도 transit이어야 한다("12분보다 짧은 구간도 바꾸고")
      - 나머지 칸은 채점하지 않는다. 둘 다 허용되는 칸에 답을 박으면 모델이 아니라
        기대값을 정한 사람의 판단을 재게 된다
      - 850m 정각은 넣지 않는다. 답이 "넘으면"의 비교 연산자로만 갈리는 칸이다

추론 단계: `--level`로 모든 호출에 같은 thinking_level을 싣는다. 운영 코드는 모든
          호출이 thinking_budget=0(→ MINIMAL)이라, `_thinking_config_for`를 이 실행 동안만
          바꿔 끼운다. 바꿔 끼운 함수가 호출마다 실제로 불렸는지 세고, 안 불렸으면 그
          호출을 실패로 남긴다 — 설정이 조용히 안 먹는 측정을 막는다.

입력: `.env`의 LLM_API_KEY. `LANGFUSE_PROMPTS_ENABLED`는 꺼져 있어야 한다(기대표가 레포
      프롬프트 기준이다). 켜져 있으면 실행을 거부한다.
출력: `<out-dir>/judge_graded_<tag><모델>_<단계>.json`. 호출별 원자료와 요약. 렌더된
      지시문의 해시와 `meta.yaml` 버전을 함께 남겨, 어느 프롬프트로 쟀는지 파일만 보고
      알 수 있게 한다.
      `--report <dir>`는 그 폴더의 결과를 모아 판정·속도·토큰 표를 낸다.
호출 시점: 수동 실행. 실제 API를 부르므로 pytest 스위트에 넣지 않는다.

    cd backend
    python -m scripts.measure_mode_judge_graded --model gemini-3.5-flash --level minimal \\
        --repeat 3 --out-dir test_results/<폴더>
    python -m scripts.measure_mode_judge_graded --report test_results/<폴더>
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import statistics
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import yaml
from google.genai import types as genai_types

import app.providers.gemini as gemini_module
import scripts.verify_schedule_condition_extraction as extraction_script
from app.config import settings
from app.domain.schedule_travel import ModeJudgmentContext, SegmentModeInput, SegmentWeather
from app.place_search_policy import WALKING_SPEED_KM_PER_MINUTE
from app.providers.gemini import RealGeminiProvider
from app.providers.gemini_prompts import build_mode_judge_instruction
from app.services.runtime.llm_execution import reset_llm_execution_metadata

THRESHOLD_KM = 0.85
WALK, TRANSIT = "walking", "transit"
TIMEOUT_S = 60.0
PROD_TIMEOUT_MS = 10_000
META_PATH = Path(__file__).resolve().parent.parent / "app/prompts/mode_judge/meta.yaml"

LEVELS = {
    "minimal": genai_types.ThinkingLevel.MINIMAL,
    "low": genai_types.ThinkingLevel.LOW,
    "high": genai_types.ThinkingLevel.HIGH,
}

# 100만 토큰당 USD (입력, 출력). 추론 토큰은 출력 단가로 청구된다. 2026-09-14 확인.
PRICES = {
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
}

TABLES: dict[str, tuple[float, ...]] = {
    # 오름차순. 850m를 0.8·0.9로 나눴다.
    "A": (0.3, 0.6, 0.8, 0.9, 1.5, 3.0),
    # 같은 거리. 누적(일정)과 독립(추천)이 의도대로 갈리는지.
    "B": (0.7, 0.7, 0.7, 0.7, 0.7),
    # 순서 섞음. "앞줄 도보, 뒷줄 대중교통" 같은 위치 편향을 잡는다.
    "C": (0.9, 0.4, 1.3),
}

# 값은 시스템이 실제로 만드는 형태만 쓴다(agent_context 강수·하늘 어휘, Companion,
# 이동 관련 무장애 3종).
CONDITIONS: dict[str, dict] = {
    "C0": {},
    "C1": {"weather": SegmentWeather(precipitation="none", sky="clear", temperature_celsius=21.0)},
    "C2": {
        "weather": SegmentWeather(precipitation="rain", sky="overcast", temperature_celsius=12.0)
    },
    "C3": {"weather": SegmentWeather(precipitation="none", sky="clear", temperature_celsius=-8.0)},
    "C4": {"weather": SegmentWeather(precipitation="none", sky="clear", temperature_celsius=34.0)},
    "C5": {"companion": "parent"},
    "C6": {"companion": "child"},
    "C7": {"accessibility_needs": ("stroller_access",)},
    "C8": {"accessibility_needs": ("wheelchair_access",)},
    "C9": {"accessibility_needs": ("wheelchair_access", "low_floor_transit")},
    "C10": {
        "companion": "parent",
        "accessibility_needs": ("stroller_access",),
        "weather": SegmentWeather(precipitation="rain", sky="overcast", temperature_celsius=8.0),
    },
}
NO_CONDITION = {"C0", "C1"}
RAIN = {"C2", "C10"}
TABLE_CONDITIONS = {"A": list(CONDITIONS), "B": ["C0", "C2", "C8"], "C": ["C0", "C2", "C8"]}
KINDS = ("일정", "추천")

# 판정 2단계의 기준 조합과 "나빠짐" 문턱. 기대표 §6(2026-09-14 개정).
BASELINE = ("gemini-3.5-flash", "minimal")
LOOSE_FLAKE_LIMIT = 5

_patch_hits = 0


# --- 입력과 기대값 ---------------------------------------------------------


def inputs() -> list[tuple[str, str, str]]:
    return [(t, c, k) for t in TABLES for c in TABLE_CONDITIONS[t] for k in KINDS]


def label(table: str, cond: str, kind: str) -> str:
    return f"{table}/{cond}/{kind}"


def expected_cell(cond: str, km: float) -> str | None:
    """칸 하나의 기대값. None은 채점하지 않는 칸이다."""
    over = km > THRESHOLD_KM
    if cond in NO_CONDITION:
        return TRANSIT if over else WALK
    if over:
        return TRANSIT
    if cond in RAIN and km == 0.8:
        return TRANSIT
    return None


def segments(table: str) -> tuple[SegmentModeInput, ...]:
    return tuple(
        SegmentModeInput(
            from_place_id=f"p{i}",
            to_place_id=f"p{i + 1}",
            order=i,
            distance_m=round(km * 1000),
            walk_minutes=round(km / WALKING_SPEED_KM_PER_MINUTE, 1),
        )
        for i, km in enumerate(TABLES[table], start=1)
    )


def context(cond: str, kind: str) -> ModeJudgmentContext:
    return ModeJudgmentContext(transport=None, sequential=(kind == "일정"), **CONDITIONS[cond])


# --- 채점 ------------------------------------------------------------------


def grade_call(table: str, cond: str, kind: str, modes: list[str]) -> dict:
    """호출 하나의 칸 채점과 표 B 검사. 개수가 틀리면 칸 채점 대신 개수 오류로 센다."""
    distances = TABLES[table]
    out: dict = {"개수_오류": len(modes) != len(distances), "칸_틀림": [], "T2": None, "T3": None}
    if out["개수_오류"]:
        return out
    for km, got in zip(distances, modes, strict=True):
        exp = expected_cell(cond, km)
        if exp is not None and got != exp:
            out["칸_틀림"].append({"km": km, "기대": exp, "판정": got})
    if table == "B" and cond in {"C2", "C8"}:
        if kind == "추천":
            # T2: 서로 대안인 같은 거리 다섯 줄은 같은 답이어야 한다.
            out["T2"] = len(set(modes)) == 1
        else:
            # T3: 같은 거리가 이어지면 transit 다음에 walking이 오지 않는다.
            first = modes.index(TRANSIT) if TRANSIT in modes else len(modes)
            out["T3"] = all(m == TRANSIT for m in modes[first:])
    return out


def grade_run(calls: list[dict]) -> dict:
    by_input: dict[str, list[dict]] = {}
    for c in calls:
        by_input.setdefault(c["입력"], []).append(c)
    graded = [c for c in calls if c.get("채점")]

    cell_wrong = [
        {"입력": c["입력"], "시도": c["시도"], **w} for c in graded for w in c["채점"]["칸_틀림"]
    ]
    flaky = {
        key: [c["판정"] for c in group if c["판정"] is not None]
        for key, group in by_input.items()
        if len({tuple(c["판정"]) for c in group if c["판정"] is not None}) > 1
    }
    # T1: 같은 시도 번호끼리 일정 첫 줄 = 추천 첫 줄.
    first_row = []
    for table in TABLES:
        for cond in TABLE_CONDITIONS[table]:
            seq = {c["시도"]: c["판정"] for c in by_input.get(label(table, cond, "일정"), [])}
            ind = {c["시도"]: c["판정"] for c in by_input.get(label(table, cond, "추천"), [])}
            for attempt in sorted(set(seq) & set(ind)):
                a, b = seq[attempt], ind[attempt]
                if a and b and a[0] != b[0]:
                    first_row.append(
                        {"표": table, "조건": cond, "시도": attempt, "일정": a[0], "추천": b[0]}
                    )
    return {
        "채점_칸_수": sum(
            sum(1 for km in TABLES[c["입력"].split("/")[0]]
                if expected_cell(c["입력"].split("/")[1], km) is not None)
            for c in graded if not c["채점"]["개수_오류"]
        ),
        "칸_틀림": cell_wrong,
        "개수_오류": [c["입력"] for c in graded if c["채점"]["개수_오류"]],
        "호출_오류": [c["입력"] for c in calls if c["오류"]],
        "T1_위반": first_row,
        "T2_위반": [c["입력"] for c in graded if c["채점"]["T2"] is False],
        "T3_위반": [c["입력"] for c in graded if c["채점"]["T3"] is False],
        "흔들린_입력": flaky,
        "반응량_표A": {
            key: [sum(m == TRANSIT for m in c["판정"][:3]) for c in group if c["판정"]]
            for key, group in by_input.items() if key.startswith("A/")
        },
    }


def latency_summary(ms: list[float]) -> dict:
    if not ms:
        return {}
    ordered = sorted(ms)
    return {
        "p50_ms": round(statistics.median(ordered)),
        "p95_ms": round(ordered[max(int(len(ordered) * 0.95) - 1, 0)]),
        "max_ms": round(ordered[-1]),
        "10초_초과": sum(1 for v in ordered if v > PROD_TIMEOUT_MS),
    }


def token_summary(calls: list[dict]) -> dict:
    ok = [c for c in calls if not c["오류"]]
    out: dict = {}
    for key in ("입력토큰", "출력토큰", "사고토큰", "캐시토큰"):
        values = [c["토큰"].get(key, 0) for c in ok]
        out[f"{key}_합"] = sum(values)
        out[f"{key}_평균"] = round(sum(values) / len(values)) if values else 0
    return out


# --- 실행 ------------------------------------------------------------------


def install_level(level: str) -> None:
    target = LEVELS[level]

    def fixed(_budget: int | None) -> genai_types.ThinkingConfig:
        global _patch_hits
        _patch_hits += 1
        return genai_types.ThinkingConfig(thinking_level=target)

    gemini_module._thinking_config_for = fixed


def take_hits() -> int:
    global _patch_hits
    hits, _patch_hits = _patch_hits, 0
    return hits


def prompt_fingerprint() -> dict:
    instruction = build_mode_judge_instruction()
    meta = yaml.safe_load(META_PATH.read_text(encoding="utf-8"))
    return {
        "버전": meta["slots"]["mode_judge.select"]["version"],
        "지시문_sha256_앞12": hashlib.sha256(instruction.encode("utf-8")).hexdigest()[:12],
        "지시문_글자수": len(instruction),
    }


async def run(args: argparse.Namespace) -> dict:
    todo = inputs()
    if args.only:
        wanted = [w.strip() for w in args.only.split(",") if w.strip()]
        valid = {label(*i) for i in todo}
        missing = [w for w in wanted if w not in valid]
        if missing:
            raise SystemExit(f"--only에 없는 입력: {missing}")
        todo = [i for i in todo if label(*i) in wanted]
    planned = len(todo) * args.repeat
    print(f"{args.model} / {args.level} — 입력 {len(todo)} × {args.repeat}회 = {planned}호출")
    if planned > args.max_calls:
        raise SystemExit(f"예정 호출 {planned}건이 --max-calls({args.max_calls})를 넘습니다.")

    provider = RealGeminiProvider(
        api_key=settings.llm_api_key,
        fast_model_names=[args.model],
        generation_model_names=[args.model],
        mode_judge_model_names=[args.model],
        timeout_seconds=TIMEOUT_S,
    )
    calls: list[dict] = []
    consecutive = 0
    # 시도를 바깥 루프로 둔다 — 같은 입력을 연달아 부르면 흔들림이 우연히 가려질 수 있다.
    for attempt in range(1, args.repeat + 1):
        for table, cond, kind in todo:
            key = label(table, cond, kind)
            reset_llm_execution_metadata()
            started = time.perf_counter()
            modes, error = None, None
            try:
                result = await provider.judge_travel_modes(segments(table), context(cond, kind))
                modes = list(result.data or ())
            except Exception as exc:  # noqa: BLE001 - 한 건 실패로 멈추지 않는다
                error = f"{type(exc).__name__}: {exc}"
            ms = round((time.perf_counter() - started) * 1000)
            hits = take_hits()
            if error is None and hits == 0:
                error, modes = "추론 단계 바꿔 끼우기가 호출되지 않음", None
            graded = grade_call(table, cond, kind, modes) if modes is not None else None
            tokens = extraction_script._tokens()
            calls.append({
                "입력": key, "시도": attempt, "판정": modes,
                "응답모델": extraction_script._served_model(), "오류": error, "ms": ms,
                "토큰": tokens, "채점": graded,
            })
            bad = graded and (graded["칸_틀림"] or graded["개수_오류"])
            mark = "ERR" if error else ("X" if bad else "ok")
            print(f"  #{attempt} {key}: {modes} {ms}ms 추론 {tokens.get('사고토큰', 0)} {mark}"
                  f"{' ' + error[:120] if error else ''}", flush=True)
            consecutive = consecutive + 1 if error else 0
            if consecutive >= args.max_consecutive_errors:
                raise SystemExit(f"연속 {consecutive}건 실패로 중단합니다.")
            if args.delay:
                await asyncio.sleep(args.delay)

    ok_ms = [c["ms"] for c in calls if not c["오류"]]
    summary = {
        **grade_run(calls),
        "호출_수": len(calls),
        "폴백": sum(1 for c in calls if c["응답모델"] and c["응답모델"] != args.model),
        **latency_summary(ok_ms),
        **token_summary(calls),
    }
    return {"요약": summary, "호출": calls}


# --- 보고 ------------------------------------------------------------------


def verdict(summary: dict, base_t1: set[tuple[str, str]]) -> tuple[str, list[str], list[str]]:
    """기대표 §6 판정. (결과, 새 첫 줄 불일치 위치, 허용 칸 흔들림 입력)."""
    stage1_fail = bool(
        summary["칸_틀림"] or summary["개수_오류"] or summary["T2_위반"] or summary["T3_위반"]
    )
    wrong_inputs = {w["입력"] for w in summary["칸_틀림"]}
    loose = [k for k in summary["흔들린_입력"] if k not in wrong_inputs]
    new_t1 = sorted({f"{v['표']}/{v['조건']}" for v in summary["T1_위반"]}
                    - {f"{t}/{c}" for t, c in base_t1})
    if stage1_fail:
        return "탈락", new_t1, loose
    worse = bool(new_t1) or len(loose) >= LOOSE_FLAKE_LIMIT
    return ("조건부" if worse else "대체 후보"), new_t1, loose


def report(directory: Path) -> None:
    results = sorted(directory.glob("judge_graded_*.json"))
    if not results:
        raise SystemExit(f"{directory}에 결과가 없습니다.")
    loaded = [(p, json.loads(p.read_text(encoding="utf-8"))) for p in results]
    # 기준 조합은 같은 tag 안에서 찾는다 — 프롬프트 수정 전후를 한 폴더에 둘 때
    # 수정 후 결과를 수정 전 기준과 비교하면 프롬프트 효과와 모델 차이가 섞인다.
    base_t1_by_tag: dict[str, set[tuple[str, str]]] = {}
    for _, d in loaded:
        if (d["실행"]["모델"], d["실행"]["추론단계"]) == BASELINE:
            base_t1_by_tag[d["실행"].get("tag", "")] = {
                (v["표"], v["조건"]) for v in d["요약"]["T1_위반"]
            }

    print("## 판정\n")
    print("| 결과 파일 | 프롬프트 | 반복 | 오류 | 채점칸 틀림 | 첫 줄 불일치 (새 위치) "
          "| 허용칸 흔들림 | 표B 검사 | **결과** |")
    print("|---|---|---|---|---|---|---|---|---|")
    for path, d in loaded:
        s, run_info = d["요약"], d["실행"]
        result, new_t1, loose = verdict(s, base_t1_by_tag.get(run_info.get("tag", ""), set()))
        if (run_info["모델"], run_info["추론단계"]) == BASELINE and result != "탈락":
            result = "기준"
        print(f"| {path.stem.removeprefix('judge_graded_')} | {run_info['프롬프트']['버전']} "
              f"| {run_info['반복']} | {len(s['호출_오류'])} "
              f"| {len(s['칸_틀림'])}/{s['채점_칸_수']} "
              f"| {len(s['T1_위반'])} ({', '.join(new_t1) or '-'}) | {len(loose)} "
              f"| {len(s['T2_위반']) + len(s['T3_위반'])} | **{result}** |")

    print("\n## 속도·토큰·비용 (성공 호출 기준, 호출당 평균)\n")
    print("| 결과 파일 | p50 | p95 | 최대 | 10초 초과 | 입력 | 출력 | 추론 | 1,000호출당 |")
    print("|---|---|---|---|---|---|---|---|---|")
    for path, d in loaded:
        s, model = d["요약"], d["실행"]["모델"]
        price_in, price_out = PRICES.get(model, (0.0, 0.0))
        ok = s["호출_수"] - len(s["호출_오류"])
        cost = (s["입력토큰_합"] * price_in
                + (s["출력토큰_합"] + s["사고토큰_합"]) * price_out) / 1e6
        per_1000 = cost / ok * 1000 if ok else 0.0
        print(f"| {path.stem.removeprefix('judge_graded_')} | {s.get('p50_ms', 0) / 1000:.1f}초 "
              f"| {s.get('p95_ms', 0) / 1000:.1f}초 | {s.get('max_ms', 0) / 1000:.1f}초 "
              f"| {s.get('10초_초과', 0)} | {s['입력토큰_평균']:,} | {s['출력토큰_평균']:,} "
              f"| {s['사고토큰_평균']:,} | ${per_1000:.2f} |")

    print("\n## 상세\n")
    for path, d in loaded:
        s = d["요약"]
        print(f"### {path.stem.removeprefix('judge_graded_')}")
        counts = Counter((w["입력"], w["km"], w["기대"], w["판정"]) for w in s["칸_틀림"])
        for (key, km, exp, got), n in sorted(counts.items()):
            print(f"- 칸 틀림: {key} {km}km 기대 {exp} → {got} ({n}회)")
        t1 = Counter((v["표"], v["조건"], v["일정"], v["추천"]) for v in s["T1_위반"])
        for (table, cond, seq, ind), n in sorted(t1.items()):
            print(f"- 첫 줄 불일치: {table}/{cond} 일정 {seq} · 추천 {ind} ({n}회)")
        for key in s["T2_위반"] + s["T3_위반"]:
            print(f"- 표 B 검사 위반: {key}")
        for key, arrays in s["흔들린_입력"].items():
            print(f"- 흔들림: {key} " + " / ".join("".join(m[0] for m in a) for a in arrays))
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=None, help="이 폴더의 결과를 표로 낸다")
    parser.add_argument("--model", default=None)
    parser.add_argument("--level", choices=list(LEVELS), default=None)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--only", default="", help="쉼표로 구분한 입력 (예: A/C8/일정)")
    parser.add_argument("--tag", default="", help="결과 파일 이름 앞에 붙인다 (예: before)")
    parser.add_argument("--delay", type=float, default=0.3, help="호출 간 대기(초)")
    parser.add_argument("--max-calls", type=int, default=110)
    parser.add_argument("--max-consecutive-errors", type=int, default=3)
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args()

    if args.report:
        report(args.report)
        return
    if not (args.model and args.level and args.out_dir):
        raise SystemExit("--model, --level, --out-dir가 필요합니다.")
    if not settings.llm_api_key:
        raise SystemExit("LLM_API_KEY가 없습니다. backend/.env를 확인하세요.")
    if settings.langfuse_prompts_enabled:
        raise SystemExit(
            "LANGFUSE_PROMPTS_ENABLED가 켜져 있습니다. 기대표는 레포 프롬프트 기준입니다."
        )

    fingerprint = prompt_fingerprint()
    install_level(args.level)
    result = asyncio.run(run(args))
    result["실행"] = {
        "모델": args.model, "추론단계": args.level, "반복": args.repeat, "only": args.only,
        "tag": args.tag, "타임아웃_s": TIMEOUT_S, "시각": datetime.now().isoformat(),
        "프롬프트": fingerprint,
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{args.tag}_" if args.tag else ""
    smoke = "smoke_" if args.only else ""
    out = args.out_dir / f"{smoke}judge_graded_{prefix}{args.model}_{args.level}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    s = result["요약"]
    print(f"\n채점칸 {s['채점_칸_수']} 틀림 {len(s['칸_틀림'])} | 첫 줄 불일치 {len(s['T1_위반'])} "
          f"| 흔들림 {len(s['흔들린_입력'])} | 호출오류 {len(s['호출_오류'])} "
          f"| p50 {s.get('p50_ms')} p95 {s.get('p95_ms')} | 프롬프트 {fingerprint}")
    print(f"저장: {out}")


if __name__ == "__main__":
    main()
