"""레포의 프롬프트 Markdown과 Langfuse Prompt Management를 맞춘다.

배경: 프롬프트를 Langfuse에서 읽게 되면(`LANGFUSE_PROMPTS_ENABLED=true`) **정본이
      둘이 된다** — git과 Langfuse. 그리고 디스크가 폴백이라, 어긋나면 예외가 아니라
      **조용히 다른 프롬프트로 도는** 상태가 된다. 그게 이 이관의 실제 비용이고
      이 스크립트가 그걸 막는 장치다.

방법: 세 방향. 어느 쪽도 확인 없이는 쓰지 않는다.

  --check  (기본) 읽기만. 43개를 하나씩 대조해 같은지, 없는지, 다른지 낸다.
           다르면 종료코드 1 — 커밋 전·CI에서 돌린다.
  --push   디스크 → Langfuse. **내용이 같으면 올리지 않는다** — create_prompt는
           부를 때마다 새 버전을 만들어서, 무조건 올리면 바뀐 것 없이 버전만
           쌓이고 "언제 뭐가 바뀌었나"를 못 읽게 된다.
  --pull   Langfuse → 디스크. UI에서 고친 것을 레포로 되돌린다. 이걸 안 하면
           레포에 없는 프롬프트로 돈 기록이 남는다.

판정 기준 — 합격/불합격:
  * `--check`가 **하나라도 다르면 불합격**이다. "레포에 없는 지침으로 답변이 나갔다"는
    회귀 판정 자체를 무의미하게 만든다.
  * 원격에만 있고 레포에 없는 이름도 불합격이다 — 지우거나 `--pull` 해야 한다.

실행: cd backend && .venv/bin/python -m scripts.sync_langfuse_prompts
      cd backend && .venv/bin/python -m scripts.sync_langfuse_prompts --push --yes
      cd backend && .venv/bin/python -m scripts.sync_langfuse_prompts --pull --yes

라벨: `--push`가 붙이는 것은 둘이다.
  * `production` — 지금 도는 버전. 새 버전으로 **옮겨간다**.
  * semver(`2.4.0`) — 그게 어느 버전인지 화면에서 바로 읽히게 한다. Langfuse의
    버전은 정수 자동 증가(`create_prompt`에 `version` 인자가 없다)라 `1,2,3,4`는
    우리가 몇 번 올렸는지를 셀 뿐, semver와 대응하지 않는다. 진입 템플릿에만
    붙는다 — 조각은 슬롯 여러 개에 걸쳐 semver가 하나로 안 정해진다.

주의: `--push`는 `production` 라벨을 새 버전으로 옮긴다. 즉 **켜져 있는 서버가
      TTL(기본 60초) 안에 새 프롬프트로 갈아탄다.** 배포 없이 바뀌는 게 이 이관의
      목적이지만, 그래서 되돌릴 것을 먼저 확인하고 눌러야 한다.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from app.config import settings
from app.observability.langfuse_prompts import prompt_name
from app.prompts.loader import PROMPT_ROOT, asset_paths
from app.prompts.registry import (
    CONFIG_SEMVER_KEY,
    CONFIG_SLOT_KEY,
    SLOT_ENTRY_TEMPLATES,
    slot_versions,
)

# 서버가 읽는 라벨. Langfuse는 이 라벨이 가리키는 버전을 기본으로 준다.
PRODUCTION_LABEL: Final = "production"

# Langfuse 라벨이 받는 문자. 소문자·숫자·`_`·`-`·`.`뿐이다(2026-08-25 실측) —
# 대문자도 `@`도 안 받는다. semver는 보통 그대로 통과하지만 프리릴리스 표기에
# 대문자가 섞이면(`2.5.0-RC1`) 서버가 거부한다.
_LABEL_PATTERN: Final = re.compile(r"^[a-z0-9_.-]+$")

STATUS_SAME: Final = "same"
STATUS_MISSING: Final = "missing"  # 원격에 없다
STATUS_DIFFERENT: Final = "different"
STATUS_ERROR: Final = "error"


@dataclass
class Comparison:
    relative_path: str
    name: str
    disk: str
    remote: str | None
    status: str
    detail: str = ""
    config: dict[str, str] = field(default_factory=dict)
    remote_config: dict[str, Any] = field(default_factory=dict)


def asset_config(relative_path: str) -> dict[str, str]:
    """프롬프트와 함께 올릴 메타데이터.

    **진입 템플릿에만 `semver`가 붙는다.** semver는 슬롯 단위인데 Langfuse 버전은
    파일 단위라, 조각(`_shared/rules/budget`)은 슬롯 여러 개에 걸쳐 있어 값이 하나로
    정해지지 않는다. 진입 템플릿은 슬롯과 1:1이라 모호함이 없다.

    이 값을 `registry.live_slot_version()`이 되읽어 관측의 버전 문자열을 만든다 —
    그래야 기록이 "레포에 적힌 값"이 아니라 "실제로 돈 값"을 말한다.
    """

    for slot, template in SLOT_ENTRY_TEMPLATES.items():
        if template != relative_path:
            continue
        version = slot_versions().get(slot)
        if version is None:
            return {}
        return {CONFIG_SLOT_KEY: slot, CONFIG_SEMVER_KEY: version}
    return {}


def push_labels(config: Mapping[str, str]) -> list[str]:
    """새 버전에 붙일 라벨. `production`에 더해 **semver를 라벨로도** 붙인다.

    **Langfuse의 버전은 정수 자동 증가라 semver를 담을 수 없다**(`create_prompt`에
    `version` 인자가 아예 없다). 그래서 `1, 2, 3, 4`는 우리가 몇 번 올렸는지를 셀 뿐,
    `2.4.0`과 대응하지 않는다 — 프롬프트를 안 바꾸고 semver만 올려 다시 밀어도 정수는
    1 오른다. 화면 목록에서 어느 버전이 `2.4.0`인지 눌러보지 않고 알 수 있어야 한다.

    `config.semver`는 **코드가** 읽어 관측의 버전 문자열을 만들고(`live_slot_version`),
    이 라벨은 **사람이** 읽는다. 둘은 같은 값의 다른 쓰임이다.

    **조각(`_shared/rules/*`)에는 안 붙는다.** semver가 슬롯 단위라 여러 슬롯에 걸친
    조각은 값이 하나로 정해지지 않는다(`asset_config` 참고) — 그런 자산은 config가
    비어 있어 여기서도 자연히 빠진다.

    **같은 semver를 다시 올리면 라벨이 새 버전으로 옮겨간다.** 라벨은 이름당 버전
    하나만 가리킨다. 그 상황은 "본문이 바뀌었는데 semver를 안 올렸다"는 뜻이라
    옮겨가는 쪽이 맞다 — 라벨은 항상 "우리가 2.4.0이라고 부르는 그 버전"이다.

    형식에 안 맞는 semver는 **라벨만 빼고 올린다.** 여기서 예외를 올리면 라벨 하나
    때문에 43개 푸시가 통째로 멈춘다.
    """

    labels = [PRODUCTION_LABEL]
    semver = config.get(CONFIG_SEMVER_KEY)
    if semver and _LABEL_PATTERN.fullmatch(semver):
        labels.append(semver)
    return labels


def _client() -> Any | None:
    """Langfuse 클라이언트. **`LANGFUSE_PROMPTS_ENABLED`와 무관하게 만든다.**

    스위치는 "서버가 원격을 읽느냐"이고, 이 스크립트는 그걸 켜기 전에 내용을 맞추는
    도구다. 스위치를 요구하면 "켜야 맞출 수 있고, 맞춰야 켤 수 있는" 교착이 된다.
    """

    missing = [
        variable
        for variable, value in (
            ("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key),
            ("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key),
        )
        if not value
    ]
    if missing:
        print(f"✗ 키가 비어 있다: {', '.join(missing)}")
        return None

    from langfuse import Langfuse

    client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        base_url=settings.langfuse_base_url,
        # 이 스크립트는 프롬프트만 다룬다 — span을 만들 일이 없다.
        tracing_enabled=False,
    )
    if not client.auth_check():
        print(f"✗ 인증 실패 — host={settings.langfuse_base_url}")
        return None
    print(f"✓ 인증 확인 — host={settings.langfuse_base_url}")
    return client


def _disk_text(relative_path: str) -> str:
    # loader.load_text()를 쓰지 않는다 — 그건 스위치가 켜져 있으면 원격을 읽어서,
    # 원격과 원격을 비교하게 된다.
    return (PROMPT_ROOT / relative_path).read_text(encoding="utf-8").strip()


def _remote_text(client: Any, name: str) -> tuple[str | None, dict[str, Any], str]:
    """원격 원문. 없으면 `(None, 사유)`.

    `cache_ttl_seconds=0`으로 캐시를 우회한다 — 대조하는 도구가 캐시된 옛 값을 보면
    "같다"는 판정 자체를 믿을 수 없다.
    """

    try:
        prompt = client.get_prompt(name, label=PRODUCTION_LABEL, cache_ttl_seconds=0)
    except Exception as exc:
        message = str(exc)
        if "not found" in message.lower() or "404" in message:
            return None, {}, "원격에 없음"
        return None, {}, f"{type(exc).__name__}: {message[:120]}"
    text = getattr(prompt, "prompt", None)
    config = getattr(prompt, "config", None)
    if not isinstance(text, str):
        return None, {}, f"text 프롬프트가 아니다({type(text).__name__})"
    return text, config if isinstance(config, dict) else {}, ""


def compare_all(client: Any) -> list[Comparison]:
    rows: list[Comparison] = []
    for relative_path in asset_paths():
        name = prompt_name(relative_path)
        disk = _disk_text(relative_path)
        config = asset_config(relative_path)
        remote, remote_config, detail = _remote_text(client, name)
        if remote is None:
            status = STATUS_MISSING if detail == "원격에 없음" else STATUS_ERROR
        elif remote != disk:
            status = STATUS_DIFFERENT
        elif config and {key: remote_config.get(key) for key in config} != config:
            # 본문은 같은데 semver만 다른 경우다 — 버전만 올리고 안 올린 상태.
            # 관측이 옛 버전 문자열을 계속 적으므로 이것도 어긋남이다.
            status = STATUS_DIFFERENT
            detail = (
                f"본문은 같고 config가 다르다 (레포 {config} / 원격 "
                f"{ {key: remote_config.get(key) for key in config} })"
            )
        else:
            status = STATUS_SAME
        rows.append(
            Comparison(relative_path, name, disk, remote, status, detail, config, remote_config)
        )
    return rows


def _print_summary(rows: list[Comparison]) -> None:
    counts = {status: 0 for status in (STATUS_SAME, STATUS_MISSING, STATUS_DIFFERENT, STATUS_ERROR)}
    for row in rows:
        counts[row.status] += 1
    print(
        f"\n같음 {counts[STATUS_SAME]} · 원격에 없음 {counts[STATUS_MISSING]} · "
        f"다름 {counts[STATUS_DIFFERENT]} · 오류 {counts[STATUS_ERROR]}  (총 {len(rows)})"
    )


def run_check(*, show_diff: bool) -> int:
    client = _client()
    if client is None:
        return 1

    rows = compare_all(client)
    for row in rows:
        if row.status == STATUS_SAME:
            continue
        mark = {STATUS_MISSING: "·", STATUS_DIFFERENT: "✗", STATUS_ERROR: "✗"}[row.status]
        print(f"  {mark} {row.relative_path:<40} {row.status} {row.detail}")
        if show_diff and row.status == STATUS_DIFFERENT and row.remote is not None:
            for line in difflib.unified_diff(
                row.remote.splitlines(),
                row.disk.splitlines(),
                fromfile=f"langfuse:{row.name}",
                tofile=f"disk:{row.relative_path}",
                lineterm="",
                n=1,
            ):
                print(f"      {line}")

    # 레포에 없는데 원격에만 있는 이름도 어긋남이다.
    known = {prompt_name(path) for path in asset_paths()}
    try:
        listed = client.api.prompts.list(limit=100)
        orphans = sorted({meta.name for meta in listed.data} - known)
    except Exception as exc:
        orphans = []
        print(f"  · 원격 목록 조회 실패 — {type(exc).__name__}: {exc}")
    for name in orphans:
        print(f"  ✗ {name:<40} 레포에 없음 (지우거나 --pull)")

    _print_summary(rows)
    bad = [row for row in rows if row.status in (STATUS_DIFFERENT, STATUS_ERROR)]
    if bad or orphans:
        print("\n✗ 어긋났다. 디스크가 폴백이라 이 상태는 조용히 다른 프롬프트로 돈다.")
        return 1
    if any(row.status == STATUS_MISSING for row in rows):
        print("\n· 아직 안 올라간 것이 있다 — --push --yes")
        return 0
    print("\n✓ 전부 같다.")
    return 0


def run_push(*, confirmed: bool, message: str) -> int:
    client = _client()
    if client is None:
        return 1

    rows = compare_all(client)
    todo = [row for row in rows if row.status in (STATUS_MISSING, STATUS_DIFFERENT)]
    blocked = [row for row in rows if row.status == STATUS_ERROR]
    if blocked:
        print("\n✗ 조회에 실패한 것이 있어 올리지 않는다 — 덮어쓸지 판단할 근거가 없다:")
        for row in blocked:
            print(f"    {row.relative_path} — {row.detail}")
        return 1

    if not todo:
        print("\n✓ 올릴 것이 없다. 전부 같다.")
        return 0

    print(f"\n=== 올릴 프롬프트 {len(todo)}개 ===")
    for row in todo:
        print(f"  {row.status:<10} {row.relative_path}")
    print(
        f"\n※ '{PRODUCTION_LABEL}' 라벨이 새 버전으로 옮겨간다. "
        f"켜져 있는 서버는 TTL({settings.langfuse_prompt_cache_ttl_seconds}초) 안에 갈아탄다."
    )
    tagged = [(row, push_labels(row.config)) for row in todo]
    with_semver = [(row, labels[-1]) for row, labels in tagged if len(labels) > 1]
    if with_semver:
        print(f"※ semver 라벨도 함께 붙는다({len(with_semver)}개):")
        for row, semver in with_semver:
            print(f"    {row.relative_path:<40} {semver}")
    skipped = [
        row
        for row in todo
        if row.config.get(CONFIG_SEMVER_KEY) and len(push_labels(row.config)) == 1
    ]
    if skipped:
        print("※ semver 형식이 라벨 규칙에 안 맞아 라벨은 빼고 올린다:")
        for row in skipped:
            print(f"    {row.relative_path:<40} {row.config[CONFIG_SEMVER_KEY]!r}")
    if not confirmed:
        print("\n실제로 올리려면 --yes 를 붙인다.")
        return 0

    failed = 0
    for row in todo:
        try:
            client.create_prompt(
                name=row.name,
                prompt=row.disk,
                labels=push_labels(row.config),
                type="text",
                config=row.config or None,
                commit_message=message,
            )
            print(f"  ✓ {row.relative_path}")
        except Exception as exc:
            failed += 1
            print(f"  ✗ {row.relative_path} — {type(exc).__name__}: {exc}")
    return 1 if failed else 0


def run_relabel(*, confirmed: bool) -> int:
    """이미 올라간 버전에 semver 라벨만 붙인다. **새 버전을 만들지 않는다.**

    `--push`는 본문이 다를 때만 올린다 — 같은 내용으로 버전만 쌓이면 "언제 뭐가
    바뀌었나"를 못 읽기 때문이다. 그래서 라벨 규칙을 나중에 도입하면 **이미 올라간
    것에는 영원히 안 붙는다.** 이 모드가 그 한 번을 메운다.

    `update_prompt(new_labels=...)`는 **기존 라벨에 더한다** — 2026-08-26에 버리는
    프롬프트로 실측했다(`production`·`keepme`가 그대로 남고 새 라벨만 늘었다).
    대체가 아니라서 `production`이 날아갈 걱정 없이 쓸 수 있다.

    `production`이 가리키는 버전에 붙인다 — 지금 실제로 도는 그 버전이다.
    """

    client = _client()
    if client is None:
        return 1

    planned: list[tuple[str, int, str]] = []
    skipped: list[tuple[str, str]] = []
    for relative_path in asset_paths():
        labels = push_labels(asset_config(relative_path))
        if len(labels) == 1:
            continue  # semver가 없는 조각
        semver = labels[-1]
        name = prompt_name(relative_path)
        try:
            prompt = client.get_prompt(name, label=PRODUCTION_LABEL, cache_ttl_seconds=0)
        except Exception as exc:
            skipped.append((relative_path, f"{type(exc).__name__}: {exc}"))
            continue
        if semver in (prompt.labels or []):
            continue  # 이미 붙어 있다
        planned.append((name, prompt.version, semver))

    if skipped:
        print("\n✗ 조회에 실패해 건너뛴다:")
        for relative_path, detail in skipped:
            print(f"    {relative_path} — {detail}")

    if not planned:
        print("\n✓ 붙일 라벨이 없다. 진입 템플릿이 전부 semver 라벨을 갖고 있다.")
        return 1 if skipped else 0

    print(f"\n=== 라벨을 붙일 버전 {len(planned)}개 ===")
    for name, version, semver in planned:
        print(f"  {name:<40} v{version} ← {semver}")
    print("\n※ 새 버전은 생기지 않는다. 기존 라벨도 그대로 남는다.")
    if not confirmed:
        print("\n실제로 붙이려면 --yes 를 붙인다.")
        return 0

    failed = 0
    for name, version, semver in planned:
        try:
            client.update_prompt(name=name, version=version, new_labels=[semver])
            print(f"  ✓ {name} v{version} ← {semver}")
        except Exception as exc:
            failed += 1
            print(f"  ✗ {name} — {type(exc).__name__}: {exc}")
    return 1 if (failed or skipped) else 0


def run_pull(*, confirmed: bool) -> int:
    client = _client()
    if client is None:
        return 1

    rows = compare_all(client)
    todo = [row for row in rows if row.status == STATUS_DIFFERENT and row.remote is not None]
    if not todo:
        print("\n✓ 되돌릴 것이 없다. 원격에만 있는 변경이 없다.")
        return 0

    print(f"\n=== 디스크를 덮어쓸 파일 {len(todo)}개 ===")
    for row in todo:
        added = len(row.remote.splitlines()) - len(row.disk.splitlines())  # type: ignore[union-attr]
        print(f"  {row.relative_path:<40} 줄 수 {added:+d}")
    if not confirmed:
        print("\n실제로 덮어쓰려면 --yes 를 붙인다. git으로 되돌릴 수 있는지 먼저 확인한다.")
        return 0

    for row in todo:
        # 파일 끝 개행은 레포 관례를 따른다 — strip()해서 비교했으므로 여기서 되붙인다.
        (PROMPT_ROOT / row.relative_path).write_text(
            row.remote.rstrip("\n") + "\n",  # type: ignore[union-attr]
            encoding="utf-8",
        )
        print(f"  ✓ {row.relative_path}")
    print("\n※ 프롬프트 본문이 바뀌었다. 슬롯 버전·HISTORY.md·스냅샷 갱신이 필요하다.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="대조만 한다 (기본)")
    mode.add_argument("--push", action="store_true", help="디스크 → Langfuse")
    mode.add_argument("--pull", action="store_true", help="Langfuse → 디스크")
    mode.add_argument(
        "--relabel", action="store_true", help="새 버전 없이 semver 라벨만 붙인다"
    )
    parser.add_argument("--yes", action="store_true", help="쓰기를 실제로 실행한다")
    parser.add_argument("--diff", action="store_true", help="--check에서 차이를 함께 낸다")
    parser.add_argument(
        "--message",
        default="레포 동기화 (scripts.sync_langfuse_prompts --push)",
        help="Langfuse 버전에 남길 커밋 메시지",
    )
    args = parser.parse_args()

    if args.push:
        return run_push(confirmed=args.yes, message=args.message)
    if args.relabel:
        return run_relabel(confirmed=args.yes)
    if args.pull:
        return run_pull(confirmed=args.yes)
    return run_check(show_diff=args.diff)


if __name__ == "__main__":
    sys.exit(main())
