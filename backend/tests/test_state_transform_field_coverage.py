"""조건 필드가 A→B 연산 변환에서 누락되지 않는지 검사한다.

`state_transform._SINGLE_FIELDS` 등은 하드코딩된 목록이라 UserConditions에
필드를 추가해도 자동으로 따라오지 않는다. 빠지면 **추출은 되는데 연산이
만들어지지 않아 상태 병합에서 값이 조용히 사라진다** — 2026-08-19에
taste_query가 실제로 그렇게 누락됐다(llm_output엔 있고 state엔 null).

타입 검사도 테스트도 이걸 못 잡았다. 필드가 늘 때마다 사람이 기억해야 하는
구조라, 그 기억을 이 테스트로 대신한다.
"""

from __future__ import annotations

import pytest

from app.schemas import UserConditions
from app.services.interpret import state_transform as st
from app.state import field_spec as fs


def test_every_condition_field_has_a_conversion_rule() -> None:
    """어느 목록에도 없는 필드는 A가 말해도 B에 저장되지 않는다."""
    covered = frozenset(st._SINGLE_FIELDS) | frozenset(st._MULTI_FIELDS)
    missing = set(UserConditions.model_fields) - covered

    assert not missing, (
        f"변환 규칙이 없는 조건 필드: {sorted(missing)}. "
        "state_transform의 _SINGLE_FIELDS / _MULTI_FIELDS_UPDATE / "
        "_MULTI_FIELDS_ADD 중 한 곳에 추가해야 상태에 저장된다."
    )


def test_conversion_lists_have_no_unknown_fields() -> None:
    """스키마에서 지운 필드가 목록에 남으면 getattr에서 터진다."""
    covered = frozenset(st._SINGLE_FIELDS) | frozenset(st._MULTI_FIELDS)
    unknown = covered - set(UserConditions.model_fields)

    assert not unknown, f"스키마에 없는 필드가 변환 목록에 있다: {sorted(unknown)}"


@pytest.mark.parametrize("field", sorted(st._SINGLE_FIELDS))
def test_single_fields_are_single_in_the_state_contract(field: str) -> None:
    """단일/복수 분류가 B의 계약과 어긋나면 연산이 거절된다."""
    assert fs.FIELD_SPECS[field].multi is False


@pytest.mark.parametrize("field", sorted(st._MULTI_FIELDS))
def test_multi_fields_are_multi_in_the_state_contract(field: str) -> None:
    assert fs.FIELD_SPECS[field].multi is True


def test_taste_query_survives_full_replace_conversion() -> None:
    """실제로 누락됐던 필드를 회귀 케이스로 고정한다."""
    operations = st._full_replace_operations(
        UserConditions(taste_query="감성적인 사진 찍기 좋은")
    )

    taste_ops = [op for op in operations if op.field == "taste_query"]
    assert len(taste_ops) == 1
    assert taste_ops[0].op == "Update"
    assert taste_ops[0].value == "감성적인 사진 찍기 좋은"
