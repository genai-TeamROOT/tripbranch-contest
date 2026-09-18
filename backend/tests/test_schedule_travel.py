from __future__ import annotations

import itertools
from dataclasses import replace

import pytest

from app.domain.schedule_travel import (
    ScheduleTravelCandidate,
    ScheduleTravelPair,
    ScheduleTravelWarning,
    TravelConfidence,
)
from app.domain.travel_route import (
    GeoCoordinate,
    RouteDestination,
    RouteSource,
    RouteStatus,
    TravelMode,
    TravelRoute,
    TravelRouteBatch,
)
from app.errors import ProviderTimeoutError
from app.providers.contracts import ProviderSource, ProviderStatus, provider_result
from app.providers.walking_route import FakeWalkingRouteProvider
from app.schemas import Transport
from app.tools.schedule_travel import (
    SCHEDULE_TRAVEL_DUPLICATE_PAIR_WARNING,
    SCHEDULE_TRAVEL_MEASURE_BUDGET_EXCEEDED_WARNING,
    SCHEDULE_TRAVEL_MEASURE_FAILED_WARNING,
    SCHEDULE_TRAVEL_MEASURE_UNAVAILABLE_WARNING,
    SCHEDULE_TRAVEL_SELF_PAIR_WARNING,
    SCHEDULE_TRAVEL_UNKNOWN_PLACE_WARNING,
    ModeJudgmentContext,
    SegmentModeInput,
    build_segment_inputs,
    estimate_schedule_travel_edges,
    measure_schedule_travel_edges,
    select_modes_for_segments,
)
from app.tools.travel_route import TravelRouteProviders, TravelRouteTool

WALKING_SPEED_MPS = 1.0
TRANSIT_SPEED_MPS = 5.5
DRIVING_SPEED_MPS = 5.5
WALK_THRESHOLD_MIN = 20


def _candidate(place_id: str, longitude: float) -> ScheduleTravelCandidate:
    return ScheduleTravelCandidate(
        place_id=place_id,
        coordinate=GeoCoordinate(latitude=37.5, longitude=longitude),
    )


def _estimate(
    candidates: list[ScheduleTravelCandidate],
    pairs: list[ScheduleTravelPair],
    *,
    transport: Transport | None = None,
):
    return estimate_schedule_travel_edges(
        candidates=candidates,
        pairs=pairs,
        transport=transport,
        walking_speed_mps=WALKING_SPEED_MPS,
        transit_speed_mps=TRANSIT_SPEED_MPS,
        driving_speed_mps=DRIVING_SPEED_MPS,
        walk_transfer_threshold_min=WALK_THRESHOLD_MIN,
    )


def test_requested_full_directional_pairs_create_six_edges_for_three_places() -> None:
    candidates = [_candidate("a", 126.90), _candidate("b", 126.91), _candidate("c", 126.92)]
    pairs = [
        ScheduleTravelPair(first.place_id, second.place_id)
        for first, second in itertools.permutations(candidates, 2)
    ]

    result = _estimate(candidates, pairs)

    assert len(result.edges) == 6
    assert set(result.edge_by_pair()) == {
        ("a", "b"),
        ("a", "c"),
        ("b", "a"),
        ("b", "c"),
        ("c", "a"),
        ("c", "b"),
    }


def test_only_requested_pairs_are_created() -> None:
    candidates = [_candidate("a", 126.90), _candidate("b", 126.91), _candidate("c", 126.92)]

    result = _estimate(
        candidates,
        [ScheduleTravelPair("a", "b"), ScheduleTravelPair("b", "c")],
    )

    assert set(result.edge_by_pair()) == {("a", "b"), ("b", "c")}


@pytest.mark.parametrize("transport", [None, Transport.PUBLIC])
def test_unspecified_or_public_transport_walks_short_segment(
    transport: Transport | None,
) -> None:
    candidates = [_candidate("a", 126.900), _candidate("b", 126.901)]

    [edge] = _estimate(
        candidates, [ScheduleTravelPair("a", "b")], transport=transport
    ).edges

    assert edge.mode is TravelMode.WALKING


