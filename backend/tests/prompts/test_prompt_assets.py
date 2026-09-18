"""프롬프트 자산과 코드·메타데이터가 어긋나지 않는지 검사한다.

이 테스트가 없던 동안 실제로 벌어진 일: `meta.yaml`이 `template: extract.md`라고 선언했지만
그 파일을 아무 코드도 읽지 않았고, 실제 프롬프트 본문은 `gemini_prompts.py`의 f-string 안에
남아 있었다. 담당자가 `.md`를 고쳐도 서비스 동작은 그대로인 **조용한 실패**였다.

여기서 막는 것:
1. 아무도 읽지 않는 프롬프트 자산(고아 파일)
2. `meta.yaml`이 선언했지만 존재하지 않거나 실제로는 쓰이지 않는 `template`·`bundle`

LLM을 호출하지 않으므로 CI에서 수 초 안에 끝난다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from app.prompts.loader import PROMPT_ROOT
from app.prompts.registry import INTENT_SLOTS, slot_versions, turn_prompt_version
from app.schemas import Intent

_APP_ROOT = Path(__file__).resolve().parents[2] / "app"
_DOC_FILENAMES = {"README.md", "HISTORY.md", "OWNERS.md"}

# 아직 어느 프롬프트에도 조합되지 않은 자산. 여기에 올리려면 "왜 남겨두는지"를 반드시
# 적는다 — 이유 없이 쌓이면 이 테스트가 무의미해진다. 실제로 프롬프트에 넣을 때는
# 출력이 바뀌므로 골드셋 평가와 함께 별도 변경으로 진행한다.
KNOWN_UNCOMPOSED: dict[str, str] = {
    "_shared/rules/factuality.md": (
        "공통 사실성 규칙 초안. 도입하면 답변 계열 프롬프트가 모두 바뀌므로 별도 평가 필요."
    ),
    "_shared/rules/safety.md": (
        "공통 안전 규칙 초안. 도입하면 분류·답변 출력이 바뀌므로 별도 평가 필요."
    ),
    "_shared/rules/service_scope.md": (
        "공통 서비스 범위 규칙 초안. 도입하면 OUT_OF_SCOPE 판정에 영향."
    ),
}


def _asset_paths() -> set[str]:
    return {
        str(path.relative_to(PROMPT_ROOT))
        for path in PROMPT_ROOT.rglob("*.md")
        if "archive" not in path.parts and path.name not in _DOC_FILENAMES
    }


def _referenced_paths() -> set[str]:
    """app/ 안의 모든 Python 소스에서 참조하는 .md 경로 문자열을 모은다.

    여러 줄로 쓰인 render_text(\\n "경로", ...) 호출도 잡도록 호출 형태가 아니라
    문자열 리터럴 자체를 찾는다 — 한 줄 호출만 보는 정규식으로 검사하다 실제 배선된
    파일을 고아로 잘못 집계한 적이 있다.
    """

    referenced: set[str] = set()
    for source in _APP_ROOT.rglob("*.py"):
        if "__pycache__" in source.parts:
            continue
        referenced |= set(re.findall(r'"([^"\n]+\.md)"', source.read_text(encoding="utf-8")))
    return referenced


def _metadata() -> list[tuple[str, dict]]:
    return [
        (path.parent.name, yaml.safe_load(path.read_text(encoding="utf-8")))
        for path in sorted(PROMPT_ROOT.glob("*/meta.yaml"))
    ]


def test_every_prompt_asset_is_loaded_by_code() -> None:
    orphans = _asset_paths() - _referenced_paths() - set(KNOWN_UNCOMPOSED)

    assert not orphans, (
        "어떤 코드도 읽지 않는 프롬프트 자산이 있습니다: "
        f"{sorted(orphans)}. 실제로 조합하거나, 이유를 적어 KNOWN_UNCOMPOSED에 올리거나, "
        "삭제하세요."
    )


def test_known_uncomposed_entries_still_exist() -> None:
    """도입·삭제가 끝난 파일이 예외 목록에 남아 목록이 썩는 것을 막는다."""

    assets = _asset_paths()
    stale = {path for path in KNOWN_UNCOMPOSED if path not in assets}
    assert not stale, f"KNOWN_UNCOMPOSED에 더 이상 존재하지 않는 파일이 있습니다: {sorted(stale)}"

    composed = set(KNOWN_UNCOMPOSED) & _referenced_paths()
    assert not composed, (
        f"이미 프롬프트에 조합된 파일이 KNOWN_UNCOMPOSED에 남아 있습니다: {sorted(composed)}"
    )


@pytest.mark.parametrize(("folder", "meta"), _metadata())
def test_metadata_declares_only_real_and_used_files(folder: str, meta: dict) -> None:
    referenced = _referenced_paths()

    for slot_id, slot in (meta.get("slots") or {}).items():
        template = slot.get("template")
        assert template, f"{folder}/{slot_id}: template이 비어 있습니다."

        # _shared는 조각 모음이라 폴더 기준 상대경로, 인텐트 폴더는 라이브러리 루트 기준.
        template_path = f"{folder}/{template}" if folder != "_shared" else f"_shared/{template}"
        assert (PROMPT_ROOT / template_path).is_file(), (
            f"{folder}/{slot_id}: 선언한 template 파일이 없습니다 — {template_path}"
        )
        if template_path not in KNOWN_UNCOMPOSED:
            assert template_path in referenced, (
                f"{folder}/{slot_id}: template을 선언했지만 어떤 코드도 읽지 않습니다 — "
                f"{template_path}"
            )

        for member in slot.get("bundle") or []:
            assert (PROMPT_ROOT / member).is_file(), (
                f"{folder}/{slot_id}: bundle에 없는 파일이 있습니다 — {member}"
            )
            assert member in referenced, (
                f"{folder}/{slot_id}: bundle에 선언했지만 실제로 조합되지 않습니다 — {member}. "
                "선언은 실제 조합과 일치해야 변경 영향 범위를 신뢰할 수 있습니다."
            )

        evals = slot.get("evals")
        if evals:
            evals_path = f"{folder}/{evals}"
            assert (PROMPT_ROOT / evals_path).is_file(), (
                f"{folder}/{slot_id}: 선언한 평가 경로가 없습니다 — {evals_path}"
            )


def test_every_runtime_slot_has_a_version() -> None:
    """실행 기록(Trace)에 실리는 슬롯이 전부 meta.yaml에 버전을 갖고 있어야 한다.

    turn_prompt_version()은 표에 없는 슬롯을 조용히 건너뛰므로, 오타나 이름 변경이
    생기면 기록에서 슬롯 하나가 소리 없이 빠진다. 여기서 먼저 터뜨린다.
    """

    versions = slot_versions()
    for intent, slots in INTENT_SLOTS.items():
        missing = [slot for slot in slots if slot not in versions]
        assert not missing, (
            f"{intent.value}가 쓰는 슬롯이 meta.yaml에 없습니다 — {missing}. "
            "슬롯 이름을 바꿨다면 INTENT_SLOTS도 함께 고치세요."
        )


def test_every_intent_is_mapped_to_slots() -> None:
    """새 인텐트를 추가하고 INTENT_SLOTS에 등록하지 않으면 버전이 기록되지 않는다."""

    unmapped = [intent.value for intent in Intent if intent not in INTENT_SLOTS]
    assert not unmapped, (
        f"INTENT_SLOTS에 등록되지 않은 인텐트가 있습니다 — {unmapped}. "
        "등록하지 않으면 그 인텐트의 실행 기록에 분류 슬롯 버전만 남습니다."
    )


@pytest.mark.parametrize("intent", list(Intent))
def test_turn_prompt_version_is_readable_and_complete(intent: Intent) -> None:
    """기록 문자열이 사람이 읽을 수 있는 형태이고 슬롯을 빠짐없이 담는지 확인한다."""

    rendered = turn_prompt_version(intent)

    assert rendered, f"{intent.value}: 기록할 프롬프트 버전이 비어 있습니다."
    assert rendered.count("+") == len(INTENT_SLOTS[intent]) - 1
    for slot in INTENT_SLOTS[intent]:
        assert f"{slot}@" in rendered, f"{intent.value}: {slot} 버전이 빠졌습니다 — {rendered}"
