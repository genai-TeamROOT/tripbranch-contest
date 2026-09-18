"""무장애 조건이 후보 수집을 실제로 좁히는지 확인한다.

**여기서 지키려는 것은 "조건이 조용히 사라지지 않는다"이다.** 무장애 필터는
후보를 8,007곳에서 949곳으로 줄이는 강한 조건이라, 배선이 어긋나면 사용자는
요구가 반영된 줄 알고 못 가는 곳을 받는다. 그런데 그 실패는 오류를 내지 않는다.

원문을 읽어 "있다/없다"를 가르는 판정 자체는 RPC 안에 있어 여기서 확인할 수 없다.
그쪽은 `test_barrier_free_search_rpc_smoke.py`가 실 DB로 확인한다.
"""

from __future__ import annotations

import pytest

from app.agent_context.schemas import UserConditions
from app.domain.models import (
    AccessibilityNeed,
    AccessibilityVerdict,
    PlaceCategoryFilter,
    PlaceDetails,
)
from app.providers.contracts import (
    ProviderResult,
    ProviderSource,
    provider_result,
)
from app.providers.stub import FakeBarrierFreePlaceSearchProvider
from app.schemas import PlaceCandidate
from app.tools.nearby_place_details import (
    NearbyPlaceDetailsQuery,
    NearbyPlaceDetailsTool,
    ToolStatus,
)

pytestmark = pytest.mark.asyncio


class _RecordingTourSearchProvider:
    """TourAPI 검색 경로가 불렸는지 기록한다."""

    def __init__(self) -> None:
        self.call_count = 0

    async def search_places(
        self,
        latitude: float,
        longitude: float,
        preferred_categories: list[str],
        search_radius_km: float,
        region_code: str | None = None,
        district_code: str | None = None,
        category_filter: PlaceCategoryFilter | None = None,
        limit: int = 10,
    ) -> ProviderResult[list[PlaceCandidate]]:
        self.call_count += 1
        return provider_result(
            [
                PlaceCandidate(
                    place_id="tour-1",
                    content_type_id="12",
                    name="TourAPI 후보",
                    category="attraction",
                    latitude=latitude,
                    longitude=longitude,
                    raw_source="tour_api",
                )
            ],
            source=ProviderSource.TOUR_API_PLACE,
        )


class _StubDetailsProvider:
    async def get_details(
        self, content_id: str, content_type_id: str
    ) -> ProviderResult[PlaceDetails]:
        return provider_result(
            PlaceDetails(
                content_id=content_id,
                content_type_id=content_type_id,
                title="상세",
                address=None,
                overview="상세정보",
                homepage=None,
                telephone=None,
                operating_hours="09:00~18:00",
                rest_date=None,
                raw_common={},
                raw_intro={},
                provider="stub",
            ),
            source=ProviderSource.TOUR_API_PLACE,
        )


def _tool(
    tour_provider: _RecordingTourSearchProvider,
    *,
    with_barrier_free: bool = True,
) -> NearbyPlaceDetailsTool:
    return NearbyPlaceDetailsTool(
        search_provider=tour_provider,
        details_provider=_StubDetailsProvider(),
        barrier_free_search_provider=(
            FakeBarrierFreePlaceSearchProvider() if with_barrier_free else None
        ),
    )


def _query(**overrides: object) -> NearbyPlaceDetailsQuery:
    return NearbyPlaceDetailsQuery(
        latitude=37.5796,
        longitude=126.9770,
        search_radius_km=2.0,
        limit=10,
        **overrides,  # type: ignore[arg-type]
    )


async def test_조건이_없으면_기존_TourAPI_경로를_그대로_쓴다() -> None:
    """무장애를 요구하지 않은 요청의 동작이 바뀌면 안 된다."""
    tour = _RecordingTourSearchProvider()
    result = await _tool(tour).execute(_query())

    assert tour.call_count == 1
    assert [place.candidate.place_id for place in result.places] == ["tour-1"]