@pytest.mark.parametrize("transport", [None, Transport.PUBLIC])
def test_unspecified_or_public_transport_uses_transit_for_long_segment(
    transport: Transport | None,
) -> None:
    candidates = [_candidate("a", 126.90), _candidate("b", 127.00)]

    [edge] = _estimate(
        candidates, [ScheduleTravelPair("a", "b")], transport=transport
    ).edges

    assert edge.mode is TravelMode.TRANSIT


def test_explicit_walking_does_not_silently_switch_long_segment_to_transit() -> None:
    candidates = [_candidate("a", 126.90), _candidate("b", 127.00)]

    [edge] = _estimate(
        candidates,
        [ScheduleTravelPair("a", "b")],
        transport=Transport.WALK,
    ).edges

    assert edge.mode is TravelMode.WALKING


def test_explicit_car_uses_driving() -> None:
    candidates = [_candidate("a", 126.900), _candidate("b", 126.901)]

    [edge] = _estimate(
        candidates,
        [ScheduleTravelPair("a", "b")],
        transport=Transport.CAR,
    ).edges

    assert edge.mode is TravelMode.DRIVING


def test_estimated_edge_exposes_source_status_confidence_and_rounded_minutes() -> None:
    candidates = [_candidate("a", 126.9000), _candidate("b", 126.9001)]

    [edge] = _estimate(candidates, [ScheduleTravelPair("a", "b")]).edges

    assert edge.distance_m > 0
    assert edge.duration_min == 1
    assert edge.status is RouteStatus.SUCCESS
    assert edge.source is RouteSource.STRAIGHT_LINE_ESTIMATE
    assert edge.confidence is TravelConfidence.LOW


def test_distinct_places_at_same_coordinate_can_have_zero_duration() -> None:
    candidates = [_candidate("a", 126.90), _candidate("b", 126.90)]

    [edge] = _estimate(candidates, [ScheduleTravelPair("a", "b")]).edges

    assert edge.distance_m == 0
    assert edge.duration_min == 0


def test_duplicate_candidate_ids_are_rejected() -> None:
    candidates = [_candidate("same", 126.90), _candidate("same", 126.91)]

    with pytest.raises(ValueError, match="중복"):
        _estimate(candidates, [])


def test_invalid_pairs_are_skipped_and_reported_as_warnings() -> None:
    candidates = [_candidate("a", 126.90), _candidate("b", 126.91)]
    pairs = [
        ScheduleTravelPair("a", "b"),
        ScheduleTravelPair("a", "b"),
        ScheduleTravelPair("a", "a"),
        ScheduleTravelPair("a", "missing"),
    ]

    result = _estimate(candidates, pairs)

    assert len(result.edges) == 1
    assert result.warnings == (
        ScheduleTravelWarning(SCHEDULE_TRAVEL_DUPLICATE_PAIR_WARNING, "a", "b"),
        ScheduleTravelWarning(SCHEDULE_TRAVEL_SELF_PAIR_WARNING, "a", "a"),
        ScheduleTravelWarning(SCHEDULE_TRAVEL_UNKNOWN_PLACE_WARNING, "a", "missing"),
    )


@pytest.mark.parametrize(
    ("walking", "transit", "driving", "threshold"),
    [
        (0.0, 5.5, 5.5, 20),
        (1.0, 0.0, 5.5, 20),
        (1.0, 5.5, 0.0, 20),
        (1.0, 5.5, 5.5, 0),
    ],
)
def test_non_positive_speed_or_threshold_is_rejected(
    walking: float,
    transit: float,
    driving: float,
    threshold: int,
) -> None:
    with pytest.raises(ValueError):
        estimate_schedule_travel_edges(
            candidates=[],
            pairs=[],
            transport=None,
            walking_speed_mps=walking,
            transit_speed_mps=transit,
            driving_speed_mps=driving,
            walk_transfer_threshold_min=threshold,
        )


# --- 실측(measure_schedule_travel_edges) ------------------------------------

