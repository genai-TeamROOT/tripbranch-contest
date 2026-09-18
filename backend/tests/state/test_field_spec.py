"""필드 정의와 스키마의 정합성 검사.

계약 문서: 1.2절, 2.2절
"""

import pytest

from app.state import field_spec as fs
from app.state.schema import ApiContext, UserConditions


class TestSchemaConsistency:
    """스키마와 스펙 표가 어긋나지 않는지 검사한다.

    필드를 추가할 때 한쪽만 고치는 실수를 잡는 것이 목적이다.
    """

    def test_필드_목록이_스키마와_일치한다(self):
        schema_fields = set(UserConditions.model_fields.keys())
        spec_fields = set(fs.FIELD_SPECS.keys())
        assert schema_fields == spec_fields

    def test_필드는_18개다(self):
        # 2026-08-19: taste_query 추가(취향 발화 전용). 15 -> 16.
        # 2026-08-22: travel_origin 추가(이동시간 출발점 판정, D-071). 16 -> 17.
        # 2026-09-02: accessibility_needs 추가(무장애 요구, TP-207). 17 -> 18.
        assert len(fs.FIELD_SPECS) == 18

    def test_api_context_필드가_스키마와_일치한다(self):
        schema_fields = set(ApiContext.model_fields.keys())
        assert schema_fields == set(fs.API_CONTEXT_FIELDS)

    def test_조건_필드와_api_필드는_겹치지_않는다(self):
        assert not (set(fs.FIELD_SPECS) & set(fs.API_CONTEXT_FIELDS))


class TestAllowedOperations:
    """계약 2.2절 허용 연산표."""

    def test_place_types는_Add를_허용하지_않는다(self):
        """복수 필드라고 해서 Add를 허용하지 않는다.

        "카페 말고 맛집"은 전체 교체이므로 Update만 사용한다.
        """
        assert not fs.allows("place_types", fs.OP_ADD)
        assert fs.allows("place_types", fs.OP_UPDATE)

    def test_place_tags는_Add를_허용한다(self):
        """"박물관도 추가"는 기존 태그에 누적한다."""
        assert fs.allows("place_tags", fs.OP_ADD)

    def test_모든_필드가_Remove를_허용한다(self):
        """conditions-schema.md v0.3 4절.

        current_location의 필수 지위는 api_context.gps_location으로 이관되었으므로
        user_conditions에는 해제 불가 필드가 없다.
        """
        for field in fs.FIELD_SPECS:
            assert fs.allows(field, fs.OP_REMOVE), f"{field}가 Remove를 허용하지 않음"

    @pytest.mark.parametrize(
        "field",
        [
            "place_types",
            "place_tags",
            "exclude_tags",
            "special_requirements",
            "accessibility_needs",
        ],
    )
    def test_복수_필드는_5개다(self, field):
        assert fs.is_multi(field)

    def test_나머지는_모두_단일_필드다(self):
        multi = {
            "place_types",
            "place_tags",
            "exclude_tags",
            "special_requirements",
            "accessibility_needs",
        }
        for field in fs.FIELD_SPECS:
            assert fs.is_multi(field) == (field in multi)


class TestTypeMatching:
    def test_정수_필드는_정수만_받는다(self):
        assert fs.matches_type("max_travel_time", 30)
        assert not fs.matches_type("max_travel_time", "30")

    def test_정수_필드가_bool을_받지_않는다(self):
        """파이썬에서 bool은 int의 하위 타입이므로 명시적으로 배제해야 한다.

        허용하면 max_travel_time=True가 1분으로 해석되는 사고가 발생한다.
        """
        assert not fs.matches_type("max_travel_time", True)
        assert not fs.matches_type("time_available", False)

    def test_복수_필드는_리스트만_받는다(self):
        assert fs.matches_type("place_tags", ["카페"])
        assert not fs.matches_type("place_tags", "카페")

    def test_복수_필드_원소는_문자열이어야_한다(self):
        assert not fs.matches_type("place_tags", [1, 2])

    def test_빈_리스트는_허용된다(self):
        assert fs.matches_type("place_tags", [])


class TestEmptyValue:
    def test_단일_필드의_기본값은_None이다(self):
        assert fs.empty_value("budget") is None

    def test_복수_필드의_기본값은_빈_리스트다(self):
        assert fs.empty_value("place_tags") == []