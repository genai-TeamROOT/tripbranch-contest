"""프롬프트 자산을 Langfuse Prompt Management로 옮기기 전에 전제를 실측한다.

배경: 이관 설계가 측정 안 된 전제 네 개에 기대고 있다. 코드를 읽어서 내린 결론이라
      돌려보기 전에는 단정하지 않는다(CLAUDE.md 판단 기준).

  (1) 부팅 비용 — `prompts.list()`가 본문을 주지 않으므로(메타데이터만) 활성 자산
      43개는 개별 `get`이다. jp.cloud 왕복 × 43이 프로세스 시작에 얼마를 더하는가.
  (2) 문법 호환 — 우리 원문의 `{{name}}`이 Langfuse `compile()`에서 **바이트 단위로
      같은 결과**를 내는가. 지금 `render_text()`는 단순 `str.replace`다.
  (3) 서버측 합성 — `get(resolve=True)`의 프롬프트 의존성이 우리 조각 주입
      (`{{budget_rule}}` 자리에 `_shared/rules/budget.md`)을 대체할 수 있는가.
      **의존성 태그 문법은 SDK 패키지에 없어 서버 문서에만 있다 — 여기서 실증한다.**
  (4) 이름 규칙 — `recommend/extract`처럼 `/`가 든 이름이 폴더로 받아들여지는가.

방법: 세 모드. 기본은 네트워크를 타지 않는다.

  --analyze  (기본) 오프라인. 자산 43개를 훑어 이름 대응·조각/런타임 자리표시자
             분류·위험 문자를 낸다. `gemini_prompts.py`를 AST로 읽어 어느 자리표시자가
             다른 파일을 꽂는 것인지 **추측하지 않고** 판별한다.
  --probe    네트워크. `spike__` 접두어로 프롬프트를 만들어 (1)(2)(3)(4)를 잰다.
             프로젝트에 쓰기를 하므로 `--yes` 없이는 무엇을 만들지만 보여주고 멈춘다.
  --cleanup  `spike__` 접두어 프롬프트를 지운다. 그 접두어 밖은 건드리지 않는다.

판정 기준 — 합격/불합격을 가르는 것:
  * (2) `compile()` 결과가 `render_text()`와 **한 바이트라도 다르면 불합격.** 프롬프트가
    조용히 달라지는 것은 회귀 판정 자체를 무의미하게 만든다.
  * (4) `/` 이름이 거부되면 불합격 — 이름 규칙을 다시 설계해야 한다.
참고로만 보는 것:
  * (1) 왕복 시간. 느리면 스레드풀로 병렬화하면 되므로 설계를 무르지는 않는다.
  * (3) 실패해도 Python 조립을 그대로 쓰면 되므로 이관 자체는 성립한다.

실행: cd backend && .venv/bin/python -m scripts.spike_langfuse_prompts
      cd backend && .venv/bin/python -m scripts.spike_langfuse_prompts --probe --yes
      cd backend && .venv/bin/python -m scripts.spike_langfuse_prompts --cleanup --yes

유닛 소비: 프롬프트 fetch가 무료 플랜 유닛을 먹는지는 **여기서 재지 않는다.** API가
소비량을 돌려주지 않아 UI의 Usage 화면을 사람이 봐야 한다. --probe 전후로 확인한다.
"""

from __future__ import annotations

import argparse
import ast
import csv
import re
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final
from urllib.parse import quote

BACKEND_ROOT: Final = Path(__file__).resolve().parents[1]
RESULTS_DIR: Final = BACKEND_ROOT / "test_results"
RESULTS_CSV: Final = RESULTS_DIR / "langfuse_prompt_spike.csv"

PROMPT_ROOT: Final = BACKEND_ROOT / "app" / "prompts"
GEMINI_PROMPTS: Final = BACKEND_ROOT / "app" / "providers" / "gemini_prompts.py"

