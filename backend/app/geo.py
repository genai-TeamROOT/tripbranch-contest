"""좌표 계산 공용 함수.

역할: 계층에 상관없이 쓰는 거리 계산을 한곳에 둔다. tools가 agent_context를 import하면
순환 참조가 되므로(agent_context가 tools를 쓴다) 양쪽 아래에 둘 모듈이 필요하다.
입력·출력: 위·경도와 거리(km).
"""

from __future__ import annotations

import math

from app.place_search_policy import EARTH_RADIUS_KM


def haversine_km(
    latitude: float, longitude: float, other_latitude: float, other_longitude: float
) -> float:
    """두 좌표 사이의 대권 거리(km)."""
    lat1, lon1, lat2, lon2 = map(
        math.radians, (latitude, longitude, other_latitude, other_longitude)
    )
    sin_lat = math.sin((lat2 - lat1) / 2) ** 2
    sin_lon = math.sin((lon2 - lon1) / 2) ** 2
    inner = sin_lat + math.cos(lat1) * math.cos(lat2) * sin_lon
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(inner))


__all__ = ["haversine_km"]
