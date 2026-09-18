"""Package B - 조건 필드 정의와 필드별 허용 연산.

계약 문서: docs/package-b/agent-state-contract-v1.md (1.2절, 2.2절)

이 모듈은 "무엇을" 정의하고, merge.py는 "어떻게"를 담당한다.
Package A와의 협의 결과가 바뀌면 이 파일만 수정한다.
"""

from dataclasses import dataclass

# ---------------------------------------------------------------- 연산

OP_ADD = "Add"
OP_UPDATE = "Update"
OP_REMOVE = "Remove"

VALID_OPS = frozenset({OP_ADD, OP_UPDATE, OP_REMOVE})

# Keep은 무동작 연산이다. State를 변경하지 않고 condition_version도 올리지 않으며,
# "A가 유지를 명시적으로 판단했다"는 신호로 변경 기록에만 남긴다. (계약 2.1절)
OP_KEEP = "Keep"
ACCEPTED_OPS = VALID_OPS | {OP_KEEP}


# ---------------------------------------------------------------- 스펙

@dataclass(frozen=True)
class FieldSpec:
    """조건 필드 1개의 정의."""

    name: str
    multi: bool                    # 복수 필드 여부
    allowed_ops: frozenset[str]    # 허용 연산
    value_type: type               # 검증할 파이썬 타입 (list는 원소 타입 str 고정)


def _single(name: str, value_type: type, *ops: str) -> FieldSpec:
    return FieldSpec(name, False, frozenset(ops), value_type)


def _multi(name: str, *ops: str) -> FieldSpec:
    return FieldSpec(name, True, frozenset(ops), list)


# P0-1 확정(07-24, agent-state-contract-v1.md 1216·1265행 참고):
# conditions-schema.md v0.3 4절 기준. 모든 필드가 Remove를 허용한다.
# v0.3에서 current_location의 필수 지위가 api_context.gps_location으로 이관되었다.
FIELD_SPECS: dict[str, FieldSpec] = {
    # 위치
    "current_location": _single("current_location", str, OP_UPDATE, OP_REMOVE),
    "search_center":        _single("search_center", str, OP_UPDATE, OP_REMOVE),

    # 장소 유형
    "place_types":          _multi("place_types", OP_UPDATE, OP_REMOVE),
    "place_tags":           _multi("place_tags", OP_ADD, OP_UPDATE, OP_REMOVE),

    # 날씨
    "weather":              _single("weather", str, OP_UPDATE, OP_REMOVE),
    "weather_intent":       _single("weather_intent", str, OP_UPDATE, OP_REMOVE),

    "concentration_intent": _single("concentration_intent", str, OP_UPDATE, OP_REMOVE),

    # 이동
    "transport":            _single("transport", str, OP_UPDATE, OP_REMOVE),
    "max_travel_time":      _single("max_travel_time", int, OP_UPDATE, OP_REMOVE),
    "travel_origin":        _single("travel_origin", str, OP_UPDATE, OP_REMOVE),

    # 시간
    "time_available":       _single("time_available", int, OP_UPDATE, OP_REMOVE),

    # 환경·동행·예산
    "environment":          _single("environment", str, OP_UPDATE, OP_REMOVE),
    "companion":            _single("companion", str, OP_UPDATE, OP_REMOVE),
    "budget":               _single("budget", str, OP_UPDATE, OP_REMOVE),

    # 취향 — 벡터 검색 질의로 쓰는 자유 문장. 리스트가 아닌 이유는 여러 개를
    # 합치면 임베딩이 뭉개지기 때문이다(한 문장 = 한 질의). special_requirements와
    # 분리한 이유는 그 필드가 "기타 전부"를 받아 일정·교통 조건이 섞이기 때문이다.
    "taste_query":          _single("taste_query", str, OP_UPDATE, OP_REMOVE),

    # 태그
    "exclude_tags":         _multi("exclude_tags", OP_ADD, OP_REMOVE),
    "special_requirements": _multi("special_requirements", OP_ADD, OP_REMOVE),

    # 무장애 요구(TP-207). exclude_tags와 동일 스펙 — 여럿이면 AND로 좁히는 목록이라
    # Update가 아니라 Add/Remove로 부분 변경한다.
    "accessibility_needs":  _multi("accessibility_needs", OP_ADD, OP_REMOVE),
}

# api_context 필드. operations 대상이 아니며 별도 경로로 갱신한다.
# field로 지정되면 unknown_field가 아니라 unsupported_operation으로 처리한다.
# (계약 2.5절, 6.5절)
API_CONTEXT_FIELDS = frozenset({
    "gps_location",
    "api_weather",
    "gps_location_updated_at",
    "api_weather_updated_at",
    # PR #188: 위치 재확인 UX 전용, gps_location_updated_at(기술적 TTL)과 별개.
    "gps_location_confirmed_at",
})


# ---------------------------------------------------------------- 조회

def get_spec(field: str) -> FieldSpec | None:
    """필드 정의를 반환한다. 정의되지 않은 필드면 None."""
    return FIELD_SPECS.get(field)


def is_known_field(field: str) -> bool:
    return field in FIELD_SPECS


def is_api_context_field(field: str) -> bool:
    return field in API_CONTEXT_FIELDS


def allows(field: str, op: str) -> bool:
    """해당 필드가 그 연산을 허용하는지."""
    spec = FIELD_SPECS.get(field)
    return spec is not None and op in spec.allowed_ops


def is_multi(field: str) -> bool:
    spec = FIELD_SPECS.get(field)
    return spec is not None and spec.multi


def matches_type(field: str, value: object) -> bool:
    """value가 해당 필드의 타입 규격에 맞는지.

    복수 필드는 list[str]이어야 하며, 원소가 하나여도 리스트로 전달한다.
    (계약 2.1절)
    """
    spec = FIELD_SPECS.get(field)
    if spec is None:
        return False

    if spec.multi:
        return isinstance(value, list) and all(isinstance(x, str) for x in value)

    # bool은 int의 하위 타입이므로 명시적으로 배제한다.
    if spec.value_type is int:
        return isinstance(value, int) and not isinstance(value, bool)

    return isinstance(value, spec.value_type)


def empty_value(field: str):
    """Remove 시 되돌릴 기본값. 단일 필드는 None, 복수 필드는 빈 리스트."""
    return [] if is_multi(field) else None