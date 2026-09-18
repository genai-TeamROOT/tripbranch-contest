"""LLM 호출 하나가 어느 프롬프트 슬롯을 쓰는지가 빠짐없이 선언돼 있는지 검사한다.

`OPERATION_SLOTS`는 관측(Langfuse)에서 generation 하나에 프롬프트 버전을 붙이는 데
쓴다. 여기 빠진 operation은 **오류 없이 버전만 없는 채로** 기록된다 — 나중에
"`recommend.extract` 2.3.0과 2.4.0 중 어느 쪽이 느린가"를 물을 때 그 호출만 통계에서
조용히 빠진다. 새 operation을 추가하면서 매핑을 빠뜨리는 실수를 여기서 잡는다.

`app/providers/gemini.py`의 `operation=` 리터럴을 소스에서 직접 훑는다. 목록을 손으로
복제하면 그 목록 자체가 낡아서, 검사한다는 사실이 오히려 안심만 준다.
"""

from __future__ import annotations

import ast
import pathlib
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import settings
from app.observability import langfuse_prompts
from app.prompts import registry
from app.prompts.loader import asset_paths
from app.prompts.registry import (
    OPERATION_SLOTS,
    operation_prompt_version,
    slot_versions,
)
from app.providers import gemini_prompts

_GEMINI_SOURCE = Path(__file__).resolve().parents[1] / "app" / "providers" / "gemini.py"
_OPERATION_LITERAL = re.compile(r'operation="([a-z_]+)"')


def _declared_operations() -> set[str]:
    found = set(_OPERATION_LITERAL.findall(_GEMINI_SOURCE.read_text(encoding="utf-8")))
    assert found, "gemini.py에서 operation 리터럴을 하나도 못 찾았다 — 정규식이 낡았다."
    return found


def test_every_llm_operation_declares_its_prompt_slot() -> None:
    """gemini.py가 부르는 모든 operation이 매핑에 있어야 한다."""
    missing = _declared_operations() - set(OPERATION_SLOTS)

    assert not missing, (
        f"프롬프트 슬롯이 선언되지 않은 operation: {sorted(missing)}. "
        "app/prompts/registry.py의 OPERATION_SLOTS에 추가한다."
    )


def test_mapping_has_no_operations_that_no_longer_exist() -> None:
    """지워진 operation이 매핑에 남아 있으면 매핑을 읽는 사람이 오해한다."""
    stale = set(OPERATION_SLOTS) - _declared_operations()

    assert not stale, f"gemini.py에 없는 operation이 매핑에 남아 있다: {sorted(stale)}"


def test_every_mapped_slot_actually_exists_in_meta_yaml() -> None:
    """오타난 슬롯 ID는 조용히 None을 만든다 — 버전이 안 붙고 끝난다."""
    versions = slot_versions()
    unknown = {slot for slot in OPERATION_SLOTS.values() if slot not in versions}

    assert not unknown, f"meta.yaml에 없는 슬롯 ID: {sorted(unknown)}"


@pytest.mark.parametrize("operation", sorted(OPERATION_SLOTS))
def test_each_operation_resolves_to_a_versioned_slot(operation: str) -> None:
    rendered = operation_prompt_version(operation)

    assert rendered is not None
    assert rendered.startswith(OPERATION_SLOTS[operation] + "@")


def test_unknown_operation_resolves_to_none_instead_of_raising() -> None:
    """관측이 호출부를 죽이면 안 된다 — 모르는 operation은 버전만 없이 지나간다."""
    assert operation_prompt_version("does_not_exist") is None


def test_answer_slots_are_included_unlike_the_trace_side_mapping() -> None:
    """`INTENT_SLOTS`와 목적이 다르다는 걸 고정한다.

    저쪽은 해석 단계 기록용이라 답변 생성 슬롯을 일부러 뺐다. 이쪽은 호출 하나가 실제로
    무엇을 썼나라서 생성 슬롯까지 넣는다 — **그 호출들이 토큰을 가장 많이 쓴다.**
    """
    mapped = set(OPERATION_SLOTS.values())

    assert "recommend.summary" in mapped
    assert "general.answer" in mapped


# --- 슬롯 → 진입 템플릿: 손으로 선언한 표라 잠근다 -----------------------------


def test_every_slot_declares_its_entry_template() -> None:
    """`prompt=` 링크는 슬롯마다 진입 템플릿이 있어야 걸린다.

    빠뜨리면 그 호출만 조용히 링크 없이 기록된다 — 버전별 지연·비용 집계에서
    통째로 빠지는데, 화면에는 generation이 정상으로 보여서 알아채기 어렵다.
    `OPERATION_SLOTS`에 슬롯을 추가하면 여기도 따라와야 한다.
    """
    assert set(registry.SLOT_ENTRY_TEMPLATES) == set(registry.OPERATION_SLOTS.values())


