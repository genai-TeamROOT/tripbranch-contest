"""집중률 대체 장소 선택과 매핑 캐시 테스트."""

from __future__ import annotations

import pytest

from app.agent_context.concentration_proxy import (
    ConcentrationMappingCache,
    haversine_km,
    select_nearest_mapped_places,
)
from app.domain.models import StoredPlaceLocation

# 내자상회(서촌) 좌표. 실측에서 이 지점 기준 매핑 장소가 0.16~0.55km에 분포했다.
_NAEJA_LATITUDE = 37.5758502431
_NAEJA_LONGITUDE = 126.9705214845


def _place(
    title: str,
    *,
    latitude: float,
    longitude: float,
    concentration_name: str | None = None,
) -> StoredPlaceLocation:
    return StoredPlaceLocation(
        content_id=f"content-{title}",
        title=title,
        address=None,
        latitude=latitude,
        longitude=longitude,
        district_code="110",
        concentration_name=concentration_name,
    )


class _RecordingRepository:
    def __init__(self, places: tuple[StoredPlaceLocation, ...]) -> None:
        self._places = places
        self.calls = 0

    async def find_concentration_mapped_places(self) -> tuple[StoredPlaceLocation, ...]:
        self.calls += 1
        return self._places


def test_selects_nearest_place_within_radius() -> None:
    nearest = _place(
        "사직공원(서울)",
        latitude=37.5757,
        longitude=126.9687,
        concentration_name="사직공원(서울)",
    )
    farther = _place(
        "경복궁", latitude=37.5788, longitude=126.9770, concentration_name="경복궁"
    )

    selected = select_nearest_mapped_places(
        (farther, nearest),
        latitude=_NAEJA_LATITUDE,
        longitude=_NAEJA_LONGITUDE,
        radius_km=0.5,
        limit=3,
    )

    # 가까운 순으로 정렬된다. 반경 밖(경복궁 0.66km)은 빠진다.
    assert selected == (nearest,)


def test_returns_none_when_all_places_are_outside_radius() -> None:
    far_away = _place(
        "해운대", latitude=35.1587, longitude=129.1604, concentration_name="해운대"
    )

    selected = select_nearest_mapped_places(
        (far_away,),
        latitude=_NAEJA_LATITUDE,
        longitude=_NAEJA_LONGITUDE,
        radius_km=0.5,
        limit=3,
    )

    assert selected == ()


def test_skips_places_without_concentration_name() -> None:
    """매핑 이름이 없으면 집중률 조회에 쓸 수 없으므로 후보에서 뺀다."""
    unmapped = _place("무명장소", latitude=37.5759, longitude=126.9706)
    mapped = _place(
        "경복궁", latitude=37.5788, longitude=126.9770, concentration_name="경복궁"
    )

    selected = select_nearest_mapped_places(
        (unmapped, mapped),
        latitude=_NAEJA_LATITUDE,
        longitude=_NAEJA_LONGITUDE,
        radius_km=1.0,
        limit=3,
    )

    assert selected == (mapped,)


def test_haversine_matches_known_distance() -> None:
    """위도 1도는 약 111.19km다 — 프로젝트 데이터와 무관하게 공식 자체를 검증한다."""
    assert haversine_km(37.0, 127.0, 38.0, 127.0) == pytest.approx(111.19, abs=0.1)
    assert haversine_km(37.5, 127.0, 37.5, 127.0) == pytest.approx(0.0, abs=1e-9)


@pytest.mark.asyncio
async def test_cache_reads_repository_only_once_across_instances() -> None:
    """캐시는 프로세스 단위다 — 요청마다 새 인스턴스를 만들어도 재조회하지 않는다."""
    repository = _RecordingRepository(
        (_place("경복궁", latitude=37.5788, longitude=126.9770, concentration_name="경복궁"),)
    )

    first = await ConcentrationMappingCache(repository).places()
    second = await ConcentrationMappingCache(repository).places()

    assert first == second
    assert repository.calls == 1


def test_orders_candidates_by_distance_and_applies_limit() -> None:
    """가까운 순으로 최대 limit개를 돌려준다 — 호출자가 순서대로 시도한다."""
    near = _place("가까운곳", latitude=37.5759, longitude=126.9706, concentration_name="가까운곳")
    middle = _place("중간곳", latitude=37.5770, longitude=126.9720, concentration_name="중간곳")
    far = _place("먼곳", latitude=37.5790, longitude=126.9750, concentration_name="먼곳")

    selected = select_nearest_mapped_places(
        (far, near, middle),
        latitude=_NAEJA_LATITUDE,
        longitude=_NAEJA_LONGITUDE,
        radius_km=1.0,
        limit=2,
    )

    assert selected == (near, middle)
