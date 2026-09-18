"""Package B - 조건 병합 엔진.

계약 문서: docs/package-b/agent-state-contract-v1.md (2.3~2.8절, 5.5절)

이 모듈은 "어떻게" 병합할지만 담당한다.
"무엇을" 허용할지는 field_spec.py가, 검증은 operations.py가 담당한다.
"""

from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from app.state import field_spec as fs
from app.state.operations import Operation
from app.state.schema import ConditionChangeLog, UserConditions, now_kst

OP_RESET = "Reset"

RESET_SOFT = "soft"
RESET_HISTORY = "history"
RESET_FULL = "full"

# 조건을 초기화하는 reset 종류. history는 이력만 비우므로 포함하지 않는다.
# (계약 5.5절)
CONDITION_CLEARING_SCOPES = frozenset({RESET_SOFT, RESET_FULL})


@dataclass
class MergeResult:
    """병합 결과.

    condition_version 증가와 시각 갱신은 호출 측 책임이며,
    changed 값을 기준으로 판단한다.
    """

    conditions: UserConditions
    change_logs: list[ConditionChangeLog] = dc_field(default_factory=list)
    changed: bool = False
    reset_applied: str | None = None


# ---------------------------------------------------------------- 연산 적용

def _apply_update(current: Any, op: Operation, multi: bool) -> Any:
    """값 전체를 교체한다. 복수 필드도 리스트 전체를 교체한다."""
    return list(op.value) if multi else op.value


def _apply_add(current: Any, op: Operation) -> Any:
    """복수 필드에만 적용된다. 순서를 유지하며 중복 원소는 건너뛴다."""
    merged = list(current)
    for item in op.value:
        if item not in merged:
            merged.append(item)
    return merged


def _apply_remove(current: Any, op: Operation, multi: bool, field: str) -> Any:
    """단일 필드는 None으로, 복수 필드는 원소 제거 또는 전체 비움."""
    if not multi:
        return None

    if not op.has_value or op.value is None:
        return []

    targets = set(op.value)
    return [item for item in current if item not in targets]


def _apply_one(conditions: UserConditions, op: Operation) -> None:
    """검증을 통과한 연산 1건을 conditions에 반영한다."""
    if op.op == fs.OP_KEEP:
        return

    multi = fs.is_multi(op.field)
    current = getattr(conditions, op.field)

    if op.op == fs.OP_UPDATE:
        new_value = _apply_update(current, op, multi)
    elif op.op == fs.OP_ADD:
        new_value = _apply_add(current, op)
    elif op.op == fs.OP_REMOVE:
        new_value = _apply_remove(current, op, multi, op.field)
    else:
        return

    setattr(conditions, op.field, new_value)


def _snapshot(value: Any) -> Any:
    """변경 기록용 값 복사.

    리스트를 그대로 담으면 이후 연산이 같은 객체를 수정해
    기록된 before_value까지 함께 바뀐다.
    """
    return list(value) if isinstance(value, list) else value


# ---------------------------------------------------------------- 본체

def merge_conditions(
    current: UserConditions,
    operations: list[Operation],
    *,
    session_id: str,
    run_id: str,
    reset_scope: str | None = None,
) -> MergeResult:
    """조건 변경 연산을 적용한다.

    operations는 이미 validate_all()을 통과한 유효 연산만 전달받는다.
    이 함수는 유효성을 다시 검사하지 않는다.

    적용 순서 (계약 2.4절):
      1. reset_scope가 조건 초기화 대상이면 먼저 적용
      2. operations를 전달받은 순서대로 순차 적용
      3. 순서를 재정렬하지 않는다
    """
    before_all = current.model_dump()
    merged = current.model_copy(deep=True)

    logs: list[ConditionChangeLog] = []
    seq = 0
    reset_applied: str | None = None

    # 1) reset 선적용
    if reset_scope in CONDITION_CLEARING_SCOPES:
        merged = UserConditions()
        reset_applied = reset_scope
        logs.append(
            ConditionChangeLog(
                session_id=session_id,
                run_id=run_id,
                seq=seq,
                op=OP_RESET,
                field=None,
                reset_scope=reset_scope,
                applied_at=now_kst(),
            )
        )
        seq += 1
    elif reset_scope == RESET_HISTORY:
        # 이력만 초기화한다. 조건은 유지하며 history 모듈이 처리한다.
        reset_applied = reset_scope

    # 2) 연산 순차 적용
    for op in operations:
        before_value = _snapshot(getattr(merged, op.field))
        _apply_one(merged, op)
        after_value = _snapshot(getattr(merged, op.field))

        logs.append(
            ConditionChangeLog(
                session_id=session_id,
                run_id=run_id,
                seq=seq,
                op=op.op,
                field=op.field,
                before_value=before_value,
                after_value=after_value,
                applied_at=now_kst(),
            )
        )
        seq += 1

    # 3) 전후 스냅샷 비교로 실제 변경 여부 판정 (계약 2.7절)
    changed = merged.model_dump() != before_all

    return MergeResult(
        conditions=merged,
        change_logs=logs,
        changed=changed,
        reset_applied=reset_applied,
    )