"""지원 지역(서울 25개 구 전체, SUPPORTED_DISTRICTS 참고) 좌표 판정을 고정한다.

경계 상자로는 갈리지 않던 지점이 있어 폴리곤을 쓴다 — 종로구는 남북으로 길쭉하고
북악산 쪽으로 굽어 있다.

지원 구가 네 곳으로 늘면서 명동·서울역이 "밖"에서 "안"으로 바뀌었다(둘 다 중구다).
2026-08-25에 여덟 곳이 더 늘면서(D-083) 청량리역(동대문구)·건대입구역(광진구)도
같은 이유로 "안"으로 옮겼다. 2026-08-26에 네 곳이 더 늘면서(D-086) 망원역·
홍대입구역(마포구)·신촌역(서대문구)도 같은 이유로 "안"으로 옮겼다. 2026-08-30에
나머지 아홉 곳(강남·송파·영등포·서초·금천·구로·관악·강동·동작)이 늘면서(D-107)
서울 25개 구 전체가 지원 구가 됐다 — 그때까지 "아직 밖"인 경계 대조군으로 쓰던
노량진역·강남역·잠실역·천호역·영등포역·구로디지털단지역도 같은 이유로 "안"으로
옮겼다. 이제 서울 안쪽 경계는 더 없고, 서울 밖(구리·남양주·고양·의정부·부산)만
"밖"으로 남는다.

경계 파일에는 서울 25개 구가 다 들어 있으므로, 구별 대표점을 뽑아 "지원 구면 안,
아니면 밖"을 전수로 확인한다. 지원 구를 늘려도 이 테스트는 손대지 않아도 된다.
"""

from __future__ import annotations

import math

import pytest

from app.service_area import (
    SUPPORTED_DISTRICTS,
    ServiceDistrict,
    _all_district_polygons,
    _select_supported,
    _service_area_polygons,
    is_within_service_area,
    supported_district_label,
)

# 실제 좌표. 경계 판정이 흔들리면 바로 드러나도록 경계 인접 지점을 함께 둔다.
_INSIDE = {
    # 종로구
    "경복궁": (37.5760, 126.9767),
    "북촌한옥마을": (37.5826, 126.9850),
    "부암동": (37.5972, 126.9663),
    "내자상회": (37.5756, 126.9722),
    # 중구
    "명동": (37.5636, 126.9827),
    "서울역": (37.5547, 126.9707),
    "남대문시장": (37.5591, 126.9770),
    "동대문디자인플라자": (37.5665, 127.0093),
    # 용산구
    "이태원역": (37.5346, 126.9946),
    "용산역": (37.5299, 126.9648),
    "남산서울타워": (37.5512, 126.9882),
    # 성동구
    "성수동 카페거리": (37.5445, 127.0557),
    "왕십리역": (37.5614, 127.0374),
    "서울숲": (37.5444, 127.0374),
    # 광진구
    "건대입구역": (37.5403, 127.0700),
    "어린이대공원": (37.5486, 127.0806),
    # 동대문구
    "청량리역": (37.5800, 127.0470),
    "경동시장": (37.5764, 127.0378),
    # 중랑구
    "면목역": (37.5794, 127.0876),
    "망우역": (37.5977, 127.0930),
    # 성북구
    "성신여대입구역": (37.5926, 127.0166),
    "길음역": (37.6023, 127.0253),
    # 강북구
    "수유역": (37.6376, 127.0253),
    "미아사거리역": (37.6135, 127.0301),
    # 도봉구
    "창동역": (37.6531, 127.0475),
    "도봉산역": (37.6893, 127.0459),
    # 노원구
    "노원역": (37.6543, 127.0616),
    "상계역": (37.6600, 127.0725),
    # 은평구
    "연신내역": (37.6191, 126.9210),
    "불광역": (37.6104, 126.9297),
    # 서대문구
    "신촌역": (37.5551, 126.9368),
    "독립문": (37.5741, 126.9569),
    # 마포구
    "홍대입구역": (37.5568, 126.9236),
    "망원역": (37.556068, 126.9101053),
    # 양천구
    "목동운동장": (37.5257, 126.8756),
    "신정네거리역": (37.5206, 126.8540),
    # 강서구
    "김포공항": (37.5583, 126.7906),
    "발산역": (37.5586, 126.8378),
    # 구로구
    "구로디지털단지역": (37.485259, 126.901499),
    "신도림역": (37.508662, 126.891122),
    # 금천구
    "가산디지털단지역": (37.480368, 126.882608),
    "독산역": (37.466107, 126.889396),
    # 영등포구
    "영등포역": (37.515241, 126.907102),
    "여의도역": (37.521898, 126.923884),
    # 동작구 — 용산구가 들어오면서 생긴 한강 남쪽 경계였다가(D-107) "안"으로 옮김
    "노량진역": (37.514203, 126.941652),
    "사당역": (37.477236, 126.981713),
    # 관악구
    "서울대입구역": (37.481207, 126.952729),
    "신림역": (37.484248, 126.929714),
    # 서초구
    "서초역": (37.491852, 127.007702),
    "교대역": (37.493025, 127.013794),
    # 강남구 — 광진구가 들어오면서 생긴 동남쪽 경계였다가(D-107) "안"으로 옮김
    "강남역": (37.496420, 127.028349),
    "삼성역": (37.508864, 127.063162),
    # 송파구·강동구 — 광진구가 들어오면서 생긴 동남쪽 경계였다가(D-107) "안"으로 옮김
    "잠실역": (37.514774, 127.104666),
    "천호역": (37.538595, 127.123551),
}
_OUTSIDE = {
    # 구리·남양주 — 중랑구·노원구가 들어오면서 새로 생긴 동쪽 경계(서울 밖)
    "구리역": (37.5991, 127.1397),
    # 고양 — 은평구가 들어오면서 새로 생긴 서북쪽 경계(서울 밖)
    "화정역": (37.6349, 126.8355),
    # 성북구·강북구가 들어와도 그대로 밖인 대조군(서울 최북단 경기 접경)
    "의정부역": (37.7383, 127.0464),
    "부산역": (35.1151, 129.0415),
}

