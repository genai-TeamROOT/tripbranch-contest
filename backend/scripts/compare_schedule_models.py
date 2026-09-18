"""일정 편성(GENERATION 쌍)을 모델별로 대조한다 — 계약 위반 횟수로 판정한다.

역할: 같은 `SchedulePlanningRequest`를 여러 모델에 넣고 **계약을 몇 번 깨는지**를
센다. 계획서는 `test_results/model_tier_2026-09-08/plan.md` §5.

**"좋은 일정"의 정답은 없다.** 그래서 기대값으로 채점하지 않는다. 대신
`plan_schedule()`이 이미 하드 검증을 걸어 두었으므로 **위반 횟수는 셀 수 있고,
그게 곧 품질 지표다.**

| 판정 | 무엇이 깨지나 |
| --- | --- |
| 항목 수 상한 초과 | `_cap_item_count()`가 잘라낸 건수 |
| `must_include` 누락 | 담아둔 장소가 1회 재시도 뒤에도 빠졌는지 |
| 예산 판정 | `within`/`over`/`under` 분포 |
| `warnings` | planner가 폐점 스탑을 감지한 건수 |
| 오류 | 스키마를 못 맞춰 통째로 실패한 건수 |

**공개 메서드를 그대로 탄다.** `compare_schedule_thinking_budget.py`는
`_call_structured()`를 직접 불러 thinking 예산을 바꾸지만, 이 스크립트는 모델만
변수로 삼으므로 프로덕션 경로(`plan_schedule()` → `generate_schedule_plan()`)를
그대로 쓴다. 그래야 하드 검증과 재시도까지 실제와 같게 돈다.

**지표는 프로덕션과 같은 함수로 계산한다.** `schedule_quality_metrics()`를 그대로
불러 쓴다 — 러너가 따로 계산하면 프로덕션과 값이 갈릴 수 있다.

**폴백을 쓰지 않는다.** 단일 모델로 넘겨 어느 모델의 답인지 확정한다. 그래도
`served_model`을 기록한다 — 다르게 나오면 그 회차는 비교에서 빼야 한다.

**후보와 거리는 고정값이다.** 이 스크립트의 목적은 프롬프트 길이·구조를 실제와
비슷하게 맞추는 것이지 거리 정확도가 아니다(그건 `app.geo.haversine_km()`로 별도
테스트된다). `compare_schedule_thinking_budget.py`가 세운 방식을 따른다.

입력: `--models`, `--repeat`, `--time-available`(쉼표로 여러 예산). `.env`에 LLM_API_KEY.
출력: 표준 출력 + `<--out-dir>/compare_schedule_<tag>.json` + `..._<tag>.md`
호출 시점: 모델 교체를 검토할 때 수동 실행(실제 API 호출 비용 때문에 pytest 제외).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from itertools import combinations
from pathlib import Path
from typing import Any

from app.config import settings
from app.providers.gemini import RealGeminiProvider
from app.schedule.budget import walkable_cluster_size
from app.schedule.metrics import WALKABLE_THRESHOLD_MIN, schedule_quality_metrics
from app.schedule.schemas import SchedulePlanningRequest
from app.schemas import RecommendationItem, UserConditions
from app.services.runtime.llm_execution import (
    get_llm_execution_metadata,
    reset_llm_execution_metadata,
)

RESULTS_DIR = Path(__file__).resolve().parent.parent / "test_results"

# 분류가 섞여야 체류시간 정책(관광지 60·문화시설 90·식당 60·쇼핑 30)이 실제처럼
# 걸린다. 한 분류만 주면 상한 계산이 현실과 달라진다.
_CANDIDATE_NAMES: tuple[tuple[str, str, str], ...] = (
    ("2824887", "경복궁", "attraction"),
    ("2824888", "국립민속박물관", "culture"),
    ("2824889", "북촌한옥마을", "attraction"),
    ("2824890", "인사동 쌈지길", "shopping"),
    ("2824891", "삼청동 한식당", "restaurant"),
    ("2824892", "창덕궁", "attraction"),
    ("2824893", "서울공예박물관", "culture"),
    ("2824894", "광화문 카페", "restaurant"),
)


def _build_candidates() -> list[RecommendationItem]:
    return [
        RecommendationItem(
            place_id=place_id,
            name=name,
            category=category,
            distance_km=round(0.3 + index * 0.4, 2),
            remaining_minutes=300,
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
    return {
        (a.place_id, b.place_id): round(abs(a.distance_km - b.distance_km) + 0.2, 2)
        for a, b in combinations(candidates, 2)
    }


def _observe_calls() -> dict[str, Any]:
    """이 편성이 실제로 쓴 모델과 토큰. 편성은 재시도로 두 번 부를 수 있다."""

    metadata = get_llm_execution_metadata()
    if metadata is None or not metadata.calls:
        return {"호출수": 0}
    served = sorted({c.served_model for c in metadata.calls if c.served_model})
    totals = {"입력토큰": 0, "출력토큰": 0, "사고토큰": 0}
    for call in metadata.calls:
        for key, value in (
            ("입력토큰", call.input_tokens),
            ("출력토큰", call.output_tokens),
            ("사고토큰", call.thoughts_tokens),
        ):
            if isinstance(value, int):
                totals[key] += value
    return {
        "호출수": len(metadata.calls),
        "응답모델": served,
        "재시도합": sum(c.retry_count or 0 for c in metadata.calls),
        **totals,
    }


async def plan_once(
    provider: RealGeminiProvider,
    *,
    time_available_min: int | None,
    must_include: list[str],
) -> dict[str, Any]:
    """한 번 편성하고 계약 위반을 센다."""

    from app.schedule.planner import plan_schedule

    candidates = _build_candidates()
    pairwise = _build_pairwise_distances_km(candidates)
    conditions = UserConditions(
        search_center="경복궁",
        time_available=time_available_min,
        # PlaceType enum 값이다 — 한글 라벨이 아니다(schemas.py::PlaceType).
        place_types=["attraction", "cultural_facility"],
    )
    request = SchedulePlanningRequest(
        candidates=candidates,
        conditions=conditions,
        must_include_place_ids=must_include,
        visit_datetime=None,
        pairwise_distances_km=pairwise,
    )

    reset_llm_execution_metadata()
    try:
        result = await plan_schedule(request, provider)
    except Exception as exc:  # noqa: BLE001 — 한 건이 실패해도 대조를 계속한다
        detail = getattr(exc, "details", None) or str(exc)
        return {"오류": f"{type(exc).__name__}: {detail}", **_observe_calls()}

    metrics = schedule_quality_metrics(
        result,
        time_available_min=time_available_min,
        saved_place_count=len(must_include),
        walkable_cluster_size=walkable_cluster_size(
            request, within_min=WALKABLE_THRESHOLD_MIN
        ),
    )
    # 계약 위반: 담아둔 장소가 재시도 뒤에도 빠졌는지, 상한을 넘겨 잘렸는지.
    omitted = list(result.omitted_saved_place_names)
    over_capacity = list(result.over_capacity_place_names)
    return {
        "오류": None,
        "항목수": len(result.items),
        "상한": result.item_capacity,
        "총소요분": result.total_duration_min,
        "예산판정": (
            result.time_budget_status.value
            if result.time_budget_status is not None
            else None
        ),
        "must_include_누락": omitted,
        "상한초과_제외": over_capacity,
        "warnings_건수": sum(len(item.warnings or []) for item in result.items),
        "분류": [item.place_name for item in result.items],
        # **체류시간을 남긴다.** 총소요만 남기면 "장소를 더 넣어 채웠는지"와
        # "같은 장소를 길게 늘려 채웠는지"를 가를 수 없다. 2026-09-08 4단계에서
        # 이 구분이 필요해 정책 상한(duration.py의 VisitDurationPolicy)과
        # 산술 대조로 추론해야 했다. fit_durations_to_budget()이 정책 천장까지
        # 늘리므로, 천장에 붙은 자리가 몇인지가 곧 부풀리기의 크기다.
        "체류시간": [item.estimated_duration_min for item in result.items],
        # **지연을 남긴다.** `plan_schedule()`이 이미 재서 결과에 실어 보내는데
        # 러너가 버리고 있었다 — 그래서 GENERATION 티어를 판정하면서 그 티어에서
        # 가장 무거운 호출의 속도 차이를 몰랐다(2026-09-09 설계 점검).
        # must_include 재시도가 붙은 회차는 두 번 부른 시간이 다 들어간다.
        "지연ms": round(result.elapsed_ms),
        "지표": metrics,
        **_observe_calls(),
    }

# 연속 실패 중단용 카운터. 설정 오류로 전 케이스를 끝까지 도는 것을 막는다
# (2026-09-08: 분류 측정에서 400 조합으로 204호출을 버렸다. RULES 함정 46).
_CONSEC = {"n": 0}


async def run_model(model: str, args: argparse.Namespace) -> dict[str, list[dict]]:
    provider = RealGeminiProvider(
        api_key=settings.llm_api_key,
        # Q2는 GENERATION 티어만 변수다. FAST를 함께 바꾸면 편성 실패가
        # 추출 탓인지 편성 탓인지 구분할 수 없어 호출이 통째로 낭비된다.
        fast_model_names=[args.fast_model],
        generation_model_names=[model],
        timeout_seconds=args.timeout,
    )
    must_include = [pid.strip() for pid in args.must_include.split(",") if pid.strip()]
    budgets = [
        None if b.strip() in ("", "none") else int(b)
        for b in args.time_available.split(",")
    ]
    out: dict[str, list[dict]] = {}
    for budget in budgets:
        label = "미지정" if budget is None else f"{budget}분"
        runs: list[dict] = []
        for _ in range(args.repeat):
            record = await plan_once(
                provider, time_available_min=budget, must_include=must_include
            )
            runs.append(record)
            print("." if record["오류"] is None else "!", end="", flush=True)
            if record["오류"] is None:
                _CONSEC["n"] = 0
            else:
                _CONSEC["n"] += 1
                if _CONSEC["n"] >= args.max_consecutive_errors:
                    raise SystemExit(
                        f"\n연속 {_CONSEC['n']}건 실패로 중단합니다. "
                        f"마지막 오류: {record['오류']}"
                    )
            await asyncio.sleep(args.delay)
        out[label] = runs
        print(f" {label}", flush=True)
    return out


def _violations(runs: list[dict]) -> dict[str, int]:
    """계약 위반 집계. 원자값으로 남기고 비율은 계산하지 않는다."""
    return {
        "오류": sum(1 for r in runs if r["오류"]),
        "must_include_누락": sum(1 for r in runs if r.get("must_include_누락")),
        "상한초과_제외": sum(1 for r in runs if r.get("상한초과_제외")),
        "warnings": sum(r.get("warnings_건수", 0) for r in runs if r["오류"] is None),
        "예산_over": sum(1 for r in runs if r.get("예산판정") == "over"),
        "예산_under": sum(1 for r in runs if r.get("예산판정") == "under"),
    }


def _markdown_report(
    models: list[str], results: dict[str, dict[str, list[dict]]], args: argparse.Namespace
) -> str:
    lines: list[str] = []
    lines.append(f"# 일정 편성 모델 대조 — {args.tag or 'schedule'}")
    lines.append("")
    lines.append(f"- 예산 {args.time_available} × 반복 {args.repeat}회 × 모델 {len(models)}개")
    lines.append(
        f"- 후보 {len(_CANDIDATE_NAMES)}곳(분류 혼합), "
        f"`must_include`={args.must_include or '없음'}"
    )
    lines.append("- thinking 예산은 코드가 지정한 값 그대로다")
    lines.append("")
    lines.append("## 1. 계약 위반 — 하나라도 늘면 그 모델은 못 쓴다")
    lines.append("")
    labels = list(next(iter(results.values())).keys())
    lines.append(
        "| 예산 | 모델 | 오류 | must_include 누락 | 상한초과 제외 "
        "| warnings | over | under |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for label in labels:
        for m in models:
            v = _violations(results[m][label])
            lines.append(
                f"| {label} | `{m}` | {v['오류']} | {v['must_include_누락']} "
                f"| {v['상한초과_제외']} | {v['warnings']} | {v['예산_over']} | {v['예산_under']} |"
            )
    lines.append("")
    lines.append("## 2. 결과 모양")
    lines.append("")
    lines.append(
        "| 예산 | 모델 | 항목수 | 상한 | 총소요분 | 체류시간 | 예산판정 | 지연ms | 호출 | 재시도 |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for label in labels:
        for m in models:
            for r in results[m][label]:
                if r["오류"]:
                    lines.append(
                        f"| {label} | `{m}` | — | — | — | — | 오류 | — "
                        f"| {r.get('호출수', 0)} | — |"
                    )
                    continue
                stays = "+".join(str(v) for v in r.get("체류시간", [])) or "—"
                lines.append(
                    f"| {label} | `{m}` | {r['항목수']} | {r['상한']} | {r['총소요분']} "
                    f"| {stays} | {r['예산판정']} | {r.get('지연ms', '—')} "
                    f"| {r.get('호출수', 0)} | {r.get('재시도합', 0)} |"
                )
    lines.append("")
    lines.append("## 3. 지연 — 모델별로 따로 모은다")
    lines.append("")
    lines.append("| 모델 | 편성 횟수 | 중앙값 | 최소 | 최대 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for m in models:
        got = sorted(
            r["지연ms"] for label in labels for r in results[m][label]
            if r["오류"] is None and "지연ms" in r
        )
        if not got:
            lines.append(f"| `{m}` | 0 | — | — | — |")
            continue
        half = len(got) // 2
        mid = got[half] if len(got) % 2 else (got[half - 1] + got[half]) // 2
        lines.append(f"| `{m}` | {len(got)} | {mid:,}ms | {got[0]:,}ms | {got[-1]:,}ms |")
    lines.append("")
    lines.append(
        "`plan_schedule()`이 잰 값이다 — 파이프라인 진입부터 결과 조립까지이고 "
        "`must_include` 재시도가 붙은 회차는 두 호출이 다 들어간다."
    )
    lines.append("")
    lines.append("## 4. 토큰 — 단가는 곱하지 않았다")
    lines.append("")
    lines.append("| 모델 | 편성 횟수 | 입력 | 출력 | 사고 | 편성당 합계 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for m in models:
        runs = [r for label in labels for r in results[m][label]]
        n = max(len(runs), 1)
        inp = sum(r.get("입력토큰", 0) for r in runs)
        outp = sum(r.get("출력토큰", 0) for r in runs)
        th = sum(r.get("사고토큰", 0) for r in runs)
        lines.append(
            f"| `{m}` | {len(runs)} | {inp:,} | {outp:,} | {th:,} | {(inp + outp + th) // n:,} |"
        )
    lines.append("")
    lines.append("**사고 토큰을 따로 세는 이유**는 과금 대상인데 출력 토큰에 안 잡히기 때문이다.")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", required=True, help="쉼표로 구분한 모델 2개 이상")
    parser.add_argument("--repeat", type=int, default=1,
                        help="조합당 반복 횟수. 먼저 1로 쓸고 갈린 조합만 올린다")
    parser.add_argument("--fast-model", default="gemini-3.5-flash",
                        help="FAST 티어 고정값. Q2에서는 변수가 아니다")
    parser.add_argument("--max-calls", type=int, default=60,
                        help="예정 호출 수가 이 값을 넘으면 실행을 거부한다")
    parser.add_argument("--max-consecutive-errors", type=int, default=3,
                        help="연속 실패가 이 횟수에 닿으면 중단한다")
    parser.add_argument("--delay", type=float, default=1.0, help="호출 간 대기(초)")
    parser.add_argument("--timeout", type=float, default=60.0, help="편성은 추출보다 길다")
    parser.add_argument(
        "--time-available",
        default="180,300,none",
        help="예산(분)을 쉼표로. none은 미지정 — 그쪽은 설계가 통째로 꺼져 있어 따로 본다",
    )
    parser.add_argument(
        "--must-include",
        default="2824891",
        help="반드시 넣어야 할 place_id(보관함 흉내). 빈 값이면 검증하지 않는다",
    )
    parser.add_argument("--tag", default="")
    parser.add_argument("--out-dir", type=Path, default=RESULTS_DIR)
    args = parser.parse_args()

    if not settings.llm_api_key:
        raise SystemExit("LLM_API_KEY가 없습니다. backend/.env를 확인하세요.")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if len(models) < 2:
        raise SystemExit("--models에 모델 2개 이상이 필요합니다.")

    print(f"예산 {args.time_available} × 반복 {args.repeat}회 × 모델 {len(models)}개\n")
    # 편성 1건이 호출 1건이 아니다. must_include가 빠지면 planner가 같은 요청을
    # 한 번 더 부른다(planner.py의 재시도는 1회 고정). 그래서 상한은 최악치로
    # 잰다 — 하한으로 재면 사용자가 보는 숫자가 실제 청구의 절반이 된다.
    planned = len(models) * args.repeat * len(args.time_available.split(","))
    worst = planned * 2
    print(
        f"예정 편성 {planned}건 → 실 호출 {planned}~{worst}건"
        f"(must_include 재시도 포함), 상한 {args.max_calls}건",
        flush=True,
    )
    if worst > args.max_calls:
        raise SystemExit(
            f"최악치 {worst}호출(편성 {planned}건 × 재시도)이 "
            f"--max-calls({args.max_calls})를 넘습니다. "
            "먼저 --repeat 1로 쓸고 갈린 조합만 좁혀서 --repeat을 올리세요. "
            "통째로 돌려야 하면 --max-calls를 명시하세요."
        )
    results: dict[str, dict[str, list[dict]]] = {}
    for model in models:
        print(f"[{model}]")
        results[model] = asyncio.run(run_model(model, args))

    print("\n" + "=" * 78)
    fell_back: list[str] = []
    for m in models:
        print(f"\n[{m}]")
        for label, runs in results[m].items():
            v = _violations(runs)
            broke = {k: n for k, n in v.items() if n}
            print(f"  {label:>6}  {broke if broke else '위반 없음'}")
            for r in runs:
                served = [s for s in r.get("응답모델", []) if s != m]
                if served:
                    fell_back.append(f"{m}/{label} -> {served}")
    if fell_back:
        print(f"\n⚠️  폴백이 낀 회차 — 비교에서 빼야 한다:\n  {sorted(set(fell_back))}")
    print("=" * 78)

    tag = args.tag or "schedule"
    out_json = args.out_dir / f"compare_schedule_{tag}.json"
    out_json.write_text(
        json.dumps(
            {
                "models": models,
                "repeat": args.repeat,
                "time_available": args.time_available,
                "must_include": args.must_include,
                "폴백_낀_회차": sorted(set(fell_back)),
                "raw": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    out_md = args.out_dir / f"compare_schedule_{tag}.md"
    out_md.write_text(_markdown_report(models, results, args), encoding="utf-8")
    print(f"원자료 저장: {out_json}")
    print(f"문서용 표 저장: {out_md}")


if __name__ == "__main__":
    main()
