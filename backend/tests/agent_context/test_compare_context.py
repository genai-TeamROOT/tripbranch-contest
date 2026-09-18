"""C의 COMPARE 비교 컨텍스트 조립을 검증한다.

핵심은 두 가지다. (1) C는 place_id를 장소명으로 해석만 하고 우열을 판정하지 않는다.
(2) B가 준 추천 시점 스냅샷은 재계산 없이 그대로 통과한다 — 사용자가 카드에서 본
값과 비교 답변의 값이 어긋나면 안 된다(D-050).
"""

from collections.abc import Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.agent_context.compare_schemas import (
    CompareCandidate,
    CompareContextRequest,
)
from app.agent_context.service import ContextService, ContextTools
from app.domain.models import StoredPlaceDetail
from app.errors import ProviderUnavailableError
from app.providers.concentration import FakeConcentrationProvider
from app.providers.geocoding import FakeGeocodingProvider
from app.providers.holiday import FakeHolidayProvider
from app.providers.stub import FakePlaceProvider, FakeWeatherProvider
from app.repositories.fake_places import (
    FakePlaceDetailsRepository,
    FakePlaceLocationRepository,
)
from app.schemas import CompareCriteria
from app.tools.concentration import GetConcentrationTool
from app.tools.holiday import GetHolidaysTool
from app.tools.nearby_place_details import NearbyPlaceDetailsTool
from app.tools.recommendation_cards import RecommendationCardTool
from app.tools.resolve_location import ResolveLocationTool
from app.tools.weather_forecast import GetWeatherForecastTool

KST = ZoneInfo("Asia/Seoul")


class _UnavailableDetailsRepository:
    """저장소 장애를 재현한다."""

    async def get_active_place_details(
        self, content_ids: Sequence[str]
    ) -> dict[str, StoredPlaceDetail]:
        raise ProviderUnavailableError("places 저장소 장애")


def _service(details_repository: object | None = None) -> ContextService:
    place_provider = FakePlaceProvider()
    return ContextService(
        ContextTools(
            location=ResolveLocationTool(
                FakeGeocodingProvider(),
                place_repository=FakePlaceLocationRepository(),
            ),
            places=NearbyPlaceDetailsTool(place_provider, place_provider),
            weather=GetWeatherForecastTool(FakeWeatherProvider()),
            holidays=GetHolidaysTool(FakeHolidayProvider()),
            concentration=GetConcentrationTool(FakeConcentrationProvider()),
            cards=RecommendationCardTool(
                details_repository or FakePlaceDetailsRepository()  # type: ignore[arg-type]
            ),
        ),
        candidate_limit=10,
        clock=lambda: datetime.now(KST),
    )


def _request(
    *,
    criteria: CompareCriteria = CompareCriteria.OVERALL,
    candidates: list[CompareCandidate] | None = None,
) -> CompareContextRequest:
    return CompareContextRequest(
        request_id="request-1",
        criteria=criteria,
        candidates=candidates
        or [
            CompareCandidate(
                place_id="fake-museum-1",
                rank=1,
                distance_km=0.4,
                remaining_minutes=180,
                environment_type="indoor",
            ),
            CompareCandidate(
                place_id="fake-cafe-1",
                rank=2,
                distance_km=0.8,
                remaining_minutes=90,
                environment_type="indoor",
            ),
        ],
    )


@pytest.mark.asyncio
async def test_resolves_place_names_and_passes_snapshot_through() -> None:
    response = await _service().fetch_compare_context(_request())

    assert response.status == "success"
    assert response.missing_place_ids == []
    assert [(item.place_id, item.place_name) for item in response.items] == [
        ("fake-museum-1", "테스트 박물관"),
        ("fake-cafe-1", "테스트 카페"),
    ]
    # 스냅샷은 재계산하지 않고 그대로 실린다.
    assert [item.distance_km for item in response.items] == [0.4, 0.8]
    assert [item.remaining_minutes for item in response.items] == [180, 90]


@pytest.mark.asyncio
async def test_passes_coordinates_for_travel_time_without_judging_them() -> None:
    """TRAVEL_TIME 전용 — C는 카드 조회 시점의 좌표를 그대로 실어 보내기만 한다.

    실측 호출·우열 판정은 A(agent_runtime.py)의 몫이라 여기서는 좌표 존재
    여부와 값만 확인한다(2026-08-21, TP-105/106 실측 연결).
    """
    response = await _service().fetch_compare_context(
        _request(criteria=CompareCriteria.TRAVEL_TIME)
    )

    assert response.status == "success"
    assert [(item.latitude, item.longitude) for item in response.items] == [
        (37.5735, 126.9788),
        (37.5720, 126.9850),
    ]