# 스파이크가 만드는 프롬프트 이름 접두어. --cleanup이 지우는 범위이기도 하다.
# 실제 자산 이름(`recommend/extract`)과 절대 겹치지 않아야 한다.
SPIKE_PREFIX: Final = "spike__"

# 프롬프트 라이브러리 자산이 아닌 Markdown. 이관 대상이 아니다.
_NON_ASSET_NAMES: Final = frozenset({"HISTORY.md", "README.md", "OWNERS.md"})

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


# ────────────────────────────────────────────────────────── 자산 훑기


@dataclass
class Asset:
    """이관 대상 Markdown 한 건."""

    relative_path: str  # loader.load_text()에 넘기는 값. 예: "recommend/extract.md"
    text: str

    @property
    def langfuse_name(self) -> str:
        """Langfuse 프롬프트 이름. 확장자를 떼면 폴더 구조가 그대로 남는다."""

        return self.relative_path.removesuffix(".md")

    @property
    def placeholders(self) -> set[str]:
        return set(_PLACEHOLDER.findall(self.text))


def collect_assets() -> list[Asset]:
    """활성 프롬프트 자산을 모은다. archive와 문서 파일은 뺀다."""

    assets: list[Asset] = []
    for path in sorted(PROMPT_ROOT.rglob("*.md")):
        if "archive" in path.relative_to(PROMPT_ROOT).parts:
            continue
        if path.name in _NON_ASSET_NAMES:
            continue
        assets.append(
            Asset(
                relative_path=path.relative_to(PROMPT_ROOT).as_posix(),
                # loader.load_text()가 strip()해서 넘기므로 같은 값으로 비교해야 한다.
                text=path.read_text(encoding="utf-8").strip(),
            )
        )
    return assets


# ─────────────────────────────────────────── 자리표시자 분류 (AST)


# 조각 주입의 세 종류. 서버측 합성(`@@@langfusePrompt@@@`)으로 옮길 수 있는지가
# 여기서 갈린다 — 이 분류가 이관 설계의 핵심이다.
KIND_STATIC = "static"  # 상수 그대로. 서버측 합성 가능
KIND_PARAMETERIZED = "parameterized"  # 조각 안에 런타임 자리표시자가 있다. 합성 가능
KIND_CONDITIONAL = "conditional"  # 없을 때는 블록이 사라진다. **합성 불가**


@dataclass
class Injection:
    """`render_text(template, key=<다른 파일 본문>)` 한 건."""

    template: str  # 예: "recommend/extract.md"
    key: str  # 예: "budget_rule"
    source: str | None  # 꽂히는 파일. 상수를 거치지 않으면 None
    lineno: int
    module_level: bool
    kind: str = KIND_STATIC
    via: str | None = None  # 헬퍼 함수를 거쳤으면 그 이름


@dataclass
class Helper:
    """조각을 만들어 돌려주는 지역 함수."""

    name: str
    sources: list[str]
    conditional: bool  # 조각이 아닌 값을 돌려주는 return이 따로 있는가


@dataclass
class CallSites:
    constants: dict[str, str] = field(default_factory=dict)  # 상수명 → 파일
    helpers: dict[str, Helper] = field(default_factory=dict)
    injections: list[Injection] = field(default_factory=list)
    runtime_keys: dict[str, set[str]] = field(default_factory=dict)  # 템플릿 → 런타임 키
    module_level_loads: list[str] = field(default_factory=list)
    in_function_loads: list[str] = field(default_factory=list)


def _literal_path(node: ast.expr) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _loader_call_path(node: ast.expr) -> str | None:
    """`load_text("x.md")` / `render_text("x.md", ...)`이면 그 경로를 돌려준다."""

    if not isinstance(node, ast.Call):
        return None
    func = node.func
    name = getattr(func, "id", None) or getattr(func, "attr", None)
    if name not in ("load_text", "render_text") or not node.args:
        return None
    return _literal_path(node.args[0])


