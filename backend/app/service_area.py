"""좌표가 MVP 지원 지역(서울 25개 구 전체, SUPPORTED_DISTRICTS 참고) 안인지 판정한다.

역할: 지원 범위 밖 위치를 해석 단계에서 걸러 낸다. 밖의 좌표가 들어오면 그 아래
로직이 "결과 없음"으로만 끝나고 이유가 전달되지 않아서다(D-044).
입력: 위도·경도.
출력: 지원 구 중 어느 하나 안이면 True.
호출 시점: ResolveLocationTool이 좌표를 얻은 직후.

경계는 `resources/boundaries/seoul.geojson` 한 장을 쓴다. 서울 25개 구가 모두 들어
있고 그중 어디를 지원할지는 `SUPPORTED_DISTRICTS`가 정한다 — 구를 늘릴 때 파일
작업 없이 목록 한 줄로 끝나게 하기 위해서다. 출처와 선택 이유는 그 옆 README에 적었다.
정적 데이터라 프로세스당 한 번만 읽는다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_BOUNDARY_PATH = (
    Path(__file__).resolve().parent.parent / "resources" / "boundaries" / "seoul.geojson"
)

# GeoJSON 좌표는 [경도, 위도] 순서다.
_Ring = tuple[tuple[float, float], ...]
_Polygon = tuple[_Ring, ...]


@dataclass(frozen=True)
class ServiceDistrict:
    """지원 구 한 곳."""

    # TourAPI 법정동 코드의 시군구 부분(lDongSignguCd). 경계 파일 안의
    # properties.code(KOSTAT 11010 등)와는 다른 코드 체계라 섞어 쓰지 않는다.
    district_code: str
    # 경계 파일의 properties.name과 정확히 같아야 한다. 이 이름으로 폴리곤을 찾는다.
    name: str


# 지원 구는 여기서 정한다. 경계 파일에는 서울 25개 구가 다 들어 있으므로, 구를
# 늘릴 때 할 일은 이 목록에 한 줄을 더하는 것뿐이다.
#
# 파일에 있는 구를 전부 지원하는 방식은 쓰지 않는다 — 지원 범위는 팀이 합의하는
# 결정이라 코드에 드러나고 리뷰를 거쳐야 한다. 목록에 있는데 경계 파일에 그 구가
# 없으면 첫 판정에서 예외로 끊는다.
#
# 2026-08-25: Supabase `places`에 이미 적재된 8개 구를 추가했다(D-083) —
# 광진구·동대문구·중랑구·성북구·강북구·도봉구·노원구·은평구. district_code는
# TourAPI 응답 실측(각 구 표본 2건)으로 주소와 대조해 확인했다.
#
# 2026-08-26: 서대문구·마포구·양천구·강서구를 추가했다(D-086) — place-sync로
# 목록·상세정보를 새로 적재한 4개 구다. district_code는 place-sync가 실제로
# 적재한 TourAPI 응답 주소와 대조해 확인했다(app/resources/tour_api/
# tour_api_ldong_codes.json과 동일).
#
# 2026-08-30: 나머지 9개 구(강남·송파·영등포·서초·금천·구로·관악·강동·동작)를
# 추가해 서울 25개 구 전체로 확대했다(D-107). Supabase `places`에 이미 이
# 9개 구의 활성 장소가 적재돼 있었는데(총 8,007건 중 3,343건) 지원 목록만
# 못 따라가 검색·위치 판정에서 걸러지고 있었다 — district_code는 그 활성
# 장소들의 실제 district_code를 그대로 옮겼다(place-sync가 TourAPI
# lDongSignguCd로 채운 값이라 위 구들과 같은 방식으로 이미 검증된 값이다).
#
# 스물다섯 곳 모두 서울특별시라 lDongRegnCd는 "11"로 같다.
SUPPORTED_DISTRICTS: tuple[ServiceDistrict, ...] = (
    ServiceDistrict("110", "종로구"),
    ServiceDistrict("140", "중구"),
    ServiceDistrict("170", "용산구"),
    ServiceDistrict("200", "성동구"),
    ServiceDistrict("215", "광진구"),
    ServiceDistrict("230", "동대문구"),
    ServiceDistrict("260", "중랑구"),
    ServiceDistrict("290", "성북구"),
    ServiceDistrict("305", "강북구"),
    ServiceDistrict("320", "도봉구"),
    ServiceDistrict("350", "노원구"),
    ServiceDistrict("380", "은평구"),
    ServiceDistrict("410", "서대문구"),
    ServiceDistrict("440", "마포구"),
    ServiceDistrict("470", "양천구"),
    ServiceDistrict("500", "강서구"),
    ServiceDistrict("530", "구로구"),
    ServiceDistrict("545", "금천구"),
    ServiceDistrict("560", "영등포구"),
    ServiceDistrict("590", "동작구"),
    ServiceDistrict("620", "관악구"),
    ServiceDistrict("650", "서초구"),
    ServiceDistrict("680", "강남구"),
    ServiceDistrict("710", "송파구"),
    ServiceDistrict("740", "강동구"),
)

# 좌표 판정(폴리곤)과 장소 검색(TourAPI 응답의 lDongSignguCd)이 같은 목록을 봐야
# 한 쪽만 늘어나는 일이 없다. 검색 쪽은 이 집합으로 응답을 거른다(D-025).
SUPPORTED_DISTRICT_CODES: frozenset[str] = frozenset(
    district.district_code for district in SUPPORTED_DISTRICTS
)


def _to_polygons(geometry: Mapping[str, object]) -> tuple[_Polygon, ...]:
    """(폴리곤, 링, 좌표) 3단 튜플. 각 폴리곤의 첫 링이 외곽선이고 나머지는 구멍이다."""
    geometry_type = geometry["type"]
    if geometry_type == "Polygon":
        raw_polygons = [geometry["coordinates"]]
    elif geometry_type == "MultiPolygon":
        raw_polygons = list(geometry["coordinates"])  # type: ignore[arg-type]
    else:
        raise ValueError(f"지원하지 않는 geometry 형식입니다: {geometry_type}")
    return tuple(
        tuple(
            tuple((float(point[0]), float(point[1])) for point in ring)
            for ring in polygon
        )
        for polygon in raw_polygons
    )


@lru_cache(maxsize=1)
def _all_district_polygons() -> tuple[tuple[str, tuple[_Polygon, ...]], ...]:
    """경계 파일에 든 모든 구의 (이름, 폴리곤들). 지원 여부와 무관하게 전부 읽는다."""
    with _BOUNDARY_PATH.open(encoding="utf-8") as fp:
        collection = json.load(fp)
    if collection.get("type") != "FeatureCollection":
        raise ValueError("경계 파일은 FeatureCollection이어야 합니다.")
    return tuple(
        (str(feature["properties"]["name"]), _to_polygons(feature["geometry"]))
        for feature in collection["features"]
    )


def _select_supported(
    all_polygons: tuple[tuple[str, tuple[_Polygon, ...]], ...],
    districts: tuple[ServiceDistrict, ...],
) -> tuple[tuple[str, tuple[_Polygon, ...]], ...]:
    """지원 구의 폴리곤만 목록 순서대로 고른다. 경계가 없는 구는 예외로 끊는다."""
    by_name = dict(all_polygons)
    missing = [district.name for district in districts if district.name not in by_name]
    if missing:
        raise ValueError(
            f"경계 파일에 없는 구가 SUPPORTED_DISTRICTS에 있습니다: {', '.join(missing)}. "
            f"{_BOUNDARY_PATH.name}를 다시 뽑아야 합니다."
        )
    return tuple(
        (district.district_code, by_name[district.name]) for district in districts
    )


@lru_cache(maxsize=1)
def _service_area_polygons() -> tuple[tuple[str, tuple[_Polygon, ...]], ...]:
    """(구 코드, 폴리곤들) 쌍의 튜플. 구가 늘어도 판정 방식은 그대로다."""
    return _select_supported(_all_district_polygons(), SUPPORTED_DISTRICTS)


def _is_inside_ring(longitude: float, latitude: float, ring: _Ring) -> bool:
    """Ray casting. 점에서 한 방향으로 반직선을 그어 변과 만나는 횟수를 센다."""
    inside = False
    count = len(ring)
    for index in range(count):
        x1, y1 = ring[index]
        x2, y2 = ring[(index + 1) % count]
        # 변이 점의 위도를 가로지를 때만 교차를 따진다.
        if (y1 > latitude) != (y2 > latitude):
            crossing_longitude = (x2 - x1) * (latitude - y1) / (y2 - y1) + x1
            if longitude < crossing_longitude:
                inside = not inside
    return inside


def _is_inside_polygon(longitude: float, latitude: float, polygon: _Polygon) -> bool:
    outer, *holes = polygon
    if not _is_inside_ring(longitude, latitude, outer):
        return False
    return not any(_is_inside_ring(longitude, latitude, hole) for hole in holes)


def is_within_service_area(latitude: float, longitude: float) -> bool:
    """좌표가 지원 구 중 어느 하나 안이면 True.

    경계선에 붙은 지점은 2018년 경계 데이터의 정밀도 한계로 밖으로 판정될 수 있다
    (구별 활성 장소의 0.3% 미만, README의 정밀도 표 참고). 저장소에서 해석된
    장소는 이미 지원 구로 등록된 것이라 호출자가 판정을 생략한다.
    """
    return find_containing_district(latitude, longitude) is not None


def find_containing_district(latitude: float, longitude: float) -> ServiceDistrict | None:
    """좌표를 포함하는 지원 구. 지원 구 밖이면 None.

    `is_within_service_area`가 bool만 필요한 호출부용이라면, 이건 "어느 구인지"까지
    필요한 호출부용이다(TP-160 위치 되묻기 대체 버튼, `app.service_area_landmarks`).
    """
    by_code = {district.district_code: district for district in SUPPORTED_DISTRICTS}
    for district_code, polygons in _service_area_polygons():
        if any(
            _is_inside_polygon(longitude, latitude, polygon) for polygon in polygons
        ):
            return by_code[district_code]
    return None


def supported_district_label(*, with_city: bool = False) -> str:
    """안내 문구에 넣을 지원 구 이름. "종로구·중구·용산구·성동구·..." (SUPPORTED_DISTRICTS 순서).

    문구가 이 목록을 읽게 해야 구를 늘릴 때 SUPPORTED_DISTRICTS 한 줄로 끝난다.
    사람이 쓴 문자열로 두면 목록만 늘고 문구는 옛 범위를 말하는 상태가 된다 —
    실제로 지원 구가 넷으로 늘어난 뒤에도 "종로구만 지원합니다"가 나갔다.

    with_city는 "서울특별시"를 앞에 붙인다. 모두 서울특별시이므로 구 이름만
    반복하지 않고 한 번만 쓴다.
    """
    names = "·".join(district.name for district in SUPPORTED_DISTRICTS)
    return f"서울특별시 {names}" if with_city else names


@lru_cache(maxsize=1)
def _representative_points() -> dict[str, tuple[float, float]]:
    """지원 구마다 그 구 **안**의 한 점. (위도, 경도).

    구 이름을 좌표로 풀어야 하는 곳이 쓴다. 실제 지오코딩은 네이버가 하지만 Fake와
    로컬 개발에는 그 답이 없어서, 경계 파일에서 직접 만든다.

    무게중심을 그대로 쓰지 않는다. 오목한 구에서는 무게중심이 구 밖으로 나간다 —
    그러면 지원 지역 판정에 걸려 "지원하지 않는 지역"이 된다. 밖으로 나가면 그
    위도에서 가로로 반직선을 그어 가장 넓은 내부 구간의 가운데를 쓴다.
    """
    points: dict[str, tuple[float, float]] = {}
    for district_code, polygons in _service_area_polygons():
        ring = max((polygon[0] for polygon in polygons), key=len)
        latitude = sum(point[1] for point in ring) / len(ring)
        longitude = sum(point[0] for point in ring) / len(ring)
        if not any(
            _is_inside_polygon(longitude, latitude, polygon) for polygon in polygons
        ):
            longitude = _widest_inside_longitude(polygons, latitude, longitude)
        points[district_code] = (latitude, longitude)
    return points


def _widest_inside_longitude(
    polygons: tuple[_Polygon, ...], latitude: float, fallback_longitude: float
) -> float:
    """그 위도에서 구 안을 지나는 가장 넓은 구간의 가운데 경도."""
    crossings: list[float] = []
    for polygon in polygons:
        for ring in polygon:
            count = len(ring)
            for index in range(count):
                x1, y1 = ring[index]
                x2, y2 = ring[(index + 1) % count]
                if (y1 > latitude) != (y2 > latitude):
                    crossings.append((x2 - x1) * (latitude - y1) / (y2 - y1) + x1)
    crossings.sort()
    widest: tuple[float, float] | None = None
    for start, end in zip(crossings[::2], crossings[1::2], strict=False):
        if widest is None or (end - start) > (widest[1] - widest[0]):
            widest = (start, end)
    return fallback_longitude if widest is None else (widest[0] + widest[1]) / 2


def district_representative_point(district_code: str) -> tuple[float, float] | None:
    """지원 구의 대표 좌표 (위도, 경도). 지원 구가 아니면 None."""
    return _representative_points().get(district_code)


# 서울을 넉넉히 감싸는 사각형. 폴리곤 판정과 **목적이 다르다.**
#
# 폴리곤은 "이 좌표가 어느 구인가"를 답하고, 이건 "이 좌표가 아예 말이 안 되는가"만
# 본다. 원본 데이터에 좌표가 깨진 장소가 있어서 필요하다(2026-09-01 실측, 활성
# 8,007곳 중 12건) — 10건이 (19.69, 117.99)라는 같은 값을 갖고 있고(남중국해),
# 삼각산은 경기 광주에 찍혀 있으며, 한 건은 위도 칸이 비고 경도 칸에 위도가 들어 있다.
#
# **여기에 폴리곤을 쓰지 않는 이유가 있다.** 셋 다 12건을 똑같이 걸러내지만 정상
# 장소를 잃는 수가 다르다 — 사각형 0곳, 서울 폴리곤 4곳, "자기 구 안" 82곳이다.
# 폴리곤이 잃는 4곳은 아차산성·망우산·청계산 옛골토성·서울둘레길 5코스로, 경기도와
# 맞닿은 산이라 좌표가 경계 밖으로 조금 넘어간 것들이다. 깨진 좌표는 경계에서 몇십
# 미터가 아니라 나라 밖에 있으므로 정밀한 경계선이 필요하지 않다.
#
# 한계도 분명하다. 서울 안쪽 어딘가에 잘못 찍힌 좌표는 이걸로 못 잡는다. 지금
# 관측된 12건이 전부 멀리 떨어져 있어 문제가 안 될 뿐이다.
SEOUL_MIN_LATITUDE = 37.40
SEOUL_MAX_LATITUDE = 37.72
SEOUL_MIN_LONGITUDE = 126.73
SEOUL_MAX_LONGITUDE = 127.19


def is_plausible_seoul_coordinate(latitude: float | None, longitude: float | None) -> bool:
    """좌표가 서울 언저리에 있기는 한지. 지원 지역 판정이 아니다.

    지원 지역 판정은 `is_within_service_area()`가 폴리곤으로 한다. 이 함수는 적재된
    값이 좌표로서 말이 되는지만 본다 — 위 상수 주석에 두 판정을 나눈 이유가 있다.
    """
    if latitude is None or longitude is None:
        return False
    return (
        SEOUL_MIN_LATITUDE <= latitude <= SEOUL_MAX_LATITUDE
        and SEOUL_MIN_LONGITUDE <= longitude <= SEOUL_MAX_LONGITUDE
    )


__all__ = [
    "SEOUL_MAX_LATITUDE",
    "SEOUL_MAX_LONGITUDE",
    "SEOUL_MIN_LATITUDE",
    "SEOUL_MIN_LONGITUDE",
    "SUPPORTED_DISTRICTS",
    "SUPPORTED_DISTRICT_CODES",
    "ServiceDistrict",
    "district_representative_point",
    "find_containing_district",
    "is_plausible_seoul_coordinate",
    "is_within_service_area",
    "supported_district_label",
]
