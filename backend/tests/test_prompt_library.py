import pytest

from app.prompts.loader import PROMPT_VARIANT_ENV, load_text, render_text
from app.prompts.registry import metadata_paths


def test_prompt_library_has_metadata_for_all_runtime_intents() -> None:
    names = {path.parent.name for path in metadata_paths()}

    assert {
        "router",
        "recommend",
        "modify",
        "info",
        "compare",
        "general",
        "out_of_scope",
        "schedule",
    } <= names


def test_prompt_loader_reads_shared_text_and_replaces_simple_placeholder() -> None:
    persona = load_text("_shared/persona/trivi.md")
    retry_note = render_text("_shared/rules/validation_retry.md", error="missing field")

    assert "트리비" in persona
    assert "missing field" in retry_note
    assert "{{error}}" not in retry_note


def test_prompt_loader_applies_selected_legacy_variant(monkeypatch) -> None:
    monkeypatch.setenv(PROMPT_VARIANT_ENV, "compare-summary@legacy-1.0.10")

    instruction = load_text("compare/summary_instruction.md")
    persona = load_text("_shared/persona/trivi.md")

    assert "km·직선거리라는 표현" not in instruction
    assert "가장 알맞은 한 곳을 분명히 추천" not in instruction
    assert "한 응답 전체에서 최대 한 번" not in persona


def test_prompt_loader_rejects_unknown_legacy_variant(monkeypatch) -> None:
    monkeypatch.setenv(PROMPT_VARIANT_ENV, "missing@legacy-0.0.0")

    with pytest.raises(ValueError, match="알 수 없는 프롬프트 기준선"):
        load_text("router/context_rules.md")