_MEASURED_SOURCE = {
    TravelMode.WALKING: RouteSource.KAKAO_WALKING,
    TravelMode.DRIVING: RouteSource.NAVER_DRIVING,
    TravelMode.TRANSIT: RouteSource.KAKAO_TRANSIT,
}


class _StubRouteProvider:
    """목적지별로 성공·실패를 미리 정해 돌려주는 경로 Provider."""

    def __init__(
        self,
        *,
        failed_place_ids: frozenset[str] = frozenset(),
        duration_seconds: int = 600,
    ) -> None:
        self._failed_place_ids = failed_place_ids
        self._duration_seconds = duration_seconds
        self.calls: list[tuple[GeoCoordinate, tuple[str, ...], TravelMode]] = []

    async def get_routes(
        self,
        origin: GeoCoordinate,
        destinations: tuple[RouteDestination, ...],
        *,
        mode: TravelMode = TravelMode.WALKING,
        radius_m: int | None = None,
    ):
        self.calls.append(
            (origin, tuple(item.place_id for item in destinations), mode)
        )
        routes = tuple(
            TravelRoute(
                place_id=item.place_id,
                mode=mode,
                status=RouteStatus.NO_DATA,
                source=_MEASURED_SOURCE[mode],
                error_code="stub_no_route",
            )
            if item.place_id in self._failed_place_ids
            else TravelRoute(
                place_id=item.place_id,
                mode=mode,
                status=RouteStatus.SUCCESS,
                source=_MEASURED_SOURCE[mode],
                distance_m=1_200,
                duration_seconds=self._duration_seconds,
            )
            for item in destinations
        )
        successful = sum(route.status is RouteStatus.SUCCESS for route in routes)
        return provider_result(
            TravelRouteBatch(routes=routes),
            source=ProviderSource.KAKAO_WALKING_ROUTE,
            status=ProviderStatus.SUCCESS
            if successful == len(routes)
            else ProviderStatus.PARTIAL,
        )


class _RaisingRouteProvider:
    """Provider가 통째로 죽은 상황."""

    async def get_routes(self, origin, destinations, *, mode=TravelMode.WALKING, radius_m=None):
        raise ProviderTimeoutError("Stub Route")


def _tool(primary, *, fallback=None, modes=(TravelMode.WALKING,)) -> TravelRouteTool:
    return TravelRouteTool(
        {
            mode: TravelRouteProviders(primary=primary, fallback=fallback)
            for mode in modes
        }
    )


def _estimated(candidates: list[ScheduleTravelCandidate], pairs: list[ScheduleTravelPair]):
    return _estimate(candidates, pairs).edges


@pytest.mark.asyncio
async def test_measured_edges_carry_provider_source_and_high_confidence() -> None:
    candidates = [_candidate("a", 126.900), _candidate("b", 126.901)]
    estimated = _estimated(candidates, [ScheduleTravelPair("a", "b")])

    result = await measure_schedule_travel_edges(
        tool=_tool(_StubRouteProvider(duration_seconds=630)),
        candidates=candidates,
        estimated_edges=estimated,
        max_measured_segments=10,
    )

    [edge] = result.edges
    assert edge.source is RouteSource.KAKAO_WALKING
    assert edge.confidence is TravelConfidence.HIGH
    assert edge.distance_m == 1_200
    # 630초는 10.5분이라 11분으로 올린다.
    assert edge.duration_min == 11
    assert edge.error_code is None
    assert result.warnings == ()


@pytest.mark.asyncio
async def test_opposite_directions_are_queried_separately() -> None:
    candidates = [_candidate("a", 126.900), _candidate("b", 126.901)]
    estimated = _estimated(
        candidates, [ScheduleTravelPair("a", "b"), ScheduleTravelPair("b", "a")]
    )
    provider = _StubRouteProvider()

    result = await measure_schedule_travel_edges(
        tool=_tool(provider),
        candidates=candidates,
        estimated_edges=estimated,
        max_measured_segments=10,
    )

    assert set(result.edge_by_pair()) == {("a", "b"), ("b", "a")}
    # 출발지가 다르므로 그룹이 갈리고, 각 호출의 목적지는 하나씩이다.
    assert [destinations for _, destinations, _ in provider.calls] == [("b",), ("a",)]
    assert provider.calls[0][0] != provider.calls[1][0]


