"""다중 턴 골드셋 CSV를 Langfuse Dataset과 맞춘다.

배경: 골드셋(dev 35건·final 15건)은 지금 `test_results/agent_quality/*.csv`에만 있다.
      `evaluate_agent_quality`가 그걸 읽어 돌리고 결과를 다시 CSV로 남긴다. 그래서
      **회차 비교를 하려면 파일을 열어야 하고, 틀린 케이스가 어느 trace였는지는
      이어지지 않는다.** Langfuse Dataset으로 올리면 케이스가 실행·trace·Score와
      한 화면에서 묶인다.

      **CSV를 대체하지 않는다.** 평가는 Langfuse가 죽어도 돌아야 하고, CSV는 git에
      남아 PR에서 diff로 읽힌다(`sync_langfuse_prompts`가 레포를 폴백으로 두는 것과
      같은 이유). 여기서 하는 일은 **거울을 하나 더 두는 것**이다.

방법: 두 방향. `--push`는 확인 없이 쓰지 않는다.

  --check (기본) 읽기만. 로컬 케이스와 Dataset 항목을 id로 맞춰 대조한다.
                 다르면 종료코드 1.
  --push         로컬 → Langfuse. `--yes` 없이는 무엇이 바뀔지만 보여주고 멈춘다.

  **항목 id는 `<데이터셋 이름>-<case_id>`다.** 같은 id로 다시 올리면 갱신되므로
  (실측 확인) 골드셋을 고쳐도 회차 비교가 끊기지 않는다. 무작위 id를 쓰면 올릴 때마다
  새 항목이 쌓여 "지난번 그 케이스"를 못 짚는다.

  **`case_id`를 그대로 쓰지 않는 이유**: 항목 id가 **데이터셋별이 아니라 프로젝트
  전역**이다(2026-08-26 실측 — 서버가 `item ids are unique per project across
  datasets`로 거부한다). 맨 `DEV-001`은 이 프로젝트의 다른 데이터셋과 부딪힐 수 있고,
  한 번 다른 데이터셋이 그 id를 쓰면 지운 뒤에도 되찾지 못한다.

  파싱·검증은 `evaluate_agent_quality.load_cases()`를 그대로 쓴다. 같은 CSV를 두 곳에서
  각자 읽으면 한쪽만 계약이 바뀌었을 때 조용히 어긋난다.

판정 기준 — 합격/불합격:
  * `--check`가 하나라도 다르면 불합격이다. Dataset이 옛 골드셋이면 회차 비교가
    거짓이 된다 — 무엇으로 잰 건지 모르는 수치가 쌓인다.
  * 원격에만 있고 로컬에 없는 id도 불합격이다. 케이스를 지웠는데 Dataset에 남아 있으면
    실행마다 "그때는 있던 케이스"가 빠진 것으로 보인다.

실행: cd backend && .venv/bin/python -m scripts.sync_langfuse_dataset
      cd backend && .venv/bin/python -m scripts.sync_langfuse_dataset --push --yes

주의: Dataset은 Langfuse 프로젝트를 팀원이 공유한다. 항목을 지우는 기능은 일부러 두지
      않았다 — 실수로 지우면 그 항목을 참조한 과거 실행 기록이 읽기 어려워진다.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any, Final

from app.config import settings
from scripts.evaluate_agent_quality import EvaluationCase, load_cases

# split 하나가 Dataset 하나다. 섞으면 "dev로 잰 수치"와 "final로 잰 수치"가 한 곳에
# 쌓여 비교가 무의미해진다 — final은 변경 직전에만 돌리는 셋이라 성격이 다르다.
DATASET_NAMES: Final[dict[str, str]] = {
    "dev": "agent-quality-dev",
    "final": "agent-quality-final",
}

STATUS_SAME: Final = "same"
STATUS_MISSING: Final = "missing"  # 원격에 없다
STATUS_DIFFERENT: Final = "different"
STATUS_EXTRA: Final = "extra"  # 원격에만 있다


@dataclass(frozen=True)
class ItemPayload:
    """Dataset 항목 하나에 실을 값. 로컬·원격 비교의 단위이기도 하다."""

    input: dict[str, Any]
    expected_output: dict[str, Any]
    metadata: dict[str, Any]

    def normalized(self) -> str:
        """비교용 문자열. 키 순서가 왕복에서 보존된다는 보장이 없어 정렬해서 편다."""

        return json.dumps(
            {"i": self.input, "e": self.expected_output, "m": self.metadata},
            ensure_ascii=False,
            sort_keys=True,
        )


def to_payload(case: EvaluationCase, split: str) -> ItemPayload:
    """골드셋 케이스 하나를 Dataset 항목 모양으로 편다.

    `input`은 **실행에 필요한 것만** 담는다 — 발화와 기기 위치. `title`·`note`는
    사람이 읽는 값이라 `metadata`로 뺀다. 그래야 나중에 이 Dataset으로 실험을 돌릴 때
    `input`을 그대로 요청 본문에 쓸 수 있다.
    """

    return ItemPayload(
        input={"turns": list(case.turns), "device_location": case.device_location},
        expected_output={
            "turn_intents": list(case.expected_turn_intents),
            "final_conditions": case.expected_final_conditions,
        },
        metadata={"title": case.title, "note": case.note, "split": split},
    )


def item_id(case_id: str, split: str) -> str:
    """Dataset 항목 id. 데이터셋 이름을 앞에 붙여 프로젝트 안에서 유일하게 만든다.

    id는 프로젝트 전역이라(모듈 docstring) 맨 `case_id`를 쓰면 다른 데이터셋과
    부딪힌다. 한 번 부딪히면 그쪽 항목을 지워도 id가 풀리지 않는다.
    """

    return f"{DATASET_NAMES[split]}-{case_id}"


@dataclass(frozen=True)
class Comparison:
    case_id: str
    split: str
    status: str
    detail: str = ""


def _client(*, tracing_enabled: bool = False) -> Any | None:
    """Langfuse 클라이언트. 키가 없거나 패키지가 없으면 `None`.

    **기본값이 `tracing_enabled=False`인 게 함정이 될 수 있다.** 이 값이 꺼져 있으면
    span을 만드는 기능이 전부 조용히 죽는다 — `create_score`는 첫머리에서 return하고
    (2026-08-26 Score 41건이 그렇게 날아갔다), `run_experiment`는 trace id가
    `0000…0`인 run item을 만든다(같은 날 3건). 읽기·쓰기만 하는 스크립트는 기본값을
    쓰고, **span이 필요한 쪽은 명시적으로 켠다.**
    """

    missing = [
        name
        for name, value in (
            ("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key),
            ("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key),
        )
        if not value
    ]
    if missing:
        print(f"✗ 환경변수가 비어 있다: {', '.join(missing)}")
        return None
    try:
        from langfuse import Langfuse
    except ModuleNotFoundError:
        print('✗ langfuse 패키지가 없다. pip install -e "." 로 설치한다.')
        return None

    # **`host=`가 아니라 `base_url=`이다.** `host`는 레거시 별칭이고 SDK 안에서
    # `base_url → LANGFUSE_BASE_URL 환경변수 → host → LANGFUSE_HOST` 순으로 밀린다
    # (4.14.5 `Langfuse.__init__`). 셸에 `LANGFUSE_BASE_URL`이 export돼 있으면
    # `host=`로 준 값이 조용히 무시돼 다른 리전으로 나간다.
    client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        base_url=settings.langfuse_base_url,
        tracing_enabled=tracing_enabled,
    )
    if not client.auth_check():
        print(f"✗ 인증 실패 — host={settings.langfuse_base_url} (리전과 키가 맞는지 본다)")
        return None
    print(f"✓ 인증 확인 — host={settings.langfuse_base_url}")
    return client


def _remote_items(client: Any, dataset_name: str) -> dict[str, ItemPayload] | None:
    """Dataset 항목 전체. 데이터셋 자체가 없으면 `None`(= 아직 안 만듦)."""

    try:
        client.api.datasets.get(dataset_name)
    except Exception:
        return None

    items: dict[str, ItemPayload] = {}
    page = 1
    while True:
        listed = client.api.dataset_items.list(dataset_name=dataset_name, page=page, limit=50)
        for item in listed.data:
            items[item.id] = ItemPayload(
                input=item.input or {},
                expected_output=item.expected_output or {},
                metadata=item.metadata or {},
            )
        if page >= (listed.meta.total_pages or 1):
            return items
        page += 1


def compare_split(client: Any, split: str) -> list[Comparison]:
    dataset_name = DATASET_NAMES[split]
    local = {
        item_id(case.case_id, split): to_payload(case, split)
        for case in load_cases(split)  # type: ignore[arg-type]
    }
    remote = _remote_items(client, dataset_name)

    if remote is None:
        return [
            Comparison(case_id, split, STATUS_MISSING, "데이터셋이 아직 없다")
            for case_id in sorted(local)
        ]

    rows: list[Comparison] = []
    for case_id in sorted(local):
        if case_id not in remote:
            rows.append(Comparison(case_id, split, STATUS_MISSING))
        elif remote[case_id].normalized() != local[case_id].normalized():
            rows.append(Comparison(case_id, split, STATUS_DIFFERENT))
        else:
            rows.append(Comparison(case_id, split, STATUS_SAME))
    for case_id in sorted(set(remote) - set(local)):
        rows.append(Comparison(case_id, split, STATUS_EXTRA, "로컬 골드셋에 없다"))
    return rows


def _print_summary(rows: list[Comparison]) -> None:
    counts = {status: sum(1 for row in rows if row.status == status) for status in
              (STATUS_SAME, STATUS_MISSING, STATUS_DIFFERENT, STATUS_EXTRA)}
    print(
        f"\n같음 {counts[STATUS_SAME]} · 원격에 없음 {counts[STATUS_MISSING]} · "
        f"다름 {counts[STATUS_DIFFERENT]} · 원격에만 {counts[STATUS_EXTRA]}  (총 {len(rows)})"
    )


def run_check(splits: tuple[str, ...]) -> int:
    client = _client()
    if client is None:
        return 1

    rows: list[Comparison] = []
    for split in splits:
        split_rows = compare_split(client, split)
        rows.extend(split_rows)
        print(f"\n=== {DATASET_NAMES[split]} ({len(split_rows)}건) ===")
        for row in split_rows:
            if row.status == STATUS_SAME:
                continue
            print(f"  ✗ {row.case_id:<12} {row.status:<10} {row.detail}")

    _print_summary(rows)
    if any(row.status != STATUS_SAME for row in rows):
        print("\n✗ 어긋났다. 이 상태로 잰 수치는 무엇으로 잰 건지 말할 수 없다.")
        return 1
    print("\n✓ 전부 같다.")
    return 0


def run_push(splits: tuple[str, ...], *, confirmed: bool) -> int:
    client = _client()
    if client is None:
        return 1

    failed = 0
    for split in splits:
        dataset_name = DATASET_NAMES[split]
        rows = compare_split(client, split)
        todo = [row for row in rows if row.status in (STATUS_MISSING, STATUS_DIFFERENT)]
        extra = [row for row in rows if row.status == STATUS_EXTRA]

        print(f"\n=== {dataset_name} ===")
        if extra:
            # 지우지 않는다 — 과거 실행이 참조하고 있을 수 있다. 사람이 판단할 일이다.
            print(f"  ※ 로컬에 없는 원격 항목 {len(extra)}개는 그대로 둔다:")
            for row in extra:
                print(f"      {row.case_id}")
        if not todo:
            print("  ✓ 올릴 것이 없다.")
            continue

        print(f"  올릴 항목 {len(todo)}개:")
        for row in todo:
            print(f"      {row.status:<10} {row.case_id}")
        if not confirmed:
            continue

        cases = {
            item_id(case.case_id, split): case
            for case in load_cases(split)  # type: ignore[arg-type]
        }
        try:
            client.create_dataset(
                name=dataset_name,
                description=f"다중 턴 골드셋 ({split}). 정본은 레포 CSV다.",
                metadata={"source": f"test_results/agent_quality/evaluation_{split}.csv"},
            )
        except Exception as exc:  # 이미 있으면 그대로 쓴다
            print(f"  (데이터셋 생성 건너뜀: {type(exc).__name__})")

        for row in todo:
            payload = to_payload(cases[row.case_id], split)
            try:
                client.create_dataset_item(
                    dataset_name=dataset_name,
                    id=row.case_id,
                    input=payload.input,
                    expected_output=payload.expected_output,
                    metadata=payload.metadata,
                )
                print(f"      ✓ {row.case_id}")
            except Exception as exc:
                failed += 1
                print(f"      ✗ {row.case_id} — {type(exc).__name__}: {exc}")

    if not confirmed:
        print("\n실제로 올리려면 --yes 를 붙인다.")
        return 0
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="대조만 한다 (기본)")
    mode.add_argument("--push", action="store_true", help="로컬 골드셋 → Langfuse")
    parser.add_argument("--yes", action="store_true", help="쓰기를 실제로 실행한다")
    parser.add_argument(
        "--split",
        choices=("dev", "final", "all"),
        default="all",
        help="어느 골드셋을 다룰지 (기본 all)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    splits: tuple[str, ...] = (
        ("dev", "final") if args.split == "all" else (args.split,)
    )
    if args.push:
        return run_push(splits, confirmed=args.yes)
    return run_check(splits)


if __name__ == "__main__":
    raise SystemExit(main())
