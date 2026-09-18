"""taste_query 조건 필드가 B-A-C 스키마를 왕복하는지 검증한다.

이 필드는 D의 취향 Feature 입력이지만, D는 B의 상태에서 만들어진 조건을 받는다
(state_transform.py::to_agent_conditions). 세 스키마 중 하나라도 빠지면 값이
조용히 사라져 취향 점수가 항상 비게 된다.
"""

from __future__ import annotations

import pytest

from app.agent_context.schemas import (
    UserConditions as ContextUserConditions,
)
from app.schemas import UserConditions
from app.state import field_spec as fs
from app.state.schema import UserConditions as StateUserConditions

_QUERY = "혼자 조용히 쉴 만한"


def test_field_exists_in_all_three_schemas() -> None:
    """하나라도 빠지면 dict 왕복에서 값이 사라진다."""
    for model in (UserConditions, StateUserConditions, ContextUserConditions):
        assert "taste_query" in model.model_fields, model.__module__


def test_state_and_api_schemas_have_identical_fields() -> None:
    """state_transform이 dict 왕복으로 변환하는 전제(필드 이름·개수 동일)를 지킨다."""
    assert set(StateUserConditions.model_fields) == set(UserConditions.model_fields)


def test_value_survives_state_to_api_conversion() -> None:
    """B 상태 -> A 조건 변환에서 취향 발화가 유지돼야 D까지 도달한다."""
    state = StateUserConditions(taste_query=_QUERY)

    converted = UserConditions.model_validate(state.model_dump())

    assert converted.taste_query == _QUERY


def test_field_spec_allows_update_and_remove() -> None:
    """단일 문자열이라 budget과 같은 연산 집합을 갖는다 — Add는 의미가 없다."""
    spec = fs.FIELD_SPECS["taste_query"]

    assert spec.multi is False
    assert spec.value_type is str
    assert spec.allowed_ops == fs.FIELD_SPECS["budget"].allowed_ops


def test_field_spec_matches_schema() -> None:
    """연산 정의와 스키마가 어긋나면 병합이 unknown_field로 거절한다."""
    assert set(fs.FIELD_SPECS) == set(UserConditions.model_fields)


def test_default_is_none_so_existing_callers_are_unaffected() -> None:
    """기존 호출부가 이 필드를 모르고도 동작해야 한다(하위호환)."""
    assert UserConditions().taste_query is None
    assert StateUserConditions().taste_query is None
    assert ContextUserConditions().taste_query is None


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_query_is_rejected_by_context_schema(blank: str) -> None:
    """빈 문자열이 통과하면 빈 질의로 임베딩을 호출하는 낭비가 생긴다."""
    with pytest.raises(ValueError):
        ContextUserConditions(taste_query=blank)
