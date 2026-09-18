"""A의 장소 조건을 C가 실행할 TourAPI 분류 조회 계획으로 변환한다."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.models import PlaceCategoryFilter
from app.providers.tour_category_registry import (
    TourCategoryRegistry,
    get_tour_category_registry,
)

PLACE_TYPE_TO_CONTENT_TYPE_ID = {
    "attraction": "12",
    "cultural_facility": "14",
    "festival": "15",
    "leisure": "28",
    "shopping": "38",
    "restaurant": "39",
}

# 하나의 태그가 여러 TourAPI 소분류를 뜻할 수 있으므로 코드 목록으로 관리한다.
PLACE_TAG_TO_SMALL_CODES = {
    "공원": (
        "VE030100",
        "VE030200",
        "VE030300",
        "VE030400",
        "VE030500",
    ),
    "궁궐": ("HS010100",),
    "산": ("NA010100",),
    "해변": ("NA020900",),
    "호수": ("NA020200",),
    "계곡": ("NA010400",),
    "전망대": ("VE010200",),
    "테마파크": ("VE020100",),
    "동물원": ("VE020300",),
    "수목원": ("NA040700",),
    "사찰": ("HS030100",),
    "성곽": ("HS010200",),
    "마을": ("VE040200",),
    "둘레길": ("VE040300",),
    "전통체험": ("EX010100",),
    "공예체험": ("EX020100", "EX020200", "EX020300", "EX020400"),
    "웰니스": (
        "EX050100",
        "EX050200",
        "EX050300",
        "EX050400",
        "EX050500",
        "EX050600",
        "EX050700",
        "EX050800",
    ),
    "박물관": ("VE070100",),
    "미술관": ("VE070600",),
    "도서관": ("VE090300",),
    "공연장": ("VE060100",),
    "과학관": ("VE070500",),
    "전시관": ("VE070300",),
    "축제": (
        "EV010100",
        "EV010200",
        "EV010300",
        "EV010400",
        "EV010500",
        "EV010600",
    ),
    "전시회": ("EV030100",),
    "공연": (
        "EV020100",
        "EV020200",
        "EV020300",
        "EV020400",
        "EV020500",
        "EV020600",
        "EV020700",
        "EV020800",
        "EV020900",
        "EV021000",
    ),
    "콘서트": ("EV020700",),
    "시장": ("SH060100", "SH060200"),
    "쇼핑몰": ("SH020100",),
    "면세점": ("SH040100", "SH040200", "SH040300"),
    "백화점": ("SH010100",),
    # "식당"은 음식점(content_type=39) 전체가 아니다. 카페·찻집·주점·제과·
    # 이동음식은 사용자가 앉아 식사할 장소를 찾을 때의 기본 후보에서 뺀다.
    "식당": (
        "FD010100",  # 관광식당
        "FD010200",  # 모범음식점
        "FD020100",  # 중식
        "FD020200",  # 일식
        "FD020300",  # 서양식
        "FD020400",  # 기타외국식
        "FD020500",  # 퓨전음식
        "FD030200",  # 피자·햄버거·샌드위치
        "FD030300",  # 치킨
        "FD030400",  # 김밥·분식
        "FD030600",  # 기타간이음식
    ),
    "한식": ("FD010100", "FD010200"),
    "일식": ("FD020200",),
    "중식": ("FD020100",),
    "양식": ("FD020300",),
    # 사용자의 "카페 추천"은 커피 전문점만이 아니라 찻집·기타음료점도 찾는
    # 요청으로 다룬다. "카페/ 찻집"이라는 TourAPI 중분류 전체와 일치시킨다.
    "카페": ("FD050100", "FD050200", "FD050300"),
    "찻집": ("FD050200",),
    "주점": ("FD040100", "FD040200", "FD040300", "FD040400", "FD040500"),
    "분식": ("FD030400",),
}


@dataclass(frozen=True)
class CategoryQueryPlan:
    """분류 조건을 검증한 결과와 Provider 호출에 사용할 필터 목록."""

    filters: tuple[PlaceCategoryFilter | None, ...]
    resolved_place_types: tuple[str, ...]
    resolved_place_tags: tuple[str, ...]
    unsupported_place_types: tuple[str, ...] = ()
    unsupported_place_tags: tuple[str, ...] = ()
    conflicting_place_tags: tuple[str, ...] = ()

    @property
    def has_unsupported_conditions(self) -> bool:
        return bool(self.unsupported_place_types or self.unsupported_place_tags)

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicting_place_tags)


@dataclass(frozen=True)
class ExcludedCategoryPlan:
    """제외 태그를 후보에서 걸러낼 소분류 코드 집합으로 바꾼 결과."""

    small_codes: frozenset[str] = frozenset()
    unmapped_tags: tuple[str, ...] = ()

    @property
    def has_unmapped_tags(self) -> bool:
        return bool(self.unmapped_tags)


def build_excluded_category_plan(
    exclude_tags: list[str] | tuple[str, ...],
) -> ExcludedCategoryPlan:
    """제외 태그를 TourAPI 소분류 코드 집합으로 변환한다.

    TourAPI는 "이 분류를 빼고 조회"를 표현할 수 없으므로 조회 후 후보에서 걸러낸다.
    매핑이 없는 태그는 조용히 무시하지 않고 unmapped_tags로 돌려준다 — 걸러진 척하고
    넘어가면 "박물관 빼줘"가 무시된 걸 아무도 모르게 된다.
    """

    normalized = _unique_normalized(exclude_tags, casefold=False)
    codes: set[str] = set()
    unmapped: list[str] = []
    for tag in normalized:
        tag_codes = PLACE_TAG_TO_SMALL_CODES.get(tag)
        if tag_codes is None:
            unmapped.append(tag)
        else:
            codes.update(tag_codes)
    return ExcludedCategoryPlan(
        small_codes=frozenset(codes),
        unmapped_tags=tuple(unmapped),
    )


def build_category_query_plan(
    place_types: list[str] | tuple[str, ...],
    place_tags: list[str] | tuple[str, ...],
    *,
    registry: TourCategoryRegistry | None = None,
) -> CategoryQueryPlan:
    """장소 유형·태그를 순서를 보존한 TourAPI 조회 필터로 변환한다.

    조건이 없으면 무분류 조회를 뜻하는 ``None`` 필터 한 건을 반환한다.
    태그가 있으면 태그의 소분류가 유형 전체 조회보다 우선한다.
    """

    category_registry = registry or get_tour_category_registry()
    normalized_types = _unique_normalized(place_types, casefold=True)
    normalized_tags = _unique_normalized(place_tags, casefold=False)

    supported_types = tuple(
        item for item in normalized_types if item in PLACE_TYPE_TO_CONTENT_TYPE_ID
    )
    unsupported_types = tuple(
        item for item in normalized_types if item not in PLACE_TYPE_TO_CONTENT_TYPE_ID
    )

    supported_tag_codes: list[tuple[str, tuple[str, ...]]] = []
    unsupported_tags: list[str] = []
    for tag in normalized_tags:
        codes = PLACE_TAG_TO_SMALL_CODES.get(tag)
        if codes is None:
            unsupported_tags.append(tag)
        else:
            supported_tag_codes.append((tag, codes))

    if not normalized_types and not normalized_tags:
        return CategoryQueryPlan(
            filters=(None,),
            resolved_place_types=(),
            resolved_place_tags=(),
        )

    explicit_content_type_ids = {PLACE_TYPE_TO_CONTENT_TYPE_ID[item] for item in supported_types}
    filters: list[PlaceCategoryFilter] = []
    resolved_tags: list[str] = []
    inferred_types: list[str] = list(supported_types)
    conflicting_tags: list[str] = []

    for tag, codes in supported_tag_codes:
        tag_filters = tuple(
            category.to_filter()
            for code in codes
            if (category := category_registry.get_by_small_code(code)) is not None
        )
        if len(tag_filters) != len(codes):
            # 정적 매핑과 분류 리소스가 어긋난 경우 조용히 넓은 검색으로 낮추지 않는다.
            unsupported_tags.append(tag)
            continue

        tag_content_type_ids = {
            item.content_type_id for item in tag_filters if item.content_type_id
        }
        if explicit_content_type_ids and not (tag_content_type_ids & explicit_content_type_ids):
            conflicting_tags.append(tag)
            continue

        filters.extend(tag_filters)
        resolved_tags.append(tag)
        for place_type, content_type_id in PLACE_TYPE_TO_CONTENT_TYPE_ID.items():
            if content_type_id in tag_content_type_ids and place_type not in inferred_types:
                inferred_types.append(place_type)

    if resolved_tags:
        planned_filters: tuple[PlaceCategoryFilter | None, ...] = tuple(
            _deduplicate_filters(filters)
        )
    elif supported_tag_codes:
        # 태그가 모두 상충하거나 리소스에서 확인되지 않으면 전체 조회하지 않는다.
        planned_filters = ()
    else:
        planned_filters = tuple(
            PlaceCategoryFilter(content_type_id=PLACE_TYPE_TO_CONTENT_TYPE_ID[place_type])
            for place_type in supported_types
        )

    return CategoryQueryPlan(
        filters=planned_filters,
        resolved_place_types=tuple(inferred_types),
        resolved_place_tags=tuple(resolved_tags),
        unsupported_place_types=unsupported_types,
        unsupported_place_tags=tuple(unsupported_tags),
        conflicting_place_tags=tuple(conflicting_tags),
    )


def _unique_normalized(
    values: list[str] | tuple[str, ...],
    *,
    casefold: bool,
) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if casefold:
            normalized = normalized.casefold()
        if normalized and normalized not in result:
            result.append(normalized)
    return tuple(result)


def _deduplicate_filters(
    filters: list[PlaceCategoryFilter],
) -> tuple[PlaceCategoryFilter, ...]:
    result: list[PlaceCategoryFilter] = []
    for category_filter in filters:
        if category_filter not in result:
            result.append(category_filter)
    return tuple(result)
