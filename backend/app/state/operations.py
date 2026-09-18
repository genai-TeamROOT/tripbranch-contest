"""Package B - 조건 변경 연산 모델과 유효성 검증.

계약 문서: docs/package-b/agent-state-contract-v1.md (2.1절, 2.5절)

검증과 적용을 분리한다. 이 모듈은 검증만 담당하며,
적용 도중 State가 절반만 변경되는 상황을 방지한다.
"""

from typing import Any

from pydantic import BaseModel

from app.state import field_spec as fs

# ---------------------------------------------------------------- 무효 사유

REASON_UNKNOWN_FIELD = "unknown_field"
REASON_UNKNOWN_OP = "unknown_op"
REASON_UNSUPPORTED_OPERATION = "unsupported_operation"
REASON_TYPE_MISMATCH = "type_mismatch"
REASON_MISSING_VALUE = "missing_value"
REASON_NULL_VALUE = "null_value"


# ---------------------------------------------------------------- 모델

class Operation(BaseModel):
    """조건 변경 연산 1건. (계약 2.1절)"""

    op: str
    field: str
    value: Any = None

    @property
    def has_value(self) -> bool:
        """value 키가 실제로 전달되었는지.

        value 키 부재(missing_value)와 value: null(null_value)을
        구분하기 위해 필요하다.
        """
        return "value" in self.model_fields_set


class IgnoredOperation(BaseModel):
    """무효 처리된 연산과 사유. (계약 2.5절)"""

    operation: Operation
    reason: str


# ---------------------------------------------------------------- 검증

def validate_one(op: Operation) -> str | None:
    """연산 1건을 검증한다. 유효하면 None, 무효하면 사유 코드를 반환한다."""

    # 1) 연산 이름
    if op.op not in fs.ACCEPTED_OPS:
        return REASON_UNKNOWN_OP

    # 2) 필드 이름
    #    api_context 필드는 "모르는 필드"가 아니라
    #    "이 경로로는 변경할 수 없는 필드"이므로 사유를 구분한다. (계약 6.5절)
    if fs.is_api_context_field(op.field):
        return REASON_UNSUPPORTED_OPERATION
    if not fs.is_known_field(op.field):
        return REASON_UNKNOWN_FIELD

    # 3) Keep은 무동작이므로 이후 검증을 하지 않는다.
    if op.op == fs.OP_KEEP:
        return None

    # 4) 해당 필드가 그 연산을 허용하는지
    if not fs.allows(op.field, op.op):
        return REASON_UNSUPPORTED_OPERATION

    # 5) Remove는 value가 선택 사항이다.
    #    단일 필드: value 무시하고 None으로 되돌림
    #    복수 필드: value 있으면 해당 원소 제거, 없으면 전체 비움
    if op.op == fs.OP_REMOVE:
        if op.has_value and op.value is not None:
            if not fs.matches_type(op.field, op.value):
                return REASON_TYPE_MISMATCH
        return None

    # 6) Add / Update는 value가 필수다.
    if not op.has_value:
        return REASON_MISSING_VALUE
    if op.value is None:
        return REASON_NULL_VALUE
    if not fs.matches_type(op.field, op.value):
        return REASON_TYPE_MISMATCH

    return None


def validate_all(
    operations: list[Operation],
) -> tuple[list[Operation], list[IgnoredOperation]]:
    """전체를 검증해 (유효 연산, 무효 연산) 으로 나눈다.

    적용 전에 전체를 검증하는 이유는, 적용 도중 무효한 연산을 만나면
    State가 절반만 변경된 상태로 남기 때문이다. (계약 2.5절)

    유효 연산의 순서는 입력 순서를 그대로 유지한다.
    B는 연산 순서를 재정렬하지 않는다. (계약 2.4절)
    """
    valid: list[Operation] = []
    ignored: list[IgnoredOperation] = []

    for op in operations:
        reason = validate_one(op)
        if reason is None:
            valid.append(op)
        else:
            ignored.append(IgnoredOperation(operation=op, reason=reason))

    return valid, ignored