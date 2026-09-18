"""근처 공중화장실 조회 Tool.

다른 Tool과 달리 외부 API가 아니라 적재된 저장소를 감싼다 — 서울시 API가 구·좌표
필터를 지원하지 않아 요청 때마다 전량을 받을 수 없기 때문이다(providers/
public_toilet.py 참고). 거리 정렬과 "지금 열림" 판정까지 여기서 끝내고, 위층은
정렬된 결과만 받는다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from app.domain.models import PublicToilet
from app.errors import AppError
from app.geo import haversine_km
from app.public_toilet_hours import OpenHours, is_open_at, parse_open_hours
from app.repositories.protocols import PublicToiletRepository
from app.tools.contracts import ToolError, ToolStatus

logger = logging.getLogger(__name__)

# 바운딩 박스에서 가져올 상한.
#
# **박스 안 전량을 받아야 한다.** 저장소 질의에는 거리 정렬이 없어(PostGIS를 쓰지
# 않는다) 상한에 걸리면 임의의 일부만 오고, 그 안에 가장 가까운 곳이 없을 수 있다.
# 실제로 60으로 뒀을 때 인사동에서 55m 24시간 화장실이 빠지고 230m가 1순위로
# 나왔다(박스 안 114곳 중 임의의 60곳만 와서 그렇다).
#
# 1km 반경 박스 안 화장실 수를 실측 4,447건 전부로 재보면 중앙값 39곳 · 95% 102곳 ·
# 최대 126곳(왕십리 일대)이다. 자료가 늘 것을 감안해 최대의 약 4배로 둔다.
_FETCH_LIMIT = 500


@dataclass(frozen=True)
class PublicToiletQuery:
    latitude: float
    longitude: float
    radius_km: float = 1.0
    # 조회 시각. "지금 열려 있나"를 판정하는 기준이라 호출부가 주입한다.
    now: datetime | None = None


@dataclass(frozen=True)
class NearbyToilet:
    """거리와 개방 여부까지 판정한 화장실 한 곳."""

    toilet: PublicToilet
    distance_km: float
    hours: OpenHours
    # 지금 열려 있는지. 개방시간을 시각으로 읽지 못한 곳은 None이다 —
    # 열림/닫힘 둘 중 하나로 단정하면 급한 사용자를 닫힌 곳으로 보내게 된다.
    open_now: bool | None


@dataclass(frozen=True)
class PublicToiletToolResult:
    status: ToolStatus
    toilets: tuple[NearbyToilet, ...]
    error: ToolError | None
    provider_metadata: tuple[object, ...] = ()


class GetPublicToiletTool:
    def __init__(self, repository: PublicToiletRepository) -> None:
        self._repository = repository

    async def execute(self, query: PublicToiletQuery) -> PublicToiletToolResult:
        try:
            rows = await self._repository.find_near(
                query.latitude,
                query.longitude,
                radius_km=query.radius_km,
                limit=_FETCH_LIMIT,
            )
        except AppError as exc:
            return PublicToiletToolResult(
                status=ToolStatus.UNAVAILABLE,
                toilets=(),
                error=ToolError(
                    code="unavailable",
                    message="근처 공중화장실 정보를 가져오지 못했습니다.",
                    cause="timeout" if exc.code == "provider_timeout" else "upstream_error",
                    retryable=exc.retryable,
                ),
            )

        if len(rows) >= _FETCH_LIMIT:
            # 상한에 닿았다는 것은 박스 안을 다 못 받았을 수 있다는 뜻이고, 저장소
            # 질의에 거리 정렬이 없어 가장 가까운 곳이 빠졌을 수 있다. 답을 막지는
            # 않되(있는 것 중 최선은 여전히 유용하다) 상한을 올릴 신호를 남긴다.
            logger.warning(
                "공중화장실 조회가 상한 %d건에 닿았습니다 — 가장 가까운 곳이 빠졌을 수 "
                "있습니다(좌표 %.5f,%.5f 반경 %.1fkm).",
                _FETCH_LIMIT,
                query.latitude,
                query.longitude,
                query.radius_km,
            )

        nearby = _rank_toilets(rows, query)
        return PublicToiletToolResult(
            status=ToolStatus.SUCCESS if nearby else ToolStatus.NO_DATA,
            toilets=nearby,
            error=None,
        )


def _rank_toilets(
    rows: tuple[PublicToilet, ...], query: PublicToiletQuery
) -> tuple[NearbyToilet, ...]:
    """반지름 안으로 걸러 "지금 열린 곳 → 가까운 곳" 순으로 정렬한다.

    급해서 묻는 질문이라 거리보다 "지금 들어갈 수 있는가"가 먼저다. 20m 더 가까운
    닫힌 화장실을 앞세우면 답이 쓸모없어진다. 개방 여부를 모르는 곳은 열린 곳
    뒤, 닫힌 곳 앞에 둔다 — 가능성은 있으니까.
    """

    weekday, minute_of_day = _now_parts(query.now)
    candidates: list[NearbyToilet] = []
    for toilet in rows:
        distance = haversine_km(
            query.latitude, query.longitude, toilet.latitude, toilet.longitude
        )
        if distance > query.radius_km:
            continue
        hours = parse_open_hours(toilet.open_hours_raw)
        candidates.append(
            NearbyToilet(
                toilet=toilet,
                distance_km=distance,
                hours=hours,
                open_now=is_open_at(hours, weekday, minute_of_day),
            )
        )

    candidates.sort(key=lambda item: (_open_rank(item.open_now), item.distance_km))
    return tuple(candidates)


def _open_rank(open_now: bool | None) -> int:
    if open_now is True:
        return 0
    if open_now is None:
        return 1
    return 2


def _now_parts(now: datetime | None) -> tuple[int, int]:
    moment = now if now is not None else datetime.now()
    return moment.weekday(), moment.hour * 60 + moment.minute


__all__ = [
    "GetPublicToiletTool",
    "NearbyToilet",
    "PublicToiletQuery",
    "PublicToiletToolResult",
]