def _renders_inside(node: ast.FunctionDef | ast.AsyncFunctionDef) -> Helper | None:
    """이 함수가 조각을 만들어 돌려주는가.

    `_build_visit_time_rules()`처럼 안에서 `render_text()`를 부르고 그 결과를 돌려주는
    헬퍼를 찾는다. 이걸 안 보면 헬퍼를 거친 주입이 "런타임 값"으로 잘못 분류된다 —
    처음 돌렸을 때 실제로 그렇게 새어나갔다.
    """

    sources: list[str] = []
    for inner in ast.walk(node):
        path = _loader_call_path(inner) if isinstance(inner, ast.Call) else None
        if path is not None:
            sources.append(path)
    if not sources:
        return None

    # 조각을 안 거치고 끝나는 return이 하나라도 있으면 조건부다 —
    # `shown_list_block`이 목록이 비면 ""를 돌려주는 것이 그 경우다.
    conditional = False
    for inner in ast.walk(node):
        if not isinstance(inner, ast.Return) or inner.value is None:
            continue
        if not any(
            _loader_call_path(sub) is not None
            for sub in ast.walk(inner.value)
            if isinstance(sub, ast.Call)
        ):
            conditional = True
    return Helper(name=node.name, sources=sources, conditional=conditional)


def analyze_call_sites() -> CallSites:
    """`gemini_prompts.py`를 읽어 자리표시자가 조각인지 런타임 값인지 가른다.

    이름으로 추측하지 않는다 — `*_rules`로 끝나도 런타임 값일 수 있고 그 반대도 있다.
    실제로 `load_text()` 결과가 흘러 들어가는지를 본다.
    """

    tree = ast.parse(GEMINI_PROMPTS.read_text(encoding="utf-8"))
    sites = CallSites()

    # 1) 모듈 수준 상수: `_BUDGET_RULE = load_text("_shared/rules/budget.md")`
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        path = _loader_call_path(node.value)
        if isinstance(target, ast.Name) and path is not None:
            sites.constants[target.id] = path

    # 1-1) 조각을 만들어 돌려주는 헬퍼 함수 (한 단계 간접 주입)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            helper = _renders_inside(node)
            if helper is not None:
                sites.helpers[node.name] = helper

    # 2) 로딩 시점(모듈 수준 / 함수 안)과 주입 관계
    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.depth = 0

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.depth += 1
            self.generic_visit(node)
            self.depth -= 1

        visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

        def visit_Call(self, node: ast.Call) -> None:
            path = _loader_call_path(node)
            if path is not None:
                bucket = sites.in_function_loads if self.depth else sites.module_level_loads
                bucket.append(path)
                for keyword in node.keywords:
                    if keyword.arg is None:
                        continue
                    value = keyword.value
                    source: str | None = None
                    kind = KIND_STATIC
                    via: str | None = None

                    if isinstance(value, ast.Name):
                        source = sites.constants.get(value.id)
                    elif (nested := _loader_call_path(value)) is not None:
                        source = nested
                        # 직접 render_text(...)를 꽂는데 인자가 붙어 있으면 매개변수 조각이다.
                        if isinstance(value, ast.Call) and value.keywords:
                            kind = KIND_PARAMETERIZED
                    else:
                        # 헬퍼 함수를 거친 간접 주입. `{{key}}` 어디에서든 한 단계는 본다.
                        for sub in ast.walk(value):
                            if not isinstance(sub, ast.Call):
                                continue
                            callee = getattr(sub.func, "id", None) or getattr(
                                sub.func, "attr", None
                            )
                            helper = sites.helpers.get(callee or "")
                            if helper is None:
                                continue
                            source = helper.sources[0]
                            via = helper.name
                            kind = KIND_CONDITIONAL if helper.conditional else KIND_PARAMETERIZED
                            break

                    if source is not None:
                        sites.injections.append(
                            Injection(
                                template=path,
                                key=keyword.arg,
                                source=source,
                                lineno=node.lineno,
                                module_level=self.depth == 0,
                                kind=kind,
                                via=via,
                            )
                        )
                    else:
                        sites.runtime_keys.setdefault(path, set()).add(keyword.arg)
            self.generic_visit(node)

    Visitor().visit(tree)
    return sites


