"""사람이 매긴 무장애 문장 판정을 Gemini와 대조해 다시 볼 문장을 뽑는다.

역할: `supabase/data/barrier_free_sentence_verdicts.csv`의 판정이 맞는지 **증명하는
것이 아니라**, 사람과 LLM이 갈리는 문장만 골라 다시 볼 목록을 만든다. 판정표를
사람이 전부 매겼기 때문에 견줄 정답이 없다 — 점수를 낼 수는 없고, 의심할 자리를
좁힐 수만 있다.

그래서 LLM에게 라벨을 새로 매기게 하지 않는다. **사람이 쓴 것과 같은 기준을 주고**
판정시킨 뒤 갈리는 것만 남긴다. 갈렸다고 LLM이 맞는 것도 아니다 — 사람이 그 목록을
보고 고칠지 정한다.

678문장 전부를 넣는다. 부분·불가로 매긴 23행만 넣으면 안 된다. 지금 가장 큰 위험은
**기본값 `possible`이 진짜 예외를 삼켰을 가능성**이고, 그건 가능으로 매긴 1,141행
안에 있기 때문이다.

문장 묶음마다 판정 기준이 다르므로 두 번에 나눠 부른다.

- 접근로·주출입구·엘리베이터 → 휠체어와 유모차를 따로 판정한다.
- 점자블록·점자안내·음성안내·안내견 → 시각안내 하나만 판정한다.

**`없다`의 뜻이 두 묶음에서 반대다.** 접근로·주출입구는 문장의 주어가 턱·단차라서
`턱이 없음`이 긍정이지만, 점자블록은 주어가 시설이라 `점자블록 없음`이 부정이다.
프롬프트가 이걸 구분해 주지 않으면 대조 결과 전체가 못 쓰게 된다.

입력: supabase/data/barrier_free_sentence_verdicts.csv (사람 판정)
출력: backend/test_results/barrier_free_verdict_crosscheck.csv
호출 시점: `python -m scripts.crosscheck_barrier_free_verdicts`로 수동 실행한다
      (1회성 검증 도구, pytest 스위트에는 넣지 않는다 — 실제 API 호출 비용 때문).
      `--dry-run`은 부르지 않고 보낼 프롬프트만 보여 준다.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import csv
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel, Field, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
VERDICTS_CSV = _REPO_ROOT / "supabase" / "data" / "barrier_free_sentence_verdicts.csv"
RESULTS_DIR = Path(__file__).resolve().parent.parent / "test_results"
RESULTS_CSV = RESULTS_DIR / "barrier_free_verdict_crosscheck.csv"

# 한 번에 보내는 문장 수. 크게 잡으면 호출 수는 줄지만 한 묶음이 깨졌을 때
# 다시 부르는 양이 커진다.
BATCH_SIZE = 20
# 호출 사이 간격. scripts/test_concentration_classification.py와 같은 값이다.
REQUEST_INTERVAL_SECONDS = 2.5

VERDICTS = ("possible", "partial", "impossible")

# 문장이 온 컬럼 → 판정 묶음. 어휘 이름은 계약과 같은 값을 쓴다
# (app.domain.models.AccessibilityNeed).
_STEP_FREE_COLUMNS = {"approach_route", "entrance_access", "elevator"}
_VISUAL_GUIDE_COLUMNS = {
    "braille_block",
    "braille_promotion",
    "audio_guide",
    "guide_dog",
}

# 사람이 판정하며 세운 기준을 그대로 옮긴 것이다. 사람과 다른 기준을 주면 갈린
# 문장이 "사람이 틀렸을 자리"가 아니라 "기준이 달랐을 자리"가 되어 쓸모가 없다.
_STEP_FREE_INSTRUCTION = """\
너는 한국 관광지의 무장애 정보 원문을 읽고, 휠체어 이용자와 유모차 동반자가 그 장소에
들어갈 수 있는지 판정한다.

