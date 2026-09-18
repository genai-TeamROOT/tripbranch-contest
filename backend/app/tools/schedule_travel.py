"""일정 구간의 거리·이동시간을 추정하고, 요청된 구간만 실제 경로로 실측한다."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Protocol

from app.domain.schedule_travel import (
    ModeJudgmentContext,
    ScheduleTravelCandidate,
    ScheduleTravelEdge,
    ScheduleTravelEstimateResult,
    ScheduleTravelPair,
    ScheduleTravelWarning,
    SegmentModeInput,
    TravelConfidence,
)
from app.domain.travel_route import (
    RouteDestination,
    RouteSource,
    RouteStatus,
    TravelMode,
    TravelRoute,
)
from app.geo import haversine_km
from app.schemas import Transport
from app.tools.travel_route import TravelRouteQuery, TravelRouteTool

SCHEDULE_TRAVEL_DUPLICATE_PAIR_WARNING = "schedule_travel_duplicate_pair"
SCHEDULE_TRAVEL_SELF_PAIR_WARNING = "schedule_travel_self_pair"
SCHEDULE_TRAVEL_UNKNOWN_PLACE_WARNING = "schedule_travel_unknown_place"
# 구간 하나의 실측이 실패했거나, 경로 Tool이 직선거리 추정으로 이미 메운 경우.
SCHEDULE_TRAVEL_MEASURE_FAILED_WARNING = "schedule_travel_measure_failed"
# 이동수단 Provider가 통째로 죽어 그 그룹의 구간이 하나도 돌아오지 않은 경우.
SCHEDULE_TRAVEL_MEASURE_UNAVAILABLE_WARNING = "schedule_travel_measure_unavailable"
# 실측 구간 수 상한을 넘겨 아예 호출하지 않은 구간.
SCHEDULE_TRAVEL_MEASURE_BUDGET_EXCEEDED_WARNING = "schedule_travel_measure_budget_exceeded"


def _validate_speeds(
    walking_speed_mps: float,
    transit_speed_mps: float,
    driving_speed_mps: float,
    walk_transfer_threshold_min: int,
) -> None:
    if walking_speed_mps <= 0 or transit_speed_mps <= 0 or driving_speed_mps <= 0:
        raise ValueError("이동수단별 속도는 0보다 커야 합니다.")
    if walk_transfer_threshold_min <= 0:
        raise ValueError("도보 전환 임계값은 0보다 커야 합니다.")


def _candidate_index(
    candidates: Sequence[ScheduleTravelCandidate],
) -> dict[str, ScheduleTravelCandidate]:
    result: dict[str, ScheduleTravelCandidate] = {}
    for candidate in candidates:
        if candidate.place_id in result:
            raise ValueError(f"후보 place_id는 중복될 수 없습니다: {candidate.place_id}")
        result[candidate.place_id] = candidate
    return result


# 사용자가 이동수단을 콕 집어 말한 경우. 거리와 무관하게 그대로 가므로 판정하는
# 쪽에 물어볼 것이 없고, 물어보면 사용자가 말한 것을 뒤집을 여지가 생긴다.
JUDGE_SKIPPED_TRANSPORTS = frozenset({Transport.WALK, Transport.CAR})

# 판정이 **고를 수 있는** 이동수단. 자동차는 여기 없다.
#
# 자동차는 사용자가 "차로 간다"고 말했을 때만 쓰는데, 그 경우는 위
# `JUDGE_SKIPPED_TRANSPORTS`에 걸려 판정을 아예 부르지 않는다. 그래서 판정이
# 자동차를 고를 상황 자체가 없다.
#
# **프롬프트에 적는 것만으로는 부족하다.** 거기 적은 것은 부탁이고 이 집합이
# 계약이다 — `TravelMode`에 driving이 있어 어휘 검증만으로는 통과해버리고, 그러면
# 차로 가겠다고 말한 적 없는 사용자에게 자동차 실측이 붙는다.
JUDGEABLE_MODES: frozenset[TravelMode] = frozenset(
    {TravelMode.WALKING, TravelMode.TRANSIT}
)


def _select_mode(
    transport: Transport | None,
    *,
    distance_m: int,
    walking_speed_mps: float,
    walk_transfer_threshold_min: int,
) -> TravelMode:
    if transport is Transport.WALK:
        return TravelMode.WALKING
    if transport is Transport.CAR:
        return TravelMode.DRIVING

    walking_seconds = distance_m / walking_speed_mps
    if walking_seconds <= walk_transfer_threshold_min * 60:
        return TravelMode.WALKING
    return TravelMode.TRANSIT


class ModeJudge(Protocol):
    """구간별 이동수단을 정하는 쪽. LLM 구현은 PR 3에서 붙인다.

    구간 하나씩이 아니라 **표 전체**를 받는다. 누적 도보량처럼 구간 사이 관계를
    보려면 전부 손에 있어야 하고, 구간마다 부르면 호출 수만 늘고 같은 문제가
    남는다.
    """

    async def judge(
        self,
        segments: Sequence[SegmentModeInput],
        context: ModeJudgmentContext,
    ) -> Sequence[TravelMode]: ...


def build_segment_inputs(
    candidates: Sequence[ScheduleTravelCandidate],
    pairs: Sequence[ScheduleTravelPair],
    *,
    walking_speed_mps: float,
) -> tuple[tuple[SegmentModeInput, ...], tuple[ScheduleTravelWarning, ...]]:
    """쓸 수 있는 구간만 추려 판정 입력 표로 만든다.

    중복 쌍·자기 자신 쌍·좌표를 모르는 장소를 걸러내고, 그 사유를 경고로 남긴다.
    걸러내는 기준은 `estimate_schedule_travel_edges()`가 쓰던 것 그대로다.

    **추려내기가 여기 한 곳에만 있어야 한다.** 판정하는 쪽과 구간 정보를 만드는
    쪽이 각자 거르면 "어느 구간이 유효한가"의 답이 둘이 되고, 갈리는 순간 판정
    결과의 키가 실제 구간과 안 맞는다. 그래서 `estimate_schedule_travel_edges()`도
    이 함수를 쓴다.
    """

    if walking_speed_mps <= 0:
        raise ValueError("도보 속도는 0보다 커야 합니다.")

    candidate_by_id = _candidate_index(candidates)
    seen_pairs: set[tuple[str, str]] = set()
    segments: list[SegmentModeInput] = []
    warnings: list[ScheduleTravelWarning] = []

    def _warn(code: str, pair: ScheduleTravelPair) -> None:
        warnings.append(
            ScheduleTravelWarning(
                code=code,
                from_place_id=pair.from_place_id,
                to_place_id=pair.to_place_id,
            )
        )

    for pair in pairs:
        key = (pair.from_place_id, pair.to_place_id)
        if key in seen_pairs:
            _warn(SCHEDULE_TRAVEL_DUPLICATE_PAIR_WARNING, pair)
            continue
        seen_pairs.add(key)

        if pair.from_place_id == pair.to_place_id:
            _warn(SCHEDULE_TRAVEL_SELF_PAIR_WARNING, pair)
            continue

        origin = candidate_by_id.get(pair.from_place_id)
        destination = candidate_by_id.get(pair.to_place_id)
        if origin is None or destination is None:
            _warn(SCHEDULE_TRAVEL_UNKNOWN_PLACE_WARNING, pair)
            continue

        distance_m = round(
            haversine_km(
                origin.coordinate.latitude,
                origin.coordinate.longitude,
                destination.coordinate.latitude,
                destination.coordinate.longitude,
            )
            * 1000
        )
        segments.append(
            SegmentModeInput(
                from_place_id=pair.from_place_id,
                to_place_id=pair.to_place_id,
                order=len(segments) + 1,
                distance_m=distance_m,
                # 판정하는 쪽이 읽을 값이라 소수 한 자리로 끊는다. 규칙 판정은 이
                # 값이 아니라 `distance_m`을 그대로 쓰므로 반올림이 결과를 바꾸지
                # 않는다.
                walk_minutes=round(distance_m / walking_speed_mps / 60, 1),
            )
        )

    return tuple(segments), tuple(warnings)


async def select_modes_for_segments(
    segments: Sequence[SegmentModeInput],
    context: ModeJudgmentContext,
    *,
    judge: ModeJudge | None,
    walking_speed_mps: float,
    walk_transfer_threshold_min: int,
) -> dict[tuple[str, str], TravelMode]:
    """구간별 이동수단을 정해 표로 돌려준다.

    `judge`가 없으면 구간마다 `_select_mode()`를 부른다 — **지금까지와 같은
    결과다.** 판정하는 쪽을 붙이기 전까지 이 경로로 돈다.

    사용자가 도보나 자동차를 명시했으면 `judge`가 있어도 부르지 않는다. 사용자가
    말한 이동수단을 판정이 뒤집으면 안 되고, 그 두 경우는 거리와도 무관해서
    물어볼 것이 없다(`_select_mode()`의 맨 위 두 줄과 같은 이유).
    """

    if not segments:
        return {}

    if judge is None or context.transport in JUDGE_SKIPPED_TRANSPORTS:
        return {
            segment.key: _select_mode(
                context.transport,
                distance_m=segment.distance_m,
                walking_speed_mps=walking_speed_mps,
                walk_transfer_threshold_min=walk_transfer_threshold_min,
            )
            for segment in segments
        }

    decided = await judge.judge(segments, context)
    # 개수가 다르면 어느 구간의 답인지 알 수 없다. 앞에서부터 짝지어 남는 쪽을
    # 버리면 엉뚱한 구간에 엉뚱한 이동수단이 붙는다.
    if len(decided) != len(segments):
        raise ValueError(
            f"판정 결과 수가 구간 수와 다릅니다: "
            f"구간 {len(segments)}개, 결과 {len(decided)}개"
        )
    modes: dict[tuple[str, str], TravelMode] = {}
    for segment, mode in zip(segments, decided, strict=True):
        # StrEnum이라 문자열이 그대로 통과할 수 있다. 모르는 값이 표에 들어가면
        # 속도를 찾는 단계에서야 터지고, 그때는 어느 구간이었는지가 사라진다.
        if not isinstance(mode, TravelMode):
            raise ValueError(
                f"알 수 없는 이동수단입니다: {mode!r} (구간 {segment.order})"
            )
        if mode not in JUDGEABLE_MODES:
            raise ValueError(
                f"판정이 고를 수 없는 이동수단입니다: {mode} (구간 {segment.order})"
            )
        modes[segment.key] = mode
    return modes


def _speed_for_mode(
    mode: TravelMode,
    *,
    walking_speed_mps: float,
    transit_speed_mps: float,
    driving_speed_mps: float,
) -> float:
    if mode is TravelMode.WALKING:
        return walking_speed_mps
    if mode is TravelMode.TRANSIT:
        return transit_speed_mps
    return driving_speed_mps


def estimate_schedule_travel_edges(
    *,
    candidates: Sequence[ScheduleTravelCandidate],
    pairs: Sequence[ScheduleTravelPair],
    transport: Transport | None,
    modes: Mapping[tuple[str, str], TravelMode] | None = None,
    walking_speed_mps: float,
    transit_speed_mps: float,
    driving_speed_mps: float,
    walk_transfer_threshold_min: int,
) -> ScheduleTravelEstimateResult:
    """요청된 방향 쌍만 직선거리 기반 일정 이동정보로 변환한다.

    이동수단 미지정과 대중교통 명시는 가까운 구간을 도보로 연결하고, 도보 예상시간이
    임계값을 넘는 구간만 대중교통으로 전환한다. 도보·자동차 명시는 그대로 유지한다.
    잘못된 일부 쌍은 전체 요청을 실패시키지 않고 건너뛰며, 사유와 그 방향 쌍을 담은
    `ScheduleTravelWarning`으로 드러낸다.

    속도 셋과 도보 전환 임계값은 이 함수가 설정을 직접 읽지 않고 인자로 받는다.
    호출자가 `Settings.walking_speed_mps`, `Settings.transit_speed_mps`,
    `Settings.driving_speed_mps`, `Settings.schedule_walk_transfer_threshold_min`을
    넘겨야 `.env` 값이 실제로 반영된다.

    `modes`를 주면 그 표에서 구간별 이동수단을 꺼내 쓰고, 안 주면 지금까지처럼
    `_select_mode()`로 직접 정한다. 표는 `select_modes_for_segments()`가 만든다.
    **이 함수는 동기·순수로 남는다** — 판정에 LLM이 붙으면 async가 되어야 하는데,
    그러면 설정을 인자로만 받는 위 설계가 함께 깨진다. 판정은 밖에서 하고 결과만
    받는다.
    """

    _validate_speeds(
        walking_speed_mps,
        transit_speed_mps,
        driving_speed_mps,
        walk_transfer_threshold_min,
    )
    segments, warnings = build_segment_inputs(
        candidates, pairs, walking_speed_mps=walking_speed_mps
    )
    edges: list[ScheduleTravelEdge] = []

    for segment in segments:
        if modes is None:
            mode = _select_mode(
                transport,
                distance_m=segment.distance_m,
                walking_speed_mps=walking_speed_mps,
                walk_transfer_threshold_min=walk_transfer_threshold_min,
            )
        elif segment.key in modes:
            mode = modes[segment.key]
        else:
            # 조용히 `_select_mode()`로 떨어지면 판정이 절반만 적용된 채 정상처럼
            # 돌아간다. 표를 준 쪽과 여기가 같은 구간 목록을 봤어야 하므로
            # (양쪽 다 build_segment_inputs()를 쓴다) 빠진 키는 버그다.
            raise ValueError(
                f"이동수단 표에 없는 구간입니다: {segment.from_place_id}"
                f" -> {segment.to_place_id}"
            )
        speed_mps = _speed_for_mode(
            mode,
            walking_speed_mps=walking_speed_mps,
            transit_speed_mps=transit_speed_mps,
            driving_speed_mps=driving_speed_mps,
        )
        duration_min = math.ceil(segment.distance_m / speed_mps / 60)
        edges.append(
            ScheduleTravelEdge(
                from_place_id=segment.from_place_id,
                to_place_id=segment.to_place_id,
                mode=mode,
                status=RouteStatus.SUCCESS,
                source=RouteSource.STRAIGHT_LINE_ESTIMATE,
                duration_min=duration_min,
                distance_m=segment.distance_m,
                confidence=TravelConfidence.LOW,
            )
        )

    return ScheduleTravelEstimateResult(edges=tuple(edges), warnings=warnings)


def _fall_back_to_estimate(
    estimated: ScheduleTravelEdge, error_code: str | None
) -> ScheduleTravelEdge:
    """실측하지 못한 구간을 추정값 그대로 두고 사유만 심는다.

    `status`는 `SUCCESS`로 남는다 — 이 Edge에 **쓸 수 있는 이동시간이 들어 있는가**를
    말하는 자리이고, 추정값도 쓸 수 있는 값이기 때문이다. "실측이 아니다"는
    `source`·`confidence`가, "왜 실측이 아닌가"는 `error_code`가 말한다. 같은 사실을
    두 필드가 나눠 말하면 소비 측이 한쪽만 보고 쓸 수 있는 구간을 버린다.
    """
    return replace(estimated, error_code=error_code)


def _resolve_measured_edge(
    estimated: ScheduleTravelEdge,
    route: TravelRoute | None,
    *,
    fallback_error_code: str | None,
    tool_error_code: str | None,
) -> tuple[ScheduleTravelEdge, str | None]:
    """경로 조회 결과 한 건을 Edge와 경고 코드로 옮긴다."""
    if route is None:
        # Provider가 통째로 실패하면 Tool이 routes를 비워 돌려준다(D-042 — 자동차·
        # 대중교통은 추정 Provider로 자동 전환하지 않는다).
        return (
            _fall_back_to_estimate(estimated, tool_error_code),
            SCHEDULE_TRAVEL_MEASURE_UNAVAILABLE_WARNING,
        )
    if route.status is not RouteStatus.SUCCESS:
        return (
            _fall_back_to_estimate(estimated, route.error_code or tool_error_code),
            SCHEDULE_TRAVEL_MEASURE_FAILED_WARNING,
        )
    if route.source is RouteSource.STRAIGHT_LINE_ESTIMATE:
        # 도보만 추정 fallback이 등록돼 있어 Tool 안에서 이미 대체된 경우다. 값은
        # 채워져 왔지만 실측이 아니므로 추정으로 취급한다 — 원인은 개별 route가
        # 아니라 결과의 fallback_causes에 남는다.
        return (
            _fall_back_to_estimate(estimated, fallback_error_code),
            SCHEDULE_TRAVEL_MEASURE_FAILED_WARNING,
        )

    # RouteStatus.SUCCESS는 거리와 소요시간이 모두 있음을 보장한다(TravelRoute 검증).
    assert route.distance_m is not None and route.duration_seconds is not None
    return (
        replace(
            estimated,
            source=route.source,
            duration_min=math.ceil(route.duration_seconds / 60),
            distance_m=route.distance_m,
            confidence=TravelConfidence.HIGH,
            error_code=None,
        ),
        None,
    )


async def measure_schedule_travel_edges(
    *,
    tool: TravelRouteTool,
    candidates: Sequence[ScheduleTravelCandidate],
    estimated_edges: Sequence[ScheduleTravelEdge],
    max_measured_segments: int,
) -> ScheduleTravelEstimateResult:
    """추정 Edge 중 요청된 구간만 실제 경로 API로 다시 잰다.

    **이동수단을 다시 고르지 않는다.** 입력으로 받은 추정 Edge의 `mode`를 그대로
    쓴다. 여기서 `_select_mode()`를 다시 부르면 추정과 실측이 서로 다른 이동수단을
    고를 수 있고, 그러면 소비 측은 값이 바뀐 이유가 경로 때문인지 이동수단이 바뀐
    탓인지 구분할 수 없다. 실측에 실패했을 때 되돌릴 값도 그 추정 Edge에 있다.

    `TravelRouteQuery`가 **출발지 하나에 목적지 여럿** 모양이라, 출발지가 매번 다른
    일정 구간은 `(출발지, 이동수단)`으로 묶어 그룹마다 따로 부른다. 결과는 목적지
    `place_id`로만 오므로 그룹의 출발지를 되붙여 방향 쌍 키를 복원한다.

    그룹은 **순차로 호출한다.** Provider마다 동시 요청 제한이 이미 걸려 있어
    그룹까지 동시에 돌리면 그 곱만큼 나가고, 전역 동시성 제어는 이 함수의 범위가
    아니다. 일정 하나의 구간이 2~4개라 순차로도 지연이 크지 않다.

    실측하지 못한 구간은 **조용히 사라지지도, 0분이 되지도 않는다.** 추정값 그대로
    남고 `error_code`와 경고에 사유가 실린다. 반환 순서는 입력 순서와 같다.
    """
    if max_measured_segments < 1:
        raise ValueError("실측 구간 수 상한은 1 이상이어야 합니다.")
    candidate_by_id = _candidate_index(candidates)

    # (방향 쌍, 그대로 내보낼 Edge, 경고). Edge가 None이면 내보내지 않고 경고만 남긴다.
    plan: list[tuple[ScheduleTravelPair, ScheduleTravelEdge | None, str | None]] = []
    measurable: list[ScheduleTravelEdge] = []
    seen_pairs: set[tuple[str, str]] = set()

    for edge in estimated_edges:
        pair = ScheduleTravelPair(edge.from_place_id, edge.to_place_id)
        key = (edge.from_place_id, edge.to_place_id)
        if key in seen_pairs:
            plan.append((pair, None, SCHEDULE_TRAVEL_DUPLICATE_PAIR_WARNING))
            continue
        seen_pairs.add(key)

        if edge.from_place_id not in candidate_by_id or edge.to_place_id not in candidate_by_id:
            # 좌표를 모르면 경로 API에 넘길 수가 없다. 상한을 깎지도 않는다.
            plan.append((pair, edge, SCHEDULE_TRAVEL_UNKNOWN_PLACE_WARNING))
            continue
        if len(measurable) >= max_measured_segments:
            # 어느 구간을 실측할지 고르는 일은 이 함수 밖이므로 입력 순서를 존중해
            # 앞에서부터 채우고 나머지를 남긴다.
            plan.append((pair, edge, SCHEDULE_TRAVEL_MEASURE_BUDGET_EXCEEDED_WARNING))
            continue

        measurable.append(edge)
        plan.append((pair, edge, None))

    groups: dict[tuple[str, TravelMode], list[ScheduleTravelEdge]] = {}
    for edge in measurable:
        groups.setdefault((edge.from_place_id, edge.mode), []).append(edge)

    resolved: dict[tuple[str, str], tuple[ScheduleTravelEdge, str | None]] = {}
    for (origin_place_id, mode), group_edges in groups.items():
        result = await tool.execute(
            TravelRouteQuery(
                origin=candidate_by_id[origin_place_id].coordinate,
                destinations=tuple(
                    RouteDestination(
                        edge.to_place_id, candidate_by_id[edge.to_place_id].coordinate
                    )
                    for edge in group_edges
                ),
                mode=mode,
            )
        )
        route_by_place_id = {route.place_id: route for route in result.routes}
        fallback_error_code = (
            result.fallback_causes[0][0] if result.fallback_causes else None
        )
        tool_error_code = result.error.code if result.error is not None else None
        for edge in group_edges:
            resolved[(edge.from_place_id, edge.to_place_id)] = _resolve_measured_edge(
                edge,
                route_by_place_id.get(edge.to_place_id),
                fallback_error_code=fallback_error_code,
                tool_error_code=tool_error_code,
            )

    edges: list[ScheduleTravelEdge] = []
    warnings: list[ScheduleTravelWarning] = []
    for pair, planned_edge, planned_warning in plan:
        code = planned_warning
        if planned_edge is not None:
            edge, measured_warning = resolved.get(
                (pair.from_place_id, pair.to_place_id), (planned_edge, None)
            )
            edges.append(edge)
            code = code or measured_warning
        if code is not None:
            warnings.append(
                ScheduleTravelWarning(
                    code=code,
                    from_place_id=pair.from_place_id,
                    to_place_id=pair.to_place_id,
                )
            )

    return ScheduleTravelEstimateResult(edges=tuple(edges), warnings=tuple(warnings))


__all__ = [
    "SCHEDULE_TRAVEL_DUPLICATE_PAIR_WARNING",
    "SCHEDULE_TRAVEL_MEASURE_BUDGET_EXCEEDED_WARNING",
    "SCHEDULE_TRAVEL_MEASURE_FAILED_WARNING",
    "SCHEDULE_TRAVEL_MEASURE_UNAVAILABLE_WARNING",
    "SCHEDULE_TRAVEL_SELF_PAIR_WARNING",
    "SCHEDULE_TRAVEL_UNKNOWN_PLACE_WARNING",
    "ModeJudge",
    "JUDGEABLE_MODES",
    "JUDGE_SKIPPED_TRANSPORTS",
    "ModeJudgmentContext",
    "SegmentModeInput",
    "build_segment_inputs",
    "estimate_schedule_travel_edges",
    "measure_schedule_travel_edges",
    "select_modes_for_segments",
]
