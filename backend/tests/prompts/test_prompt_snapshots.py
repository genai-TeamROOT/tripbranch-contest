"""프롬프트 렌더 결과 골든 스냅샷.

역할: 프롬프트 본문을 Python f-string에서 Markdown 자산으로 옮기는 이관이 **텍스트를 한
글자도 바꾸지 않았음**을 바이트 단위로 증명한다. 기존 test_llm_provider.py의 부분 문자열
단언은 "있어야 할 문장이 있는지"만 보므로, 의도치 않게 사라지거나 추가된 문장은 잡지
못한다. 이 스냅샷이 그 빈틈을 덮는다.

스냅샷을 의도적으로 갱신할 때만 아래로 재생성한다(diff를 반드시 눈으로 확인할 것):

    UPDATE_PROMPT_SNAPSHOTS=1 .venv/bin/python -m pytest tests/prompts -q
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.providers import gemini_prompts
from tests.prompts import fixtures

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"
_UPDATE = os.getenv("UPDATE_PROMPT_SNAPSHOTS") == "1"


def _assert_snapshot(name: str, rendered: str) -> None:
    path = SNAPSHOT_DIR / f"{name}.txt"
    if _UPDATE:
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
        return
    assert path.is_file(), (
        f"스냅샷이 없습니다: {path.name}. "
        "UPDATE_PROMPT_SNAPSHOTS=1로 생성한 뒤 내용을 확인하세요."
    )
    expected = path.read_text(encoding="utf-8")
    assert rendered == expected, f"{name} 렌더 결과가 스냅샷과 다릅니다."


@pytest.mark.parametrize(("name", "kwargs"), fixtures.CLASSIFY_CASES)
def test_classify_snapshot(name: str, kwargs: dict) -> None:
    _assert_snapshot(name, gemini_prompts.build_intent_classification_instruction(**kwargs))


def test_recommend_extract_snapshot() -> None:
    _assert_snapshot(
        "recommend_extract", gemini_prompts.build_recommend_extraction_instruction()
    )


@pytest.mark.parametrize(("name", "kwargs"), fixtures.MODIFY_CASES)
def test_modify_extract_snapshot(name: str, kwargs: dict) -> None:
    rendered = gemini_prompts.build_modify_extraction_instruction(
        fixtures.current_conditions(), **kwargs
    )
    _assert_snapshot(name, rendered)


@pytest.mark.parametrize(("name", "kwargs"), fixtures.INFO_CASES)
def test_info_extract_snapshot(name: str, kwargs: dict) -> None:
    _assert_snapshot(name, gemini_prompts.build_info_extraction_instruction(**kwargs))


@pytest.mark.parametrize(("name", "kwargs"), fixtures.COMPARE_CASES)
def test_compare_extract_snapshot(name: str, kwargs: dict) -> None:
    _assert_snapshot(name, gemini_prompts.build_compare_extraction_instruction(**kwargs))


def test_general_extract_snapshot() -> None:
    _assert_snapshot("general_extract", gemini_prompts.build_general_extraction_instruction())


@pytest.mark.parametrize(("name", "topic"), fixtures.GENERAL_ANSWER_TOPICS)
def test_general_answer_snapshot(name: str, topic) -> None:
    _assert_snapshot(name, gemini_prompts.build_general_answer_instruction(topic))


@pytest.mark.parametrize(("name", "question_type"), fixtures.INFO_ANSWER_QUESTION_TYPES)
def test_info_answer_snapshot(name: str, question_type: str) -> None:
    _assert_snapshot(name, gemini_prompts.build_info_answer_instruction(question_type))


@pytest.mark.parametrize(("name", "intent"), fixtures.SUMMARY_INTENTS)
def test_recommendation_summary_snapshot(name: str, intent) -> None:
    _assert_snapshot(name, gemini_prompts.build_recommendation_summary_instruction(intent))


def test_place_reason_snapshot() -> None:
    """인자가 없는 슬롯이라 케이스도 하나다(순위·조건·사용자 조건을 넘기지 않는다)."""

    _assert_snapshot("place_reason", gemini_prompts.build_place_reason_instruction())


def test_review_evidence_filter_snapshot() -> None:
    """근거 선별은 판정만 하는 호출이라 페르소나도 인자도 없다."""

    _assert_snapshot(
        "review_evidence_filter",
        gemini_prompts.build_review_evidence_filter_instruction(),
    )


def test_review_answer_snapshot() -> None:
    _assert_snapshot(
        "review_answer", gemini_prompts.build_review_answer_instruction()
    )


@pytest.mark.parametrize(("name", "criteria"), fixtures.COMPARE_SUMMARY_CRITERIA)
def test_compare_summary_snapshot(name: str, criteria) -> None:
    _assert_snapshot(name, gemini_prompts.build_compare_summary_instruction(criteria))


@pytest.mark.parametrize(("name", "kwargs"), fixtures.SCHEDULE_PLAN_CASES)
def test_schedule_plan_snapshot(name: str, kwargs: dict) -> None:
    _assert_snapshot(name, gemini_prompts.build_schedule_planning_instruction(**kwargs))


def test_schedule_plan_context_snapshot() -> None:
    rendered = gemini_prompts.format_schedule_planning_context(
        fixtures.planning_request(), fixtures.START_TIME
    )
    _assert_snapshot("schedule_plan_context", rendered)


def test_schedule_plan_context_with_must_include_snapshot() -> None:
    """보관함에 담긴 장소가 [반드시 포함]에 이름과 함께 실린다. (SCHEDULE-12)"""

    rendered = gemini_prompts.format_schedule_planning_context(
        fixtures.planning_request_with_must_include(), fixtures.START_TIME
    )
    _assert_snapshot("schedule_plan_context__must_include", rendered)


def test_schedule_fill_snapshot() -> None:
    _assert_snapshot("schedule_fill", gemini_prompts.build_schedule_fill_instruction())


def test_schedule_fill_context_snapshot() -> None:
    rendered = gemini_prompts.format_schedule_fill_context(
        fixtures.fill_request(), fixtures.START_TIME
    )
    _assert_snapshot("schedule_fill_context", rendered)


def test_validation_retry_note_snapshot() -> None:
    rendered = gemini_prompts.format_validation_retry_note(ValueError("items: field required"))
    _assert_snapshot("validation_retry_note", rendered)