# 지원 구의 공식 면적(km²). 경계 파일이 엉뚱한 구로 바뀌면 여기서 걸린다.
# 서대문·마포·양천·강서구는 위키백과 infobox 기준(2026-08-26 확인).
# 구로·금천·영등포·동작·관악·서초·강남·송파·강동구는 위키백과 infobox 기준
# (2026-08-30 확인, D-107).
_OFFICIAL_AREA_KM2 = {
    "종로구": 23.91,
    "중구": 9.96,
    "용산구": 21.87,
    "성동구": 16.85,
    "광진구": 17.06,
    "동대문구": 14.22,
    "중랑구": 18.50,
    "성북구": 24.58,
    "강북구": 23.60,
    "도봉구": 20.70,
    "노원구": 35.44,
    "은평구": 29.70,
    "서대문구": 17.61,
    "마포구": 23.85,
    "양천구": 17.41,
    "강서구": 41.43,
    "구로구": 20.12,
    "금천구": 13.02,
    "영등포구": 24.53,
    "동작구": 16.35,
    "관악구": 29.57,
    "서초구": 46.98,
    "강남구": 39.55,
    "송파구": 33.88,
    "강동구": 24.59,
}
_EARTH_RADIUS_KM = 6371.0

_Polygons = tuple[tuple[tuple[tuple[float, float], ...], ...], ...]


def _largest_ring(polygons: _Polygons) -> tuple[tuple[float, float], ...]:
    return max((polygon[0] for polygon in polygons), key=len)


def _representative_point(polygons: _Polygons) -> tuple[float, float]:
    """구 안쪽의 한 점을 (위도, 경도)로 돌려준다.

    무게중심은 오목한 구에서 밖으로 나갈 수 있어, 그럴 때는 무게중심 위도에서
    가로 반직선을 그어 가장 넓은 내부 구간의 가운데를 쓴다.
    """
    ring = _largest_ring(polygons)
    latitude = sum(y for _, y in ring) / len(ring)
    longitude = sum(x for x, _ in ring) / len(ring)
    if _contains(polygons, latitude, longitude):
        return latitude, longitude

    crossings = sorted(
        (x2 - x1) * (latitude - y1) / (y2 - y1) + x1
        for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1], strict=True)
        if (y1 > latitude) != (y2 > latitude)
    )
    spans = [
        (right - left, (left + right) / 2)
        for left, right in zip(crossings[::2], crossings[1::2], strict=False)
    ]
    if not spans:
        raise AssertionError("가로 반직선이 폴리곤을 지나지 않습니다.")
    return latitude, max(spans)[1]


