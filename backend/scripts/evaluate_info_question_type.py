"""INFO question_type 분류를 여러 번 돌려 정확도와 흔들림을 함께 잰다.

입력:
    backend/app/prompts/info/evals/question_type_cases.csv

출력(실행마다 새로 생성):
    backend/test_results/info_question_type/<run_id>/case_results.csv
    backend/test_results/info_question_type/<run_id>/summary.json

호출:
    backend/.venv/bin/python -m scripts.evaluate_info_question_type --label baseline
    backend/.venv/bin/python -m scripts.evaluate_info_question_type --repeat 5

한 문장을 여러 번 돌리는 것이 이 스크립트의 이유다. 한 번만 재면 "맞았다"와 "이번에는
맞았다"가 구분되지 않는다 — 2026-08-25에 "보성사터에 아이들과 가기 좋아?"가 세 번 중
두 번은 general_info, 한 번은 facility로 갈렸고, 한 번만 봤다면 그중 무엇이든 결론이
됐을 것이다.

`extract_info_query`만 부른다. 인텐트 분류(router)는 이미 INFO로 잘 보내고 있어
이 스크립트의 관심사가 아니다.

실제 Gemini를 호출하므로 pytest에 포함하지 않는다. `app/prompts/README.md`가 정한 대로
이 결과는 빠른 단일 턴 실험이며, 머지의 단독 근거는 다중 턴 회귀
(`test_results/agent_quality/`)다.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import csv
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.providers.factory import get_llm_provider
from app.state.schema import now_kst

_KST = ZoneInfo("Asia/Seoul")
_CASES_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "prompts"
    / "info"
    / "evals"
    / "question_type_cases.csv"
)
_RESULTS_ROOT = Path(__file__).resolve().parents[1] / "test_results" / "info_question_type"


def _load_cases() -> list[dict[str, str]]:
    with _CASES_PATH.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


async def _classify_once(llm: object, user_input: str) -> str:
    """분류 결과 한 건. 실패는 문자열로 남겨 집계에서 드러나게 한다."""
    try:
        result = await llm.extract_info_query(  # type: ignore[attr-defined]
            user_input,
            has_previous_recommendation=False,
            reference_date=now_kst().date(),
            conversation_place_name=None,
        )
    except Exception as error:  # noqa: BLE001 - 실패도 관측 대상이다
        return f"__error__:{type(error).__name__}"
    info = result.data.info
    if info is None:
        # INFO가 아닌 인텐트로 판정된 경우다. 빈 값으로 두면 오분류와 구분되지 않는다.
        return f"__intent__:{result.data.intent.value}"
    return info.question_type.value


async def _run(repeat: int, label: str) -> Path:
    cases = _load_cases()
    llm = get_llm_provider()
    rows: list[dict[str, object]] = []

    for case in cases:
        observed = [
            await _classify_once(llm, case["user_input"]) for _ in range(repeat)
        ]
        counts = collections.Counter(observed)
        expected = case["expected_question_type"]
        rows.append(
            {
                "case_id": case["case_id"],
                "user_input": case["user_input"],
                "expected": expected,
                "group": case["group"],
                "hits": counts[expected],
                "repeat": repeat,
                # 관측된 값이 하나뿐이면 흔들리지 않은 것이다. 정답 여부와는 별개다 —
                # 늘 같은 값으로 틀리는 것과 실행마다 갈리는 것은 원인이 다르다.
                "stable": len(counts) == 1,
                "observed": " ".join(
                    f"{value}x{count}" for value, count in counts.most_common()
                ),
            }
        )
        mark = "O" if counts[expected] == repeat else ("~" if counts[expected] else "X")
        print(f"{mark} {case['case_id']} {case['user_input']}")
        print(f"    기대 {expected} / 관측 {rows[-1]['observed']}")

    run_id = f"{datetime.now(_KST):%Y-%m-%d_%H%M}_{label}"
    run_dir = _RESULTS_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / "case_results.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    total = len(rows) * repeat
    summary = {
        "label": label,
        "repeat": repeat,
        "cases": len(rows),
        "accuracy": round(sum(int(row["hits"]) for row in rows) / total, 4),
        "stable_cases": sum(1 for row in rows if row["stable"]),
        "by_group": {
            group: {
                "cases": len(group_rows),
                "accuracy": round(
                    sum(int(row["hits"]) for row in group_rows)
                    / (len(group_rows) * repeat),
                    4,
                ),
                "stable_cases": sum(1 for row in group_rows if row["stable"]),
            }
            for group, group_rows in _by_group(rows).items()
        },
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("\n" + json.dumps(summary, ensure_ascii=False, indent=2))
    return run_dir


def _by_group(rows: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault(str(row["group"]), []).append(row)
    return grouped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=5, help="문장당 반복 횟수")
    parser.add_argument(
        "--label", default="current", help="결과 폴더 이름에 붙일 기준선 이름"
    )
    args = parser.parse_args()
    if args.repeat < 1:
        raise SystemExit("--repeat은 1 이상이어야 합니다.")
    run_dir = asyncio.run(_run(args.repeat, args.label))
    print(f"\n결과: {run_dir}")


if __name__ == "__main__":
    main()
