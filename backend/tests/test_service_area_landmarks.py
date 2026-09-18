"""TP-160 위치 되묻기 대체 버튼 — 구별 대표 스팟 매칭 로직."""

from __future__ import annotations

from app.service_area import SUPPORTED_DISTRICTS
from app.service_area_landmarks import (
    DISTRICT_LANDMARKS,
    find_district_by_gps,
    find_district_by_text,
)


def test_district_landmarks_cover_every_supported_district() -> None:
    assert DISTRICT_LANDMARKS.keys() == {district.name for district in SUPPORTED_DISTRICTS}
    for landmarks in DISTRICT_LANDMARKS.values():
        assert len(landmarks) >= 2


def test_find_district_by_text_matches_full_name() -> None:
    district = find_district_by_text("중구 맛집 추천해줘")
    assert district is not None
    assert district.name == "중구"


def test_find_district_by_text_matches_stem_without_gu_suffix() -> None:
    district = find_district_by_text("용산 카페 추천")
    assert district is not None
    assert district.name == "용산구"


def test_find_district_by_text_short_stem_requires_full_name() -> None:
    """"중구"의 stem "중"은 1글자라 "중식당" 같은 무관한 단어에 오탐하면 안 된다."""
    assert find_district_by_text("중식당 추천해줘") is None


def test_find_district_by_text_no_match_returns_none() -> None:
    assert find_district_by_text("아무 데나 괜찮아요") is None


def test_find_district_by_gps_prefers_polygon_containment() -> None:
    # 용산역 좌표 — 용산구 폴리곤 내부.
    district = find_district_by_gps(37.5299, 126.9648)
    assert district is not None
    assert district.name == "용산구"


def test_find_district_by_gps_falls_back_to_nearest_when_outside_all_polygons() -> None:
    # 구리역(경기 구리시) — 서울 25개 구 전체가 지원 구가 된 뒤에도(D-107)
    # 여전히 어느 폴리곤에도 안 들어가는 서울 밖 지점이다(test_service_area.py
    # _OUTSIDE 픽스처와 동일 좌표). 가장 가까운 지원 구가 15km 이내라 그
    # 구(중랑구)를 반환해야 한다.
    district = find_district_by_gps(37.5991, 127.1397)
    assert district is not None
    assert district.name == "중랑구"


def test_find_district_by_gps_returns_none_far_outside_seoul() -> None:
    # 부산시청 좌표 — 어느 지원 구에서도 15km를 훌쩍 넘는다.
    assert find_district_by_gps(35.1796, 129.0756) is None
