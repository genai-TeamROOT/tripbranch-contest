"""지원 구(SUPPORTED_DISTRICTS)별 대표 스팟 — 위치 되묻기 대체 버튼(TP-160)에 쓴다.

역할: "카페 추천해줘"(위치 언급 없음)나 "용산 카페 추천"(지역검색/Geocoding이
후보를 못 찾음)처럼 위치를 확정 못 지었을 때, 사용자 발화나 GPS로 짐작한 구의
대표 스팟을 되묻기 버튼으로 보여준다. 종로구 4곳(경복궁/인사동/광화문/북촌)만
고정으로 보여주던 옛 방식(D-044)이 서비스 지역이 16개 구로 늘어난 뒤에도 안
바뀌어 생긴 버그(D-083, D-086 "남은 것" 항목, TP-160)를 고친다.

좌표는 새로 조사하지 않고 이미 이 저장소에서 검증된 두 출처에서 그대로 옮겼다:
종로구 4곳은 `resources/seoul_realtime/population_areas_121.json`(서울시 실시간
인구 지역 목록), 그 다음 15개 구는 `tests/test_service_area.py`의 `_INSIDE`
딕셔너리(경계 판정 회귀 테스트가 이미 실측 검증한 좌표)에서 구당 2곳씩 옮겼다.
테스트 파일을 import하지 않고 값만 옮긴 이유는 판정 회귀 테스트와 이 대표 스팟
데이터가 서로 다른 책임이라서다 — 한쪽이 바뀌어도 다른 쪽이 흔들리면 안 된다.

2026-08-30: 서울 25개 구 전체로 확대되면서(D-107) 나머지 9개 구(강남·송파·
영등포·서초·금천·구로·관악·강동·동작)를 추가했다. 이 9곳은 `_INSIDE` 딕셔너리에
없었으므로(D-086까지는 오히려 "아직 밖"인 경계 대조군으로 쓰였다) Naver Local
Search로 구별 지하철역 2곳씩 새로 조회해 채웠다 — 주소가 해당 구로 확인된 값만
썼다.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.geo import haversine_km
from app.service_area import SUPPORTED_DISTRICTS, ServiceDistrict, find_containing_district


@dataclass(frozen=True)
class DistrictLandmark:
    name: str
    latitude: float
    longitude: float


DISTRICT_LANDMARKS: dict[str, tuple[DistrictLandmark, ...]] = {
    "종로구": (
        DistrictLandmark("경복궁", 37.5799, 126.9768),
        DistrictLandmark("인사동", 37.5739, 126.9861),
        DistrictLandmark("광화문", 37.5709, 126.9772),
        DistrictLandmark("북촌", 37.5822, 126.9840),
    ),
    "중구": (
        DistrictLandmark("명동", 37.5636, 126.9827),
        DistrictLandmark("서울역", 37.5547, 126.9707),
    ),
    "용산구": (
        DistrictLandmark("이태원역", 37.5346, 126.9946),
        DistrictLandmark("용산역", 37.5299, 126.9648),
    ),
    "성동구": (
        DistrictLandmark("성수동 카페거리", 37.5445, 127.0557),
        DistrictLandmark("왕십리역", 37.5614, 127.0374),
    ),
    "광진구": (
        DistrictLandmark("건대입구역", 37.5403, 127.0700),
        DistrictLandmark("어린이대공원", 37.5486, 127.0806),
    ),
    "동대문구": (
        DistrictLandmark("청량리역", 37.5800, 127.0470),
        DistrictLandmark("경동시장", 37.5764, 127.0378),
    ),
    "중랑구": (
        DistrictLandmark("면목역", 37.5794, 127.0876),
        DistrictLandmark("망우역", 37.5977, 127.0930),
    ),
    "성북구": (
        DistrictLandmark("성신여대입구역", 37.5926, 127.0166),
        DistrictLandmark("길음역", 37.6023, 127.0253),
    ),
    "강북구": (
        DistrictLandmark("수유역", 37.6376, 127.0253),
        DistrictLandmark("미아사거리역", 37.6135, 127.0301),
    ),
    "도봉구": (
        DistrictLandmark("창동역", 37.6531, 127.0475),
        DistrictLandmark("도봉산역", 37.6893, 127.0459),
    ),
    "노원구": (
        DistrictLandmark("노원역", 37.6543, 127.0616),
        DistrictLandmark("상계역", 37.6600, 127.0725),
    ),
    "은평구": (
        DistrictLandmark("연신내역", 37.6191, 126.9210),
        DistrictLandmark("불광역", 37.6104, 126.9297),
    ),
    "서대문구": (
        DistrictLandmark("신촌역", 37.5551, 126.9368),
        DistrictLandmark("독립문", 37.5741, 126.9569),
    ),
    "마포구": (
        DistrictLandmark("홍대입구역", 37.5568, 126.9236),
        DistrictLandmark("망원역", 37.556068, 126.9101053),
    ),
    "양천구": (
        DistrictLandmark("목동운동장", 37.5257, 126.8756),
        DistrictLandmark("신정네거리역", 37.5206, 126.8540),
    ),
    "강서구": (
        DistrictLandmark("김포공항", 37.5583, 126.7906),
        DistrictLandmark("발산역", 37.5586, 126.8378),
    ),
    "구로구": (
        DistrictLandmark("구로디지털단지역", 37.485259, 126.901499),
        DistrictLandmark("신도림역", 37.508662, 126.891122),
    ),
    "금천구": (
        DistrictLandmark("가산디지털단지역", 37.480368, 126.882608),
        DistrictLandmark("독산역", 37.466107, 126.889396),
    ),
    "영등포구": (
        DistrictLandmark("영등포역", 37.515241, 126.907102),
        DistrictLandmark("여의도역", 37.521898, 126.923884),
    ),
    "동작구": (
        DistrictLandmark("사당역", 37.477236, 126.981713),
        DistrictLandmark("노량진역", 37.514203, 126.941652),
    ),
    "관악구": (
        DistrictLandmark("서울대입구역", 37.481207, 126.952729),
        DistrictLandmark("신림역", 37.484248, 126.929714),
    ),
    "서초구": (
        DistrictLandmark("서초역", 37.491852, 127.007702),
        DistrictLandmark("교대역", 37.493025, 127.013794),
    ),
    "강남구": (
        DistrictLandmark("강남역", 37.496420, 127.028349),
        DistrictLandmark("삼성역", 37.508864, 127.063162),
    ),
    "송파구": (
        DistrictLandmark("잠실역", 37.514774, 127.104666),
        DistrictLandmark("롯데월드타워", 37.512570, 127.102562),
    ),
    "강동구": (
        DistrictLandmark("천호역", 37.538595, 127.123551),
        DistrictLandmark("강동역", 37.535958, 127.132160),
    ),
}

assert DISTRICT_LANDMARKS.keys() == {district.name for district in SUPPORTED_DISTRICTS}, (
    "DISTRICT_LANDMARKS는 SUPPORTED_DISTRICTS와 구 이름이 정확히 일치해야 한다"
)

# 구 이름 stem("용산구" → "용산")이 이 길이 미만이면 stem 매칭을 생략하고
# 풀네임만 대조한다 — "중구" → "중"처럼 짧은 stem은 "중식당" 같은 무관한
# 단어에도 걸려 오탐을 만든다.
_MIN_STEM_LENGTH_FOR_TEXT_MATCH = 2

# 폴리곤 밖(경계 정밀도 한계)일 때만 쓰는 최근접 탐색의 상한. 서울 전역을
# 넉넉히 덮는 값이라 이 밖이면 사실상 지원 구 밖이라고 본다.
_GPS_NEAREST_DISTRICT_MAX_DISTANCE_KM = 15.0


def find_district_by_text(query: str) -> ServiceDistrict | None:
    """사용자 발화 원문에 지원 구 이름이 들어 있으면 그 구를 반환한다."""

    for district in SUPPORTED_DISTRICTS:
        if district.name in query:
            return district
        stem = district.name.removesuffix("구")
        if len(stem) >= _MIN_STEM_LENGTH_FOR_TEXT_MATCH and stem in query:
            return district
    return None


def find_district_by_gps(latitude: float, longitude: float) -> ServiceDistrict | None:
    """GPS 좌표를 포함하거나(우선) 가장 가까운 지원 구를 반환한다."""

    contained = find_containing_district(latitude, longitude)
    if contained is not None:
        return contained

    nearest_district: ServiceDistrict | None = None
    nearest_distance_km: float | None = None
    for district in SUPPORTED_DISTRICTS:
        for landmark in DISTRICT_LANDMARKS[district.name]:
            distance_km = haversine_km(latitude, longitude, landmark.latitude, landmark.longitude)
            if nearest_distance_km is None or distance_km < nearest_distance_km:
                nearest_distance_km = distance_km
                nearest_district = district
    if nearest_distance_km is None or nearest_distance_km > _GPS_NEAREST_DISTRICT_MAX_DISTANCE_KM:
        return None
    return nearest_district


def landmarks_for_district(name: str) -> tuple[DistrictLandmark, ...]:
    return DISTRICT_LANDMARKS.get(name, ())


__all__ = [
    "DISTRICT_LANDMARKS",
    "DistrictLandmark",
    "find_district_by_gps",
    "find_district_by_text",
    "landmarks_for_district",
]