판정 값은 셋이다.
- possible: 들어갈 수단이 있다.
- partial: 들어가긴 하지만 못 가는 구역이 남는다.
- impossible: 아예 들어갈 수 없다.

반드시 지킬 것.

1. **`없다`가 긍정인 문장이 많다.** 이 문장들은 주어가 턱·단차·계단이다.
   `출입구에 턱이 없음`, `단차 없음`은 장애물이 없다는 뜻이라 possible이다.
   시설이 없다는 뜻으로 잘못 읽으면 안 된다.

2. **수단이 있으면 possible이다.** 리프트, 보조출입구, 후문, 경사로, 실내 경사로,
   직원 호출, 전화 요망 전부 수단이다. 주출입구가 계단이어도 다른 길로 들어갈 수
   있다고 말하면 possible이다.

3. **불편하다는 말은 impossible이 아니다.** `내부가 협소하여 불편할 수 있음`,
   `이동에 주의가 필요함`은 들어갈 수는 있다는 뜻이다.

4. **못 가는 구역이 남으면 partial이다.** `건물에는 들어가나 2층은 계단`,
   `외부는 관람 가능하나 내부 출입 불가`처럼 장소의 일부에 못 닿는 경우다.

5. **엘리베이터는 휠체어와 유모차 둘 다 탄다.** possible이다.
   에스컬레이터는 둘 다 못 탄다.

6. **휠체어와 유모차가 갈리는 자리가 있다.**
   - 통로가 좁아 휠체어·전동 스쿠터 진입이 어렵다는 문장: 휠체어는 impossible이지만
     유모차는 폭이 작아 possible이다.
   - 흙길·자갈·비포장 구간: 유모차는 지나갈 수 있어 possible이다.
   - 턱·단차·계단: 둘 다 똑같이 막힌다.

7. **원문에 없는 사실을 지어내지 마라.** 문장이 말하는 것만 읽는다.

reason은 한국어로 한 문장, 판정을 가른 근거만 쓴다."""

_VISUAL_GUIDE_INSTRUCTION = """\
너는 한국 관광지의 무장애 정보 원문을 읽고, 시각장애인을 위한 안내 시설이 있는지
판정한다. 대상은 점자블록, 점자 안내판, 음성 안내, 안내견 동반이다.

판정 값은 셋이다.
- possible: 그 안내 시설이 있다.
- partial: 있지만 일부 구역에만 있거나 조건이 붙는다.
- impossible: 없다.

반드시 지킬 것.

1. **여기서는 `없다`가 부정이다.** 이 문장들은 주어가 시설 자체다.
   `점자블록 없음`, `음성안내 미설치`는 impossible이다.
   앞 묶음(턱·단차)과 반대라는 점에 주의한다.

2. **설치 위치를 적은 것은 제한이 아니다.** 이 데이터는
   `점자블록 있음(주출입구)`, `점자블록 있음(화장실, 엘리베이터)`처럼 **어디에
   설치했는지를 괄호에 적는 형식**이다. 위치가 적혀 있다고 partial로 내리지 마라.
   시설이 있다고 말하면 possible이다.

3. **partial은 원문이 스스로 모자람을 말할 때만 쓴다.** `일부 구간만 설치`,
   `점자블록이 끊긴 구간 있음`, `예약자에 한해 제공`처럼 문장이 제한을 직접
   말하는 경우다. 위치를 적어 둔 것과 제한을 말하는 것은 다르다.

4. **안내견은 동반 가능이면 possible, 불가면 impossible이다.**

5. **원문에 없는 사실을 지어내지 마라.** 문장이 말하는 것만 읽는다.
   설치 정보가 없어 판단할 수 없으면 impossible이 아니라 possible로 두고,
   reason에 판단할 근거가 없다고 적는다.

