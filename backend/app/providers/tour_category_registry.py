"""TourAPI 신분류 기준 데이터를 한 번 로드해 조회하는 메모리 Registry."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.domain.models import PlaceCategoryFilter

_DEFAULT_RESOURCE_PATH = (
    Path(__file__).resolve().parents[2]
    / "resources"
    / "tour_api"
    / "tour_api_category_codes.json"
)
_REQUIRED_STRING_FIELDS = (
    "lcls_systm1",
    "lcls_systm1_name",
    "lcls_systm2",
    "lcls_systm2_name",
    "lcls_systm3",
    "lcls_systm3_name",
    "content_type_id",
    "content_type_name",
)


def _normalize_name(value: str) -> str:
    """표시 이름은 보존하고 검색 키에서만 공백과 영문 대소문자를 정규화한다."""
    return "".join(value.split()).casefold()


@dataclass(frozen=True)
class TourCategory:
    lcls_systm1: str
    lcls_systm1_name: str
    lcls_systm2: str
    lcls_systm2_name: str
    lcls_systm3: str
    lcls_systm3_name: str
    content_type_id: str
    multilingual_content_type_id: str | None
    content_type_name: str

    def to_filter(self) -> PlaceCategoryFilter:
        return PlaceCategoryFilter(
            content_type_id=self.content_type_id,
            lcls_systm1=self.lcls_systm1,
            lcls_systm2=self.lcls_systm2,
            lcls_systm3=self.lcls_systm3,
        )


class TourCategoryRegistry:
    """TourAPI 대·중·소분류를 코드와 이름으로 즉시 조회한다."""

    def __init__(self, categories: tuple[TourCategory, ...]) -> None:
        if not categories:
            raise ValueError("TourAPI 분류 데이터가 비어 있습니다.")

        self._categories = categories
        self._by_small_code: dict[str, TourCategory] = {}
        by_small_name: defaultdict[str, list[TourCategory]] = defaultdict(list)
        by_middle_code: defaultdict[str, list[TourCategory]] = defaultdict(list)
        by_middle_name: defaultdict[str, list[TourCategory]] = defaultdict(list)
        by_large_code: defaultdict[str, list[TourCategory]] = defaultdict(list)
        by_large_name: defaultdict[str, list[TourCategory]] = defaultdict(list)

        for category in categories:
            if category.lcls_systm3 in self._by_small_code:
                raise ValueError(
                    f"중복된 TourAPI 소분류 코드입니다: {category.lcls_systm3}"
                )
            self._by_small_code[category.lcls_systm3] = category
            by_small_name[_normalize_name(category.lcls_systm3_name)].append(category)
            by_middle_code[category.lcls_systm2].append(category)
            by_middle_name[_normalize_name(category.lcls_systm2_name)].append(category)
            by_large_code[category.lcls_systm1].append(category)
            by_large_name[_normalize_name(category.lcls_systm1_name)].append(category)

        self._by_small_name = self._freeze_index(by_small_name)
        self._by_middle_code = self._freeze_index(by_middle_code)
        self._by_middle_name = self._freeze_index(by_middle_name)
        self._by_large_code = self._freeze_index(by_large_code)
        self._by_large_name = self._freeze_index(by_large_name)

    @staticmethod
    def _freeze_index(
        source: Mapping[str, list[TourCategory]],
    ) -> dict[str, tuple[TourCategory, ...]]:
        return {key: tuple(value) for key, value in source.items()}

    @classmethod
    def from_default_resource(cls) -> TourCategoryRegistry:
        return cls.from_path(_DEFAULT_RESOURCE_PATH)

    @classmethod
    def from_path(cls, path: Path) -> TourCategoryRegistry:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"TourAPI 분류 데이터를 읽을 수 없습니다: {path}") from exc
        if not isinstance(payload, list):
            raise ValueError("TourAPI 분류 데이터의 최상위 값은 배열이어야 합니다.")

        categories = tuple(
            cls._parse_category(item, index=index)
            for index, item in enumerate(payload)
        )
        return cls(categories)

    @staticmethod
    def _parse_category(item: Any, index: int) -> TourCategory:
        if not isinstance(item, Mapping):
            raise ValueError(f"TourAPI 분류 데이터 {index}번 항목은 객체여야 합니다.")

        values: dict[str, str] = {}
        for field in _REQUIRED_STRING_FIELDS:
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"TourAPI 분류 데이터 {index}번 항목의 {field}가 비어 있습니다."
                )
            values[field] = value.strip()

        multilingual = item.get("multilingual_content_type_id")
        if multilingual is not None and not isinstance(multilingual, str):
            raise ValueError(
                "TourAPI 분류 데이터 "
                f"{index}번 항목의 multilingual_content_type_id 형식이 잘못됐습니다."
            )

        return TourCategory(
            **values,
            multilingual_content_type_id=multilingual.strip()
            if isinstance(multilingual, str) and multilingual.strip()
            else None,
        )

    @property
    def categories(self) -> tuple[TourCategory, ...]:
        return self._categories

    def get_by_small_code(self, code: str) -> TourCategory | None:
        return self._by_small_code.get(code.strip())

    def find_by_small_name(self, name: str) -> tuple[TourCategory, ...]:
        return self._by_small_name.get(_normalize_name(name), ())

    def find_by_middle_code(self, code: str) -> tuple[TourCategory, ...]:
        return self._by_middle_code.get(code.strip(), ())

    def find_by_middle_name(self, name: str) -> tuple[TourCategory, ...]:
        return self._by_middle_name.get(_normalize_name(name), ())

    def find_by_large_code(self, code: str) -> tuple[TourCategory, ...]:
        return self._by_large_code.get(code.strip(), ())

    def find_by_large_name(self, name: str) -> tuple[TourCategory, ...]:
        return self._by_large_name.get(_normalize_name(name), ())


@lru_cache(maxsize=1)
def get_tour_category_registry() -> TourCategoryRegistry:
    """프로세스에서 Registry를 한 번만 생성해 재사용한다."""
    return TourCategoryRegistry.from_default_resource()
