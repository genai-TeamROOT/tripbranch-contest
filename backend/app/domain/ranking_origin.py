"""거리·경로·근거 문장이 함께 쓰는 랭킹 기준점을 정한다.

후보를 **모으는** 중심과 후보를 **줄 세우는** 기준점은 다르다(TP-112).

- 수집 중심은 검색 기준점(`context.location`)이다. "안국역 근처 갈만한 곳"이면
  안국역 주변을 뒤지는 게 맞다.
- 랭킹 기준점은 사용자 위치(`context.user_location`)다. 실제로 이동하는 사람은
  사용자이기 때문이다. 안국역에서 같은 거리에 있는 두 후보라도 사용자 쪽에 있는
  쪽이 실제로 더 가깝고, 타겟 기준으로는 그 둘이 동점이라 구분되지 않는다.

사용자 위치를 모르면(발화도 기기 GPS도 없는 요청) 검색 기준점으로 돌아간다 —
지어낸 좌표로 줄을 세우지 않는다.

**예외**: "안국역에서 10분"처럼 발화가 조사로 출발점을 확정하면(`UserConditions.
travel_origin == TravelOrigin.SEARCH_CENTER`), 위 기본 순서를 따르지 않고
검색 기준점을 그대로 랭킹 기준점으로 쓴다 — 사용자가 이미 "거기서부터"라고
말했기 때문이다(D-071).
"""

from __future__ import annotations

import math

from app.agent_context.schemas import (
    ContextValue,
    RecommendationContext,
    ResolvedLocation,
)
from app.place_search_policy import EARTH_RADIUS_KM
from app.schemas import TravelOrigin, TravelOriginToggle, UserConditions


def haversine_km(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    """두 좌표 사이 대권거리(km). 소수점 3자리에서 반올림한다."""

    latitude_delta = math.radians(latitude_b - latitude_a)
    longitude_delta = math.radians(longitude_b - longitude_a)
    value = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(math.radians(latitude_a))
        * math.cos(math.radians(latitude_b))
        * math.sin(longitude_delta / 2) ** 2
    )
    return round(EARTH_RADIUS_KM * 2 * math.asin(math.sqrt(value)), 3)


def _usable(value: ContextValue[ResolvedLocation] | None) -> ResolvedLocation | None:
    """조회에 성공해 좌표가 실제로 들어 있는 것만 통과시킨다."""

    if value is None or value.status not in {"success", "partial"} or value.data is None:
        return None
    return value.data


def resolve_ranking_origin(
    context: RecommendationContext,
    conditions: UserConditions | None = None,
) -> ResolvedLocation | None:
    """거리·경로·근거 문장이 기준으로 삼을 좌표.

    `conditions.travel_origin`이 SEARCH_CENTER면("안국역에서 10분") 검색
    기준점을 그대로 쓴다 — 발화가 이미 출발점을 확정했으므로 사용자 위치로
    되돌릴 이유가 없다. 그 외에는 사용자 위치를 우선한다(기존 동작, D-067).
    """

    if (
        conditions is not None
        and conditions.travel_origin is TravelOrigin.SEARCH_CENTER
    ):
        search_center = _usable(context.location)
        if search_center is not None:
            return search_center

    return _usable(context.user_location) or _usable(context.location)


def resolve_search_center(context: RecommendationContext) -> ResolvedLocation | None:
    """후보를 모은 중심. 랭킹 기준점과 달리 언제나 검색 기준점이다."""

    return _usable(context.location)


def resolve_user_to_target_km(context: RecommendationContext) -> float | None:
    """사용자 위치에서 검색 기준점까지의 직선거리(km).

    거리 점수의 분모를 사용자 기준으로 되돌리는 데 쓴다. 사용자가 이동시간을
    말하지 않은 요청은 분모가 `DEFAULT_PLACE_SEARCH_RADIUS_KM`인데, 이 값은
    "타겟 주변 얼마를 뒤지는가"라는 **수집 정책**에서 빌려온 거리라 원점이 타겟에
    묶여 있다. 분자만 사용자 기준으로 바꾸면 사용자가 타겟에서 멀 때 모든 후보가
    분모를 넘겨 거리 점수가 전부 0이 된다(가중치 0.20이 통째로 죽는다).

    후보는 전부 타겟 중심 수집 반경 안에 있으므로, 삼각부등식에 따라 사용자
    기준 거리는 이 값 + 수집 반경을 넘을 수 없다. 그래서 이 값을 분모에 더하면
    어떤 후보도 0으로 잘리지 않는다(`to_search_radius_km` 참고).

    사용자가 이동시간을 **말한** 요청에는 쓰지 않는다. 그때 분모는 사용자가 말한
    시간 약속이고, 시간 약속은 어디서 재든 같은 값이라 이미 원점이 없다 —
    거기 이 거리를 더하면 "30분"이 사실상 30분+α가 된다(scoring.py 참고).

    사용자 위치나 검색 기준점을 모르면 None이다. 둘이 같은 곳으로 해석됐으면
    0.0이 나오고, 그때는 분모가 그대로 유지된다.
    """

    origin = _usable(context.user_location)
    target = _usable(context.location)
    if origin is None or target is None:
        return None
    return haversine_km(
        origin.location.latitude,
        origin.location.longitude,
        target.location.latitude,
        target.location.longitude,
    )


def resolve_travel_origin_toggle(
    context: RecommendationContext | None,
    conditions: UserConditions | None,
) -> TravelOriginToggle | None:
    """"OO 기준으로 다시 보기" 비차단형 전환 제안을 만든다(D-071).

    조사가 이미 출발점을 확정한 요청(`travel_origin`이 채워진 요청)에는
    만들지 않는다 — 되물을 이유가 없다. 이동시간 제약이 없는 요청도 만들지
    않는다 — 출발점 논의 자체가 의미 없다. 사용자 위치와 검색 기준점이 둘 다
    알려져 있고 실제로 다른 지점일 때만 제안한다 — 같은 지점이면 전환해도
    답이 똑같다.
    """

    if context is None or conditions is None:
        return None
    if conditions.max_travel_time is None:
        return None
    if conditions.travel_origin is not None:
        return None

    user_location = _usable(context.user_location)
    search_center = _usable(context.location)
    if user_location is None or search_center is None:
        return None
    if search_center.source == "device_gps":
        return None  # 부를 이름이 없다.
    if user_location.location == search_center.location:
        return None  # 같은 지점이면 전환할 의미가 없다.

    return TravelOriginToggle(
        alternative_origin=TravelOrigin.SEARCH_CENTER,
        alternative_origin_name=search_center.requested_query,
    )


__all__ = [
    "haversine_km",
    "resolve_ranking_origin",
    "resolve_search_center",
    "resolve_travel_origin_toggle",
    "resolve_user_to_target_km",
]