async def test_조건이_있으면_TourAPI를_부르지_않는다() -> None:
    """후보 출처가 저장소로 바뀐다. 두 경로를 겹쳐 부르면 호출만 낭비한다."""
    tour = _RecordingTourSearchProvider()
    result = await _tool(tour).execute(
        _query(accessibility_needs=(AccessibilityNeed.WHEELCHAIR_ACCESS,))
    )

    assert tour.call_count == 0
    place_ids = {place.candidate.place_id for place in result.places}
    assert place_ids == {"fake-bf-cafe-1", "fake-bf-museum-1"}


async def test_요구한_편의가_없는_곳은_빠진다() -> None:
    """유아 시설만 있고 단차 정보가 없는 곳은 휠체어 요구에 남으면 안 된다."""
    result = await _tool(_RecordingTourSearchProvider()).execute(
        _query(accessibility_needs=(AccessibilityNeed.WHEELCHAIR_ACCESS,))
    )

    assert "fake-bf-nursery-1" not in {
        place.candidate.place_id for place in result.places
    }


async def test_유모차_요구는_단차와_유아시설을_모두_만족해야_남는다() -> None:
    """어휘를 나눈 이유를 못 박는다.

    유아 시설만 보고 고르면 수유실은 있는데 계단으로 올라가야 하는 곳이 섞인다.
    둘을 함께 요구했을 때 그런 곳이 빠지는지가 이 설계의 값어치다.
    """
    tool = _tool(_RecordingTourSearchProvider())

    infant_only = await tool.execute(
        _query(accessibility_needs=(AccessibilityNeed.INFANT_FACILITIES,))
    )
    both = await tool.execute(
        _query(
            accessibility_needs=(
                AccessibilityNeed.STROLLER_ACCESS,
                AccessibilityNeed.INFANT_FACILITIES,
            )
        )
    )

    infant_only_ids = {place.candidate.place_id for place in infant_only.places}
    both_ids = {place.candidate.place_id for place in both.places}

    # 유아 시설만 요구하면 단차를 모르는 곳도 남는다.
    assert "fake-bf-nursery-1" in infant_only_ids
    # 둘 다 요구하면 그 곳이 빠진다. 이것이 두 값으로 나눈 이유다.
    assert "fake-bf-nursery-1" not in both_ids
    assert both_ids == {"fake-bf-cafe-1"}


async def test_추천_대상이_아닌_유형은_편의를_다_갖춰도_빠진다() -> None:
    """숙박은 무장애 편의를 다 갖춰도 후보가 아니다(TourAPI 경로와 같은 규칙)."""
    result = await _tool(_RecordingTourSearchProvider()).execute(
        _query(accessibility_needs=(AccessibilityNeed.ACCESSIBLE_PARKING,))
    )

    assert "fake-bf-lodging-1" not in {
        place.candidate.place_id for place in result.places
    }


async def test_결과는_검색_중심에서_가까운_순이다() -> None:
    result = await _tool(_RecordingTourSearchProvider()).execute(
        _query(accessibility_needs=(AccessibilityNeed.WHEELCHAIR_ACCESS,))
    )

    assert [place.candidate.place_id for place in result.places] == [
        "fake-bf-cafe-1",
        "fake-bf-museum-1",
    ]


async def test_좁힐_수단이_없으면_넓은_결과_대신_unavailable로_답한다() -> None:
    """조건을 무시한 결과를 조용히 주지 않는다.

    무장애 provider 없이 조건이 들어오면 배선이 어긋난 것이다. 그대로 TourAPI
    결과를 주면 사용자는 요구가 반영된 줄 알고 못 가는 곳을 받고, 오류는 어디에도
    남지 않는다(D-042와 같은 이유).
    """
    tour = _RecordingTourSearchProvider()
    result = await _tool(tour, with_barrier_free=False).execute(
        _query(accessibility_needs=(AccessibilityNeed.WHEELCHAIR_ACCESS,))
    )

    assert result.status is ToolStatus.UNAVAILABLE
    assert result.places == ()
    assert tour.call_count == 0


