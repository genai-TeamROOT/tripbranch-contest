"""연산 유효성 검증.

계약 문서: 2.1절, 2.5절
"""

import pytest

from app.state.operations import (
    REASON_MISSING_VALUE,
    REASON_NULL_VALUE,
    REASON_TYPE_MISMATCH,
    REASON_UNKNOWN_FIELD,
    REASON_UNKNOWN_OP,
    REASON_UNSUPPORTED_OPERATION,
    Operation,
    validate_all,
    validate_one,
)


class TestValidOperations:
    @pytest.mark.parametrize(
        "raw",
        [
            {"op": "Update", "field": "max_travel_time", "value": 15},
            {"op": "Add", "field": "place_tags", "value": ["박물관"]},
            {"op": "Update", "field": "place_types", "value": ["restaurant"]},
            {"op": "Remove", "field": "budget"},
            {"op": "Remove", "field": "place_tags", "value": ["카페"]},
            {"op": "Remove", "field": "place_tags"},
            {"op": "Keep", "field": "companion"},
        ],
    )
    def test_유효한_연산은_None을_반환한다(self, raw):
        assert validate_one(Operation(**raw)) is None


class TestInvalidOperations:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ({"op": "Delete", "field": "budget", "value": "free"}, REASON_UNKNOWN_OP),
            ({"op": "REPLACE", "field": "budget", "value": "free"}, REASON_UNKNOWN_OP),
            ({"op": "Update", "field": "price", "value": 1000}, REASON_UNKNOWN_FIELD),
            ({"op": "Update", "field": "radius_m", "value": 1000}, REASON_UNKNOWN_FIELD),
        ],
    )
    def test_이름_오류(self, raw, expected):
        assert validate_one(Operation(**raw)) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            {"op": "Add", "field": "place_types", "value": ["shopping"]},
            {"op": "Add", "field": "budget", "value": "free"},
        ],
    )
    def test_허용되지_않은_연산(self, raw):
        assert validate_one(Operation(**raw)) == REASON_UNSUPPORTED_OPERATION

    @pytest.mark.parametrize(
        "field", ["gps_location", "api_weather", "gps_location_updated_at"]
    )
    def test_api_context_필드는_unsupported로_처리된다(self, field):
        """unknown_field가 아니라 unsupported_operation이어야 한다.

        "모르는 필드"가 아니라 "이 경로로는 변경할 수 없는 필드"이며,
        A가 6.5절 경로를 써야 한다는 것을 사유로 알 수 있어야 한다.
        """
        op = Operation(op="Update", field=field, value="x")
        assert validate_one(op) == REASON_UNSUPPORTED_OPERATION

    @pytest.mark.parametrize(
        "raw",
        [
            {"op": "Update", "field": "max_travel_time", "value": "30분"},
            {"op": "Update", "field": "max_travel_time", "value": True},
            {"op": "Add", "field": "place_tags", "value": "카페"},
            {"op": "Update", "field": "place_types", "value": "restaurant"},
            {"op": "Remove", "field": "place_tags", "value": "카페"},
        ],
    )
    def test_타입_불일치(self, raw):
        assert validate_one(Operation(**raw)) == REASON_TYPE_MISMATCH


class TestValueDistinction:
    """value 키 부재와 value: null을 구분한다.

    원인이 다르므로 사유도 달라야 한다.
      missing_value : LLM이 값 추출에 실패
      null_value    : 해제 의도를 Remove 대신 null로 표현
    """

    def test_value_키가_없으면_missing_value(self):
        op = Operation(op="Update", field="budget")
        assert validate_one(op) == REASON_MISSING_VALUE

    def test_value가_null이면_null_value(self):
        op = Operation(op="Update", field="budget", value=None)
        assert validate_one(op) == REASON_NULL_VALUE

    def test_두_경우가_다른_사유를_반환한다(self):
        missing = validate_one(Operation(op="Update", field="budget"))
        null = validate_one(Operation(op="Update", field="budget", value=None))
        assert missing != null


class TestValidateAll:
    def test_유효와_무효를_분리한다(self):
        ops = [
            Operation(op="Update", field="max_travel_time", value=15),
            Operation(op="Update", field="price", value=1000),
            Operation(op="Add", field="place_tags", value=["박물관"]),
        ]
        valid, ignored = validate_all(ops)

        assert len(valid) == 2
        assert len(ignored) == 1
        assert ignored[0].reason == REASON_UNKNOWN_FIELD

    def test_유효_연산의_순서가_유지된다(self):
        """B는 연산 순서를 재정렬하지 않는다. (계약 2.4절)"""
        ops = [
            Operation(op="Add", field="place_tags", value=["A"]),
            Operation(op="Update", field="price", value=1),
            Operation(op="Remove", field="place_tags", value=["B"]),
            Operation(op="Update", field="budget", value="free"),
        ]
        valid, _ = validate_all(ops)

        assert [(o.op, o.field) for o in valid] == [
            ("Add", "place_tags"),
            ("Remove", "place_tags"),
            ("Update", "budget"),
        ]

    def test_빈_배열은_빈_결과를_반환한다(self):
        valid, ignored = validate_all([])
        assert valid == []
        assert ignored == []