def test_entry_templates_point_at_real_assets() -> None:
    """선언한 경로가 실제 파일이어야 하고, 동기화 대상 안에 있어야 한다.

    자산 목록 밖을 가리키면 Langfuse에 올라가지 않은 이름을 조회하게 되고, 링크는
    영영 안 걸리는데 오류도 안 난다.
    """
    assets = set(asset_paths())

    for slot, template in sorted(registry.SLOT_ENTRY_TEMPLATES.items()):
        assert template in assets, f"{slot} → {template}"


def test_entry_templates_are_not_fragments_of_one_another() -> None:
    """진입 템플릿은 다른 템플릿에 꽂히는 조각이 아니어야 한다.

    조각을 링크하면 "이 호출이 쓴 프롬프트"가 `_shared/rules/budget` 같은 부품으로
    잡혀, 슬롯 단위 비교가 무의미해진다.
    """
    entries = set(registry.SLOT_ENTRY_TEMPLATES.values())
    fragments = {
        path
        for path in asset_paths()
        if path.startswith("_shared/") or path.endswith("_rules.md")
    }

    assert entries & fragments == set()


# --- TTL 라이브가 반쪽이 되지 않게 잠근다 ---------------------------------------


def test_no_prompt_is_read_at_import_time() -> None:
    """`gemini_prompts.py`는 프롬프트를 **모듈 수준에서 읽지 않는다.**

    읽으면 그 값이 import 시점에 박혀 `LANGFUSE_PROMPTS_ENABLED=true`여도 바뀌지
    않는다. 2026-08-26 이전에는 22곳이 그랬고, 공유 규칙(`_shared/rules/*`)이 전부
    거기 있었다 — UI에서 `recommend/extract`를 고치면 반영되는데 `budget`을 고치면
    아무 일도 안 일어나는, **같은 화면에서 하나는 먹고 하나는 안 먹는** 상태였다.

    부팅 비용도 같은 문제였다. 22곳이 순차 왕복이 되어 import가 0.26초 → 1.76초였다.
    지금은 켜도 꺼도 0.25초다.
    """
    module = ast.parse(
        pathlib.Path(gemini_prompts.__file__).read_text(encoding="utf-8")
    )

    module_level: list[str] = []
    for node in module.body:
        # 함수·클래스 본문은 import 때 안 돈다 — 최상위 문장만 본다.
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            name = getattr(inner.func, "id", None) or getattr(inner.func, "attr", None)
            if name in ("load_text", "render_text") and inner.args:
                argument = inner.args[0]
                module_level.append(
                    argument.value if isinstance(argument, ast.Constant) else "<동적>"
                )

    assert module_level == []


# --- 관측 버전 문자열이 "실제로 돈 것"을 말하게 한다 ---------------------------


def test_version_string_comes_from_meta_yaml_when_prompts_are_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """프롬프트 관리가 꺼져 있으면 레포가 곧 실제로 돈 것이다 — 지금까지와 같다."""
    monkeypatch.setattr(settings, "langfuse_prompts_enabled", False)

    assert registry.live_slot_version("recommend.extract") is None
    expected = registry.slot_versions()["recommend.extract"]
    assert registry.operation_prompt_version("extract_recommend_conditions") == (
        f"recommend.extract@{expected}"
    )


def test_version_string_follows_the_prompt_that_actually_ran(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """켜져 있으면 **원격 프롬프트가 스스로 밝힌 semver**를 적는다.

    `meta.yaml`을 그대로 믿으면 UI에서 고친 뒤 기록이 거짓말을 한다 — 레포는 2.4.0인데
    실제로는 다른 지침이 돌고, 그 상태로 회귀를 판정하면 판정 자체가 무의미해진다.
    """
    monkeypatch.setattr(settings, "langfuse_prompts_enabled", True)
    monkeypatch.setattr(
        langfuse_prompts,
        "prompt_object",
        lambda path: SimpleNamespace(
            config={"slot": "recommend.extract", "semver": "9.9.9"}
        ),
    )

    assert registry.live_slot_version("recommend.extract") == "9.9.9"
    assert registry.operation_prompt_version("extract_recommend_conditions") == (
        "recommend.extract@9.9.9"
    )
    # meta.yaml은 건드리지 않는다 — 레포는 여전히 승인 이력의 정본이다.
    assert registry.slot_versions()["recommend.extract"] != "9.9.9"


def test_version_string_falls_back_when_the_remote_says_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """config가 없거나 조회에 실패하면 `meta.yaml`로 돌아간다 — 버전이 비면 안 된다."""
    monkeypatch.setattr(settings, "langfuse_prompts_enabled", True)
    expected = registry.slot_versions()["recommend.extract"]

    for stub in (None, SimpleNamespace(config=None), SimpleNamespace(config={})):
        monkeypatch.setattr(langfuse_prompts, "prompt_object", lambda path, s=stub: s)
        assert registry.operation_prompt_version("extract_recommend_conditions") == (
            f"recommend.extract@{expected}"
        )