@pytest.mark.asyncio
async def test_items_are_ordered_by_rank_not_request_order() -> None:
    response = await _service().fetch_compare_context(
        _request(
            candidates=[
                CompareCandidate(place_id="fake-cafe-1", rank=2, distance_km=0.8),
                CompareCandidate(place_id="fake-museum-1", rank=1, distance_km=0.4),
            ]
        )
    )

    assert [item.rank for item in response.items] == [1, 2]
    assert [item.place_id for item in response.items] == ["fake-museum-1", "fake-cafe-1"]


@pytest.mark.asyncio
async def test_unknown_place_is_dropped_and_reported_as_partial() -> None:
    """이름을 못 찾은 후보는 빼고 나머지로 비교하되, 빠졌다는 사실을 남긴다."""

    response = await _service().fetch_compare_context(
        _request(
            candidates=[
                CompareCandidate(place_id="fake-museum-1", rank=1, distance_km=0.4),
                CompareCandidate(place_id="fake-cafe-1", rank=2, distance_km=0.8),
                CompareCandidate(place_id="not-in-store", rank=3, distance_km=1.2),
            ]
        )
    )

    assert response.status == "partial"
    assert response.missing_place_ids == ["not-in-store"]
    assert [item.place_id for item in response.items] == ["fake-museum-1", "fake-cafe-1"]
    # place_id를 이름 자리에 넣지 않는다 — 사용자에게 내부 ID가 보이면 안 된다.
    assert all(item.place_name != item.place_id for item in response.items)


@pytest.mark.asyncio
async def test_fewer_than_two_resolved_places_is_no_data() -> None:
    """한 곳만 남으면 비교가 성립하지 않는다."""

    response = await _service().fetch_compare_context(
        _request(
            candidates=[
                CompareCandidate(place_id="fake-museum-1", rank=1, distance_km=0.4),
                CompareCandidate(place_id="not-in-store", rank=2, distance_km=0.8),
            ]
        )
    )

    assert response.status == "no_data"
    assert response.items == []
    assert response.missing_place_ids == ["not-in-store"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("criteria", "field"),
    [
        (CompareCriteria.TIME, "remaining_minutes"),
    ],
)
async def test_missing_snapshot_for_criteria_is_no_data(
    criteria: CompareCriteria, field: str
) -> None:
    """기준 값이 전원 비어 있으면 비교할 사실이 없다 — LLM에 빈 값을 넘기지 않는다.

    TRAVEL_TIME은 이 파라미터라이즈 대상이 아니다 — 그 판정 필드(latitude)는
    candidate 요청이 아니라 카드 조회 응답에서 채워지므로 이 픽스처 방식(값
    없는 candidate)으로는 재현할 수 없다. 별도 테스트
    (test_passes_coordinates_for_travel_time_without_judging_them)로 커버한다.
    """

    response = await _service().fetch_compare_context(
        _request(
            criteria=criteria,
            candidates=[
                CompareCandidate(place_id="fake-museum-1", rank=1),
                CompareCandidate(place_id="fake-cafe-1", rank=2),
            ],
        )
    )

    assert response.status == "no_data"


@pytest.mark.asyncio
async def test_overall_does_not_require_any_single_snapshot_field() -> None:
    """overall은 세 값을 함께 설명하는 방식이라 특정 필드를 요구하지 않는다."""

    response = await _service().fetch_compare_context(
        _request(
            criteria=CompareCriteria.OVERALL,
            candidates=[
                CompareCandidate(place_id="fake-museum-1", rank=1, environment_type="indoor"),
                CompareCandidate(place_id="fake-cafe-1", rank=2, environment_type="indoor"),
            ],
        )
    )

    assert response.status == "success"
    assert [item.environment_type for item in response.items] == ["indoor", "indoor"]


