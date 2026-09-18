from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.providers.tour_category_registry import (
    TourCategoryRegistry,
    get_tour_category_registry,
)


def test_default_registry_loads_and_indexes_categories() -> None:
    registry = TourCategoryRegistry.from_default_resource()

    assert len(registry.categories) == 240
    assert len(registry.find_by_large_code("FD")) == 21
    assert len(registry.find_by_middle_code("FD05")) == 3


def test_registry_finds_cafe_by_code_and_normalized_name() -> None:
    registry = TourCategoryRegistry.from_default_resource()

    by_code = registry.get_by_small_code("FD050100")
    by_name = registry.find_by_small_name("  카 페 ")

    assert by_code is not None
    assert by_name == (by_code,)
    assert by_code.lcls_systm3_name == "카페"
    assert by_code.to_filter().content_type_id == "39"
    assert by_code.to_filter().lcls_systm1 == "FD"
    assert by_code.to_filter().lcls_systm2 == "FD05"
    assert by_code.to_filter().lcls_systm3 == "FD050100"


def test_registry_finds_middle_and_large_names() -> None:
    registry = TourCategoryRegistry.from_default_resource()

    middle = registry.find_by_middle_name("카페/찻집")
    large = registry.find_by_large_name("음식")

    assert {category.lcls_systm3 for category in middle} == {
        "FD050100",
        "FD050200",
        "FD050300",
    }
    assert len(large) == 21


def test_registry_returns_empty_result_for_unknown_category() -> None:
    registry = TourCategoryRegistry.from_default_resource()

    assert registry.get_by_small_code("UNKNOWN") is None
    assert registry.find_by_small_name("없는 분류") == ()
    assert registry.find_by_middle_name("없는 분류") == ()
    assert registry.find_by_large_name("없는 분류") == ()


def test_registry_rejects_duplicate_small_code(tmp_path: Path) -> None:
    source = TourCategoryRegistry.from_default_resource().categories[0]
    item = {
        field: getattr(source, field)
        for field in (
            "lcls_systm1",
            "lcls_systm1_name",
            "lcls_systm2",
            "lcls_systm2_name",
            "lcls_systm3",
            "lcls_systm3_name",
            "content_type_id",
            "multilingual_content_type_id",
            "content_type_name",
        )
    }
    path = tmp_path / "duplicate.json"
    path.write_text(json.dumps([item, item], ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="중복된 TourAPI 소분류 코드"):
        TourCategoryRegistry.from_path(path)


def test_registry_rejects_missing_required_field(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps([{"lcls_systm1": "FD"}]), encoding="utf-8")

    with pytest.raises(ValueError, match="lcls_systm1_name"):
        TourCategoryRegistry.from_path(path)


def test_cached_registry_is_reused_and_loaded_during_app_lifespan() -> None:
    get_tour_category_registry.cache_clear()
    first = get_tour_category_registry()
    second = get_tour_category_registry()

    assert first is second
    with TestClient(create_app()) as client:
        assert client.app.state.tour_category_registry is first
