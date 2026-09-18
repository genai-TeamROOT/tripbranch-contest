"""app.schedule.planner.plan_schedule() 회귀 테스트.

계약 문서: docs/design/int-07-schedule.md 6절(모듈 설계), 6.2.1절(basis_note),
9절("estimated_arrival 기준 시각" 미결 사항 — visit_datetime 없으면 현재 시각
fallback으로 해소, "D 후보 3개 미만" 미결 사항 — SCHEDULE-07로 해소).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.config import Settings
from app.domain.schedule_travel import ScheduleTravelCandidate
from app.domain.travel_route import (
    GeoCoordinate,
    RouteDestination,
    RouteSource,
    RouteStatus,
    TravelMode,
    TravelRoute,
    TravelRouteBatch,
)
from app.errors import AppError
from app.providers.contracts import ProviderSource, ProviderStatus, provider_result
from app.schedule.associations import CoVisitedHint
from app.schedule.budget import SCHEDULE_TIME_TOLERANCE_MIN, derive_item_range
from app.schedule.planner import _round_up_start, plan_partial_schedule, plan_schedule
from app.schedule.schemas import (
    ScheduleLLMItem,
    ScheduleLLMPlan,
    SchedulePartialFillRequest,
    SchedulePartialLLMPlan,
    SchedulePlanningRequest,
)
from app.schemas import (
    RecommendationItem,
    ScheduleBudgetStatus,
    ScheduleItem,
    UserConditions,
)
from app.tools.travel_route import TravelRouteProviders, TravelRouteTool

_KST = ZoneInfo("Asia/Seoul")


def _candidate(
    place_id: str,
    *,
    operating_hours_display: str | None = None,
    image_url: str | None = None,
    image_url_fallback: str | None = None,
    category: str = "attraction",
) -> RecommendationItem:
    return RecommendationItem(
        place_id=place_id,
        name=f"장소 {place_id}",
        category=category,
        distance_km=0.3,
        remaining_minutes=120,
        operating_hours_display=operating_hours_display,
        image_url=image_url,
        image_url_fallback=image_url_fallback,
        environment_type="indoor",
        recommendation_reason="테스트용 고정 후보입니다.",
        explanations=[],
        warnings=[],
        score=0.5,
        feature_scores={},
        weights_used={},
    )


class _RecordingLLM:
    """generate_schedule_plan()에 실제로 어떤 request가 넘어오는지 기록하는 더블."""

    def __init__(self, plan: ScheduleLLMPlan) -> None:
        self._plan = plan
        self.received_request: SchedulePlanningRequest | None = None
        self.call_count = 0

    async def judge_travel_modes(self, segments, context):
        """구간 이동수단 판정(TP-227). 규칙과 같은 답을 내 이 이중체를 쓰는 테스트가
        판정 도입 전과 같은 결과를 보게 한다.

        **없으면 안 된다.** 이 메서드가 빠지면 판정 경로가 AttributeError로 끊기고,
        예전에는 그것이 규칙 폴백으로 삼켜져 판정이 한 번도 안 도는데 테스트가
        통과했다(TP-227에서 6건 발견).
        """

        from app.providers.contracts import ProviderSource, provider_result

        del context
        return provider_result(
            tuple(
                "transit" if segment.walk_minutes > 20.0 else "walking"
                for segment in segments
            ),
            source=ProviderSource.FAKE_LLM,
        )

    async def generate_schedule_plan(self, request: SchedulePlanningRequest):
        self.received_request = request
        self.call_count += 1
        return provider_result(self._plan, source=ProviderSource.FAKE_LLM)


def _sample_item(
    place_id: str, order: int, *, estimated_duration_min: int = 60
) -> ScheduleLLMItem:
    """LLM이 돌려주는 항목. 시각이 없다(TP-215)."""

    return ScheduleLLMItem(
        order=order,
        place_id=place_id,
        place_name=f"장소 {place_id}",
        estimated_duration_min=estimated_duration_min,
        reason="테스트 이유",
    )


def _sample_plan() -> ScheduleLLMPlan:
    """ScheduleLLMPlan.items는 min_length=3 제약이 있어(SCHEDULE-07) 3개로 채운다."""
    return ScheduleLLMPlan(
        items=[
            _sample_item("place-1", 1),
            _sample_item("place-2", 2),
            _sample_item("place-3", 3),
        ],
        route_summary="테스트 동선 요약",
    )


def _three_candidates() -> list[RecommendationItem]:
    return [_candidate("place-1"), _candidate("place-2"), _candidate("place-3")]


@pytest.mark.asyncio
async def test_plan_schedule_fills_basis_note_from_visit_datetime() -> None:
    """basis_note는 LLM이 만들지 않고 planner가 visit_datetime으로 결정적으로 채운다."""
    llm = _RecordingLLM(_sample_plan())
    request = SchedulePlanningRequest(
        candidates=_three_candidates(),
        conditions=UserConditions(),
        visit_datetime=datetime(2026, 8, 7, 15, 30, tzinfo=_KST),
        pairwise_distances_km={},
    )

    result = await plan_schedule(request, llm)

    assert result.basis_note == (
        "15:30 기준으로 짠 일정이에요. "
        "실제로 가시는 시간에는 운영시간이나 날씨가 달라질 수 있어요."
    )
    assert [item.place_id for item in result.items] == [
        item.place_id for item in _sample_plan().items
    ]
    # 체류 60분 x 3 + 폴백 이동 15분 x 2 (TP-215 — LLM이 준 값이 아니라 계산값)
    assert result.total_duration_min == 210
    assert result.route_summary == "테스트 동선 요약"


@pytest.mark.asyncio
async def test_plan_schedule_measures_elapsed_ms() -> None:
    """RecommendationResponse.elapsed_ms(recommendation_pipeline.py)와 같은 패턴으로
    plan_schedule() 진입부터 결과 조립까지의 처리시간을 잰다 — 개발자 화면이
    SCHEDULE도 RECOMMEND처럼 "서버 소요"를 보여줄 수 있게 한다."""
    llm = _RecordingLLM(_sample_plan())
    request = SchedulePlanningRequest(
        candidates=_three_candidates(),
        conditions=UserConditions(),
        visit_datetime=datetime(2026, 8, 7, 15, 30, tzinfo=_KST),
        pairwise_distances_km={},
    )
    fake_ticks = iter([0.0, 0.25])

    result = await plan_schedule(request, llm, timer=lambda: next(fake_ticks))

    assert result.elapsed_ms == 250.0


@pytest.mark.asyncio
async def test_plan_schedule_falls_back_to_now_when_visit_datetime_missing() -> None:
    """visit_datetime이 없으면 현재 시각(KST)을 기준으로 LLM 호출과 basis_note 둘
    다에 일관되게 쓴다(9절 미결 사항 해소)."""
    llm = _RecordingLLM(_sample_plan())
    request = SchedulePlanningRequest(
        candidates=_three_candidates(),
        conditions=UserConditions(),
        visit_datetime=None,
        pairwise_distances_km={},
    )

    result = await plan_schedule(request, llm)

    assert llm.received_request is not None
    assert llm.received_request.visit_datetime is not None
    resolved_hhmm = llm.received_request.visit_datetime.strftime("%H:%M")
    assert resolved_hhmm in result.basis_note


@pytest.mark.asyncio
async def test_plan_schedule_normalizes_inconsistent_empty_plan() -> None:
    """LLM이 items는 빈 배열이면서 route_summary/total_duration_min은 그럴듯한
    문장으로 채워 보내는 비일관 응답을 실제로 준 적이 있다(2026-08-10 real Gemini
    수동 테스트). items가 비면 나머지 필드도 결정적으로 덮어써야 한다.

    ScheduleLLMPlan.items에는 min_length=1이 걸려 있어(SCHEDULE-10) 일반적인
    생성 경로로는 이런 객체(items=[])를 더 이상 만들 수 없다(검증 실패 → 재시도
    → 그래도 실패하면 예외). 이 테스트는 검증을 우회하는 model_construct()로
    "어떤 경로로든 비일관 객체가 들어왔을 때"의 방어 로직 자체를 계속 검증한다."""
    inconsistent_plan = ScheduleLLMPlan.model_construct(
        items=[],
        route_summary="장소 세 곳을 도는 알찬 코스예요.",
    )
    llm = _RecordingLLM(inconsistent_plan)
    request = SchedulePlanningRequest(
        candidates=_three_candidates(),
        conditions=UserConditions(),
        visit_datetime=datetime(2026, 8, 7, 15, 0, tzinfo=_KST),
        pairwise_distances_km={},
    )

    result = await plan_schedule(request, llm)

    assert result.items == []
    assert result.total_duration_min == 0
    assert result.route_summary == (
        "일정을 짤 만한 곳을 충분히 찾지 못했어요. "
        "지역을 조금 넓히거나 다른 종류의 장소로 다시 말씀해 주세요."
    )
    # basis_note는 items 유무와 무관하게 계속 채워진다
    assert result.basis_note.startswith("15:00 기준으로")
    assert result.elapsed_ms >= 0


@pytest.mark.asyncio
async def test_plan_schedule_passes_candidates_and_distances_through_untouched() -> None:
    llm = _RecordingLLM(_sample_plan())
    request = SchedulePlanningRequest(
        candidates=_three_candidates(),
        conditions=UserConditions(),
        visit_datetime=datetime(2026, 8, 7, 15, 0, tzinfo=_KST),
        pairwise_distances_km={("place-1", "place-2"): 1.2},
    )

    await plan_schedule(request, llm)

    assert llm.received_request is not None
    assert [c.place_id for c in llm.received_request.candidates] == [
        "place-1",
        "place-2",
        "place-3",
    ]
    assert llm.received_request.pairwise_distances_km == {("place-1", "place-2"): 1.2}


class TestPlanScheduleComputesArrivals:
    """TP-215: 도착시각은 LLM이 만들지 않고 시작 시각 + 누적(체류 + 이동)으로
    계산된다. 거리 정보가 없으면 구간마다 폴백 이동시간(15분)을 쓴다."""

    @pytest.mark.asyncio
    async def test_도착시각이_체류와_이동의_누적과_일치한다(self) -> None:
        plan = ScheduleLLMPlan(
            items=[
                _sample_item("place-1", 1),
                _sample_item("place-2", 2),
                _sample_item("place-3", 3),
            ],
            route_summary="테스트 동선 요약",
        )
        llm = _RecordingLLM(plan)
        request = SchedulePlanningRequest(
            candidates=_three_candidates(),
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 7, 10, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, llm)

        # attraction 정책 체류 60분 + 폴백 이동 15분이 누적된다.
        assert [item.estimated_arrival for item in result.items] == [
            "10:00",
            "11:15",
            "12:30",
        ]
        assert result.total_duration_min == 60 + 15 + 60 + 15 + 60

    @pytest.mark.asyncio
    async def test_비현실적인_체류시간_제안은_정책_범위로_조정된다(self) -> None:
        """LLM이 "관광지 37분"을 줘도 그대로 실리지 않는다 (TP-215)."""

        plan = ScheduleLLMPlan(
            items=[
                _sample_item("place-1", 1, estimated_duration_min=37),
                _sample_item("place-2", 2),
                _sample_item("place-3", 3),
            ],
            route_summary="테스트 동선 요약",
        )
        llm = _RecordingLLM(plan)
        request = SchedulePlanningRequest(
            candidates=_three_candidates(),
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 7, 10, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, llm)

        # attraction 정책은 최소 60분이다(app.schedule.duration).
        assert result.items[0].estimated_duration_min == 60


class TestPlanScheduleSkipsLLMWhenCandidatesTooFew:
    """SCHEDULE-07: 후보가 3개 미만이면 LLM을 아예 부르지 않는다 — 9절 "D 후보
    3개 미만" 미결 사항 해소. ScheduleLLMPlan.items의 min_length=3 제약을 애초에
    만족시킬 수 없는 상황에서 굳이 호출·재시도·실패를 반복하지 않는다."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("candidate_count", [0, 1, 2])
    async def test_llm_never_called(self, candidate_count: int) -> None:
        llm = _RecordingLLM(_sample_plan())
        request = SchedulePlanningRequest(
            candidates=[_candidate(f"place-{i}") for i in range(candidate_count)],
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 7, 15, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, llm)

        assert llm.call_count == 0
        assert llm.received_request is None
        assert result.items == []
        assert result.total_duration_min == 0
        assert result.route_summary == (
            "일정을 짤 만한 곳을 충분히 찾지 못했어요. "
            "지역을 조금 넓히거나 다른 종류의 장소로 다시 말씀해 주세요."
        )
        assert result.elapsed_ms >= 0
        assert result.basis_note.startswith("15:00 기준으로")

    @pytest.mark.asyncio
    async def test_llm_called_when_exactly_three_candidates(self) -> None:
        """경계값: 정확히 3개면 스킵하지 않고 정상 호출한다."""
        llm = _RecordingLLM(_sample_plan())
        request = SchedulePlanningRequest(
            candidates=_three_candidates(),
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 7, 15, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        await plan_schedule(request, llm)

        assert llm.call_count == 1


class TestPlanScheduleCandidateGuardIsDynamic:
    """SCHEDULE-10: 후보 부족 가드의 최솟값이 고정 3이 아니라 target_item_range()가
    계산한 값을 쓴다 — time_available이 짧으면(예: 90분) 최솟값이 1로 낮아져,
    후보가 3개 미만이어도 LLM을 정상 호출해야 한다."""

    @pytest.mark.asyncio
    async def test_짧은_시간이면_후보_한개로도_LLM을_부른다(self) -> None:
        one_item_plan = ScheduleLLMPlan(
            items=[_sample_item("place-1", 1)],
            route_summary="테스트 동선 요약",
        )
        llm = _RecordingLLM(one_item_plan)
        request = SchedulePlanningRequest(
            candidates=[_candidate("place-1")],
            conditions=UserConditions(time_available=90),
            visit_datetime=datetime(2026, 8, 7, 15, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, llm)

        assert llm.call_count == 1
        assert len(result.items) == 1

    @pytest.mark.asyncio
    async def test_짧은_시간이어도_후보가_아예_없으면_여전히_스킵한다(self) -> None:
        llm = _RecordingLLM(_sample_plan())
        request = SchedulePlanningRequest(
            candidates=[],
            conditions=UserConditions(time_available=90),
            visit_datetime=datetime(2026, 8, 7, 15, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, llm)

        assert llm.call_count == 0
        assert result.items == []

    @pytest.mark.asyncio
    async def test_시간_제한이_없으면_여전히_3개_미만에서_스킵한다(self) -> None:
        """기존 SCHEDULE-07 동작(시간 제한 없을 때 최소 3개) 회귀 방지."""
        llm = _RecordingLLM(_sample_plan())
        request = SchedulePlanningRequest(
            candidates=[_candidate("place-1"), _candidate("place-2")],
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 7, 15, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, llm)

        assert llm.call_count == 0
        assert result.items == []


class TestPlanScheduleFlagsClosedStops:
    """폐점 후보를 일정에 넣을 때 estimated_arrival 기준으로 운영시간과 대조해
    구조적으로 경고를 붙인다. 프롬프트에 운영시간을 함께 전달해 LLM이 애초에
    피하도록 유도하지만(build_schedule_planning_instruction), 그 지시만으로는
    부족하다고 판단해(6.2.1절 — 근거 데이터가 단일 시각 기준) planner.py가
    응답을 받은 뒤 다시 결정적으로 검사한다. (docs/design/int-07-schedule.md
    9절 "폐점 스탑 감지" 항목 해소)"""

    @pytest.mark.asyncio
    async def test_도착_예정_시각이_마감_이후면_경고를_붙인다(self) -> None:
        plan = ScheduleLLMPlan(
            items=[
                _sample_item("place-1", 1),
                _sample_item("place-2", 2),
                _sample_item("place-3", 3),
            ],
            route_summary="테스트 동선 요약",
        )
        llm = _RecordingLLM(plan)
        candidates = [
            _candidate("place-1", operating_hours_display="09:00~18:00"),
            _candidate("place-2"),
            _candidate("place-3"),
        ]
        request = SchedulePlanningRequest(
            candidates=candidates,
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 7, 19, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, llm)

        assert result.items[0].warnings == [
            "19:00 도착 예정인데 이곳은 09:00~18:00 운영이에요. "
            "가시기 전에 한 번 확인해 주세요."
        ]
        assert result.items[1].warnings == []
        assert result.items[2].warnings == []

    @pytest.mark.asyncio
    async def test_운영시간_내_도착이면_경고가_없다(self) -> None:
        plan = ScheduleLLMPlan(
            items=[
                _sample_item("place-1", 1),
                _sample_item("place-2", 2),
                _sample_item("place-3", 3),
            ],
            route_summary="테스트 동선 요약",
        )
        llm = _RecordingLLM(plan)
        candidates = [
            _candidate("place-1", operating_hours_display="09:00~18:00"),
            _candidate("place-2"),
            _candidate("place-3"),
        ]
        request = SchedulePlanningRequest(
            candidates=candidates,
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 7, 9, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, llm)

        assert result.items[0].warnings == []

    @pytest.mark.asyncio
    async def test_운영시간_미확인_후보는_경고하지_않는다(self) -> None:
        """operating_hours_display가 None(운영시간 자체를 모름)이면 폐점이라고
        단정할 근거가 없다 — scoring.py의 "운영시간 미확인은 폐점이 아니다"
        원칙과 동일하게, 검사 대상에서 제외한다."""
        llm = _RecordingLLM(_sample_plan())
        request = SchedulePlanningRequest(
            candidates=_three_candidates(),
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 7, 15, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, llm)

        assert all(item.warnings == [] for item in result.items)

    @pytest.mark.asyncio
    async def test_24시간_운영은_경고하지_않는다(self) -> None:
        plan = ScheduleLLMPlan(
            items=[
                _sample_item("place-1", 1),
                _sample_item("place-2", 2),
                _sample_item("place-3", 3),
            ],
            route_summary="테스트 동선 요약",
        )
        llm = _RecordingLLM(plan)
        candidates = [
            _candidate("place-1", operating_hours_display="24시간"),
            _candidate("place-2"),
            _candidate("place-3"),
        ]
        request = SchedulePlanningRequest(
            candidates=candidates,
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 7, 15, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, llm)

        assert result.items[0].warnings == []


class _RecordingFillLLM:
    """generate_schedule_fill()에 실제로 어떤 request가 넘어오는지 기록하는 더블."""

    def __init__(self, plan: SchedulePartialLLMPlan) -> None:
        self._plan = plan
        self.received_request: SchedulePartialFillRequest | None = None
        self.call_count = 0

    async def judge_travel_modes(self, segments, context):
        """구간 이동수단 판정(TP-227). 규칙과 같은 답을 내 이 이중체를 쓰는 테스트가
        판정 도입 전과 같은 결과를 보게 한다.

        **없으면 안 된다.** 이 메서드가 빠지면 판정 경로가 AttributeError로 끊기고,
        예전에는 그것이 규칙 폴백으로 삼켜져 판정이 한 번도 안 도는데 테스트가
        통과했다(TP-227에서 6건 발견).
        """

        from app.providers.contracts import ProviderSource, provider_result

        del context
        return provider_result(
            tuple(
                "transit" if segment.walk_minutes > 20.0 else "walking"
                for segment in segments
            ),
            source=ProviderSource.FAKE_LLM,
        )

    async def generate_schedule_fill(self, request: SchedulePartialFillRequest):
        self.received_request = request
        self.call_count += 1
        return provider_result(self._plan, source=ProviderSource.FAKE_LLM)


def _pinned(place_id: str, order: int, *, estimated_arrival: str = "14:00") -> ScheduleItem:
    return ScheduleItem(
        order=order,
        place_id=place_id,
        place_name=f"장소 {place_id}",
        estimated_arrival=estimated_arrival,
        estimated_duration_min=60,
        travel_to_next_min=15,
        reason="기존 일정 유지",
    )


class TestPlanPartialSchedule:
    """SCHEDULE-09 2단계: 일부 자리만 새로 채우는 plan_partial_schedule() 회귀 테스트.

    (SCHEDULE-부분수정-해결방향-설계안.md 3절)
    """

    @pytest.mark.asyncio
    async def test_merges_pinned_and_new_items_in_order(self) -> None:
        pinned = [_pinned("place-1", 1), _pinned("place-3", 3)]
        new_item = _sample_item("place-2", 2)
        llm = _RecordingFillLLM(SchedulePartialLLMPlan(new_items=[new_item]))
        request = SchedulePartialFillRequest(
            pinned_items=pinned,
            target_orders=[2],
            candidates=[_candidate("place-2")],
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 11, 15, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_partial_schedule(request, llm)

        assert [item.place_id for item in result.items] == ["place-1", "place-2", "place-3"]
        assert [item.order for item in result.items] == [1, 2, 3]
        assert result.route_summary == "2곳은 그대로 두고 1곳만 다른 곳으로 바꿨어요."
        # 이동시간은 이번 순서 기준으로 전부 다시 계산된다(TP-215) — 예전처럼
        # stale한 값을 None으로 무효화하고 넘어가지 않는다. 거리 정보가 없으므로
        # 구간마다 폴백(15분)이 들어가고, 마지막 항목만 None이다.
        assert [item.travel_to_next_min for item in result.items] == [15, 15, None]
        # 체류 60분 x 3 + 이동 15분 x 2
        assert result.total_duration_min == 210
        assert result.basis_note.startswith("15:00 기준으로")
        assert result.elapsed_ms >= 0

    @pytest.mark.asyncio
    async def test_pinned_places_are_removed_from_fill_candidates(self) -> None:
        """유지 대상이 후보에 섞여 있으면 후보에서 빼고 LLM에 넘긴다.

        섞인 채로 넘기면 그 자리에 같은 장소가 다시 뽑혀 한 일정에 중복으로
        들어간다. 프롬프트도 "pinned_items의 place_id를 다시 고르지 마세요"라고
        지시하지만(fill.md) LLM 지시는 구조적 보장이 아니다.

        지금까지는 호출부의 제외 목록(recommended ∪ rejected)이 pinned를 먼저
        걸러내 드러나지 않았다 — 그 목록이 무엇을 담는지에 편성 정확성이
        딸려 있으면 안 된다.
        """
        pinned = [_pinned("place-1", 1), _pinned("place-3", 3)]
        llm = _RecordingFillLLM(SchedulePartialLLMPlan(new_items=[_sample_item("place-2", 2)]))
        request = SchedulePartialFillRequest(
            pinned_items=pinned,
            target_orders=[2],
            # place-1은 pinned인데 후보에도 들어 있다.
            candidates=[_candidate("place-1"), _candidate("place-2")],
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 11, 15, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_partial_schedule(request, llm)

        assert llm.received_request is not None
        assert [c.place_id for c in llm.received_request.candidates] == ["place-2"]
        assert [item.place_id for item in result.items] == ["place-1", "place-2", "place-3"]

    @pytest.mark.asyncio
    async def test_pinned_only_when_every_candidate_is_pinned(self) -> None:
        """후보가 전부 유지 대상이면 채울 수 있는 새 장소가 없다 — LLM을 부르지
        않고 pinned만 살려 안내한다(후보 0건과 같은 처리)."""
        pinned = [_pinned("place-1", 1), _pinned("place-3", 3)]
        llm = _RecordingFillLLM(SchedulePartialLLMPlan(new_items=[_sample_item("place-1", 2)]))
        request = SchedulePartialFillRequest(
            pinned_items=pinned,
            target_orders=[2],
            candidates=[_candidate("place-1"), _candidate("place-3")],
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 11, 15, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_partial_schedule(request, llm)

        assert llm.call_count == 0
        assert [item.place_id for item in result.items] == ["place-1", "place-3"]

    @pytest.mark.asyncio
    async def test_recomputes_every_travel_time_instead_of_invalidating(self) -> None:
        """이동시간은 stale해질 수 없다 — 병합 후 전체 구간을 다시 계산한다(TP-215).

        예전에는 교체된 자리 직전의 pinned 항목이 들고 있던 travel_to_next_min을
        "다음 자리가 바뀌었으니 더는 맞지 않는다"며 None으로 지웠다. 지금은 그
        값을 이번 순서 기준으로 새로 구하므로 지울 것이 없다."""
        pinned = [_pinned("place-1", 1), _pinned("place-2", 2)]
        new_item = _sample_item("place-3", 3)
        llm = _RecordingFillLLM(SchedulePartialLLMPlan(new_items=[new_item]))
        request = SchedulePartialFillRequest(
            pinned_items=pinned,
            target_orders=[3],
            candidates=[_candidate("place-3")],
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 11, 15, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_partial_schedule(request, llm)

        assert [item.place_id for item in result.items] == ["place-1", "place-2", "place-3"]
        assert [item.travel_to_next_min for item in result.items] == [15, 15, None]

    @pytest.mark.asyncio
    async def test_downstream_arrivals_follow_the_new_chain(self) -> None:
        """중간 자리가 바뀌면 뒤이어 오는 항목의 도착 시각이 새 체인을 따른다.

        예전에는 LLM이 준 새 항목의 도착 시각을 앵커로 믿고 그 뒤만 다시 맞췄다 —
        앵커 자체가 검증되지 않은 값이었다. 지금은 유지되는 첫 자리의 도착
        시각만 기준점으로 쓰고 나머지는 전부 계산한다(TP-215)."""
        pinned = [
            _pinned("place-1", 1, estimated_arrival="14:00"),
            # place-3의 원래 도착 시각(16:55)은 옛 place-2(체류 90분+이동 10분)
            # 기준으로 계산됐던 값이라, 새 place-2가 다른 체류·이동 시간을 쓰면
            # 더 이상 맞지 않는다.
            _pinned("place-3", 3, estimated_arrival="16:55"),
        ]
        new_item = _sample_item("place-2", 2, estimated_duration_min=120)
        llm = _RecordingFillLLM(SchedulePartialLLMPlan(new_items=[new_item]))
        request = SchedulePartialFillRequest(
            pinned_items=pinned,
            target_orders=[2],
            candidates=[_candidate("place-2")],
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 11, 15, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_partial_schedule(request, llm)

        arrivals = {item.place_id: item.estimated_arrival for item in result.items}
        # place-1은 그대로 유지되는 첫 자리라 도착 시각이 기준점이 된다.
        assert arrivals["place-1"] == "14:00"
        # place-2는 14:00 + 체류 60분 + 이동 15분.
        assert arrivals["place-2"] == "15:15"
        # place-3은 15:15 + 새 장소 체류 120분 + 이동 15분 = 17:30.
        # 스냅샷으로 들고 있던 16:55가 아니어야 한다.
        assert arrivals["place-3"] == "17:30"

    @pytest.mark.asyncio
    async def test_measures_elapsed_ms(self) -> None:
        """plan_schedule()과 같은 패턴으로 timer 주입값을 그대로 반영한다."""
        pinned = [_pinned("place-1", 1), _pinned("place-3", 3)]
        new_item = _sample_item("place-2", 2)
        llm = _RecordingFillLLM(SchedulePartialLLMPlan(new_items=[new_item]))
        request = SchedulePartialFillRequest(
            pinned_items=pinned,
            target_orders=[2],
            candidates=[_candidate("place-2")],
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 11, 15, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )
        fake_ticks = iter([0.0, 0.1])

        result = await plan_partial_schedule(request, llm, timer=lambda: next(fake_ticks))

        assert result.elapsed_ms == 100.0

    @pytest.mark.asyncio
    async def test_no_fresh_candidates_keeps_pinned_only(self) -> None:
        """대체할 새 후보가 없으면 "일정 전체 실패"가 아니라 pinned만 그대로 유지한다."""
        pinned = [_pinned("place-1", 1), _pinned("place-3", 3)]
        llm = _RecordingFillLLM(SchedulePartialLLMPlan(new_items=[]))
        request = SchedulePartialFillRequest(
            pinned_items=pinned,
            target_orders=[2],
            candidates=[],
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 11, 15, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_partial_schedule(request, llm)

        assert llm.call_count == 0
        assert [item.place_id for item in result.items] == ["place-1", "place-3"]
        assert "그대로 뒀어요" in result.route_summary
        assert result.elapsed_ms >= 0

    @pytest.mark.asyncio
    async def test_raises_when_llm_returns_wrong_orders(self) -> None:
        """LLM이 target_orders와 다른 order를 반환하면(개수 불일치 포함)
        pinned를 신뢰할 수 없는 상태로 병합하지 않고 명확히 실패시킨다."""
        pinned = [_pinned("place-1", 1), _pinned("place-3", 3)]
        wrong_item = _sample_item("place-2", 99)  # target_orders=[2]와 불일치
        llm = _RecordingFillLLM(SchedulePartialLLMPlan(new_items=[wrong_item]))
        request = SchedulePartialFillRequest(
            pinned_items=pinned,
            target_orders=[2],
            candidates=[_candidate("place-2")],
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 11, 15, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        with pytest.raises(AppError) as exc_info:
            await plan_partial_schedule(request, llm)

        assert exc_info.value.code == "llm_output_invalid"

    @pytest.mark.asyncio
    async def test_새로_채운_자리도_운영시간_기준으로_검사한다(self) -> None:
        """pinned 항목은 candidates에 없어(REJECT_SPECIFIC 부분 재편성 특성상 이번
        요청의 candidates는 새로 채울 자리의 후보만 담고 있다) 검사 대상이
        아니지만, 새로 채운 자리(new_items)는 이번 candidates에 있으므로 그대로
        검사된다."""
        pinned = [_pinned("place-1", 1), _pinned("place-3", 3)]
        new_item = _sample_item("place-2", 2)
        llm = _RecordingFillLLM(SchedulePartialLLMPlan(new_items=[new_item]))
        request = SchedulePartialFillRequest(
            pinned_items=pinned,
            target_orders=[2],
            candidates=[_candidate("place-2", operating_hours_display="09:00~15:00")],
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 11, 15, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_partial_schedule(request, llm)

        by_place = {item.place_id: item for item in result.items}
        assert by_place["place-2"].warnings != []
        assert by_place["place-1"].warnings == []
        assert by_place["place-3"].warnings == []

    @pytest.mark.asyncio
    async def test_empty_target_orders_returns_pinned_unchanged(self) -> None:
        """방어적 분기 — 정상 흐름(REJECT_SPECIFIC 파싱)에서는 발생하지 않는다."""
        pinned = [_pinned("place-1", 1), _pinned("place-2", 2)]
        llm = _RecordingFillLLM(SchedulePartialLLMPlan(new_items=[]))
        request = SchedulePartialFillRequest(
            pinned_items=pinned,
            target_orders=[],
            candidates=[_candidate("place-3")],
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 11, 15, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_partial_schedule(request, llm)

        assert llm.call_count == 0
        assert [item.place_id for item in result.items] == ["place-1", "place-2"]


class TestPlanPartialScheduleKeepsTheAnchor:
    """TP-215: 그대로 유지되는 첫 자리의 도착 시각이 시간표의 기준점이다.

    자리 하나를 바꿨다고 일정 전체가 앞뒤로 움직이면 "나머지는 그대로 뒀다"는
    말이 안 맞는다. 그래서 이 값만은 반올림하지 않고 그대로 쓴다."""

    @pytest.mark.asyncio
    async def test_유지되는_첫_자리의_도착_시각에서_이어_계산한다(self) -> None:
        pinned = [_pinned("place-1", 1, estimated_arrival="13:52")]
        new_item = _sample_item("place-2", 2)
        llm = _RecordingFillLLM(SchedulePartialLLMPlan(new_items=[new_item]))
        request = SchedulePartialFillRequest(
            pinned_items=pinned,
            target_orders=[2],
            candidates=[_candidate("place-2")],
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 11, 15, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_partial_schedule(request, llm)

        # 13:52 그대로 + 체류 60분 + 이동 15분 = 15:07
        assert [item.estimated_arrival for item in result.items] == ["13:52", "15:07"]

    @pytest.mark.asyncio
    async def test_대체_후보가_없어_pinned만_남아도_기준점을_지킨다(self) -> None:
        pinned = [_pinned("place-1", 1, estimated_arrival="13:52")]
        llm = _RecordingFillLLM(SchedulePartialLLMPlan(new_items=[]))
        request = SchedulePartialFillRequest(
            pinned_items=pinned,
            target_orders=[2],
            candidates=[],
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 11, 15, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_partial_schedule(request, llm)

        assert [item.estimated_arrival for item in result.items] == ["13:52"]


class TestCoVisitedFetcherWiring:
    """place_associations(D-088) 연동은 opt-in이다 — co_visited_fetcher를 안 넘기면
    plan_schedule()은 기존과 완전히 동일하게 동작해야 한다. 실패 시에도 SCHEDULE
    전체를 막지 않고 힌트 없이 계속돼야 한다."""

    @pytest.mark.asyncio
    async def test_fetcher를_안_넘기면_co_visited_hints가_비어있다(self) -> None:
        llm = _RecordingLLM(_sample_plan())
        request = SchedulePlanningRequest(
            candidates=_three_candidates(),
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 26, 15, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        await plan_schedule(request, llm)

        assert llm.received_request is not None
        assert llm.received_request.co_visited_hints == []

    @pytest.mark.asyncio
    async def test_fetcher가_반환한_힌트가_LLM_요청에_실린다(self) -> None:
        llm = _RecordingLLM(_sample_plan())
        request = SchedulePlanningRequest(
            candidates=_three_candidates(),
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 26, 15, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )
        expected_hint = CoVisitedHint(from_place_id="place-1", to_place_id="place-2", rank=1)
        received_place_ids: list[str] = []

        async def fake_fetcher(place_ids, settings):
            received_place_ids.extend(place_ids)
            return [expected_hint]

        await plan_schedule(request, llm, co_visited_fetcher=fake_fetcher, settings=Settings())

        assert llm.received_request is not None
        assert llm.received_request.co_visited_hints == [expected_hint]
        assert sorted(received_place_ids) == ["place-1", "place-2", "place-3"]

    @pytest.mark.asyncio
    async def test_fetcher가_예외를_던져도_일정_편성은_그대로_진행된다(self) -> None:
        llm = _RecordingLLM(_sample_plan())
        request = SchedulePlanningRequest(
            candidates=_three_candidates(),
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 26, 15, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        async def failing_fetcher(place_ids, settings):
            raise RuntimeError("네트워크 실패 흉내")

        result = await plan_schedule(
            request, llm, co_visited_fetcher=failing_fetcher, settings=Settings()
        )

        assert llm.received_request is not None
        assert llm.received_request.co_visited_hints == []
        assert [item.place_id for item in result.items] == [
        item.place_id for item in _sample_plan().items
    ]


class TestCoVisitedFetcherWiringForPartialSchedule:
    """plan_partial_schedule()도 같은 opt-in 계약을 따른다 — 다만 조회 대상
    place_id는 candidates뿐 아니라 pinned_items까지 합친 집합이어야 한다."""

    @pytest.mark.asyncio
    async def test_fetcher를_안_넘기면_co_visited_hints가_비어있다(self) -> None:
        pinned_items = [_pinned("place-1", 1), _pinned("place-3", 3)]
        new_item = _sample_item("place-2", 2)
        llm = _RecordingFillLLM(SchedulePartialLLMPlan(new_items=[new_item]))
        request = SchedulePartialFillRequest(
            pinned_items=pinned_items,
            target_orders=[2],
            candidates=[_candidate("place-2")],
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 26, 15, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        await plan_partial_schedule(request, llm)

        assert llm.received_request is not None
        assert llm.received_request.co_visited_hints == []

    @pytest.mark.asyncio
    async def test_pinned과_candidates_place_id를_합쳐서_조회한다(self) -> None:
        pinned_items = [_pinned("place-1", 1), _pinned("place-3", 3)]
        new_item = _sample_item("place-2", 2)
        llm = _RecordingFillLLM(SchedulePartialLLMPlan(new_items=[new_item]))
        request = SchedulePartialFillRequest(
            pinned_items=pinned_items,
            target_orders=[2],
            candidates=[_candidate("place-2")],
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 26, 15, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )
        expected_hint = CoVisitedHint(from_place_id="place-1", to_place_id="place-2", rank=1)
        received_place_ids: list[str] = []

        async def fake_fetcher(place_ids, settings):
            received_place_ids.extend(place_ids)
            return [expected_hint]

        await plan_partial_schedule(
            request, llm, co_visited_fetcher=fake_fetcher, settings=Settings()
        )

        assert llm.received_request is not None
        assert llm.received_request.co_visited_hints == [expected_hint]
        assert sorted(received_place_ids) == ["place-1", "place-2", "place-3"]

    @pytest.mark.asyncio
    async def test_fetcher가_예외를_던져도_부분_재편성은_그대로_진행된다(self) -> None:
        pinned_items = [_pinned("place-1", 1)]
        new_item = _sample_item("place-2", 2)
        llm = _RecordingFillLLM(SchedulePartialLLMPlan(new_items=[new_item]))
        request = SchedulePartialFillRequest(
            pinned_items=pinned_items,
            target_orders=[2],
            candidates=[_candidate("place-2")],
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 26, 15, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        async def failing_fetcher(place_ids, settings):
            raise RuntimeError("네트워크 실패 흉내")

        result = await plan_partial_schedule(
            request, llm, co_visited_fetcher=failing_fetcher, settings=Settings()
        )

        assert llm.received_request is not None
        assert llm.received_request.co_visited_hints == []
        assert result.items[-1].place_id == "place-2"


class TestRoundUpStart:
    """_round_up_start()의 경계값 (TP-215).

    항목마다 도착시각을 올리던 것을 시작 시각 한 번으로 옮겼다 — 항목마다 올리면
    화면의 시각이 체류·이동의 누적과 어긋나기 때문이다(planner 주석 참고).
    """

    @pytest.mark.parametrize(
        ("minute", "expected_minute"),
        [
            (59, 0),
            (14, 20),
            (30, 30),  # 이미 10분 단위면 그대로
            (0, 0),
            (1, 10),
        ],
    )
    def test_10분_단위로_올림한다(self, minute: int, expected_minute: int) -> None:
        rounded = _round_up_start(datetime(2026, 8, 7, 13, minute, tzinfo=_KST))
        assert rounded.minute == expected_minute

    def test_자정을_넘기면_날짜가_함께_넘어간다(self) -> None:
        rounded = _round_up_start(datetime(2026, 8, 7, 23, 55, tzinfo=_KST))
        assert rounded == datetime(2026, 8, 8, 0, 0, tzinfo=_KST)

    def test_초가_남아_있으면_다음_단위로_올린다(self) -> None:
        rounded = _round_up_start(datetime(2026, 8, 7, 13, 30, 1, tzinfo=_KST))
        assert rounded == datetime(2026, 8, 7, 13, 40, tzinfo=_KST)


# ------------------------------------------------ 보관함 강제 포함 (SCHEDULE-12)


class _SequenceLLM:
    """호출마다 다른 plan을 돌려주는 더블. must_include 재시도 검증용."""

    def __init__(self, *plans: ScheduleLLMPlan) -> None:
        self._plans = list(plans)
        self.received_requests: list[SchedulePlanningRequest] = []

    @property
    def call_count(self) -> int:
        return len(self.received_requests)

    async def judge_travel_modes(self, segments, context):
        """구간 이동수단 판정(TP-227). 규칙과 같은 답을 내 이 이중체를 쓰는 테스트가
        판정 도입 전과 같은 결과를 보게 한다.

        **없으면 안 된다.** 이 메서드가 빠지면 판정 경로가 AttributeError로 끊기고,
        예전에는 그것이 규칙 폴백으로 삼켜져 판정이 한 번도 안 도는데 테스트가
        통과했다(TP-227에서 6건 발견).
        """

        from app.providers.contracts import ProviderSource, provider_result

        del context
        return provider_result(
            tuple(
                "transit" if segment.walk_minutes > 20.0 else "walking"
                for segment in segments
            ),
            source=ProviderSource.FAKE_LLM,
        )

    async def generate_schedule_plan(self, request: SchedulePlanningRequest):
        self.received_requests.append(request)
        plan = self._plans[min(self.call_count - 1, len(self._plans) - 1)]
        return provider_result(plan, source=ProviderSource.FAKE_LLM)


def _plan_of(*place_ids: str) -> ScheduleLLMPlan:
    return ScheduleLLMPlan(
        items=[
            _sample_item(place_id, order) for order, place_id in enumerate(place_ids, start=1)
        ],
        route_summary="테스트 동선 요약",
    )


@pytest.mark.asyncio
async def test_must_include_is_passed_to_llm() -> None:
    """강제 포함 목록이 LLM 요청에 그대로 실린다."""
    llm = _RecordingLLM(_plan_of("place-1", "place-2", "place-3"))
    request = SchedulePlanningRequest(
        candidates=_three_candidates(),
        must_include_place_ids=["place-2"],
        conditions=UserConditions(),
        pairwise_distances_km={},
    )

    result = await plan_schedule(request, llm)

    assert llm.received_request is not None
    assert llm.received_request.must_include_place_ids == ["place-2"]
    assert result.omitted_saved_place_names == []


@pytest.mark.asyncio
async def test_must_include_not_in_candidates_is_dropped_silently() -> None:
    """후보에 없는 id는 강제할 수 없다 — 이름을 모르므로 안내도 여기서 채우지 않는다.

    폐점 하드 필터 등으로 D가 걸러낸 경우다. 안내 문구는 호출부(agent_runtime)가
    보관함에 저장된 이름으로 따로 채운다.
    """
    llm = _RecordingLLM(_plan_of("place-1", "place-2", "place-3"))
    request = SchedulePlanningRequest(
        candidates=_three_candidates(),
        must_include_place_ids=["place-2", "없는-장소"],
        conditions=UserConditions(),
        pairwise_distances_km={},
    )

    result = await plan_schedule(request, llm)

    assert llm.received_request is not None
    assert llm.received_request.must_include_place_ids == ["place-2"]
    assert result.omitted_saved_place_names == []


@pytest.mark.asyncio
async def test_must_include_over_item_cap_is_trimmed_in_saved_order() -> None:
    """상한을 넘으면 담은 순서대로 앞에서부터만 쓰고, 나머지는 이름으로 알린다.

    time_available=150분이면 derive_item_range()가 최대 2곳이다(관광지 최소 60분,
    이동 폴백 15분 -> 2곳 135분은 허용 오차 안, 3곳 210분은 밖).
    점수 순이 아니라 담은 순으로 자르는 이유는 "왜 그 곳이 빠졌는지" 설명할 수
    있어야 하기 때문이다.
    """
    llm = _RecordingLLM(_plan_of("place-1", "place-2"))
    request = SchedulePlanningRequest(
        candidates=_three_candidates(),
        must_include_place_ids=["place-1", "place-2", "place-3"],
        conditions=UserConditions(time_available=150),
        pairwise_distances_km={},
    )

    result = await plan_schedule(request, llm)

    assert llm.received_request is not None
    assert llm.received_request.must_include_place_ids == ["place-1", "place-2"]
    # 상한 자르기는 omitted(동선 누락)와 다른 필드로 나간다 — 사용자가 할 수 있는
    # 일이 다르다("보관함에서 다른 곳을 빼면 들어간다"가 여기서만 확정적으로 참). TP-223
    assert result.over_capacity_place_names == ["장소 place-3"]
    assert result.omitted_saved_place_names == []


@pytest.mark.asyncio
async def test_must_include_missing_triggers_one_retry() -> None:
    """LLM이 강제 포함을 빠뜨리면 한 번 다시 부른다. 두 번째가 맞으면 안내는 없다."""
    llm = _SequenceLLM(
        _plan_of("place-1", "place-2", "place-3"),
        _plan_of("place-1", "place-2", "place-9"),
    )
    request = SchedulePlanningRequest(
        candidates=[*_three_candidates(), _candidate("place-9")],
        must_include_place_ids=["place-9"],
        conditions=UserConditions(),
        pairwise_distances_km={},
    )

    result = await plan_schedule(request, llm)

    assert llm.call_count == 2
    assert result.omitted_saved_place_names == []
    assert [item.place_id for item in result.items] == ["place-1", "place-2", "place-9"]


@pytest.mark.asyncio
async def test_must_include_missing_after_retry_keeps_result() -> None:
    """재시도 후에도 빠지면 502로 죽이지 않고 결과를 살린다.

    plan_partial_schedule()의 하드 실패와 다른 선택이다 — 저쪽은 유지해야 할
    기존 일정이 걸려 있지만, 보관함은 부분 성공이 전체 실패보다 낫다.

    **살린 결과를 그대로 내보내지는 않는다**(TP-223). LLM이 담아둔 곳 대신 담지
    않은 곳을 넣었으므로 그 자리를 되돌린다 — 되돌릴 자리조차 없을 때만 이름을
    실어 안내한다(`Test밀려난_보관함_장소_되돌리기`).
    """
    llm = _SequenceLLM(_plan_of("place-1", "place-2", "place-3"))
    request = SchedulePlanningRequest(
        candidates=[*_three_candidates(), _candidate("place-9")],
        must_include_place_ids=["place-9"],
        conditions=UserConditions(),
        pairwise_distances_km={},
    )

    result = await plan_schedule(request, llm)

    assert llm.call_count == 2
    # 첫 낯선 자리가 담아둔 곳으로 바뀌고 나머지 자리는 그대로다.
    assert [item.place_id for item in result.items] == [
        "place-9",
        "place-2",
        "place-3",
    ]
    assert result.omitted_saved_place_names == []


@pytest.mark.asyncio
async def test_empty_must_include_does_not_retry() -> None:
    """강제 포함이 없으면 검증도 재시도도 없다 — 기존 동작과 완전히 같다."""
    llm = _SequenceLLM(_plan_of("place-1", "place-2", "place-3"))
    request = SchedulePlanningRequest(
        candidates=_three_candidates(),
        conditions=UserConditions(),
        pairwise_distances_km={},
    )

    result = await plan_schedule(request, llm)

    assert llm.call_count == 1
    assert result.omitted_saved_place_names == []


# ------------------------------------------------ 시각 계산 엔진 (TP-215)


class TestPlanScheduleRejectsUnknownPlaceIds:
    """LLM이 후보에 없는 place_id를 만들어내면 그 항목을 버린다.

    통과시키면 되돌릴 수 없다 — record_recommendation()에 "추천됨"으로 기록되고,
    이후 턴의 제외 목록에 올라 실재하는 장소를 영구히 가린다.
    """

    @pytest.mark.asyncio
    async def test_후보에_없는_항목은_결과에서_빠진다(self) -> None:
        plan = ScheduleLLMPlan(
            items=[
                _sample_item("place-1", 1),
                _sample_item("지어낸-장소", 2),
                _sample_item("place-3", 3),
            ],
            route_summary="테스트 동선 요약",
        )
        llm = _RecordingLLM(plan)
        request = SchedulePlanningRequest(
            candidates=_three_candidates(),
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 9, 2, 13, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, llm)

        assert [item.place_id for item in result.items] == ["place-1", "place-3"]

    @pytest.mark.asyncio
    async def test_남은_항목의_순서를_다시_매긴다(self) -> None:
        """가운데가 빠졌다고 order에 구멍이 나면 프론트 타임라인이 어긋난다."""

        plan = ScheduleLLMPlan(
            items=[
                _sample_item("place-1", 1),
                _sample_item("지어낸-장소", 2),
                _sample_item("place-3", 3),
            ],
            route_summary="테스트 동선 요약",
        )
        llm = _RecordingLLM(plan)
        request = SchedulePlanningRequest(
            candidates=_three_candidates(),
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 9, 2, 13, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, llm)

        assert [item.order for item in result.items] == [1, 2]

    @pytest.mark.asyncio
    async def test_전부_지어낸_값이면_빈_일정으로_안내한다(self) -> None:
        plan = ScheduleLLMPlan(
            items=[_sample_item(f"지어낸-{i}", i) for i in range(1, 4)],
            route_summary="테스트 동선 요약",
        )
        llm = _RecordingLLM(plan)
        request = SchedulePlanningRequest(
            candidates=_three_candidates(),
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 9, 2, 13, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, llm)

        assert result.items == []
        assert result.total_duration_min == 0
        assert "찾지 못했어요" in result.route_summary

    @pytest.mark.asyncio
    async def test_부분_재편성에서는_하드_실패한다(self) -> None:
        """유지해야 할 기존 일정이 걸려 있어 자리를 비울 수 없다 — 조용히 빼면
        그 뒤 항목들의 순서가 밀려 사용자가 유지하기로 한 일정이 망가진다."""

        llm = _RecordingFillLLM(
            SchedulePartialLLMPlan(new_items=[_sample_item("지어낸-장소", 2)])
        )
        request = SchedulePartialFillRequest(
            pinned_items=[_pinned("place-1", 1), _pinned("place-3", 3)],
            target_orders=[2],
            candidates=[_candidate("place-2")],
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 9, 2, 13, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        with pytest.raises(AppError) as exc_info:
            await plan_partial_schedule(request, llm)

        assert exc_info.value.code == "llm_output_invalid"


class TestPlanScheduleTimelineIntegration:
    """TP-215 완료 조건 — 대기·자정 넘김·결정론을 편성 경로 전체로 확인한다."""

    @pytest.mark.asyncio
    async def test_개장_전에_도착하면_기다렸다가_방문한다(self) -> None:
        plan = ScheduleLLMPlan(
            items=[_sample_item("place-1", 1), _sample_item("place-2", 2)],
            route_summary="테스트 동선 요약",
        )
        llm = _RecordingLLM(plan)
        candidates = [
            _candidate("place-1"),
            _candidate("place-2", operating_hours_display="15:00~21:00"),
        ]
        request = SchedulePlanningRequest(
            candidates=candidates,
            conditions=UserConditions(time_available=150),
            visit_datetime=datetime(2026, 9, 2, 13, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, llm)

        # 13:00 + 체류 60 + 이동 15 = 14:15 도착. 개장은 15:00이다.
        assert result.items[1].estimated_arrival == "14:15"
        # 기다렸다가 여는 시각에 들어가므로 경고를 붙이지 않는다.
        assert result.items[1].warnings == []
        # 대기 45분도 사용자가 실제로 쓰는 시간이라 총합에 들어간다.
        assert result.total_duration_min == 60 + 15 + 45 + 60

    @pytest.mark.asyncio
    async def test_자정을_넘겨도_순서와_시각이_뒤집히지_않는다(self) -> None:
        plan = ScheduleLLMPlan(
            items=[_sample_item("place-1", 1), _sample_item("place-2", 2)],
            route_summary="테스트 동선 요약",
        )
        llm = _RecordingLLM(plan)
        request = SchedulePlanningRequest(
            candidates=[_candidate("place-1"), _candidate("place-2")],
            conditions=UserConditions(time_available=150),
            visit_datetime=datetime(2026, 9, 2, 23, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, llm)

        assert [item.estimated_arrival for item in result.items] == ["23:00", "00:15"]
        # 자정 기준 분으로 비교하면 뒤집히지만(1380 > 15), 실제 소요는 75분이다.
        assert result.total_duration_min == 60 + 15 + 60

    @pytest.mark.asyncio
    async def test_같은_입력이면_같은_시간표가_나온다(self) -> None:
        def _request() -> SchedulePlanningRequest:
            return SchedulePlanningRequest(
                candidates=_three_candidates(),
                conditions=UserConditions(),
                visit_datetime=datetime(2026, 9, 2, 13, 0, tzinfo=_KST),
                pairwise_distances_km={("place-1", "place-2"): 1.2},
            )

        first = await plan_schedule(_request(), _RecordingLLM(_sample_plan()))
        second = await plan_schedule(_request(), _RecordingLLM(_sample_plan()))

        assert [i.estimated_arrival for i in first.items] == [
            i.estimated_arrival for i in second.items
        ]
        assert first.total_duration_min == second.total_duration_min

    @pytest.mark.asyncio
    async def test_거리_정보가_있으면_폴백_대신_그_값을_쓴다(self) -> None:
        llm = _RecordingLLM(_sample_plan())
        request = SchedulePlanningRequest(
            candidates=_three_candidates(),
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 9, 2, 13, 0, tzinfo=_KST),
            # 도보 가정 속도(0.07km/분)로 1.4km는 20분이다.
            pairwise_distances_km={("place-1", "place-2"): 1.4},
        )

        result = await plan_schedule(request, llm)

        assert result.items[0].travel_to_next_min == 20
        # 거리를 모르는 구간은 폴백(15분)이 그대로 쓰인다.
        assert result.items[1].travel_to_next_min == 15

    @pytest.mark.asyncio
    async def test_LLM_호출_횟수가_늘지_않는다(self) -> None:
        llm = _RecordingLLM(_sample_plan())
        request = SchedulePlanningRequest(
            candidates=_three_candidates(),
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 9, 2, 13, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        await plan_schedule(request, llm)

        assert llm.call_count == 1


class Test구간_이동정보_배선:
    """TP-216 — 좌표가 오면 직선거리 추정 대신 구간 Edge로 시각을 계산한다."""

    @staticmethod
    def _travel_candidates() -> list[ScheduleTravelCandidate]:
        # 경도 0.01도 ~= 880m. 세 곳을 일렬로 둔다.
        return [
            ScheduleTravelCandidate(
                place_id=f"place-{index}",
                coordinate=GeoCoordinate(latitude=37.5, longitude=127.0 + 0.01 * index),
            )
            for index in (1, 2, 3)
        ]

    @pytest.mark.asyncio
    async def test_좌표가_오면_폴백_15분을_쓰지_않는다(self) -> None:
        """좌표가 없을 때의 총합(210분)과 달라야 Edge가 실제로 쓰인 것이다."""

        llm = _RecordingLLM(_sample_plan())
        request = SchedulePlanningRequest(
            candidates=_three_candidates(),
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 7, 15, 30, tzinfo=_KST),
            pairwise_distances_km={},
            travel_candidates=self._travel_candidates(),
        )

        result = await plan_schedule(request, llm)

        assert result.total_duration_min != 210
        assert all(
            item.travel_to_next_min is not None for item in result.items[:-1]
        )
        assert result.items[-1].travel_to_next_min is None

    @pytest.mark.asyncio
    async def test_도착시각이_구간_이동시간_누적과_일치한다(self) -> None:
        llm = _RecordingLLM(_sample_plan())
        request = SchedulePlanningRequest(
            candidates=_three_candidates(),
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 7, 15, 30, tzinfo=_KST),
            pairwise_distances_km={},
            travel_candidates=self._travel_candidates(),
        )

        result = await plan_schedule(request, llm)

        minutes = 15 * 60 + 30
        for previous, current in zip(result.items, result.items[1:], strict=False):
            minutes += previous.estimated_duration_min + (previous.travel_to_next_min or 0)
            assert current.estimated_arrival == f"{minutes // 60:02d}:{minutes % 60:02d}"

    @pytest.mark.asyncio
    async def test_좌표를_안_넘기면_예전_계산_그대로다(self) -> None:
        """이 필드를 모르는 호출부의 동작이 바뀌지 않는다."""

        llm = _RecordingLLM(_sample_plan())
        request = SchedulePlanningRequest(
            candidates=_three_candidates(),
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 7, 15, 30, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, llm)

        assert result.total_duration_min == 210


class _WalkingMeasureProvider:
    """도보 구간 실측이 성공한 상황."""

    async def get_routes(
        self,
        origin: GeoCoordinate,
        destinations: tuple[RouteDestination, ...],
        *,
        mode: TravelMode = TravelMode.WALKING,
        radius_m: int | None = None,
    ):
        routes = tuple(
            TravelRoute(
                place_id=item.place_id,
                mode=mode,
                status=RouteStatus.SUCCESS,
                source=RouteSource.KAKAO_WALKING,
                distance_m=1_200,
                duration_seconds=540,
            )
            for item in destinations
        )
        return provider_result(
            TravelRouteBatch(routes=routes),
            source=ProviderSource.KAKAO_WALKING_ROUTE,
            status=ProviderStatus.SUCCESS,
        )


class Test구간_표기_배선:
    """TP-216 완료 조건 — 실측/추정 구분이 응답에 실린다.

    화면이 이동수단을 스스로 추측하지 않게 하는 것이 이 필드들의 목적이다.
    예전에는 프론트가 전 구간을 "도보 이동"으로 고정 표기했고, 편성이 긴 구간을
    대중교통으로 전환하기 시작하면서 그 표기가 사실과 어긋났다.
    """

    @staticmethod
    def _travel_candidates() -> list[ScheduleTravelCandidate]:
        return [
            ScheduleTravelCandidate(
                place_id=f"place-{index}",
                coordinate=GeoCoordinate(latitude=37.5, longitude=127.0 + 0.01 * index),
            )
            for index in (1, 2, 3)
        ]

    def _request(self) -> SchedulePlanningRequest:
        return SchedulePlanningRequest(
            candidates=_three_candidates(),
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 7, 15, 30, tzinfo=_KST),
            pairwise_distances_km={},
            travel_candidates=self._travel_candidates(),
        )

    @pytest.mark.asyncio
    async def test_추정_구간은_이동수단을_싣고_실측이_아니라고_말한다(self) -> None:
        result = await plan_schedule(self._request(), _RecordingLLM(_sample_plan()))

        assert [item.travel_to_next_mode for item in result.items] == [
            TravelMode.WALKING,
            TravelMode.WALKING,
            None,
        ]
        assert [item.travel_to_next_measured for item in result.items] == [
            False,
            False,
            False,
        ]

    @pytest.mark.asyncio
    async def test_실측_구간은_실측이라고_말한다(self) -> None:
        tool = TravelRouteTool({TravelMode.WALKING: TravelRouteProviders(
            primary=_WalkingMeasureProvider()
        )})

        result = await plan_schedule(
            self._request(), _RecordingLLM(_sample_plan()), travel_route_tool=tool
        )

        assert [item.travel_to_next_measured for item in result.items] == [
            True,
            True,
            False,
        ]
        # 실측이 도착시각까지 움직였는지 함께 본다 — 표기만 바뀌고 값이 그대로면
        # 사용자에게는 거짓말이 된다.
        assert [item.travel_to_next_min for item in result.items] == [9, 9, None]

    @pytest.mark.asyncio
    async def test_마지막_항목은_이동_표기가_없다(self) -> None:
        result = await plan_schedule(self._request(), _RecordingLLM(_sample_plan()))

        last = result.items[-1]
        assert last.travel_to_next_min is None
        assert last.travel_to_next_mode is None
        assert last.travel_to_next_measured is False

    @pytest.mark.asyncio
    async def test_좌표가_없으면_이동수단을_말하지_않는다(self) -> None:
        """시간표 폴백(15분)을 쓴 구간이다. 근거가 없으므로 수단도 말하지 않는다."""

        request = SchedulePlanningRequest(
            candidates=_three_candidates(),
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 7, 15, 30, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, _RecordingLLM(_sample_plan()))

        assert [item.travel_to_next_min for item in result.items] == [15, 15, None]
        assert all(item.travel_to_next_mode is None for item in result.items)
        assert all(item.travel_to_next_measured is False for item in result.items)

    @pytest.mark.asyncio
    async def test_부분_재편성_결과에도_실린다(self) -> None:
        """유지 항목만 남는 경로에서도 같은 표에서 나와야 한다."""

        llm = _RecordingFillLLM(
            SchedulePartialLLMPlan(
                new_items=[
                    ScheduleLLMItem(
                        order=2,
                        place_id="place-2",
                        place_name="장소 2",
                        estimated_duration_min=60,
                        reason="이유",
                    )
                ]
            )
        )
        request = SchedulePartialFillRequest(
            pinned_items=[_pinned("place-1", 1), _pinned("place-3", 3)],
            target_orders=[2],
            candidates=_three_candidates(),
            conditions=UserConditions(),
            pairwise_distances_km={},
            travel_candidates=self._travel_candidates(),
        )

        result = await plan_partial_schedule(request, llm)

        assert [item.travel_to_next_mode for item in result.items] == [
            TravelMode.WALKING,
            TravelMode.WALKING,
            None,
        ]
        assert all(item.travel_to_next_measured is False for item in result.items)


class Test제외_사유_분리:
    """빠진 이유와 새로 들어온 것을 사유별로 갈라 내보낸다. (TP-223)

    담지 않은 장소가 빈 자리를 채우는 것 자체는 설계된 동작이다
    (prompts/schedule/plan.md — "남는 자리를 다른 후보로 채우세요"). 말하지 않으면
    사용자에게는 끼어든 것으로 보여 버그로 신고된다.
    """

    @pytest.mark.asyncio
    async def test_담지_않은_장소가_들어가면_이름을_알린다(self) -> None:
        llm = _RecordingLLM(_plan_of("place-1", "place-2", "place-3"))
        request = SchedulePlanningRequest(
            candidates=_three_candidates(),
            must_include_place_ids=["place-1"],
            conditions=UserConditions(),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, llm)

        assert result.added_place_names == ["장소 place-2", "장소 place-3"]

    @pytest.mark.asyncio
    async def test_보관함을_안_쓴_턴에는_비어_있다(self) -> None:
        """그때는 모든 장소가 새로 찾은 곳이라 알릴 내용이 아니다."""

        llm = _RecordingLLM(_plan_of("place-1", "place-2", "place-3"))
        request = SchedulePlanningRequest(
            candidates=_three_candidates(),
            conditions=UserConditions(),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, llm)

        assert result.added_place_names == []

    @pytest.mark.asyncio
    async def test_상한으로_잘린_보관함_장소는_새로_찾은_곳이_아니다(self) -> None:
        """자르기 **전** 목록과 비교해야 한다.

        time_available=150분이면 상한이 2곳이라 must_include는 place-1·place-2로
        줄지만, LLM이 잘린 place-3을 고르면 그 곳은 사용자가 담아둔 곳이다.
        줄어든 목록과 비교하면 "새로 찾아 넣었어요"라고 거짓말을 하게 된다.
        """

        llm = _SequenceLLM(
            _plan_of("place-1", "place-3"),
            _plan_of("place-1", "place-3"),
        )
        request = SchedulePlanningRequest(
            candidates=_three_candidates(),
            must_include_place_ids=["place-1", "place-2", "place-3"],
            conditions=UserConditions(time_available=150),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, llm)

        assert result.added_place_names == []

    @pytest.mark.asyncio
    async def test_상한_초과와_동선_누락이_서로_다른_필드로_나간다(self) -> None:
        """해결책이 다르다 — 상한은 "다른 곳을 빼면 들어간다"가 확정적으로 참이다."""

        llm = _SequenceLLM(
            _plan_of("place-1", "place-3"),
            _plan_of("place-1", "place-3"),
        )
        request = SchedulePlanningRequest(
            candidates=_three_candidates(),
            must_include_place_ids=["place-1", "place-2", "place-3"],
            conditions=UserConditions(time_available=150),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, llm)

        # place-3은 상한으로 잘렸고, place-2는 재시도 후에도 LLM이 빠뜨렸다.
        assert result.over_capacity_place_names == ["장소 place-3"]
        assert result.omitted_saved_place_names == ["장소 place-2"]

    @pytest.mark.asyncio
    async def test_일정을_못_짠_턴에도_상한_초과는_알린다(self) -> None:
        """담아둔 곳이 왜 안 보이는지는 일정이 나왔든 아니든 똑같이 궁금하다."""

        # 후보에 없는 id만 오면 _drop_unknown_places()가 전부 버려 items가 빈다
        # (ScheduleLLMPlan은 min_length=1이라 빈 배열을 직접 만들 수 없다).
        llm = _SequenceLLM(_plan_of("place-999"), _plan_of("place-999"))
        request = SchedulePlanningRequest(
            candidates=_three_candidates(),
            must_include_place_ids=["place-1", "place-2", "place-3"],
            conditions=UserConditions(time_available=150),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, llm)

        assert result.items == []
        assert result.over_capacity_place_names == ["장소 place-3"]


class Test밀려난_보관함_장소_되돌리기:
    """담지 않은 장소가 차지한 자리는 밀려난 보관함 장소에게 돌려준다. (TP-223)

    항목 수가 상한 이하인데 담아둔 곳이 빠지고 안 담은 곳이 들어갔다면, 그것은
    "자리가 없어서"가 아니라 "자리를 남에게 줬다"는 뜻이다.
    """

    @staticmethod
    def _four_candidates() -> list[RecommendationItem]:
        return [_candidate(f"place-{index}") for index in (1, 2, 3, 4)]

    @pytest.mark.asyncio
    async def test_낯선_장소_자리를_되돌린다(self) -> None:
        # 보관함 [1,2], LLM은 place-2를 빼고 담지 않은 place-4를 넣는다(재시도해도 동일).
        llm = _SequenceLLM(
            _plan_of("place-1", "place-4"),
            _plan_of("place-1", "place-4"),
        )
        request = SchedulePlanningRequest(
            candidates=self._four_candidates(),
            must_include_place_ids=["place-1", "place-2"],
            conditions=UserConditions(),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, llm)

        assert [item.place_id for item in result.items] == ["place-1", "place-2"]
        # 자리 수는 그대로다 — 되돌리기지 삭제가 아니다.
        assert len(result.items) == 2
        assert result.omitted_saved_place_names == []
        assert result.added_place_names == []

    @pytest.mark.asyncio
    async def test_되돌린_자리의_이유를_바꾼다(self) -> None:
        """LLM이 쓴 문장은 원래 그 자리에 있던 다른 장소를 설명하는 글이다."""

        llm = _SequenceLLM(
            _plan_of("place-1", "place-4"),
            _plan_of("place-1", "place-4"),
        )
        request = SchedulePlanningRequest(
            candidates=self._four_candidates(),
            must_include_place_ids=["place-1", "place-2"],
            conditions=UserConditions(),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, llm)

        restored = next(item for item in result.items if item.place_id == "place-2")
        assert restored.reason == "보관함에 담아두신 곳이에요."
        assert restored.place_name == "장소 place-2"

    @pytest.mark.asyncio
    async def test_동선_요약도_바꾼다(self) -> None:
        """LLM 요약은 지금 일정에 없는 장소를 이름으로 언급할 수 있다."""

        llm = _SequenceLLM(
            _plan_of("place-1", "place-4"),
            _plan_of("place-1", "place-4"),
        )
        request = SchedulePlanningRequest(
            candidates=self._four_candidates(),
            must_include_place_ids=["place-1", "place-2"],
            conditions=UserConditions(),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, llm)

        assert result.route_summary == "담아두신 곳들을 순서대로 이어봤어요."

    @pytest.mark.asyncio
    async def test_자리가_남아서_들어온_장소는_건드리지_않는다(self) -> None:
        """밀려난 보관함 장소가 없으면 설계된 동작 그대로다."""

        llm = _RecordingLLM(_plan_of("place-1", "place-2", "place-4"))
        request = SchedulePlanningRequest(
            candidates=self._four_candidates(),
            must_include_place_ids=["place-1", "place-2"],
            conditions=UserConditions(),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, llm)

        assert [item.place_id for item in result.items] == [
            "place-1",
            "place-2",
            "place-4",
        ]
        assert result.added_place_names == ["장소 place-4"]
        assert result.omitted_saved_place_names == []
        # 요약을 뺏지 않는다 — 되돌린 자리가 없다.
        assert result.route_summary != "담아두신 곳들을 순서대로 이어봤어요."

    @pytest.mark.asyncio
    async def test_되돌릴_자리가_모자라면_나머지는_안내로_남는다(self) -> None:
        """낯선 자리가 하나뿐인데 밀려난 곳이 둘이면 하나만 되돌아온다."""

        # 보관함 [1,2,3], LLM은 place-1과 낯선 place-4만 준다 → 2·3이 빠졌다.
        llm = _SequenceLLM(
            _plan_of("place-1", "place-4"),
            _plan_of("place-1", "place-4"),
        )
        request = SchedulePlanningRequest(
            candidates=self._four_candidates(),
            must_include_place_ids=["place-1", "place-2", "place-3"],
            conditions=UserConditions(),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, llm)

        # 담은 순서로 앞의 것이 먼저 돌아온다 — 자르기와 같은 기준이다.
        assert [item.place_id for item in result.items] == ["place-1", "place-2"]
        assert result.omitted_saved_place_names == ["장소 place-3"]
        assert result.added_place_names == []


class TestFitDurationsToTimeAvailable:
    """TP-238 완료 조건 — 체류시간이 활동 가능 시간에 맞춰 조절되는지 편성 경로로 확인한다."""

    @pytest.mark.asyncio
    async def test_초과하던_편성이_정책_최소값까지_줄어든다(self) -> None:
        """민원 그대로의 모양이다 — 3시간 요청에 관광지 3곳, LLM이 90분씩 제안.

        예전에는 90 x 3 + 이동 30 = 300분이 그대로 나갔다. 이제 곳당 60분까지
        줄어 210분이 되고, 허용 오차 안이라 곳 수는 셋 그대로 남는다.
        """

        plan = ScheduleLLMPlan(
            items=[
                _sample_item("place-1", 1, estimated_duration_min=90),
                _sample_item("place-2", 2, estimated_duration_min=90),
                _sample_item("place-3", 3, estimated_duration_min=90),
            ],
            route_summary="테스트 동선 요약",
        )
        request = SchedulePlanningRequest(
            candidates=_three_candidates(),
            conditions=UserConditions(time_available=180),
            visit_datetime=datetime(2026, 9, 2, 13, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, _RecordingLLM(plan))

        assert [item.estimated_duration_min for item in result.items] == [60, 60, 60]
        assert result.total_duration_min == 60 * 3 + 15 * 2
        assert result.time_budget_status is ScheduleBudgetStatus.WITHIN

    @pytest.mark.asyncio
    async def test_조절한_체류시간이_5분_배수로_나가고_합이_총합과_같다(self) -> None:
        """TP-244 완료 조건 — 저장값·표시값·항목 합이 전부 같은 수를 가리킨다.

        **1분 단위로 나누던 때 이 입력이 86/74/60을 냈다.** 화면
        (`ScheduleCard.tsx`)은 값을 그대로 찍으므로 "86분 머무름"이 뜬다.
        표시할 때만 반올림하면 항목 표시의 합(85+75+60)과 총합 표시가 어긋나서
        TP-215가 없앤 상태로 되돌아간다 — 그래서 배정값 자체를 5분 배수로 둔다.
        """

        plan = ScheduleLLMPlan(
            items=[
                _sample_item("place-1", 1, estimated_duration_min=120),
                _sample_item("place-2", 2, estimated_duration_min=90),
                _sample_item("place-3", 3, estimated_duration_min=60),
            ],
            route_summary="테스트 동선 요약",
        )
        request = SchedulePlanningRequest(
            candidates=_three_candidates(),
            conditions=UserConditions(time_available=250),
            visit_datetime=datetime(2026, 9, 2, 13, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, _RecordingLLM(plan))
        durations = [item.estimated_duration_min for item in result.items]

        assert durations == [85, 75, 60]
        assert all(minutes % 5 == 0 for minutes in durations)
        # 대기가 없는 구성이라 총합은 체류 합 + 이동(15분 x 2구간)과 정확히 같다.
        assert result.total_duration_min == sum(durations) + 15 * 2
        assert result.time_budget_status is ScheduleBudgetStatus.WITHIN

    @pytest.mark.asyncio
    async def test_LLM이_어중간한_체류시간을_줘도_5분_배수로_나간다(self) -> None:
        """조절이 아예 일어나지 않는 경로다 — 허용 오차 안이라 fit이 no-op이다.

        그래서 **배정 단계에서 맞추지 않으면 67분이 그대로 화면에 뜬다.**
        프롬프트가 라운드 숫자를 안내하지만 그건 부탁이고, LLM이 어길 때 막을
        곳은 `resolve_visit_duration()` 하나다.
        """

        plan = ScheduleLLMPlan(
            items=[
                _sample_item("place-1", 1, estimated_duration_min=67),
                _sample_item("place-2", 2, estimated_duration_min=63),
            ],
            route_summary="테스트 동선 요약",
        )
        request = SchedulePlanningRequest(
            candidates=[_candidate("place-1"), _candidate("place-2")],
            conditions=UserConditions(time_available=150),
            visit_datetime=datetime(2026, 9, 2, 13, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, _RecordingLLM(plan))
        durations = [item.estimated_duration_min for item in result.items]

        assert durations == [65, 65]
        assert result.total_duration_min == sum(durations) + 15

    @pytest.mark.asyncio
    async def test_개장_전_대기가_늘어도_예산_쪽으로_움직인다(self) -> None:
        """**줄인 만큼 총합이 줄지 않는다.**

        체류를 줄이면 도착이 당겨져 개장까지 기다리는 시간이 그만큼 늘어난다.
        여기서는 체류를 60분 걷었는데 대기가 30분 늘어 총합은 30분만 줄었다.
        이걸 모르면 "fit을 넣었는데 총합이 안 줄어든다"로 헤매게 된다.

        수렴할 때까지 반복하지 않는다 — 남는 오차는 판정이 알린다.
        """

        plan = ScheduleLLMPlan(
            items=[
                _sample_item("place-1", 1, estimated_duration_min=90),
                _sample_item("place-2", 2, estimated_duration_min=90),
            ],
            route_summary="테스트 동선 요약",
        )
        request = SchedulePlanningRequest(
            candidates=[
                _candidate("place-1"),
                _candidate("place-2", operating_hours_display="15:00~21:00"),
            ],
            conditions=UserConditions(time_available=150),
            visit_datetime=datetime(2026, 9, 2, 13, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, _RecordingLLM(plan))

        # 조절 전: 체류 90+90, 이동 15, 대기 15 => 210분
        # 조절 후: 체류 60+60, 이동 15, 대기 45 => 180분
        assert [item.estimated_duration_min for item in result.items] == [60, 60]
        assert result.total_duration_min == 180
        assert result.time_budget_status is ScheduleBudgetStatus.WITHIN

    @pytest.mark.asyncio
    async def test_시간을_말하지_않아도_기본_예산으로_배분한다(self) -> None:
        """**예전에는 여기서 그냥 돌아갔다.** 그래서 개수 상한이 상수이던 시절과
        겹쳐 420분 일정이 아무 안내 없이 나갔다 — 실측 5건(2026-09-07).

        기본 예산 240분으로 배분하면 90x3 + 이동 30 = 300분이 70x3 + 30 = 240분이
        된다. **판정은 여전히 내리지 않는다** — 사용자가 말하지 않은 시간을
        "지켰다"·"넘었다"로 판정하면 화면이 하지도 않은 약속을 말한다.

        후보를 셋 두는 이유는 time_available이 없을 때 후보 3곳을 요구하기
        때문이다(`required_candidate_count()`) — 둘만 주면 편성 자체가 안 되고
        이 테스트는 빈 items를 보고 통과해 버린다.
        """

        plan = ScheduleLLMPlan(
            items=[
                _sample_item("place-1", 1, estimated_duration_min=90),
                _sample_item("place-2", 2, estimated_duration_min=90),
                _sample_item("place-3", 3, estimated_duration_min=90),
            ],
            route_summary="테스트 동선 요약",
        )
        request = SchedulePlanningRequest(
            candidates=_three_candidates(),
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 9, 2, 13, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, _RecordingLLM(plan))

        assert [item.estimated_duration_min for item in result.items] == [70, 70, 70]
        assert result.total_duration_min == 70 * 3 + 15 * 2
        assert result.time_budget_status is None

    @pytest.mark.asyncio
    async def test_부분_재편성에서_유지한_자리의_체류시간은_변하지_않는다(self) -> None:
        """사용자가 유지하기로 한 자리를 조용히 줄이면 "그대로 뒀다"는 약속이 깨진다.

        유지 항목은 후보 목록에 없다는 기존 불변식으로 갈라낸다.
        """

        pinned = [_pinned("place-1", 1)]
        llm = _RecordingFillLLM(
            SchedulePartialLLMPlan(
                new_items=[_sample_item("place-2", 2, estimated_duration_min=90)]
            )
        )
        request = SchedulePartialFillRequest(
            pinned_items=pinned,
            target_orders=[2],
            candidates=[_candidate("place-2")],
            conditions=UserConditions(time_available=90),
            visit_datetime=datetime(2026, 9, 2, 13, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_partial_schedule(request, llm)

        # 유지한 자리는 60분 그대로, 새로 채운 자리만 최소값까지 줄어든다.
        assert [item.estimated_duration_min for item in result.items] == [60, 60]


class Test고른_분류로_상한을_다시_잰다:
    """실측 재현 — 3시간 요청에 문화시설 3곳이 나와 276분이 됐다. (2026-09-07)

    민원이 TP-238·239로 닫힌 줄 알았는데 이 경로가 남아 있었다. 후보 풀에 값싼
    분류가 섞여 있으면 `derive_item_range()`가 3곳을 허용하는데, LLM이 최소
    체류 90분인 문화시설을 고르면 `fit_durations_to_budget()`이 정책 최소값에서
    멈춰 되돌릴 여유가 0이다.
    """

    @staticmethod
    def _mixed_candidates() -> list[RecommendationItem]:
        """문화시설 3곳 + 쇼핑 2곳. 쇼핑(최소 30분)이 상한 계산을 느슨하게 만든다."""

        return [
            _candidate("place-1", category="cultural_facility"),
            _candidate("place-2", category="cultural_facility"),
            _candidate("place-3", category="cultural_facility"),
            _candidate("place-4", category="shopping"),
            _candidate("place-5", category="shopping"),
        ]

    @staticmethod
    def _request() -> SchedulePlanningRequest:
        return SchedulePlanningRequest(
            candidates=Test고른_분류로_상한을_다시_잰다._mixed_candidates(),
            conditions=UserConditions(time_available=180),
            visit_datetime=datetime(2026, 9, 5, 14, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

    def test_유도값은_값싼_분류로_계산돼_3곳을_허용한다(self) -> None:
        """**이것이 문제의 입력이다.** 체류 최소값을 작은 것부터 세면
        30+30+90 + 이동 30 = 180분이라 3곳이 예산 안으로 보인다."""

        assert derive_item_range(self._request())[1] == 3

    @pytest.mark.asyncio
    async def test_문화시설_세_곳을_고르면_두_곳으로_줄인다(self) -> None:
        """**가드를 지우면 이 테스트가 잡는다.** 줄이지 않으면 90x3 + 이동 30 =
        300분이 되고 180분 요청에 120분 초과다. 두 곳이면 195분으로 허용 오차
        안에 들어온다.
        """

        plan = ScheduleLLMPlan(
            items=[
                _sample_item("place-1", 1, estimated_duration_min=90),
                _sample_item("place-2", 2, estimated_duration_min=90),
                _sample_item("place-3", 3, estimated_duration_min=90),
            ],
            route_summary="테스트 동선 요약",
        )

        result = await plan_schedule(self._request(), _RecordingLLM(plan))

        assert [item.place_id for item in result.items] == ["place-1", "place-2"]
        assert result.item_capacity == 2
        assert result.total_duration_min == 90 + 90 + 15
        assert result.time_budget_status is ScheduleBudgetStatus.WITHIN

    @pytest.mark.asyncio
    async def test_값싼_분류를_고르면_세_곳_그대로_간다(self) -> None:
        """**대조군.** 줄이는 것이 목적이 아니라 예산을 지키는 것이 목적이다.
        쇼핑(최소 30분)을 고르면 세 곳이 그대로 남아야 한다 — 여기서 줄면
        가드가 예산과 무관하게 곳 수를 깎고 있다는 뜻이다.
        """

        plan = ScheduleLLMPlan(
            items=[
                _sample_item("place-4", 1, estimated_duration_min=30),
                _sample_item("place-5", 2, estimated_duration_min=30),
                _sample_item("place-1", 3, estimated_duration_min=90),
            ],
            route_summary="테스트 동선 요약",
        )

        result = await plan_schedule(self._request(), _RecordingLLM(plan))

        assert len(result.items) == 3
        assert result.item_capacity == 3

    @pytest.mark.asyncio
    async def test_시간을_말하지_않아도_기본_예산으로_다시_잰다(self) -> None:
        """**처음에는 "잴 예산이 없으니 다시 재지 않는다"로 만들었다.** 그런데
        그 경로가 상한 상수 5와 겹쳐 420분 일정을 만들고 있었다(실측 5건).

        기본 예산 240분으로 재면 문화시설(최소 90분) 세 곳은 300분이라 막히고
        두 곳(195분)까지만 들어간다. 판정은 여전히 None이다.
        """

        plan = ScheduleLLMPlan(
            items=[
                _sample_item("place-1", 1, estimated_duration_min=90),
                _sample_item("place-2", 2, estimated_duration_min=90),
                _sample_item("place-3", 3, estimated_duration_min=90),
            ],
            route_summary="테스트 동선 요약",
        )
        request = SchedulePlanningRequest(
            candidates=self._mixed_candidates(),
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 9, 5, 14, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, _RecordingLLM(plan))

        assert [item.place_id for item in result.items] == ["place-1", "place-2"]
        assert result.time_budget_status is None

    @pytest.mark.asyncio
    async def test_줄어든_자리_때문에_빠진_보관함_장소를_안내한다(self) -> None:
        """**상한이 줄면 보관함 판정도 다시 해야 한다.** 안 하면
        `over_capacity_place_names`가 처음 상한(3)으로 계산된 값이라, 줄어든
        자리 때문에 못 들어간 장소가 아무 안내 없이 사라진다.
        """

        plan = ScheduleLLMPlan(
            items=[
                _sample_item("place-1", 1, estimated_duration_min=90),
                _sample_item("place-2", 2, estimated_duration_min=90),
                _sample_item("place-3", 3, estimated_duration_min=90),
            ],
            route_summary="테스트 동선 요약",
        )
        request = SchedulePlanningRequest(
            candidates=self._mixed_candidates(),
            must_include_place_ids=["place-1", "place-2", "place-3"],
            conditions=UserConditions(time_available=180),
            visit_datetime=datetime(2026, 9, 5, 14, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, _RecordingLLM(plan))

        assert result.item_capacity == 2
        assert result.over_capacity_place_names == ["장소 place-3"]


class Test시간을_말하지_않은_턴:
    """실측 재현 — 시간을 말하지 않으면 258~420분 일정이 안내 없이 나갔다.

    TP-238·239·242·244가 만든 장치 넷이 모두 `time_available`이 있을 때만
    작동했다. 상한은 상수 `(3, 5)`, 배분은 `budget_min is None`에서 그냥 반환,
    판정과 안내는 None이었다. 2026-09-07 지표 13턴 중 5턴이 그 경로였다.

    기본 예산 240분을 **개수 상한과 배분에만** 쓴다. 판정과 말풍선은 그대로
    조용하다 — 말하지 않은 시간을 지켰다고도 넘겼다고도 하지 않는다.
    """

    @staticmethod
    def _cultural(count: int) -> list[RecommendationItem]:
        return [
            _candidate(f"place-{index}", category="cultural_facility")
            for index in range(1, count + 1)
        ]

    @pytest.mark.asyncio
    async def test_문화시설_네_곳_제안이_예산_안으로_들어온다(self) -> None:
        """**실측 420분 케이스의 모양이다.** 문화시설 4곳 x 90분 + 이동이면
        390분인데, 예전에는 상한이 상수 5라 그대로 통과했다.

        기본 예산 240분(허용 오차 30 포함 270)으로 재면 두 곳까지다 —
        90x2 + 15 = 195. 세 곳은 300분이라 막힌다.
        """

        plan = ScheduleLLMPlan(
            items=[
                _sample_item(f"place-{index}", index, estimated_duration_min=90)
                for index in range(1, 5)
            ],
            route_summary="테스트 동선 요약",
        )
        request = SchedulePlanningRequest(
            candidates=self._cultural(4),
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 9, 5, 14, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, _RecordingLLM(plan))

        assert len(result.items) == 2
        assert result.total_duration_min == 90 * 2 + 15
        assert result.total_duration_min <= 240 + SCHEDULE_TIME_TOLERANCE_MIN

    @pytest.mark.asyncio
    async def test_판정과_말풍선은_여전히_조용하다(self) -> None:
        """**여기가 가정과 판정을 가르는 자리다.** 기본 예산으로 편성을 다듬되,
        사용자가 말하지 않은 시간을 근거로 되묻지 않는다. `classify_budget()`이
        None을 그대로 None으로 두는 팀 결정을 이 변경이 흔들지 않아야 한다.
        """

        from app.services.runtime.response_composer import compose_schedule_message

        plan = ScheduleLLMPlan(
            items=[
                _sample_item(f"place-{index}", index, estimated_duration_min=90)
                for index in range(1, 5)
            ],
            route_summary="테스트 동선 요약",
        )
        request = SchedulePlanningRequest(
            candidates=self._cultural(4),
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 9, 5, 14, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, _RecordingLLM(plan))
        message = compose_schedule_message(result, time_available_min=None)

        assert result.time_budget_status is None
        assert "말씀하셨는데" not in message
        assert "짧아요" not in message
        assert "길어졌어요" not in message


class TestDeriveItemCapacity:
    """TP-239 완료 조건 — 개수 상한이 예산에서 유도되고 결과에 실려 나간다."""

    @pytest.mark.asyncio
    async def test_두시간_요청에_네곳이_나오지_않는다(self) -> None:
        """옛 버킷은 120분에 2~4곳이었다. 관광지 최소 60분·이동 15분이면 4곳은
        최소 285분이라 애초에 지킬 수 없는 상한이었다."""

        llm = _RecordingLLM(_plan_of("place-1", "place-2"))
        request = SchedulePlanningRequest(
            candidates=_three_candidates(),
            conditions=UserConditions(time_available=120),
            visit_datetime=datetime(2026, 9, 4, 13, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, llm)

        assert result.item_capacity == 2
        assert llm.received_request is not None

    @pytest.mark.asyncio
    async def test_후보가_가까우면_상한이_늘어난다(self) -> None:
        """**폴백 15분이 아니라 이번 후보들의 실제 거리를 쓴다는 증거다.**

        같은 3시간 요청인데 후보가 서로 2km면 3곳이 안 들어가고, 도보 거리면 들어간다.
        """

        near = {("place-1", "place-2"): 0.15, ("place-2", "place-3"): 0.15,
                ("place-1", "place-3"): 0.2}
        far = {("place-1", "place-2"): 2.0, ("place-2", "place-3"): 2.0,
               ("place-1", "place-3"): 3.0}

        async def capacity(distances: dict) -> int | None:
            request = SchedulePlanningRequest(
                candidates=_three_candidates(),
                conditions=UserConditions(time_available=180),
                visit_datetime=datetime(2026, 9, 4, 13, 0, tzinfo=_KST),
                pairwise_distances_km=distances,
            )
            result = await plan_schedule(request, _RecordingLLM(_sample_plan()))
            return result.item_capacity

        assert await capacity(near) == 3
        assert await capacity(far) == 2

    @pytest.mark.asyncio
    async def test_상한이_줄면_보관함_안내가_그_수를_말한다(self) -> None:
        """**모듈 경계를 넘는 계약이다.** planner가 상한을 실어 보내고
        response_composer가 그 값으로 문구를 만든다 — 화면은 후보를 몰라서 상한을
        다시 계산할 수 없다.
        """

        from app.services.runtime.response_composer import compose_schedule_message

        llm = _RecordingLLM(_plan_of("place-1", "place-2"))
        request = SchedulePlanningRequest(
            candidates=_three_candidates(),
            must_include_place_ids=["place-1", "place-2", "place-3"],
            conditions=UserConditions(time_available=150),
            visit_datetime=datetime(2026, 9, 4, 13, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, llm)
        message = compose_schedule_message(result, time_available_min=150)

        assert result.item_capacity == 2
        assert result.over_capacity_place_names == ["장소 place-3"]
        assert "한 번에 2곳까지만 넣을 수 있어서" in message

    @pytest.mark.asyncio
    async def test_부분_재편성에는_상한이_실리지_않는다(self) -> None:
        """그때 개수는 유지 항목과 교체 대상이 정한다 — 상한이 관여하지 않는다."""

        llm = _RecordingFillLLM(
            SchedulePartialLLMPlan(new_items=[_sample_item("place-2", 2)])
        )
        request = SchedulePartialFillRequest(
            pinned_items=[_pinned("place-1", 1)],
            target_orders=[2],
            candidates=[_candidate("place-2")],
            conditions=UserConditions(time_available=180),
            visit_datetime=datetime(2026, 9, 4, 13, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_partial_schedule(request, llm)

        assert result.item_capacity is None

    @pytest.mark.asyncio
    async def test_LLM이_상한을_넘겨_주면_잘라낸다(self) -> None:
        """**프롬프트로 부탁만 하면 안 막힌다** (SCHEDULE-07과 같은 철학).

        고치기 전 실측: 상한 2곳인 120분 요청에 LLM이 4곳을 주면 4곳 285분이
        그대로 나갔다. ScheduleLLMPlan의 max_length는 하드 캡(5)이라 못 막는다.
        """

        llm = _RecordingLLM(_plan_of("place-1", "place-2", "place-3", "place-4"))
        request = SchedulePlanningRequest(
            candidates=[_candidate(f"place-{i}") for i in range(1, 6)],
            conditions=UserConditions(time_available=120),
            visit_datetime=datetime(2026, 9, 4, 13, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, llm)

        assert result.item_capacity == 2
        assert len(result.items) == 2
        assert [item.order for item in result.items] == [1, 2]
        assert result.time_budget_status is ScheduleBudgetStatus.WITHIN

    @pytest.mark.asyncio
    async def test_잘린_자리에_있던_보관함_장소는_되돌아온다(self) -> None:
        """자르기와 되돌리기를 따로 만들지 않았다는 확인.

        상한 2곳인데 LLM이 담아둔 place-3을 세 번째에 놓으면, 자르기가 그것을
        떨어뜨리고 되돌리기가 담지 않은 자리와 맞바꾼다.
        """

        llm = _SequenceLLM(
            _plan_of("place-1", "place-2", "place-3"),
            _plan_of("place-1", "place-2", "place-3"),
        )
        request = SchedulePlanningRequest(
            candidates=[_candidate(f"place-{i}") for i in range(1, 6)],
            must_include_place_ids=["place-3"],
            conditions=UserConditions(time_available=120),
            visit_datetime=datetime(2026, 9, 4, 13, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, llm)

        assert len(result.items) == 2
        assert "place-3" in [item.place_id for item in result.items]
        assert result.omitted_saved_place_names == []


class Test일정_항목의_사진:
    """편성이 후보의 사진을 일정 항목으로 옮기는지. (일정 화면 카드용)

    **화면이 장소별로 다시 조회하지 않게 하려는 것이다.** `/chat/place-details`로도
    사진을 얻을 수 있지만 그 경로는 INFO 전체(이름 재해석 + 외부 조회 + 취향
    인사이트)를 타므로, 정류장 수만큼 부르면 일정을 열 때마다 외부 호출이 그 수만큼
    나간다. 후보는 편성 시점에 이미 사진을 들고 있다.
    """

    @pytest.mark.asyncio
    async def test_후보의_사진이_일정_항목에_실린다(self) -> None:
        plan = ScheduleLLMPlan(
            items=[
                _sample_item("place-1", 1),
                _sample_item("place-2", 2),
                _sample_item("place-3", 3),
            ],
            route_summary="테스트 동선 요약",
        )
        llm = _RecordingLLM(plan)
        request = SchedulePlanningRequest(
            candidates=[
                _candidate(
                    "place-1",
                    image_url="https://tong.visitkorea.or.kr/a.jpg",
                    image_url_fallback="https://tong.visitkorea.or.kr/a-big.jpg",
                ),
                _candidate("place-2"),
                _candidate("place-3"),
            ],
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 7, 19, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, llm)

        assert result.items[0].image_url == "https://tong.visitkorea.or.kr/a.jpg"
        assert result.items[0].image_url_fallback == "https://tong.visitkorea.or.kr/a-big.jpg"
        # 사진이 없는 후보는 None 그대로다 — 화면이 자리표시를 그린다.
        assert result.items[1].image_url is None
        assert result.items[1].image_url_fallback is None

    @pytest.mark.asyncio
    async def test_유지된_항목은_자기가_들고_온_사진을_쓴다(self) -> None:
        """부분 재편성의 pinned 항목은 후보 목록에 없다.

        그래서 후보에서 사진을 찾으면 유지한 자리가 전부 자리표시로 바뀐다(실사용
        재현 — "특정 장소 빼고 다시 짜줘" 뒤 새로 고른 자리만 사진이 나왔다).
        호출부가 pinned 항목에 채워 보낸 사진을 그대로 실어야 한다. 사진이 없는
        pinned 항목은 None 그대로다.
        """
        pinned = [
            _pinned("place-1", 1).model_copy(
                update={
                    "image_url": "https://tong.visitkorea.or.kr/a.jpg",
                    "image_url_fallback": "https://tong.visitkorea.or.kr/a-big.jpg",
                }
            ),
            _pinned("place-3", 3),
        ]
        llm = _RecordingFillLLM(SchedulePartialLLMPlan(new_items=[_sample_item("place-2", 2)]))
        request = SchedulePartialFillRequest(
            pinned_items=pinned,
            target_orders=[2],
            candidates=[_candidate("place-2", image_url="https://tong.visitkorea.or.kr/b.jpg")],
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 11, 15, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_partial_schedule(request, llm)

        by_id = {item.place_id: item for item in result.items}
        assert by_id["place-2"].image_url == "https://tong.visitkorea.or.kr/b.jpg"
        assert by_id["place-1"].image_url == "https://tong.visitkorea.or.kr/a.jpg"
        assert by_id["place-1"].image_url_fallback == "https://tong.visitkorea.or.kr/a-big.jpg"
        assert by_id["place-3"].image_url is None
        assert by_id["place-3"].image_url_fallback is None

    @pytest.mark.asyncio
    async def test_채울_후보가_없어도_유지된_항목의_사진은_남는다(self) -> None:
        """새로 채울 후보가 없어 유지 항목만으로 결과를 만드는 경로도 같다."""
        pinned = [
            _pinned("place-1", 1).model_copy(
                update={"image_url": "https://tong.visitkorea.or.kr/a.jpg"}
            ),
            _pinned("place-3", 3),
        ]
        llm = _RecordingFillLLM(SchedulePartialLLMPlan(new_items=[_sample_item("place-2", 2)]))
        request = SchedulePartialFillRequest(
            pinned_items=pinned,
            target_orders=[2],
            candidates=[],
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 11, 15, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_partial_schedule(request, llm)

        by_id = {item.place_id: item for item in result.items}
        assert set(by_id) == {"place-1", "place-3"}
        assert by_id["place-1"].image_url == "https://tong.visitkorea.or.kr/a.jpg"


class Test붙어_있는_곳은_짧게_머문다:
    """TP-243 — 사용자 문의 원문: "5분 거리 이내인 세 장소는 묶어서 1시간 반으로".

    **묶기 요청처럼 보이지만 체류시간 규칙 요청이다.** 골목에 붙어 있는 곳들을
    각각 한 시간씩 앉아 있게 만들던 것은 분류 최소값 클램프였다. 거리만 다르고
    나머지가 같은 두 요청을 나란히 두어, 바뀐 것이 거리 하나임을 보인다.
    """

    @staticmethod
    def _request(km: float) -> SchedulePlanningRequest:
        place_ids = [f"place-{i}" for i in range(1, 5)]
        return SchedulePlanningRequest(
            candidates=[_candidate(place_id) for place_id in place_ids],
            conditions=UserConditions(time_available=180),
            visit_datetime=datetime(2026, 9, 5, 14, 0, tzinfo=_KST),
            pairwise_distances_km={
                (a, b): km
                for index, a in enumerate(place_ids)
                for b in place_ids[index + 1 :]
            },
        )

    @staticmethod
    def _plan() -> ScheduleLLMPlan:
        return ScheduleLLMPlan(
            items=[
                _sample_item(f"place-{i}", i, estimated_duration_min=60)
                for i in range(1, 5)
            ],
            route_summary="테스트 동선 요약",
        )

    @pytest.mark.asyncio
    async def test_도보_거리면_네_곳이_짧은_체류로_들어간다(self) -> None:
        """0.15km는 도보 3분이라 묶이고, 체류 최소값이 45분까지 내려간다."""

        result = await plan_schedule(self._request(0.15), _RecordingLLM(self._plan()))

        assert len(result.items) == 4
        assert [item.estimated_duration_min for item in result.items] == [45, 45, 45, 45]
        assert result.time_budget_status is ScheduleBudgetStatus.WITHIN

    @pytest.mark.asyncio
    async def test_도보_기준을_넘으면_세_곳으로_줄고_60분에서_멈춘다(self) -> None:
        """**대조군.** 거리 말고는 위와 모든 입력이 같다.

        0.5km는 도보 8분이라 묶음 기준(5분) 밖이다. **이동시간 차이로는 설명되지
        않는 대조군이다** — 8분이면 예산에 여유가 있는데도(60x3 + 8x2 = 196분)
        네 곳이 안 되는 이유는 분류 최소값 60분이 바닥이기 때문이다. 2km처럼 먼
        값을 쓰면 이동이 커져서 줄어든 것인지 묶이지 않아 줄어든 것인지 갈리지
        않는다.
        """

        result = await plan_schedule(self._request(0.5), _RecordingLLM(self._plan()))

        assert len(result.items) == 3
        assert [item.estimated_duration_min for item in result.items] == [60, 60, 60]
        assert result.time_budget_status is ScheduleBudgetStatus.WITHIN

    @pytest.mark.asyncio
    async def test_묶음_번호가_항목에_실린다(self) -> None:
        """화면이 한 묶음으로 그리려면 번호가 항목에 있어야 한다(TP-243).

        배열 모양은 그대로 두고 번호만 얹는다 — 저장된 옛 스냅샷을 읽는 경로가
        두 모양을 다 읽지 않아도 되게 하려는 것이다.
        """

        result = await plan_schedule(self._request(0.15), _RecordingLLM(self._plan()))

        assert [item.cluster_id for item in result.items] == [1, 1, 1, 1]

    @pytest.mark.asyncio
    async def test_묶이지_않으면_번호가_없다(self) -> None:
        """**대조군.** 번호가 늘 실리면 화면이 안 붙은 곳까지 묶어서 그린다."""

        result = await plan_schedule(self._request(0.5), _RecordingLLM(self._plan()))

        assert [item.cluster_id for item in result.items] == [None, None, None]

    @pytest.mark.asyncio
    async def test_부분_재편성에도_번호가_실린다(self) -> None:
        """같은 일정을 어떤 턴에서 보느냐에 따라 화면이 다르게 그리면 안 된다.

        **체류시간은 여전히 안 건드린다** — 유지하기로 한 자리의 60분이 그대로다
        (`_draft_from_schedule_item()`의 "그대로 뒀다는 약속"). 이 턴에서 바뀌는
        것은 표시용 묶음 번호뿐이다.
        """

        pinned = [_pinned("place-1", 1), _pinned("place-3", 3)]
        llm = _RecordingFillLLM(
            SchedulePartialLLMPlan(new_items=[_sample_item("place-2", 2)])
        )
        place_ids = ["place-1", "place-2", "place-3"]
        request = SchedulePartialFillRequest(
            pinned_items=pinned,
            target_orders=[2],
            candidates=[_candidate("place-2")],
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 11, 15, 0, tzinfo=_KST),
            pairwise_distances_km={
                (a, b): 0.15
                for index, a in enumerate(place_ids)
                for b in place_ids[index + 1 :]
            },
        )

        result = await plan_partial_schedule(request, llm)

        assert [item.cluster_id for item in result.items] == [1, 1, 1]
        assert [item.estimated_duration_min for item in result.items] == [60, 60, 60]
