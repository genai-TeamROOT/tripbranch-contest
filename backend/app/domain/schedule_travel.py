"""일정 편성이 소비하는 방향성 구간 이동정보 계약."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.domain.travel_route import (
    GeoCoordinate,
    RouteSource,
    RouteStatus,
    TravelMode,
)
from app.schemas import Transport


class TravelConfidence(StrEnum):
    """일정 구간 이동시간의 신뢰 수준."""

    HIGH = "high"
    LOW = "low"


@dataclass(frozen=True)
class ScheduleTravelCandidate:
    """일정 구간 계산에 필요한 장소 식별자와 좌표."""

    place_id: str
    coordinate: GeoCoordinate

    def __post_init__(self) -> None:
        if not self.place_id.strip():
            raise ValueError("place_id는 비어 있을 수 없습니다.")


@dataclass(frozen=True)
class SegmentWeather:
    """구간 이동수단 판정에 쓰는 날씨 **사실**. 좋다/나쁘다는 담지 않는다(D-051).

    C의 계약 타입(`agent_context.schemas.WeatherForecast`)을 그대로 쓰지 않는
    이유는 방향이다 — 이 모듈은 순수 계약이라 위쪽 계약을 import하면 의존이
    거꾸로 서고, `app.schedule.schemas`가 이 타입을 쓰는 순간 순환이 된다.
    담는 사실은 같고, 옮겨 담는 것은 A(`agent_runtime._segment_weather()`)가 한다.

    사용자가 발화에서 말한 날씨(`UserConditions.weather`)와 다르다. 그쪽은
    "말했다"는 사실이고 이쪽은 조회한 예보다.
    """

    precipitation: str | None = None
    sky: str | None = None
    temperature_celsius: float | None = None


@dataclass(frozen=True)
class SegmentModeInput:
    """이동수단을 정할 구간 한 줄. 판정하는 쪽이 보는 유일한 재료다.

    **누적 도보량을 여기 담지 않는다.** "이 구간 전까지 몇 분 걸었나"는 앞 구간이
    도보인지 이미 정해져 있어야 나오는 값인데, 그게 지금 정하려는 값이라 순환이다.
    대신 구간마다 `walk_minutes`("걸으면 몇 분")만 주고, 누적은 판정하는 쪽이 표
    전체를 보고 직접 더한다 — 전 구간을 한 번에 넘기는 설계라 가능하다.
    """

    from_place_id: str
    to_place_id: str
    # 추려낸 구간 안에서의 순번(1부터). `pairs`의 인덱스가 아니다 — 중복·자기쌍·
    # 좌표 없는 장소가 빠지므로 둘이 어긋난다.
    order: int
    distance_m: int
    # 이 구간을 도보로 갔을 때의 예상 분. 실제로 도보로 갈지는 아직 모른다.
    walk_minutes: float

    @property
    def key(self) -> tuple[str, str]:
        return (self.from_place_id, self.to_place_id)


@dataclass(frozen=True)
class ModeJudgmentContext:
    """전 구간이 공유하는 판정 조건.

    `UserConditions`를 그대로 넘기지 않는다. 판정에 쓰는 값만 옮겨 담아야 나중에
    "이 판정이 무엇을 보고 정했나"가 타입에 그대로 드러난다.
    """

    transport: Transport | None
    companion: str | None = None
    accessibility_needs: tuple[str, ...] = ()
    # 구간이 순서대로 이어지는가. 일정은 전 구간을 다 지나가므로 True이고, 추천은
    # 후보가 서로 대안이라 False다 — 사용자는 그중 한 곳만 간다.
    #
    # **이 값이 앞 구간을 봐도 되는지를 가른다.** 독립인데 앞 후보를 근거로 삼으면
    # 목록에서 몇 번째냐에 따라 같은 거리가 다르게 판정된다.
    sequential: bool = True
    # 조회된 예보. 조회에 실패했거나 값이 없는 턴에서는 비어 있다.
    weather: SegmentWeather | None = None


@dataclass(frozen=True)
class ScheduleTravelPair:
    """이동정보가 필요한 방향성 장소 쌍."""

    from_place_id: str
    to_place_id: str

    def __post_init__(self) -> None:
        if not self.from_place_id.strip() or not self.to_place_id.strip():
            raise ValueError("구간의 place_id는 비어 있을 수 없습니다.")


@dataclass(frozen=True)
class ScheduleTravelEdge:
    """한 방향 구간의 정규화된 이동정보."""

    from_place_id: str
    to_place_id: str
    mode: TravelMode
    status: RouteStatus
    source: RouteSource
    duration_min: int
    distance_m: int | None
    confidence: TravelConfidence
    error_code: str | None = None

    def __post_init__(self) -> None:
        if not self.from_place_id.strip() or not self.to_place_id.strip():
            raise ValueError("구간의 place_id는 비어 있을 수 없습니다.")
        if self.from_place_id == self.to_place_id:
            raise ValueError("자기 자신으로 향하는 이동 구간은 만들 수 없습니다.")
        if self.duration_min < 0:
            raise ValueError("duration_min은 0 이상이어야 합니다.")
        if self.distance_m is not None and self.distance_m < 0:
            raise ValueError("distance_m는 0 이상이어야 합니다.")


@dataclass(frozen=True)
class ScheduleTravelWarning:
    """건너뛴 구간 하나와 그 사유.

    경고 코드만 모아두면 후보가 10곳일 때 90개 요청 중 어느 구간이 빠졌는지
    알 수 없으므로, 사유와 함께 어느 방향 쌍에서 났는지를 같이 남긴다.
    """

    code: str
    from_place_id: str
    to_place_id: str


@dataclass(frozen=True)
class ScheduleTravelEstimateResult:
    """요청된 구간들의 추정 이동정보와 비차단 경고."""

    edges: tuple[ScheduleTravelEdge, ...]
    warnings: tuple[ScheduleTravelWarning, ...] = ()

    def edge_by_pair(self) -> dict[tuple[str, str], ScheduleTravelEdge]:
        return {
            (edge.from_place_id, edge.to_place_id): edge
            for edge in self.edges
        }


__all__ = [
    "ScheduleTravelCandidate",
    "ScheduleTravelEdge",
    "ScheduleTravelEstimateResult",
    "ScheduleTravelPair",
    "ModeJudgmentContext",
    "ScheduleTravelWarning",
    "SegmentModeInput",
    "SegmentWeather",
    "TravelConfidence",
]
