"""저장된 파싱 결과를 읽기 경로가 그대로 쓰는지. (TP-231)

읽기 경로가 요청마다 원문을 다시 파싱하던 것을 대체한다. 후보 수만큼 정규식이
돌았고, 파싱이 LLM으로 바뀌면 그 자리에서는 아예 부를 수 없다.
"""

from __future__ import annotations

from app.domain.operating_hours import (
    OPERATING_PARSER_VERSION,
    deserialize_operating_schedule,
    normalize_operating_schedule,
    resolve_operating_schedule,
)
from app.services.place_sync import serialize_operating_schedule

_RAW = "하절기(3월~10월) 09:00~18:00 / 동절기(11월~2월) 09:00~17:00"
_REST = "매주 월요일"


def _fresh():
    return normalize_operating_schedule(
        content_type_id="12", operating_hours=_RAW, rest_date=_REST
    )


class Test왕복:
    """직렬화와 역직렬화가 짝이다. 한쪽만 바뀌면 조용히 폴백으로 떨어진다."""

    def test_저장했다_읽으면_같다(self) -> None:
        original = _fresh()
        restored = deserialize_operating_schedule(
            serialize_operating_schedule(original),
            raw_operating_hours=_RAW,
            raw_rest_date=_REST,
        )
        assert restored is not None
        assert restored.rules == original.rules
        assert restored.closure_rules == original.closure_rules
        assert restored.availability == original.availability

    def test_주기_휴무도_왕복한다(self) -> None:
        """TP-231에서 더한 필드가 저장·복원을 지나 살아남는지."""
        from app.domain.operating_hours import (
            ClosureRule,
            OperatingAvailability,
            OperatingParseStatus,
            OperatingSchedule,
        )

        original = OperatingSchedule(
            raw_operating_hours=_RAW, raw_rest_date=_REST,
            cleaned_operating_hours=None, cleaned_rest_date=None,
            availability=OperatingAvailability.SCHEDULED, rules=(),
            closure_rules=(
                ClosureRule(weekdays=frozenset({6}), source_text="2,4주 일요일",
                            week_ordinals=frozenset({2, 4})),
                ClosureRule(weekdays=frozenset({0}), source_text="월 1회 월요일",
                            uncertain=True),
            ),
            parse_status=OperatingParseStatus.PARSED,
            assumption_reason=None, warnings=(),
        )
        restored = deserialize_operating_schedule(
            serialize_operating_schedule(original),
            raw_operating_hours=_RAW, raw_rest_date=_REST,
        )
        assert restored is not None
        assert restored.closure_rules == original.closure_rules


class Test파서_버전_게이트:
    """파서를 고치고 재적재를 잊었을 때 옛 결과가 조용히 나가는 것을 막는다."""

    def test_버전이_같으면_저장값을_쓴다(self) -> None:
        stored = serialize_operating_schedule(_fresh())
        # 저장값에만 있는 표식을 넣어 실제로 그것을 썼는지 본다.
        stored["warnings"] = ["저장값에서 왔다"]
        result = resolve_operating_schedule(
            content_type_id="12", operating_hours=_RAW, rest_date=_REST,
            stored=stored, stored_parser_version=OPERATING_PARSER_VERSION,
        )
        assert result.warnings == ("저장값에서 왔다",)

    def test_버전이_다르면_원문을_다시_읽는다(self) -> None:
        stored = serialize_operating_schedule(_fresh())
        stored["warnings"] = ["저장값에서 왔다"]
        result = resolve_operating_schedule(
            content_type_id="12", operating_hours=_RAW, rest_date=_REST,
            stored=stored, stored_parser_version="operating-hours-0.0.1",
        )
        assert result.warnings != ("저장값에서 왔다",)
        assert result.rules == _fresh().rules

    def test_저장값이_없으면_원문을_읽는다(self) -> None:
        result = resolve_operating_schedule(
            content_type_id="12", operating_hours=_RAW, rest_date=_REST
        )
        assert result.rules == _fresh().rules

    def test_저장값이_깨졌으면_원문을_읽는다(self) -> None:
        """행 하나가 망가졌다고 그 요청이 죽으면 안 된다."""
        result = resolve_operating_schedule(
            content_type_id="12", operating_hours=_RAW, rest_date=_REST,
            stored={"availability": "그런거없음"},
            stored_parser_version=OPERATING_PARSER_VERSION,
        )
        assert result.rules == _fresh().rules