def _contains(polygons: _Polygons, latitude: float, longitude: float) -> bool:
    from app.service_area import _is_inside_polygon

    return any(_is_inside_polygon(longitude, latitude, polygon) for polygon in polygons)


def _polygon_area_km2(polygons: _Polygons) -> float:
    """외곽선 면적의 합. 위도에 맞춘 평면 근사라 구 규모에서는 오차가 작다."""
    total = 0.0
    for polygon in polygons:
        for ring in polygon:
            mean_latitude = math.radians(sum(point[1] for point in ring) / len(ring))
            projected = [
                (
                    math.radians(x) * _EARTH_RADIUS_KM * math.cos(mean_latitude),
                    math.radians(y) * _EARTH_RADIUS_KM,
                )
                for x, y in ring
            ]
            doubled = 0.0
            for index in range(len(projected) - 1):
                x1, y1 = projected[index]
                x2, y2 = projected[index + 1]
                doubled += x1 * y2 - x2 * y1
            total += abs(doubled / 2)
    return total


@pytest.mark.parametrize(("name", "point"), _INSIDE.items())
def test_지원_지역_안(name: str, point: tuple[float, float]) -> None:
    assert is_within_service_area(*point), name


@pytest.mark.parametrize(("name", "point"), _OUTSIDE.items())
def test_지원_지역_밖(name: str, point: tuple[float, float]) -> None:
    assert not is_within_service_area(*point), name


@pytest.mark.parametrize(
    "district_name", [name for name, _ in _all_district_polygons()]
)
def test_서울_25개_구_대표점이_지원_여부대로_갈린다(district_name: str) -> None:
    """지원 구를 늘려도 이 테스트는 목록을 따라 저절로 바뀐다."""
    supported = district_name in {district.name for district in SUPPORTED_DISTRICTS}
    latitude, longitude = _representative_point(dict(_all_district_polygons())[district_name])
    assert is_within_service_area(latitude, longitude) is supported, district_name


def test_경계_데이터는_한_번만_읽는다() -> None:
    """정적 데이터라 요청마다 파일을 열지 않는다."""
    first = _service_area_polygons()
    assert _service_area_polygons() is first
    assert _all_district_polygons() is _all_district_polygons()


def test_지원_구마다_폴리곤을_고른다() -> None:
    """목록에 있는 구가 하나라도 빠지면 서비스 범위가 조용히 줄어든다."""
    selected = [district_code for district_code, _ in _service_area_polygons()]
    assert selected == [district.district_code for district in SUPPORTED_DISTRICTS]


@pytest.mark.parametrize("district", SUPPORTED_DISTRICTS, ids=lambda d: d.name)
def test_경계_폴리곤_면적이_공식_면적과_맞는다(district: ServiceDistrict) -> None:
    """2018년 경계라 오차가 있지만, 다른 구 파일이 들어오면 몇 배씩 벌어진다."""
    polygons = dict(_all_district_polygons())[district.name]
    official = _OFFICIAL_AREA_KM2[district.name]
    measured = _polygon_area_km2(polygons)
    assert abs(measured - official) / official < 0.01, f"{district.name} {measured:.2f}"


def test_경계_파일에_없는_구는_예외로_끊는다() -> None:
    """지원 목록에만 있고 경계가 없으면 그 구는 조용히 범위에서 빠진다."""
    ghost = ServiceDistrict("999", "없는구")
    with pytest.raises(ValueError, match="없는구"):
        _select_supported(_all_district_polygons(), (ghost,))


def test_안내_문구용_이름이_지원_목록을_그대로_따른다() -> None:
    """문구가 목록을 읽지 않으면 구만 늘고 안내는 옛 범위를 말하는 상태가 된다.

    실제로 지원 구가 넷으로 늘어난 뒤에도 "종로구만 지원합니다"가 나갔다(TP-127).
    """
    label = supported_district_label()

    assert label == "·".join(district.name for district in SUPPORTED_DISTRICTS)
    for district in SUPPORTED_DISTRICTS:
        assert district.name in label


def test_안내_문구용_이름에_시_이름을_붙일_수_있다() -> None:
    """모두 서울특별시라 구마다 반복하지 않고 앞에 한 번만 쓴다."""
    assert supported_district_label(with_city=True) == (
        f"서울특별시 {supported_district_label()}"
    )