@pytest.mark.asyncio
async def test_segments_with_different_modes_are_measured_in_one_request() -> None:
    candidates = [_candidate("a", 126.900), _candidate("b", 126.901), _candidate("c", 127.000)]
    # a→b는 가까워 도보, a→c는 멀어 대중교통으로 추정된다.
    estimated = _estimated(
        candidates, [ScheduleTravelPair("a", "b"), ScheduleTravelPair("a", "c")]
    )
    assert {edge.mode for edge in estimated} == {TravelMode.WALKING, TravelMode.TRANSIT}
    provider = _StubRouteProvider()

    result = await measure_schedule_travel_edges(
        tool=_tool(provider, modes=(TravelMode.WALKING, TravelMode.TRANSIT)),
        candidates=candidates,
        estimated_edges=estimated,
        max_measured_segments=10,
    )

    measured = result.edge_by_pair()
    assert measured[("a", "b")].mode is TravelMode.WALKING
    assert measured[("a", "c")].mode is TravelMode.TRANSIT
    assert all(edge.confidence is TravelConfidence.HIGH for edge in result.edges)
    # 출발지는 같지만 이동수단이 달라 그룹이 갈린다.
    assert [mode for _, _, mode in provider.calls] == [
        TravelMode.WALKING,
        TravelMode.TRANSIT,
    ]


@pytest.mark.asyncio
async def test_measured_mode_never_differs_from_estimated_mode() -> None:
    candidates = [_candidate("a", 126.90), _candidate("b", 127.00)]
    estimated = _estimated(candidates, [ScheduleTravelPair("a", "b")])
    assert estimated[0].mode is TravelMode.TRANSIT

    result = await measure_schedule_travel_edges(
        tool=_tool(_StubRouteProvider(), modes=(TravelMode.WALKING, TravelMode.TRANSIT)),
        candidates=candidates,
        estimated_edges=estimated,
        max_measured_segments=10,
    )

    [edge] = result.edges
    assert edge.mode is TravelMode.TRANSIT


@pytest.mark.asyncio
async def test_failed_segment_keeps_estimate_and_reports_error_code() -> None:
    candidates = [_candidate("a", 126.900), _candidate("b", 126.901)]
    [estimate] = _estimated(candidates, [ScheduleTravelPair("a", "b")])

    result = await measure_schedule_travel_edges(
        tool=_tool(_StubRouteProvider(failed_place_ids=frozenset({"b"}))),
        candidates=candidates,
        estimated_edges=[estimate],
        max_measured_segments=10,
    )

    [edge] = result.edges
    assert edge.duration_min == estimate.duration_min
    assert edge.source is RouteSource.STRAIGHT_LINE_ESTIMATE
    assert edge.confidence is TravelConfidence.LOW
    assert edge.error_code == "stub_no_route"
    assert result.warnings == (
        ScheduleTravelWarning(SCHEDULE_TRAVEL_MEASURE_FAILED_WARNING, "a", "b"),
    )


@pytest.mark.asyncio
async def test_dead_provider_keeps_estimate_and_reports_unavailable() -> None:
    candidates = [_candidate("a", 126.900), _candidate("b", 126.901)]
    [estimate] = _estimated(candidates, [ScheduleTravelPair("a", "b")])

    result = await measure_schedule_travel_edges(
        # 자동차·대중교통처럼 추정 fallback이 등록되지 않은 구성이다(D-042).
        tool=_tool(_RaisingRouteProvider()),
        candidates=candidates,
        estimated_edges=[estimate],
        max_measured_segments=10,
    )

    [edge] = result.edges
    assert edge.duration_min == estimate.duration_min
    assert edge.source is RouteSource.STRAIGHT_LINE_ESTIMATE
    assert edge.error_code is not None
    assert result.warnings == (
        ScheduleTravelWarning(SCHEDULE_TRAVEL_MEASURE_UNAVAILABLE_WARNING, "a", "b"),
    )


