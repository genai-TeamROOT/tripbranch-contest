"""서울시 실시간 도시데이터 지역 선택 규칙 회귀 테스트.

인구(121개)와 상권(82개) 목록이 분리돼 있는지, 그리고 82개 목록에는 없지만
121개 목록에는 있는 지역(예: 경복궁)이 인구 조회에서만 잡히는지를 확인한다
(2026-08-26, D-084).
"""

import json

import pytest

from app.agent_context import seoul_realtime_areas
from app.agent_context.seoul_realtime_areas import (
    COMMERCIAL_AREAS,
    POPULATION_AREAS,
    _load_areas,
    population_areas_in_district,
    select_nearest_commercial_area,
    select_nearest_population_area,
)
from app.service_area import find_containing_district

# 실시간 도시데이터 매뉴얼 V8.5(2026-04) 표 2-2/표 3-9에 실린 카테고리별 개수.
_MANUAL_POPULATION_COUNTS = {
    "고궁·문화유산": 5,
    "관광특구": 7,
    "공원": 33,
    "발달상권": 28,
    "인구밀집지역": 48,
}
_MANUAL_COMMERCIAL_COUNTS = {
    "관광특구": 7,
    "발달상권": 28,
    "인구밀집지역": 45,
    "고궁·문화유산": 2,
}

_VALID_AREA = {
    "code": "POI999",
    "name": "테스트 지역",
    "latitude": 37.5,
    "longitude": 127.0,
    "category": "관광특구",
}


def _write_areas(tmp_path, rows):
    path = tmp_path / "areas.json"
    path.write_text(
        json.dumps({"source": {"name": "test"}, "areas": rows}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path

# 경복궁 대표 좌표(POI008). 121개 인구 목록에는 있지만 82개 상권 목록에는 없다.
_GYEONGBOKGUNG_LAT = 37.5798759
_GYEONGBOKGUNG_LNG = 126.9767642


def test_area_list_sizes() -> None:
    assert len(POPULATION_AREAS) == 121
    assert len(COMMERCIAL_AREAS) == 82


def test_commercial_areas_are_subset_of_population_areas() -> None:
    population_codes = {area.code for area in POPULATION_AREAS}
    assert {area.code for area in COMMERCIAL_AREAS} <= population_codes


def test_yongridan_gil_coordinate_selects_yongridan_gil() -> None:
    selected = select_nearest_commercial_area(latitude=37.5311, longitude=126.9715)

    assert selected is not None
    area, distance_km = selected
    assert area.code == "POI076"
    assert area.name == "용리단길"
    assert distance_km < 0.1


def test_outside_official_commercial_coverage_returns_none() -> None:
    # 부산 좌표는 서울시 주요 상권의 최근접 대체 범위 밖이다.
    selected = select_nearest_commercial_area(latitude=35.1796, longitude=129.0756)

    assert selected is None


def test_gyeongbokgung_is_found_by_population_lookup() -> None:
    selected = select_nearest_population_area(
        latitude=_GYEONGBOKGUNG_LAT, longitude=_GYEONGBOKGUNG_LNG
    )

    assert selected is not None
    area, distance_km = selected
    assert area.code == "POI008"
    assert area.name == "경복궁"
    assert distance_km == 0.0


def test_gyeongbokgung_is_not_in_commercial_only_lookup() -> None:
    # 상권 82개 목록에는 경복궁이 없어서, 근처 좌표를 넣어도 다른(더 먼) 지역으로
    # 대체되거나(광화문·덕수궁 등) 대체 범위를 벗어나 None이 된다 — 경복궁 자체는
    # 절대 나오지 않는다.
    selected = select_nearest_commercial_area(
        latitude=_GYEONGBOKGUNG_LAT, longitude=_GYEONGBOKGUNG_LNG
    )

    assert selected is None or selected[0].code != "POI008"


def _category_counts_in_file(path) -> dict[str, int]:
    with path.open(encoding="utf-8") as fp:
        payload = json.load(fp)
    counts: dict[str, int] = {}
    for row in payload["areas"]:
        counts[row["category"]] = counts.get(row["category"], 0) + 1
    return counts


def test_population_categories_match_manual_table_2_2() -> None:
    counts = _category_counts_in_file(seoul_realtime_areas._POPULATION_PATH)
    assert counts == _MANUAL_POPULATION_COUNTS


def test_commercial_categories_match_manual_table_3_9() -> None:
    counts = _category_counts_in_file(seoul_realtime_areas._COMMERCIAL_PATH)
    assert counts == _MANUAL_COMMERCIAL_COUNTS


def test_loader_rejects_duplicate_code(tmp_path) -> None:
    path = _write_areas(tmp_path, [_VALID_AREA, {**_VALID_AREA}])

    with pytest.raises(ValueError, match="코드가 중복"):
        _load_areas(path)


def test_loader_rejects_out_of_range_coordinate(tmp_path) -> None:
    path = _write_areas(tmp_path, [{**_VALID_AREA, "latitude": 90.0}])

    with pytest.raises(ValueError, match="위도가 범위 밖"):
        _load_areas(path)


def test_loader_rejects_missing_required_field(tmp_path) -> None:
    row = {k: v for k, v in _VALID_AREA.items() if k != "name"}
    path = _write_areas(tmp_path, [row])

    with pytest.raises(ValueError, match="필수 필드가 없습니다"):
        _load_areas(path)


def test_catalog_district_matches_the_coordinate() -> None:
    """파일에 적어 둔 구가 좌표로 판정한 구와 같은지 본다.

    **이 테스트가 파일에 적는 방식의 값이다.** 좌표를 고치면서 구를 안 고치면 두 값이
    조용히 어긋나는데, 그때 틀어지는 것은 "강서구 지금 사람 많아?"의 답 전체다.
    전수 대조가 121곳 + 82곳에 18ms 남짓이라 매번 돌려도 부담이 없다.
    """

    for area in (*POPULATION_AREAS, *COMMERCIAL_AREAS):
        district = find_containing_district(area.latitude, area.longitude)
        expected = district.name if district else None
        assert area.district == expected, (
            f"{area.name}: 파일 {area.district!r} vs 좌표 {expected!r}"
        )


def test_areas_outside_seoul_have_no_district() -> None:
    """서울 경계 밖이거나 구 경계에 걸친 곳은 구가 비어 있다.

    서울대공원은 과천이고 아차산은 광진구·중랑구 경계에 놓인다. 이 둘이 None인 것은
    버그가 아니라 사실이므로, 나중에 "왜 두 곳만 비었지?"로 다시 파지 않게 못박는다.
    """

    without_district = {area.name for area in POPULATION_AREAS if area.district is None}

    assert without_district == {"서울대공원", "아차산"}


def test_population_areas_in_district_finds_every_area_in_it() -> None:
    names = [area.name for area in population_areas_in_district("강서구")]

    assert names == ["발산역", "서울식물원·마곡나루역", "김포공항", "강서한강공원"]


def test_population_areas_in_district_is_empty_where_seoul_provides_none() -> None:
    """중랑구에는 실시간 인구 지역이 하나도 없다.

    호출부는 이 경우를 되묻기가 아니라 "제공 지역이 없다"는 답으로 다뤄야 한다 —
    되물어봐야 사용자가 내놓을 수 있는 답이 없다.
    """

    assert population_areas_in_district("중랑구") == ()