# ─────────────────────────────────────────────────────── 오프라인 분석


def run_analyze() -> int:
    assets = collect_assets()
    sites = analyze_call_sites()
    by_path = {asset.relative_path: asset for asset in assets}

    print(f"=== ① 이관 대상 자산: {len(assets)}개 ===")
    total = sum(len(asset.text.encode()) for asset in assets)
    print(f"총 {total:,} bytes, 평균 {total // max(len(assets), 1):,} bytes")
    widest = max((len(a.relative_path) for a in assets), default=0)
    for asset in assets[:5]:
        print(f"  {asset.relative_path:<{widest}}  →  {asset.langfuse_name}")
    print(f"  … 나머지 {max(len(assets) - 5, 0)}개")

    print("\n=== ② 로딩 시점 ===")
    print(f"모듈 수준(import 때 1회, TTL 라이브에서 안 바뀜) : {len(sites.module_level_loads)}곳")
    print(f"함수 안(요청마다)                                : {len(sites.in_function_loads)}곳")
    frozen = sorted(set(sites.module_level_loads))
    print(f"\n지연화해야 하는 파일 {len(frozen)}개 (C 영역 gemini_prompts.py):")
    for path in frozen:
        print(f"  {path}")

    print("\n=== ③ 자리표시자 분류 ===")
    injected = {inj.key for inj in sites.injections}
    runtime = {key for keys in sites.runtime_keys.values() for key in keys}
    declared = {ph for asset in assets for ph in asset.placeholders}
    print(f"원문에 선언된 자리표시자        : {len(declared)}종")
    print(f"조각 주입(다른 .md가 꽂힌다)    : {len(injected)}종  ← 서버측 합성 후보")
    print(f"런타임 값(요청마다 다르다)      : {len(runtime)}종  ← compile()이 채운다")
    unresolved = declared - injected - runtime
    if unresolved:
        print(f"\n⚠ 어느 쪽도 아닌 자리표시자 {len(unresolved)}종 — 호출부를 못 찾았다:")
        for key in sorted(unresolved):
            print(f"    {{{{{key}}}}}")

    print("\n=== ④ 조각 주입 관계 — 종류별 ===")
    labels = {
        KIND_STATIC: "합성 가능 (상수 그대로)",
        KIND_PARAMETERIZED: "합성 가능 (조각 안 자리표시자는 부모 compile이 채운다)",
        KIND_CONDITIONAL: "합성 불가 — Python 조립을 남긴다",
    }
    for kind in (KIND_STATIC, KIND_PARAMETERIZED, KIND_CONDITIONAL):
        group = [inj for inj in sites.injections if inj.kind == kind]
        print(f"\n  [{kind}] {len(group)}건 — {labels[kind]}")
        for inj in sorted(group, key=lambda i: (i.template, i.key)):
            through = f"  (via {inj.via}())" if inj.via else ""
            print(f"    {inj.template:<34} {{{{{inj.key}}}}}  ←  {inj.source}{through}")

    print("\n=== ⑤ 위험 문자 ===")
    tag_hits = [a.relative_path for a in assets if "@@@" in a.text]
    # `{{name}}`을 지운 뒤 남는 중괄호. Langfuse compile이 어떻게 다루는지 모른다.
    brace_hits = []
    for asset in assets:
        stripped = _PLACEHOLDER.sub("", asset.text)
        if "{" in stripped or "}" in stripped:
            brace_hits.append(asset.relative_path)
    print(f"'@@@'(의존성/미디어 태그와 충돌) : {len(tag_hits)}개 {tag_hits}")
    print(f"단독 중괄호                      : {len(brace_hits)}개 {brace_hits}")

    print("\n=== ⑥ 슬롯 진입점 (generation에 prompt= 로 링크할 대상) ===")
    try:
        from app.prompts.registry import OPERATION_SLOTS
    except Exception as exc:  # pragma: no cover - 진단용
        print(f"  registry를 읽을 수 없다: {exc}")
        return 1

    # 진입점 = 어딘가에 조각으로 꽂히지 않는 템플릿. 헬퍼를 거친 간접 주입도
    # `injected_sources`에 들어가므로 `info/visit_time_rules.md`가 진입점으로
    # 새어나오지 않는다 — 고치기 전에는 실제로 새어나왔다.
    injected_sources = {inj.source for inj in sites.injections}
    entries = sorted(
        (
            {inj.template for inj in sites.injections}
            | set(sites.in_function_loads)
            | set(sites.module_level_loads)
        )
        - injected_sources
    )
    print(f"슬롯 {len(set(OPERATION_SLOTS.values()))}종, operation {len(OPERATION_SLOTS)}개")
    print(f"진입점 후보 {len(entries)}개:")
    for path in entries:
        mark = "" if path in by_path else "  ⚠ 파일 없음"
        print(f"  {path}{mark}")
    print("\n※ slot → 진입점 대응표는 아직 코드에 없다. registry.py에 추가해야 한다(A 영역).")
    return 0