@pytest.mark.asyncio
async def test_tool_level_walking_fallback_is_reported_as_estimate() -> None:
    candidates = [_candidate("a", 126.900), _candidate("b", 126.901)]
    [estimate] = _estimated(candidates, [ScheduleTravelPair("a", "b")])

    result = await measure_schedule_travel_edges(
        # 도보만 추정 fallback이 붙어 있어 Tool 안에서 값이 채워져 돌아온다.
        tool=_tool(
            _StubRouteProvider(failed_place_ids=frozenset({"b"})),
            fallback=FakeWalkingRouteProvider(walking_speed_mps=WALKING_SPEED_MPS),
        ),
        candidates=candidates,
        estimated_edges=[estimate],
        max_measured_segments=10,
    )

    [edge] = result.edges
    # 값이 채워져 왔어도 실측이 아니므로 실측으로 올리지 않는다.
    assert edge.source is RouteSource.STRAIGHT_LINE_ESTIMATE
    assert edge.confidence is TravelConfidence.LOW
    assert edge.error_code == "stub_no_route"
    assert result.warnings == (
        ScheduleTravelWarning(SCHEDULE_TRAVEL_MEASURE_FAILED_WARNING, "a", "b"),
    )


@pytest.mark.asyncio
async def test_segments_beyond_budget_stay_estimated_in_input_order() -> None:
    candidates = [_candidate("a", 126.900), _candidate("b", 126.901), _candidate("c", 126.902)]
    estimated = _estimated(
        candidates,
        [
            ScheduleTravelPair("a", "b"),
            ScheduleTravelPair("b", "c"),
            ScheduleTravelPair("c", "a"),
        ],
    )
    provider = _StubRouteProvider()

    result = await measure_schedule_travel_edges(
        tool=_tool(provider),
        candidates=candidates,
        estimated_edges=estimated,
        max_measured_segments=2,
    )

    measured = result.edge_by_pair()
    assert measured[("a", "b")].confidence is TravelConfidence.HIGH
    assert measured[("b", "c")].confidence is TravelConfidence.HIGH
    assert measured[("c", "a")].confidence is TravelConfidence.LOW
    assert len(provider.calls) == 2
    assert result.warnings == (
        ScheduleTravelWarning(SCHEDULE_TRAVEL_MEASURE_BUDGET_EXCEEDED_WARNING, "c", "a"),
    )


@pytest.mark.asyncio
async def test_segment_without_coordinates_is_not_measured_and_does_not_spend_budget() -> None:
    candidates = [_candidate("a", 126.900), _candidate("b", 126.901)]
    [known] = _estimated(candidates, [ScheduleTravelPair("a", "b")])
    unknown = replace(known, from_place_id="a", to_place_id="missing")
    provider = _StubRouteProvider()

    result = await measure_schedule_travel_edges(
        tool=_tool(provider),
        candidates=candidates,
        estimated_edges=[unknown, known],
        max_measured_segments=1,
    )

    measured = result.edge_by_pair()
    assert measured[("a", "missing")].confidence is TravelConfidence.LOW
    # 좌표를 몰라 건너뛴 구간이 상한을 깎지 않으므로 뒤 구간이 실측된다.
    assert measured[("a", "b")].confidence is TravelConfidence.HIGH
    assert result.warnings == (
        ScheduleTravelWarning(SCHEDULE_TRAVEL_UNKNOWN_PLACE_WARNING, "a", "missing"),
    )


@pytest.mark.asyncio
async def test_measure_rejects_non_positive_budget() -> None:
    with pytest.raises(ValueError):
        await measure_schedule_travel_edges(
            tool=_tool(_StubRouteProvider()),
            candidates=[],
            estimated_edges=[],
            max_measured_segments=0,
        )


# ── 판정 갈아끼우기 자리 (TP-225) ──────────────────────────────────────


class _RecordingJudge:
    """호출됐는지와 무엇을 받았는지를 남기는 가짜 판정."""

    def __init__(self, decided: list[TravelMode] | None = None) -> None:
        self.decided = decided
        self.calls: list[tuple[tuple[SegmentModeInput, ...], ModeJudgmentContext]] = []

    async def judge(self, segments, context):
        self.calls.append((tuple(segments), context))
        if self.decided is None:
            return [TravelMode.WALKING] * len(segments)
        return self.decided


