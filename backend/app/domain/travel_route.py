"""이동 경로 Provider와 Tool이 공유하는 순수 도메인 계약."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True)
class GeoCoordinate:
    """외부 프레임워크에 의존하지 않는 위·경도 값 객체."""

    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        if not -90 <= self.latitude <= 90:
            raise ValueError("latitude는 -90 이상 90 이하여야 합니다.")
        if not -180 <= self.longitude <= 180:
            raise ValueError("longitude는 -180 이상 180 이하여야 합니다.")


class TravelMode(StrEnum):
    """조회 대상 이동수단. `Transport`(사용자 조건)와 달리 경로 조회 축이다."""

    WALKING = "walking"
    TRANSIT = "transit"
    DRIVING = "driving"


class RouteSource(StrEnum):
    KAKAO_WALKING = "kakao_walking"
    NAVER_DRIVING = "naver_driving"
    KAKAO_TRANSIT = "kakao_transit"
    STRAIGHT_LINE_ESTIMATE = "straight_line_estimate"


class RouteStatus(StrEnum):
    SUCCESS = "success"
    NO_DATA = "no_data"
    UNAVAILABLE = "unavailable"


# 외부 API로 실제 경로를 잰 source. 여기 없는 source(직선거리 추정)는 채점에도
# 근거 문장에도 쓰지 않는다.
#
# 이 집합이 도메인 계약에 있는 이유는 읽는 곳이 둘이기 때문이다 — 채점
# (`domain/scoring.py::_applied_travel_route`)과, 한 후보를 두 이동수단으로
# 조회했을 때 어느 값을 쓸지 고르는 쪽(`agent_runtime.py::_fastest_routes`)이다.
# 양쪽에 따로 적으면 한쪽만 바뀌었을 때 조용히 어긋난다: 고르는 쪽이 추정을
# 실측보다 빠르다고 채택하면, 채점이 그 값을 버려 회차 전체가 직선거리로 내려간다.
MEASURED_ROUTE_SOURCES = frozenset(
    {
        RouteSource.KAKAO_WALKING,
        RouteSource.NAVER_DRIVING,
        RouteSource.KAKAO_TRANSIT,
    }
)


@dataclass(frozen=True)
class RouteDestination:
    place_id: str
    coordinate: GeoCoordinate

    def __post_init__(self) -> None:
        if not self.place_id.strip():
            raise ValueError("place_id는 비어 있을 수 없습니다.")


@dataclass(frozen=True)
class TravelRoute:
    """목적지 한 곳의 정규화된 이동 결과.

    `mode`는 기본값을 두지 않는다. 어떤 이동수단으로 잰 값인지가 소비 측의
    판정(속도 예산·응답 표기)을 직접 움직이므로, 기본값으로 조용히 도보라고
    적히면 대중교통 결과가 도보로 채점된다.
    """

    place_id: str
    mode: TravelMode
    status: RouteStatus
    source: RouteSource
    distance_m: int | None = None
    duration_seconds: int | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        if not self.place_id.strip():
            raise ValueError("place_id는 비어 있을 수 없습니다.")
        if self.distance_m is not None and self.distance_m < 0:
            raise ValueError("distance_m는 0 이상이어야 합니다.")
        if self.duration_seconds is not None and self.duration_seconds < 0:
            raise ValueError("duration_seconds는 0 이상이어야 합니다.")
        if self.status is RouteStatus.SUCCESS and (
            self.distance_m is None or self.duration_seconds is None
        ):
            raise ValueError("성공한 경로에는 거리와 소요 시간이 모두 필요합니다.")


@dataclass(frozen=True)
class TravelRouteBatch:
    routes: tuple[TravelRoute, ...]
    transaction_id: str | None = None