async def test_빈_조건으로_저장소_검색을_부르면_막는다() -> None:
    """빈 목록이 조건 없는 전체 검색으로 바뀌면 안 된다."""
    with pytest.raises(ValueError):
        await FakeBarrierFreePlaceSearchProvider().search_places_with_accessibility(
            latitude=37.5796,
            longitude=126.9770,
            search_radius_km=2.0,
            needs=(),
            limit=10,
        )


async def test_계약은_모르는_어휘를_거부하지_않는다() -> None:
    """A가 어휘를 늘려도 요청 전체가 깨지면 안 된다(weather_intent와 같은 이유).

    걸러내는 것은 C의 몫이고, 걸러냈다는 사실은 경고로 남는다.
    """
    conditions = UserConditions(accessibility_needs=["wheelchair_access", "hearing_loop"])

    assert conditions.accessibility_needs == ["wheelchair_access", "hearing_loop"]


async def test_서비스가_모르는_어휘를_버리고_경고를_남긴다() -> None:
    """C가 아는 값만 남기되, 버렸다는 사실이 결과에 남아야 한다."""
    from app.agent_context.service import _resolve_accessibility_needs

    needs, has_unknown = _resolve_accessibility_needs(
        ["wheelchair_access", "hearing_loop", "  ", "wheelchair_access"]
    )

    assert needs == (AccessibilityNeed.WHEELCHAIR_ACCESS,)
    assert has_unknown is True


async def test_아는_어휘만_오면_경고가_붙지_않는다() -> None:
    from app.agent_context.service import _resolve_accessibility_needs

    needs, has_unknown = _resolve_accessibility_needs(
        ["stroller_access", "infant_facilities"]
    )

    assert needs == (
        AccessibilityNeed.STROLLER_ACCESS,
        AccessibilityNeed.INFANT_FACILITIES,
    )
    assert has_unknown is False


async def test_모르는_어휘만_오면_조건_없는_검색으로_바뀌지_않는다() -> None:
    """전부 모르는 값이면 좁힐 조건이 하나도 없다.

    그때 저장소 검색을 부르면 조건 없는 전체 반경 검색이 된다. 빈 목록을 돌려주어
    호출 자체가 일어나지 않게 하고, 경고로 그 사실을 남긴다.
    """
    from app.agent_context.service import _resolve_accessibility_needs

    needs, has_unknown = _resolve_accessibility_needs(["hearing_loop"])

    assert needs == ()
    assert has_unknown is True


async def test_노인_동반에_쓰는_어휘가_단차와_따로_걸린다() -> None:
    """의자식 테이블·저상버스·휠체어 대여는 휠체어 접근과 다른 자료다.

    단차 정보가 없는 곳에도 이 값들이 있을 수 있고 그 반대도 마찬가지다. 한 묶음으로
    합쳐 두면 오래 걷기 힘든 동행에게 쓸모 있는 곳이 휠체어 조건에 걸려 사라진다.
    """
    tool = _tool(_RecordingTourSearchProvider())

    seating = await tool.execute(
        _query(accessibility_needs=(AccessibilityNeed.SEATING_AVAILABLE,))
    )
    wheelchair = await tool.execute(
        _query(accessibility_needs=(AccessibilityNeed.WHEELCHAIR_ACCESS,))
    )

    seating_ids = {place.candidate.place_id for place in seating.places}
    wheelchair_ids = {place.candidate.place_id for place in wheelchair.places}

    # 경로당은 의자식 테이블은 있지만 단차 정보가 없다.
    assert "fake-bf-senior-1" in seating_ids
    assert "fake-bf-senior-1" not in wheelchair_ids