async def _select(
    segments,
    *,
    judge=None,
    transport: Transport | None = None,
):
    return await select_modes_for_segments(
        segments,
        ModeJudgmentContext(transport=transport),
        judge=judge,
        walking_speed_mps=WALKING_SPEED_MPS,
        walk_transfer_threshold_min=WALK_THRESHOLD_MIN,
    )


def test_build_segment_inputs_drops_duplicate_self_and_unknown_pairs() -> None:
    """추려내기가 기존 루프와 같은 세 가지를 거르고 사유를 남긴다."""
    candidates = [_candidate("a", 126.90), _candidate("b", 126.91)]
    pairs = [
        ScheduleTravelPair("a", "b"),
        ScheduleTravelPair("a", "b"),  # 중복
        ScheduleTravelPair("a", "a"),  # 자기 자신
        ScheduleTravelPair("a", "zzz"),  # 좌표를 모르는 장소
        ScheduleTravelPair("b", "a"),
    ]

    segments, warnings = build_segment_inputs(
        candidates, pairs, walking_speed_mps=WALKING_SPEED_MPS
    )

    assert [segment.key for segment in segments] == [("a", "b"), ("b", "a")]
    # order는 `pairs`의 인덱스가 아니라 추려낸 뒤의 순번이다.
    assert [segment.order for segment in segments] == [1, 2]
    assert [warning.code for warning in warnings] == [
        SCHEDULE_TRAVEL_DUPLICATE_PAIR_WARNING,
        SCHEDULE_TRAVEL_SELF_PAIR_WARNING,
        SCHEDULE_TRAVEL_UNKNOWN_PLACE_WARNING,
    ]


def test_build_segment_inputs_reports_walking_minutes_for_each_segment() -> None:
    """실제로 도보로 갈지와 무관하게 "걸으면 몇 분"을 싣는다."""
    candidates = [_candidate("a", 126.90), _candidate("b", 126.95)]

    segments, _ = build_segment_inputs(
        candidates, [ScheduleTravelPair("a", "b")], walking_speed_mps=WALKING_SPEED_MPS
    )

    segment = segments[0]
    assert segment.walk_minutes == pytest.approx(
        segment.distance_m / WALKING_SPEED_MPS / 60, abs=0.05
    )
    # 이 거리는 임계를 넘어 대중교통으로 전환되는 구간이다 — 그래도 도보 기준 분이다.
    assert segment.walk_minutes > WALK_THRESHOLD_MIN


@pytest.mark.asyncio
async def test_select_modes_without_judge_matches_existing_estimate() -> None:
    """판정하는 쪽이 없으면 지금까지의 결과와 정확히 같다. 이 카드의 회귀 기준이다."""
    candidates = [
        _candidate("a", 126.90),
        _candidate("b", 126.905),  # 가까움 → 도보
        _candidate("c", 126.99),  # 멀리 → 대중교통
    ]
    pairs = [ScheduleTravelPair("a", "b"), ScheduleTravelPair("b", "c")]

    segments, _ = build_segment_inputs(
        candidates, pairs, walking_speed_mps=WALKING_SPEED_MPS
    )
    modes = await _select(segments)

    baseline = _estimate(candidates, pairs)
    assert modes == {(edge.from_place_id, edge.to_place_id): edge.mode for edge in baseline.edges}
    # 두 이동수단이 다 나오는 표본이어야 이 동치가 의미를 갖는다.
    assert set(modes.values()) == {TravelMode.WALKING, TravelMode.TRANSIT}


