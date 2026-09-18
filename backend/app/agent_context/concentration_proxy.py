"""집중률 대체 장소(proxy) 선택과 매핑 캐시.

역할: INFO 단일 장소 질의에서 대상 장소의 집중률 데이터가 없을 때, 집중률 매핑이
있는 장소 중 가장 가까운 곳을 대체 기준으로 고른다(D-036).
입력: 기준 좌표와 탐색 반경.
출력: StoredPlaceLocation(집중률 API가 쓰는 이름 포함) 또는 None.
호출 시점: ContextService.fetch_info_context()의 fallback 단계.

매핑에 있는 장소는 집중률 API에 데이터가 존재한다는 뜻이라, TourAPI 위치 검색으로
가까운 관광지를 고르던 기존 방식과 달리 "골랐는데 데이터가 없더라"가 생기지 않는다.
목록은 동기화 배치로만 바뀌므로 프로세스 단위로 한 번만 읽어 재사용한다.
"""

from __future__ import annotations

import asyncio

from app.domain.models import StoredPlaceLocation
from app.geo import haversine_km
from app.repositories.protocols import ConcentrationMappingRepository

# 매핑 목록은 프로세스 단위로 공유한다. Repository는 요청마다 새 httpx 클라이언트로
# 만들어지므로 인스턴스에 캐시를 두면 매 요청 다시 읽게 된다. 조회 결과만 모듈 수준에
# 두고 Repository는 캐시가 비었을 때만 쓴다(tour_category_registry의 lru_cache와 같은 결).
_cached_places: tuple[StoredPlaceLocation, ...] | None = None
_cache_lock = asyncio.Lock()


def clear_concentration_mapping_cache() -> None:
    """테스트에서 프로세스 캐시를 비운다."""
    global _cached_places
    _cached_places = None


class ConcentrationMappingCache:
    """매핑 목록을 프로세스 단위로 한 번만 읽어 재사용한다.

    동시 요청이 겹쳐도 조회가 한 번만 나가도록 Lock으로 묶는다. 동기화로 매핑이
    갱신되면 재시작 전까지 반영되지 않는다 — 매핑은 자주 바뀌지 않으므로 TTL 없이
    시작하고, 필요해지면 무효화 경로를 추가한다.
    """

    def __init__(self, repository: ConcentrationMappingRepository) -> None:
        self._repository = repository

    async def places(self) -> tuple[StoredPlaceLocation, ...]:
        global _cached_places
        if _cached_places is not None:
            return _cached_places
        async with _cache_lock:
            # Lock 대기 중 다른 호출이 채웠을 수 있다.
            if _cached_places is None:
                _cached_places = await self._repository.find_concentration_mapped_places()
        return _cached_places


def select_nearest_mapped_places(
    places: tuple[StoredPlaceLocation, ...],
    *,
    latitude: float,
    longitude: float,
    radius_km: float,
    limit: int,
) -> tuple[StoredPlaceLocation, ...]:
    """반경 안의 매핑 장소를 가까운 순으로 최대 limit개 돌려준다.

    한 곳만 쓰지 않는 이유: 매핑에 이름이 있어도 집중률 API 조회가 실패할 수 있다
    (표기 차이·API 갱신). 실측에서 매핑 100건 중 30건이 조회에 실패했고, 안국역은
    가장 가까운 "서울 운현궁"이 실패해 그대로 no_data가 됐다. 호출자가 순서대로
    시도해 첫 성공을 채택한다.

    거리만으로 정렬한다 — 인지도 반영은 별도 논의 대상이다(D-036 후속).
    """
    within_radius = [
        (haversine_km(latitude, longitude, place.latitude, place.longitude), place)
        for place in places
        if place.concentration_name
    ]
    ordered = sorted(
        (item for item in within_radius if item[0] <= radius_km), key=lambda item: item[0]
    )
    return tuple(place for _, place in ordered[:limit])


__all__ = [
    "ConcentrationMappingCache",
    "clear_concentration_mapping_cache",
    "haversine_km",
    "select_nearest_mapped_places",
]
