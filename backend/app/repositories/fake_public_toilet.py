"""공중화장실 위치 저장소 테스트 더블."""

from __future__ import annotations

from collections.abc import Sequence

from app.domain.models import PublicToilet
from app.geo import haversine_km


class FakePublicToiletRepository:
    """실제 저장소의 두 가지 성질을 일부러 그대로 흉내 낸다.

    1. **거리 정렬을 하지 않는다.** 실제 질의는 PostgREST 바운딩 박스라 정렬이
       없다(PostGIS를 쓰지 않는다). 여기서 거리로 정렬해 주면 Tool이 정렬을 안
       해도 테스트가 통과해, 정렬 책임이 어디 있는지 흐려진다.
    2. **박스는 원보다 넓다.** 반지름을 정확히 적용하지 않고 넉넉히 줘서, 반지름
       밖을 걸러내는 Tool 쪽 로직이 실제로 검증되게 한다.

    이 두 성질을 흉내 내지 않았을 때 실제로 놓친 버그가 있다 — 상한에 걸려 박스
    안 일부만 오면 가장 가까운 곳이 빠질 수 있는데, 더블이 거리순으로 주고 있어
    테스트가 이를 잡지 못했다.
    """

    def __init__(self, toilets: Sequence[PublicToilet] = ()) -> None:
        self._toilets = {toilet.toilet_id: toilet for toilet in toilets}

    async def find_near(
        self, latitude: float, longitude: float, *, radius_km: float, limit: int
    ) -> tuple[PublicToilet, ...]:
        within = [
            toilet
            for toilet in self._toilets.values()
            if haversine_km(latitude, longitude, toilet.latitude, toilet.longitude)
            <= radius_km * 1.5
        ]
        # 삽입 순서 그대로 상한까지 자른다 — 실제 질의의 "임의 순서 + 상한"과 같다.
        return tuple(within[:limit])

    async def upsert_toilets(self, toilets: Sequence[PublicToilet]) -> None:
        self._toilets.update({toilet.toilet_id: toilet for toilet in toilets})


__all__ = ["FakePublicToiletRepository"]