async def test_휠체어_대여는_휠체어_접근과_다른_조건이다() -> None:
    """TourAPI의 `wheelchair` 응답 키가 대여를 뜻해 이름이 뒤집히기 쉽다."""
    tool = _tool(_RecordingTourSearchProvider())

    rental = await tool.execute(
        _query(accessibility_needs=(AccessibilityNeed.WHEELCHAIR_RENTAL,))
    )
    access = await tool.execute(
        _query(accessibility_needs=(AccessibilityNeed.WHEELCHAIR_ACCESS,))
    )

    assert {p.candidate.place_id for p in rental.places} == {"fake-bf-senior-1"}
    assert "fake-bf-senior-1" not in {p.candidate.place_id for p in access.places}


async def test_같은_장소가_휠체어와_유모차에_다른_판정을_준다() -> None:
    """좁은 통로는 휠체어만 막고 유모차는 지나간다. 두 어휘를 나눈 이유다.

    후보 목록은 아직 같을 수 있다 — `partial`은 후보로 남기기 때문이다. 갈리는
    것은 **판정**이고, 그게 답변의 안내 문구를 가른다. 후보 수만 견주면 이 차이가
    보이지 않는다.
    """
    tool = _tool(_RecordingTourSearchProvider())

    wheelchair = await tool.execute(
        _query(accessibility_needs=(AccessibilityNeed.WHEELCHAIR_ACCESS,))
    )
    stroller = await tool.execute(
        _query(accessibility_needs=(AccessibilityNeed.STROLLER_ACCESS,))
    )

    def verdict_of(result: object, place_id: str, need: AccessibilityNeed) -> object:
        for place in result.places:  # type: ignore[attr-defined]
            if place.candidate.place_id == place_id:
                return (place.accessibility_verdicts or {}).get(need)
        raise AssertionError(f"{place_id}가 후보에 없습니다.")

    cafe = "fake-bf-cafe-1"
    assert (
        verdict_of(wheelchair, cafe, AccessibilityNeed.WHEELCHAIR_ACCESS)
        is AccessibilityVerdict.PARTIAL
    )
    assert (
        verdict_of(stroller, cafe, AccessibilityNeed.STROLLER_ACCESS)
        is AccessibilityVerdict.POSSIBLE
    )


async def test_부분_판정도_후보로_남는다() -> None:
    """들어갈 수는 있는데 못 가는 구역이 남는 곳을 추천에서 빼지 않는다.

    빼면 팔각정 하나 못 간다고 장소가 통째로 사라진다. 대신 판정을 함께 올려
    답변이 그 사실을 말하게 한다.
    """
    tool = _tool(_RecordingTourSearchProvider())

    result = await tool.execute(
        _query(accessibility_needs=(AccessibilityNeed.WHEELCHAIR_ACCESS,))
    )

    partial = [
        place
        for place in result.places
        if (place.accessibility_verdicts or {}).get(AccessibilityNeed.WHEELCHAIR_ACCESS)
        is AccessibilityVerdict.PARTIAL
    ]
    assert partial, "부분 판정이 후보에서 빠졌습니다."


async def test_요구하지_않은_어휘의_판정은_올리지_않는다() -> None:
    """묻지 않은 편의를 답변이 말하게 두지 않는다.

    무장애 박물관은 시각 안내가 `partial`이지만, 휠체어만 물었으면 그 값은
    올라오면 안 된다.
    """
    tool = _tool(_RecordingTourSearchProvider())

    result = await tool.execute(
        _query(accessibility_needs=(AccessibilityNeed.WHEELCHAIR_ACCESS,))
    )

    for place in result.places:
        assert set(place.accessibility_verdicts or {}) <= {
            AccessibilityNeed.WHEELCHAIR_ACCESS
        }


async def test_무장애_조건이_없으면_판정도_없다() -> None:
    """TourAPI 경로는 판정을 만들지 않는다. 빈 값이 아니라 아예 None이다."""
    tool = _tool(_RecordingTourSearchProvider())

    result = await tool.execute(_query())

    assert result.places
    assert all(place.accessibility_verdicts is None for place in result.places)