@pytest.mark.asyncio
async def test_partial_snapshot_is_kept_when_some_places_have_the_value() -> None:
    """일부만 값이 있으면 비교는 성립한다 — 있는 값만 설명하면 된다."""

    response = await _service().fetch_compare_context(
        _request(
            criteria=CompareCriteria.TIME,
            candidates=[
                CompareCandidate(place_id="fake-museum-1", rank=1, remaining_minutes=180),
                CompareCandidate(place_id="fake-cafe-1", rank=2),
            ],
        )
    )

    assert response.status == "success"
    assert [item.remaining_minutes for item in response.items] == [180, None]


@pytest.mark.asyncio
async def test_repository_failure_surfaces_as_unavailable() -> None:
    response = await _service(_UnavailableDetailsRepository()).fetch_compare_context(
        _request()
    )

    assert response.status == "unavailable"
    assert response.items == []
    assert response.error is not None


@pytest.mark.asyncio
async def test_missing_card_tool_is_unavailable_not_silent_success() -> None:
    """Tool이 주입되지 않은 구성에서 조용히 빈 비교를 내지 않는다."""

    place_provider = FakePlaceProvider()
    service = ContextService(
        ContextTools(
            location=ResolveLocationTool(
                FakeGeocodingProvider(),
                place_repository=FakePlaceLocationRepository(),
            ),
            places=NearbyPlaceDetailsTool(place_provider, place_provider),
            weather=GetWeatherForecastTool(FakeWeatherProvider()),
            holidays=GetHolidaysTool(FakeHolidayProvider()),
        ),
        candidate_limit=10,
    )

    response = await service.fetch_compare_context(_request())

    assert response.status == "unavailable"
    assert response.error is not None
    assert response.error.code == "place_lookup_not_configured"


# --- Fake ToolProvider가 실제 C와 같은 규칙을 따르는지 -------------------------
#
# A의 Runtime 테스트는 FakeToolProvider를 쓴다. 판정 규칙이 갈리면 A가 실제
# 경로에서는 나지 않는 조합을 통과시키게 된다("조용한 fake").


def _fake_request(
    *,
    criteria: CompareCriteria = CompareCriteria.OVERALL,
    candidates: list[CompareCandidate],
) -> CompareContextRequest:
    return CompareContextRequest(
        request_id="request-fake", criteria=criteria, candidates=candidates
    )


@pytest.mark.asyncio
async def test_fake_tool_provider_resolves_names_and_keeps_snapshot() -> None:
    from app.services.runtime.stubs import FakeToolProvider

    response = await FakeToolProvider().fetch_compare_context(
        _fake_request(
            candidates=[
                CompareCandidate(place_id="fake-place-2", rank=2, distance_km=0.9),
                CompareCandidate(place_id="fake-place-1", rank=1, distance_km=0.3),
            ]
        )
    )

    assert response.status == "success"
    assert [item.rank for item in response.items] == [1, 2]
    assert [item.place_name for item in response.items] == ["경복궁", "창덕궁"]
    assert [item.distance_km for item in response.items] == [0.3, 0.9]


@pytest.mark.asyncio
async def test_fake_tool_provider_reports_partial_like_real_service() -> None:
    from app.services.runtime.stubs import FakeToolProvider

    response = await FakeToolProvider().fetch_compare_context(
        _fake_request(
            candidates=[
                CompareCandidate(place_id="fake-place-1", rank=1, distance_km=0.3),
                CompareCandidate(place_id="fake-place-2", rank=2, distance_km=0.9),
                CompareCandidate(place_id="unknown-place", rank=3, distance_km=1.5),
            ]
        )
    )

    assert response.status == "partial"
    assert response.missing_place_ids == ["unknown-place"]
    assert len(response.items) == 2


@pytest.mark.asyncio
async def test_fake_tool_provider_no_data_rules_match_real_service() -> None:
    from app.services.runtime.stubs import FakeToolProvider

    provider = FakeToolProvider()

    too_few = await provider.fetch_compare_context(
        _fake_request(
            candidates=[
                CompareCandidate(place_id="fake-place-1", rank=1, distance_km=0.3),
                CompareCandidate(place_id="unknown-place", rank=2, distance_km=0.9),
            ]
        )
    )
    assert too_few.status == "no_data"

    no_facts = await provider.fetch_compare_context(
        _fake_request(
            criteria=CompareCriteria.TIME,
            candidates=[
                CompareCandidate(place_id="fake-place-1", rank=1),
                CompareCandidate(place_id="fake-place-2", rank=2),
            ],
        )
    )
    assert no_facts.status == "no_data"
