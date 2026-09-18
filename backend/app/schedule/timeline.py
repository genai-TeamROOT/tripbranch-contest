"""일정 시간표 계산기. (TP-215)

**왜 필요한가.** 지금까지 도착시각·이동시간·총 소요시간을 전부 LLM이 따로
만들었다. `estimated_arrival`은 응답을 받은 뒤 10분 단위로 올리기만 했지
(`planner._round_up_arrival`) 체류·이동으로부터 다시 계산하지 않았고,
`total_duration_min`은 LLM이 준 값을 그대로 실었다. 그래서 항목들의 합과
총합이 서로 맞는지 확인하는 곳이 없었다.

이 모듈은 **순서와 체류시간이 정해지면 나머지 시각을 전부 결정적으로 계산한다.**
LLM은 무엇을 어떤 순서로 갈지와 얼마나 머물지를 제안하고, 시계는 코드가 본다.

**자정을 넘는 일정.** `datetime`으로 계산한다. 기존 후처리는 "HH:MM"을 자정 기준
분으로 파싱해서(`planner._parse_hhmm_minutes`) 01:30이 90분으로 읽혔고, 23:00에
시작한 일정과 순서를 비교하면 뒤집혔다.

**이동시간의 출처.** `travel_minutes` 콜러블로 주입받는다. 지금은 직선거리를
가정 속도로 나눈 값이고(`estimated_travel_minutes`), 실측 경로로 갈아끼우는 것이
TP-216이다 — 그때 이 파일은 안 바뀌고 콜러블만 바뀐다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.place_search_policy import (
    NON_WALKING_SPEED_KM_PER_MINUTE,
    WALKING_SPEED_KM_PER_MINUTE,
)
from app.schemas import Transport, UserConditions

# (출발 place_id, 도착 place_id) -> 이동 소요(분). 없으면 None을 돌려준다.
TravelMinutes = Callable[[str, str], int | None]

# 이동 정보를 못 구했을 때 쓰는 값(분). 0으로 두면 순간이동하는 일정이 나오고,
# 크게 잡으면 없는 근거로 일정을 망친다. 프롬프트가 안내하던 "구간 사이 이동
# 15분 내외"와 같은 값을 쓴다. budget.travel_estimate_minutes()가 거리 정보를
# 못 구했을 때 쓰는 폴백도 이 값이라, 개수 상한을 정한 가정과 시간표가 쓰는 값이
# 어긋나지 않는다 — 거리가 있으면 양쪽 다 실제 거리를 쓴다(TP-239).
FALLBACK_TRAVEL_MINUTES = 15


@dataclass(frozen=True)
class TimelineStop:
    """시간표 계산의 입력 한 건. 순서는 리스트 순서를 따른다."""

    place_id: str
    visit_duration_min: int
    # 운영 시작·종료 시각(자정 기준 분). 모르면 None이고, 그때는 대기시간을
    # 잡지 않는다 — 근거 없이 기다리게 만들지 않는다.
    opens_at_min: int | None = None
    closes_at_min: int | None = None


@dataclass(frozen=True)
class PlannedStop:
    """시간표가 확정한 방문 한 건."""

    order: int
    place_id: str
    arrival_at: datetime
    visit_start_at: datetime
    departure_at: datetime
    visit_duration_min: int
    waiting_before_visit_min: int
    travel_to_next_min: int | None


@dataclass(frozen=True)
class Timeline:
    stops: tuple[PlannedStop, ...]
    total_duration_min: int

    @property
    def ends_at(self) -> datetime | None:
        return self.stops[-1].departure_at if self.stops else None


def travel_speed_km_per_minute(conditions: UserConditions) -> float:
    """이 요청의 이동 속도 가정(km/분).

    **검색 반경을 만든 가정과 같은 값이어야 한다.** 반경이
    `말한 이동시간 x 속도`로 정해지므로(`recommendation_transform.to_search_radius_km`)
    일정 쪽이 다른 속도를 쓰면 "반경 안에서 모은 후보인데 시간 안에 못 돈다"가
    정상이 된다. 실측값을 넣는 것이 왜 반경 산정과 함께 가야 하는지는
    `place_search_policy.NON_WALKING_SPEED_KM_PER_MINUTE` 주석에 있다.

    판정 규칙은 `recommendation_transform._radius_uses_walking_speed()`를 그대로
    옮긴 것이다. 두 파일이 소유가 갈려 있어 아직 한 곳으로 합치지 못했다 —
    한쪽만 바꾸면 분자와 분모의 기준이 어긋난다.
    """

    uses_walking = conditions.transport is Transport.WALK or conditions.max_travel_time is None
    return WALKING_SPEED_KM_PER_MINUTE if uses_walking else NON_WALKING_SPEED_KM_PER_MINUTE


def estimated_travel_minutes(
    distances_km: Mapping[tuple[str, str], float],
    *,
    speed_km_per_minute: float,
) -> TravelMinutes:
    """직선거리를 가정 속도로 나눠 이동시간을 만드는 콜러블을 돌려준다.

    거리 행렬은 방향이 없다(`app.geo.haversine_km`) — 양방향 키를 모두 본다.
    실측으로 갈아끼우면 방향이 생기므로(TP-216) 그때는 이 함수를 안 쓴다.
    """

    def resolve(from_place_id: str, to_place_id: str) -> int | None:
        distance_km = distances_km.get((from_place_id, to_place_id))
        if distance_km is None:
            distance_km = distances_km.get((to_place_id, from_place_id))
        if distance_km is None or speed_km_per_minute <= 0:
            return None
        return max(1, round(distance_km / speed_km_per_minute))

    return resolve


def _minutes_of_day(moment: datetime) -> int:
    return moment.hour * 60 + moment.minute


def _waiting_minutes(
    arrival: datetime,
    opens_at_min: int | None,
    *,
    started_on: date,
) -> int:
    """운영 시작 전에 도착했으면 기다려야 하는 분.

    **출발한 날 안에서만 판정한다.** `opens_at_min`은 자정 기준 분이라 날짜 정보가
    없어서, 자정을 넘겨 도착하면 그날 개장까지의 시간이 전부 대기로 잡힌다 —
    23시에 시작해 01:30에 도착한 일정에 "09시 개장까지 7시간 30분 대기"를 붙이는
    식이다. 그건 대기가 아니라 문 닫은 곳을 넣었다는 뜻이고, 운영시간 경고로
    다뤄야 한다(그 경고는 이미 planner가 붙인다). 여기서 기다리게 만들면 총
    소요시간까지 함께 망가진다.
    """

    if opens_at_min is None or arrival.date() != started_on:
        return 0
    arrived_at_min = _minutes_of_day(arrival)
    if arrived_at_min >= opens_at_min:
        return 0
    return opens_at_min - arrived_at_min


def build_timeline(
    stops: Sequence[TimelineStop],
    *,
    start_at: datetime,
    travel_minutes: TravelMinutes,
    fallback_travel_min: int = FALLBACK_TRAVEL_MINUTES,
) -> Timeline:
    """순서대로 방문할 때의 도착·대기·출발·총 소요시간을 계산한다.

    총 소요시간은 **첫 장소 도착부터 마지막 장소 체류 종료까지**다. 첫 구간
    이동시간(출발지 -> 첫 장소)은 출발지를 모르므로 포함하지 않는다 — 지금
    `visit_datetime`은 "이 시각에 첫 장소에 있다"는 뜻으로 쓰인다.
    """

    if not stops:
        return Timeline(stops=(), total_duration_min=0)

    planned: list[PlannedStop] = []
    cursor = start_at

    for index, stop in enumerate(stops):
        arrival = cursor
        waiting = _waiting_minutes(arrival, stop.opens_at_min, started_on=start_at.date())
        visit_start = arrival + timedelta(minutes=waiting)
        departure = visit_start + timedelta(minutes=stop.visit_duration_min)

        next_stop = stops[index + 1] if index + 1 < len(stops) else None
        if next_stop is None:
            travel_to_next: int | None = None
        else:
            resolved = travel_minutes(stop.place_id, next_stop.place_id)
            travel_to_next = fallback_travel_min if resolved is None else resolved

        planned.append(
            PlannedStop(
                order=index + 1,
                place_id=stop.place_id,
                arrival_at=arrival,
                visit_start_at=visit_start,
                departure_at=departure,
                visit_duration_min=stop.visit_duration_min,
                waiting_before_visit_min=waiting,
                travel_to_next_min=travel_to_next,
            )
        )
        cursor = departure + timedelta(minutes=travel_to_next or 0)

    total = round((planned[-1].departure_at - planned[0].arrival_at).total_seconds() / 60)
    return Timeline(stops=tuple(planned), total_duration_min=total)


__all__ = [
    "FALLBACK_TRAVEL_MINUTES",
    "PlannedStop",
    "Timeline",
    "TimelineStop",
    "TravelMinutes",
    "build_timeline",
    "estimated_travel_minutes",
    "travel_speed_km_per_minute",
]