# ──────────────────────────────────────────────────────── 네트워크 측정


def _client():
    """Langfuse 클라이언트. 키가 없으면 여기서 멈춘다."""

    from app.config import settings

    missing = [
        name
        for name, value in (
            ("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key),
            ("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key),
        )
        if not value
    ]
    if missing:
        print(f"✗ 키가 비어 있다: {', '.join(missing)}")
        print("  backend/.env 에 넣고 다시 돌린다. (스파이크는 LANGFUSE_ENABLED와 무관하다)")
        return None

    from langfuse import Langfuse

    client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        base_url=settings.langfuse_base_url,
    )
    if not client.auth_check():
        print(f"✗ 인증 실패 — host={settings.langfuse_base_url}")
        return None
    print(f"✓ 인증 확인 — host={settings.langfuse_base_url}")
    return client


def _probe_targets(assets: list[Asset]) -> list[Asset]:
    """측정에 쓸 표본. 성격이 다른 것을 고른다.

    조각 하나, 런타임 값을 가진 진입점 하나, 가장 큰 것 하나 — 크기와 자리표시자
    유무가 결과를 가를 수 있으므로 한 종류만 재지 않는다.
    """

    by_path = {asset.relative_path: asset for asset in assets}
    picks: list[Asset] = []
    for wanted in ("_shared/rules/budget.md", "info/extract.md"):
        if wanted in by_path:
            picks.append(by_path[wanted])
    largest = max(assets, key=lambda a: len(a.text))
    if largest not in picks:
        picks.append(largest)
    return picks


def run_probe(*, confirmed: bool, rounds: int) -> int:
    assets = collect_assets()
    targets = _probe_targets(assets)

    print(f"=== 만들 프롬프트 {len(targets) + 2}개 (모두 '{SPIKE_PREFIX}' 접두어) ===")
    for asset in targets:
        print(f"  {SPIKE_PREFIX}{asset.langfuse_name}   ({len(asset.text):,} chars)")
    print(f"  {SPIKE_PREFIX}dep_parent   (의존성 태그 실증용)")
    print(f"  {SPIKE_PREFIX}slash/nested/name   (폴더 이름 실증용)")
    if not confirmed:
        print("\n실제로 만들려면 --yes 를 붙인다. 되돌리려면 --cleanup --yes.")
        return 0

    client = _client()
    if client is None:
        return 1

    rows: list[dict[str, object]] = []

    # ── (4) 이름에 '/'가 들어가는가 ──────────────────────────────
    print("\n=== (4) 이름 규칙 — 폴더형 이름 ===")
    nested = f"{SPIKE_PREFIX}slash/nested/name"
    try:
        client.create_prompt(name=nested, prompt="ok {{v}}", labels=["production"], type="text")
        fetched = client.get_prompt(nested, cache_ttl_seconds=0)
        verdict = "합격" if fetched.prompt == "ok {{v}}" else "불합격(본문 불일치)"
    except Exception as exc:
        verdict = f"불합격 — {type(exc).__name__}: {exc}"
    print(f"  {nested} → {verdict}")
    rows.append({"measure": "folder_name", "target": nested, "result": verdict})

    # ── (2) compile() == render_text() ────────────────────────
    print("\n=== (2) 문법 호환 — compile()이 render_text()와 같은가 ===")
    from app.prompts.loader import render_text

    for asset in targets:
        name = f"{SPIKE_PREFIX}{asset.langfuse_name}"
        try:
            client.create_prompt(name=name, prompt=asset.text, labels=["production"], type="text")
        except Exception as exc:
            print(f"  ✗ {name} 생성 실패 — {type(exc).__name__}: {exc}")
            rows.append({"measure": "compile_equal", "target": name, "result": f"생성실패 {exc}"})
            continue

        # 자리표시자에 구분 가능한 값을 넣어 양쪽을 같은 조건으로 만든다.
        values = {key: f"<{key}>" for key in sorted(asset.placeholders)}
        ours = render_text(asset.relative_path, **values)
        try:
            theirs = client.get_prompt(name, cache_ttl_seconds=0).compile(**values)
        except Exception as exc:
            print(f"  ✗ {name} compile 실패 — {type(exc).__name__}: {exc}")
            rows.append(
                {"measure": "compile_equal", "target": name, "result": f"compile실패 {exc}"}
            )
            continue

        if ours == theirs:
            print(f"  ✓ {asset.relative_path}  ({len(values)}개 자리표시자, {len(ours):,} chars)")
            result = "합격"
        else:
            print(f"  ✗ {asset.relative_path} — 결과가 다르다")
            print(f"      우리 {len(ours):,} chars / Langfuse {len(theirs):,} chars")
            for i, (a, b) in enumerate(zip(ours, theirs, strict=False)):
                if a != b:
                    print(f"      첫 차이 {i}번째: {a!r} vs {b!r}")
                    print(f"      …{ours[max(0, i - 40) : i + 40]!r}")
                    break
            result = "불합격"
        rows.append({"measure": "compile_equal", "target": asset.relative_path, "result": result})

    # ── (3) 서버측 의존성 합성 ────────────────────────────────
    print("\n=== (3) 서버측 합성 — 의존성 태그가 통하는가 ===")
    child = targets[0]
    child_name = f"{SPIKE_PREFIX}{child.langfuse_name}"
    parent_name = f"{SPIKE_PREFIX}dep_parent"
    # 문서에만 있는 문법이라 두 형태를 다 시도한다.
    candidates = [
        f"@@@langfusePrompt:name={child_name}|label=production@@@",
        f"@@@langfusePrompt:name={child_name}|version=1@@@",
    ]
    dep_verdict = "미지원 또는 문법 불일치"
    for index, tag in enumerate(candidates):
        name = parent_name if index == 0 else f"{parent_name}{index}"
        try:
            client.create_prompt(
                name=name, prompt=f"HEAD\n{tag}\nTAIL", labels=["production"], type="text"
            )
            resolved = client.get_prompt(name, cache_ttl_seconds=0).prompt
        except Exception as exc:
            print(f"  ✗ {tag[:48]}… — {type(exc).__name__}: {exc}")
            continue
        if "@@@" not in resolved and child.text[:30] in resolved:
            dep_verdict = f"지원 — {tag.split('|')[1].rstrip('@')}"
            print(f"  ✓ 합성됨 ({len(resolved):,} chars) — {tag}")
            break
        print(f"  · 태그가 그대로 남았다 — {tag}")
    print(f"  판정: {dep_verdict}")
    rows.append({"measure": "dependency", "target": parent_name, "result": dep_verdict})

    # ── (5) 매개변수 조각 — 합성 뒤에도 부모 compile이 채우는가 ──
    #
    # 이게 이관에서 가장 위험한 가정이다. `info/extract.md`가 `{{visit_time_rules}}`
    # 자리에 `info/visit_time_rules.md`를 꽂는데, 그 조각 안에는 또
    # `{{reference_date}}`가 있다. 서버가 조각을 끼운 뒤 그 자리표시자가 살아남아야
    # 부모 `compile()`이 채울 수 있다. 죽으면 이 슬롯은 서버측 합성을 못 쓴다.
    print("\n=== (5) 매개변수 조각 — 합성 뒤 자리표시자가 살아남는가 ===")
    inner_name = f"{SPIKE_PREFIX}param_child"
    outer_name = f"{SPIKE_PREFIX}param_parent"
    param_verdict = "불합격"
    try:
        client.create_prompt(
            name=inner_name,
            prompt="기준일은 {{reference_date}}다.",
            labels=["production"],
            type="text",
        )
        client.create_prompt(
            name=outer_name,
            prompt=f"HEAD\n@@@langfusePrompt:name={inner_name}|label=production@@@\nTAIL",
            labels=["production"],
            type="text",
        )
        fetched = client.get_prompt(outer_name, cache_ttl_seconds=0)
        raw = fetched.prompt
        compiled = fetched.compile(reference_date="2026-08-26")
        print(f"  합성 결과   : {raw!r}")
        print(f"  compile 결과: {compiled!r}")
        print(f"  선언된 변수 : {sorted(fetched.variables)}")
        if compiled == "HEAD\n기준일은 2026-08-26다.\nTAIL":
            param_verdict = "합격 — 조각 안 자리표시자가 부모 compile로 채워진다"
            print(f"  ✓ {param_verdict}")
        else:
            print("  ✗ 기대와 다르다 — 이 슬롯은 Python 조립을 남겨야 한다")
    except Exception as exc:
        param_verdict = f"불합격 — {type(exc).__name__}: {exc}"
        print(f"  ✗ {param_verdict}")
    rows.append(
        {"measure": "parameterized_fragment", "target": outer_name, "result": param_verdict}
    )

    # ── (1) 부팅 비용 ─────────────────────────────────────────
    print(f"\n=== (1) 부팅 비용 — 왕복 {rounds}회 ===")
    name = f"{SPIKE_PREFIX}{targets[0].langfuse_name}"
    cold: list[float] = []
    for _ in range(rounds):
        started = time.perf_counter()
        client.get_prompt(name, cache_ttl_seconds=0)  # 캐시 우회 = 매번 실측
        cold.append((time.perf_counter() - started) * 1000)
    warm_started = time.perf_counter()
    for _ in range(rounds):
        client.get_prompt(name)  # 캐시 적중
    warm = (time.perf_counter() - warm_started) * 1000 / rounds

    median = statistics.median(cold)
    print(f"  캐시 우회 1회 : 중위 {median:.0f}ms  (최소 {min(cold):.0f} / 최대 {max(cold):.0f})")
    print(f"  캐시 적중 1회 : {warm:.3f}ms")
    print(f"  → 순차 43회 추정 : {median * 43 / 1000:.1f}초")

    pool_started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: client.get_prompt(name, cache_ttl_seconds=0), range(43)))
    pooled = time.perf_counter() - pool_started
    print(f"  → 43회 8스레드 실측 : {pooled:.1f}초")
    rows.append(
        {
            "measure": "latency_ms_median",
            "target": name,
            "result": f"{median:.0f}",
        }
    )
    rows.append(
        {"measure": "serial_43_sec_est", "target": "-", "result": f"{median * 43 / 1000:.1f}"}
    )
    rows.append({"measure": "pooled_43_sec", "target": "-", "result": f"{pooled:.1f}"})

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with RESULTS_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["measure", "target", "result"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n결과: {RESULTS_CSV.relative_to(BACKEND_ROOT)}")
    print("정리: .venv/bin/python -m scripts.spike_langfuse_prompts --cleanup --yes")
    print("\n※ 유닛 소비는 API가 알려주지 않는다 — Langfuse UI의 Usage를 직접 확인한다.")
    return 0


def _delete_prompt(name: str) -> tuple[bool, str]:
    """프롬프트 하나를 지운다. 실패하면 이유를 돌려준다.

    **SDK의 `api.prompts.delete()`를 쓰지 않는다.** 이름에 `/`가 들어가면 그게
    경로 구분자로 나가서 라우트가 안 잡히고, API가 아니라 웹앱의 404 HTML이 온다
    (2026-08-25에 `spike__slash/nested/name`으로 실제로 겪었다 — 슬래시 없는 이름만
    지워졌다). `quote(safe="")`로 인코딩하면 204다. 우리 자산 이름은 전부 폴더형이라
    이관 도구는 반드시 이 경로를 타야 한다.
    """

    import httpx

    from app.config import settings

    url = f"{settings.langfuse_base_url.rstrip('/')}/api/public/v2/prompts/{quote(name, safe='')}"
    response = httpx.request(
        "DELETE",
        url,
        auth=(settings.langfuse_public_key, settings.langfuse_secret_key),
        timeout=20,
    )
    if response.status_code in (200, 204):
        return True, ""
    # 의존성이 걸린 경우 400 + 어느 프롬프트가 막고 있는지 알려준다.
    detail = response.text[:200].replace("\n", " ")
    return False, f"{response.status_code} {detail}"


def run_cleanup(*, confirmed: bool) -> int:
    client = _client()
    if client is None:
        return 1

    listed = client.api.prompts.list(limit=100)
    names = sorted({meta.name for meta in listed.data if meta.name.startswith(SPIKE_PREFIX)})
    if not names:
        print(f"'{SPIKE_PREFIX}' 접두어 프롬프트가 없다. 지울 것 없음.")
        return 0

    print(f"=== 지울 프롬프트 {len(names)}개 ===")
    for name in names:
        print(f"  {name}")
    if not confirmed:
        print("\n실제로 지우려면 --yes 를 붙인다.")
        return 0

    # Langfuse가 의존성 순서를 강제한다 — 부모가 남아 있으면 조각을 못 지운다.
    # 어느 쪽이 부모인지 API가 알려주지 않으므로 더 못 지울 때까지 돌린다.
    pending = list(names)
    while pending:
        stuck: list[tuple[str, str]] = []
        for name in pending:
            ok, reason = _delete_prompt(name)
            if ok:
                print(f"  ✓ {name}")
            else:
                stuck.append((name, reason))
        if len(stuck) == len(pending):
            print(f"\n✗ {len(stuck)}개를 못 지웠다:")
            for name, reason in stuck:
                print(f"    {name} — {reason}")
            return 1
        pending = [name for name, _ in stuck]
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--analyze", action="store_true", help="오프라인 분석 (기본)")
    mode.add_argument("--probe", action="store_true", help="네트워크 측정. 프롬프트를 만든다")
    mode.add_argument("--cleanup", action="store_true", help="spike__ 프롬프트를 지운다")
    parser.add_argument("--yes", action="store_true", help="쓰기/삭제를 실제로 실행한다")
    parser.add_argument("--rounds", type=int, default=5, help="왕복 측정 횟수 (기본 5)")
    args = parser.parse_args()

    if args.probe:
        return run_probe(confirmed=args.yes, rounds=args.rounds)
    if args.cleanup:
        return run_cleanup(confirmed=args.yes)
    return run_analyze()


if __name__ == "__main__":
    sys.exit(main())