reason은 한국어로 한 문장, 판정을 가른 근거만 쓴다."""


class _StepFreeJudgement(BaseModel):
    index: int = Field(description="보낸 문장의 번호")
    wheelchair: str = Field(description="possible / partial / impossible")
    stroller: str = Field(description="possible / partial / impossible")
    reason: str


class _StepFreeBatch(BaseModel):
    judgements: list[_StepFreeJudgement]


class _VisualGuideJudgement(BaseModel):
    index: int = Field(description="보낸 문장의 번호")
    visual_guide: str = Field(description="possible / partial / impossible")
    reason: str


class _VisualGuideBatch(BaseModel):
    judgements: list[_VisualGuideJudgement]


@dataclass(frozen=True)
class Sentence:
    """대조할 문장 하나. human은 어휘별 사람 판정이다."""

    id: str
    column_kind: str
    text: str
    place_count: int
    human: dict[str, str]

    @property
    def group(self) -> str:
        return "step_free" if self.column_kind in _STEP_FREE_COLUMNS else "visual_guide"


def load_sentences() -> list[Sentence]:
    """판정표를 문장 단위로 되돌린다. 파일은 어휘별로 한 줄씩 펼쳐져 있다."""
    if not VERDICTS_CSV.exists():
        raise FileNotFoundError(f"판정표가 없습니다: {VERDICTS_CSV}")

    merged: dict[str, dict] = {}
    with VERDICTS_CSV.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            entry = merged.setdefault(
                row["id"],
                {
                    "column_kind": row["column_kind"],
                    "text": row["sentence_text"],
                    "place_count": int(row["place_count"]),
                    "human": {},
                },
            )
            entry["human"][row["need"]] = row["verdict"]

    sentences = [Sentence(id=key, **value) for key, value in merged.items()]
    unknown = {s.column_kind for s in sentences} - _STEP_FREE_COLUMNS - _VISUAL_GUIDE_COLUMNS
    if unknown:
        # 조용히 건너뛰면 그 문장만 대조 없이 통과한다. 판정표에 컬럼이 늘었다는
        # 뜻이므로 기준을 먼저 정해야 한다.
        raise ValueError(f"판정 기준을 모르는 컬럼입니다: {sorted(unknown)}")
    # 순서를 고정한다. 다시 돌렸을 때 묶음이 달라지면 결과를 견줄 수 없다.
    return sorted(sentences, key=lambda s: (s.column_kind, s.id))


def build_prompt(batch: list[Sentence]) -> str:
    lines = ["다음 문장들을 판정해라. index를 그대로 돌려준다.", ""]
    for index, sentence in enumerate(batch):
        text = sentence.text.replace("\n", " ")
        lines.append(f"[{index}] ({sentence.column_kind}) {text}")
    return "\n".join(lines)


async def _judge_batch(
    client: genai.Client, model_name: str, batch: list[Sentence]
) -> dict[int, dict[str, str]]:
    """묶음 하나를 판정한다. 반환은 {문장 번호: {어휘: 판정, 'reason': 이유}}."""
    is_step_free = batch[0].group == "step_free"
    schema = _StepFreeBatch if is_step_free else _VisualGuideBatch
    instruction = _STEP_FREE_INSTRUCTION if is_step_free else _VISUAL_GUIDE_INSTRUCTION

    response = await client.aio.models.generate_content(
        model=model_name,
        contents=build_prompt(batch),
        config=genai_types.GenerateContentConfig(
            system_instruction=instruction,
            response_mime_type="application/json",
            response_schema=schema,
            # 판정이라 흔들리면 안 된다. 다시 돌렸을 때 같은 답이 나와야 한다.
            temperature=0.0,
        ),
    )
    parsed = schema.model_validate_json(response.text or "")

    out: dict[int, dict[str, str]] = {}
    for item in parsed.judgements:
        if is_step_free:
            verdicts = {
                "wheelchair_access": item.wheelchair,
                "stroller_access": item.stroller,
            }
        else:
            verdicts = {"visual_guide": item.visual_guide}
        bad = {v for v in verdicts.values() if v not in VERDICTS}
        if bad:
            raise ValueError(f"모르는 판정 값입니다: {sorted(bad)}")
        out[item.index] = {**verdicts, "reason": item.reason}
    return out


async def judge(
    client: genai.Client, model_name: str, sentences: list[Sentence]
) -> dict[str, dict[str, str]]:
    """전 문장을 묶음으로 판정한다. 묶음이 깨지면 한 문장씩 다시 부른다."""
    groups = collections.defaultdict(list)
    for sentence in sentences:
        groups[sentence.group].append(sentence)

    results: dict[str, dict[str, str]] = {}
    batches = [
        group[i : i + BATCH_SIZE]
        for group in groups.values()
        for i in range(0, len(group), BATCH_SIZE)
    ]
    for number, batch in enumerate(batches, start=1):
        label = f"[{number}/{len(batches)}] {batch[0].group} {len(batch)}문장"
        try:
            judged = await _judge_batch(client, model_name, batch)
            missing = set(range(len(batch))) - judged.keys()
            if missing:
                raise ValueError(f"판정이 빠진 문장이 있습니다: {sorted(missing)}")
            for index, verdict in judged.items():
                results[batch[index].id] = {**verdict, "model": model_name}
            print(f"{label} 완료")
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            # 묶음이 깨졌다고 통째로 버리지 않는다. 버리면 그 20문장이 대조 없이
            # 사라지는데, 결과 파일만 보면 그 사실이 드러나지 않는다.
            print(f"{label} 묶음 실패({exc}) → 한 문장씩 다시 부릅니다")
            for sentence in batch:
                try:
                    single = await _judge_batch(client, model_name, [sentence])
                    results[sentence.id] = {**single[0], "model": model_name}
                except (ValidationError, ValueError, KeyError, json.JSONDecodeError) as inner:
                    print(f"    {sentence.id} 실패: {inner}")
                await asyncio.sleep(REQUEST_INTERVAL_SECONDS)
        await asyncio.sleep(REQUEST_INTERVAL_SECONDS)
    return results


def load_previous() -> dict[str, dict[str, str]]:
    """앞서 돌린 결과를 읽는다. `--group`으로 한 묶음만 다시 부를 때 쓴다.

    다시 부르지 않은 묶음까지 빈 값으로 덮으면, 결과 파일만 봐서는 그 묶음을
    대조했는지 아닌지 알 수 없게 된다.
    """
    if not RESULTS_CSV.exists():
        return {}
    previous: dict[str, dict[str, str]] = {}
    with RESULTS_CSV.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if not row.get("llm"):
                continue
            entry = previous.setdefault(row["id"], {})
            entry[row["need"]] = row["llm"]
            entry["reason"] = row.get("llm_reason", "")
            entry["model"] = row.get("model", "")
    return previous


def write_results(
    sentences: list[Sentence], judged: dict[str, dict[str, str]]
) -> list[dict[str, str]]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str]] = []
    for sentence in sentences:
        verdict = judged.get(sentence.id)
        for need, human in sorted(sentence.human.items()):
            llm = (verdict or {}).get(need, "")
            rows.append(
                {
                    "id": sentence.id,
                    "column_kind": sentence.column_kind,
                    "need": need,
                    "sentence_text": sentence.text,
                    "human": human,
                    "llm": llm,
                    # 부르지 못한 문장은 "일치"로 두지 않는다. 조용히 넘어가면
                    # 대조하지 않은 문장이 대조한 것처럼 보인다.
                    "agree": "" if not llm else str(human == llm).lower(),
                    "llm_reason": (verdict or {}).get("reason", ""),
                    # 묶음마다 다른 모델로 돌릴 수 있어 행마다 남긴다. 없으면
                    # 어느 판정이 어느 모델 것인지 나중에 알 수 없다.
                    "model": (verdict or {}).get("model", ""),
                    "place_count": str(sentence.place_count),
                }
            )
    with RESULTS_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def report(rows: list[dict[str, str]]) -> None:
    print(f"\n결과: {RESULTS_CSV}")
    missing = [r for r in rows if not r["agree"]]
    if missing:
        print(f"판정을 받지 못한 행 {len(missing)}개 — 대조하지 못했습니다.")

    for need in sorted({r["need"] for r in rows}):
        scoped = [r for r in rows if r["need"] == need and r["agree"]]
        if not scoped:
            continue
        agreed = sum(1 for r in scoped if r["agree"] == "true")
        print(f"  {need:<18} 일치 {agreed}/{len(scoped)} · 갈림 {len(scoped) - agreed}")

    diverged = [r for r in rows if r["agree"] == "false"]
    print(f"\n다시 볼 문장 {len(diverged)}행")
    if len(diverged) > 60:
        # 갈린 것이 이만큼 많으면 라벨이 틀린 게 아니라 기준이 모호한 것이다.
        # 라벨을 고칠 게 아니라 프롬프트를 먼저 손봐야 한다.
        print("  → 너무 많습니다. 기준이 모호하다는 뜻이니 프롬프트부터 봅니다.")
    for row in sorted(diverged, key=lambda r: -int(r["place_count"]))[:40]:
        text = row["sentence_text"].replace("\n", " ")[:56]
        print(
            f"  {row['place_count']:>4}곳 [{row['need']:<17}] "
            f"사람={row['human']:<10} LLM={row['llm']:<10} {text}"
        )
        print(f"        └ {row['llm_reason'][:96]}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="사람이 매긴 무장애 문장 판정을 Gemini와 대조한다"
    )
    parser.add_argument("--model", help="판정 모델 (생략 시 LLM_FAST_MODEL_NAME)")
    parser.add_argument(
        "--limit", type=int, help="앞에서 이만큼만 대조한다 (묶음 시험용)"
    )
    parser.add_argument(
        "--group",
        choices=("step_free", "visual_guide"),
        help="이 묶음만 다시 부른다. 나머지는 앞서 돌린 결과를 그대로 둔다",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="부르지 않고 보낼 프롬프트와 호출 수만 보여 준다",
    )
    return parser


async def main() -> None:
    args = build_parser().parse_args()
    settings = Settings()
    model_name = args.model or settings.llm_fast_model_name

    sentences = load_sentences()
    if args.limit:
        sentences = sentences[: args.limit]
    # 부를 문장과 파일에 남길 문장은 다르다. `--group`을 주면 한 묶음만 부르고,
    # 나머지 묶음은 앞서 돌린 판정을 그대로 옮겨 적는다.
    targets = [s for s in sentences if not args.group or s.group == args.group]
    counts = collections.Counter(s.group for s in targets)
    calls = sum(-(-count // BATCH_SIZE) for count in counts.values())
    print(f"문장 {len(targets)}개 · {dict(counts)} · 모델 {model_name} · 호출 {calls}번")

    if args.dry_run:
        for group in sorted(counts):
            batch = [s for s in targets if s.group == group][:BATCH_SIZE]
            instruction = (
                _STEP_FREE_INSTRUCTION if group == "step_free" else _VISUAL_GUIDE_INSTRUCTION
            )
            print(f"\n{'=' * 70}\n{group} 기준\n{'=' * 70}\n{instruction}")
            print(f"\n--- 첫 묶음 ---\n{build_prompt(batch)}")
        return

    if not settings.llm_api_key.strip():
        raise ValueError("LLM_API_KEY가 필요합니다.")

    started = time.monotonic()
    client = genai.Client(api_key=settings.llm_api_key)
    judged = await judge(client, model_name, targets)
    if args.group:
        previous = load_previous()
        kept = {key: value for key, value in previous.items() if key not in judged}
        print(f"앞서 돌린 판정 {len(kept)}문장을 그대로 둡니다.")
        judged = {**kept, **judged}
    rows = write_results(sentences, judged)
    print(f"\n걸린 시간 {time.monotonic() - started:.1f}초")
    report(rows)


if __name__ == "__main__":
    asyncio.run(main())