@pytest.mark.asyncio
async def test_judged_modes_actually_change_estimated_edges() -> None:
    """표가 실제로 소비되는지 본다.

    표를 만들었는데 아무도 읽지 않으면 다음 PR에서 LLM을 붙여도 결과가 안 바뀐다.
    그 실패는 테스트도 로그도 통과하므로 여기서 못 박는다.
    """
    candidates = [_candidate("a", 126.90), _candidate("b", 126.905)]
    pairs = [ScheduleTravelPair("a", "b")]

    # 규칙대로면 가까워서 도보인 구간이다.
    assert _estimate(candidates, pairs).edges[0].mode is TravelMode.WALKING

    segments, _ = build_segment_inputs(
        candidates, pairs, walking_speed_mps=WALKING_SPEED_MPS
    )
    judge = _RecordingJudge([TravelMode.TRANSIT])
    modes = await _select(segments, judge=judge)

    result = estimate_schedule_travel_edges(
        candidates=candidates,
        pairs=pairs,
        transport=None,
        modes=modes,
        walking_speed_mps=WALKING_SPEED_MPS,
        transit_speed_mps=TRANSIT_SPEED_MPS,
        driving_speed_mps=DRIVING_SPEED_MPS,
        walk_transfer_threshold_min=WALK_THRESHOLD_MIN,
    )

    assert judge.calls
    assert result.edges[0].mode is TravelMode.TRANSIT
    # 이동수단이 바뀌면 소요시간도 그 속도로 다시 계산돼야 한다.
    assert result.edges[0].duration_min < _estimate(candidates, pairs).edges[0].duration_min


@pytest.mark.asyncio
async def test_select_modes_rejects_wrong_result_count() -> None:
    candidates = [_candidate("a", 126.90), _candidate("b", 126.905), _candidate("c", 126.91)]
    segments, _ = build_segment_inputs(
        candidates,
        [ScheduleTravelPair("a", "b"), ScheduleTravelPair("b", "c")],
        walking_speed_mps=WALKING_SPEED_MPS,
    )

    with pytest.raises(ValueError, match="구간 수와 다릅니다"):
        await _select(segments, judge=_RecordingJudge([TravelMode.WALKING]))


@pytest.mark.asyncio
async def test_select_modes_rejects_unknown_mode_value() -> None:
    """StrEnum이라 문자열이 그대로 통과할 수 있어 여기서 막는다."""
    candidates = [_candidate("a", 126.90), _candidate("b", 126.905)]
    segments, _ = build_segment_inputs(
        candidates, [ScheduleTravelPair("a", "b")], walking_speed_mps=WALKING_SPEED_MPS
    )

    with pytest.raises(ValueError, match="알 수 없는 이동수단"):
        await _select(segments, judge=_RecordingJudge(["보행"]))  # type: ignore[list-item]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transport", "expected"),
    [(Transport.WALK, TravelMode.WALKING), (Transport.CAR, TravelMode.DRIVING)],
)
async def test_explicit_transport_never_asks_the_judge(
    transport: Transport, expected: TravelMode
) -> None:
    """사용자가 말한 이동수단을 판정이 뒤집으면 안 된다."""
    candidates = [_candidate("a", 126.90), _candidate("b", 126.99)]  # 임계를 넘는 거리
    segments, _ = build_segment_inputs(
        candidates, [ScheduleTravelPair("a", "b")], walking_speed_mps=WALKING_SPEED_MPS
    )
    judge = _RecordingJudge([TravelMode.TRANSIT])

    modes = await _select(segments, judge=judge, transport=transport)

    assert judge.calls == []
    assert modes[("a", "b")] is expected


def test_estimate_rejects_modes_table_missing_a_segment() -> None:
    """반쪽짜리 표를 조용히 규칙으로 메우지 않는다."""
    candidates = [_candidate("a", 126.90), _candidate("b", 126.905), _candidate("c", 126.91)]
    pairs = [ScheduleTravelPair("a", "b"), ScheduleTravelPair("b", "c")]

    with pytest.raises(ValueError, match="이동수단 표에 없는 구간"):
        estimate_schedule_travel_edges(
            candidates=candidates,
            pairs=pairs,
            transport=None,
            modes={("a", "b"): TravelMode.WALKING},
            walking_speed_mps=WALKING_SPEED_MPS,
            transit_speed_mps=TRANSIT_SPEED_MPS,
            driving_speed_mps=DRIVING_SPEED_MPS,
            walk_transfer_threshold_min=WALK_THRESHOLD_MIN,
        )
