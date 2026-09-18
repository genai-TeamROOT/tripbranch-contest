"""일정 구간 이동시간을 추정하고, 확정된 구간만 실측으로 덮는다. (TP-216)

**왜 별도 모듈인가.** 시간표 계산기(`timeline.py`)는 이동시간을 콜러블로 주입받는
동기 함수다(TP-215). 실측은 외부 API 호출이라 비동기이고, 어느 구간을 잴지는
LLM이 순서를 정한 뒤에야 정해진다. 그래서 "순서가 정해진 뒤 필요한 구간만 미리
확정해 표로 만들고, 시간표에는 그 표를 읽는 콜러블을 넘긴다"로 나눈다.
`timeline.py`는 이 카드에서 한 줄도 바뀌지 않는다.

**속도 상수를 어디서 가져오는가.** `place_search_policy`의 값을 mps로 환산해
넘긴다. `Settings.walking_speed_mps`(1.2 = 4.32km/h)와
`Settings.driving_speed_mps`(5.5 = 19.8km/h)를 쓰지 않는 이유는 검색 반경을 만든
가정과 분모를 맞추기 위해서다 — 차이가 3% 남짓이라 화면으로도 테스트로도 안 잡히지만,
`place_search_policy.NON_WALKING_SPEED_KM_PER_MINUTE` 주석이 못 박은 불변식이
조용히 깨진다. `estimate_schedule_travel_edges()`가 속도를 인자로 받고 설정을 직접
읽지 않는 설계라 호출자 쪽에서 닫을 수 있다.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Sequence

from app.config import Settings
from app.domain.schedule_travel import (
    ScheduleTravelCandidate,
    ScheduleTravelEdge,
    ScheduleTravelPair,
    ScheduleTravelWarning,
    SegmentWeather,
    TravelConfidence,
)
from app.errors import AppError
from app.observability.langfuse_tracing import observe_step, record_score
from app.place_search_policy import (
    NON_WALKING_SPEED_KM_PER_MINUTE,
    WALKING_SPEED_KM_PER_MINUTE,
)
from app.schedule.timeline import TravelMinutes
from app.schemas import UserConditions
from app.tools.mode_judge import narrow_accessibility_needs
from app.tools.schedule_travel import (
    SCHEDULE_TRAVEL_MEASURE_BUDGET_EXCEEDED_WARNING,
    ModeJudge,
    ModeJudgmentContext,
    build_segment_inputs,
    estimate_schedule_travel_edges,
    measure_schedule_travel_edges,
    select_modes_for_segments,
)
from app.tools.travel_route import TravelRouteTool

logger = logging.getLogger(__name__)


def _to_mps(km_per_minute: float) -> float:
    """km/분 → m/초."""

    return km_per_minute * 1000.0 / 60.0


# 반경 산정과 같은 가정을 mps로 옮긴 값. 여기서만 환산하고 다른 곳에 복사하지 않는다.
WALKING_SPEED_MPS = _to_mps(WALKING_SPEED_KM_PER_MINUTE)
NON_WALKING_SPEED_MPS = _to_mps(NON_WALKING_SPEED_KM_PER_MINUTE)


def is_measured(edge: ScheduleTravelEdge) -> bool:
    """이 구간이 경로 API 실측으로 채워졌는지.

    `confidence`만 본다. `source`로도 같은 판정이 되지만(추정은 항상
    `STRAIGHT_LINE_ESTIMATE`) 그러려면 provider 이름 목록을 여기에 한 벌 더 적어야
    하고, provider가 늘 때마다 두 곳이 갈린다. TP-219가 실측 성공에만 `HIGH`를
    싣기로 한 자리를 그대로 읽는다.
    """

    return edge.confidence is TravelConfidence.HIGH


def summarize_schedule_travel(
    *,
    edges: Sequence[ScheduleTravelEdge],
    warnings: Sequence[ScheduleTravelWarning],
    measure_attempted: bool,
) -> dict[str, object]:
    """구간 이동정보 확정 한 번을 span에 실을 집계로 접는다. (TP-216)

    **실측 성공률과 상한 초과가 이 요약의 존재 이유다.** 실측하지 못한 구간은
    추정값 그대로 남아 응답에서는 성공처럼 보인다 — 도착시각과 총 소요시간은
    나오고 어디가 추정이었는지는 화면에만 남는다. 추세로 봐야 하는 값이라
    span의 output만으로는 부족해 `record_score()`로 함께 올린다
    (`tools/travel_route.summarize_fanout()`과 같은 구조).

    place_id와 좌표는 싣지 않는다. 여기서 알고 싶은 건 개수와 분포지 어디를
    갔느냐가 아니다 — 경고의 방향 쌍도 코드별 건수로만 접는다.
    """

    measured = sum(1 for edge in edges if is_measured(edge))
    estimated = len(edges) - measured
    by_code = Counter(warning.code for warning in warnings)
    budget_exceeded = by_code.get(SCHEDULE_TRAVEL_MEASURE_BUDGET_EXCEEDED_WARNING, 0)

    if not measure_attempted:
        level = "DEFAULT"
    elif estimated:
        level = "WARNING"
    else:
        level = "DEFAULT"

    return {
        "segments": len(edges),
        "measured": measured,
        "estimated": estimated,
        # 실측을 시도하지 않은 턴(경로 Tool 미주입)에는 None이다. 0.0으로 적으면
        # "실측이 전부 실패한 턴"과 구분되지 않아 성공률 추세가 통째로 왜곡된다.
        "measured_ratio": (
            round(measured / len(edges), 3) if edges and measure_attempted else None
        ),
        "measure_attempted": measure_attempted,
        # 실측 구간 수 상한에 걸려 아예 호출하지 않은 구간 수. 지금 상한은 4이고
        # 일정 항목 상한이 5곳이라 간선은 최대 4개다 — 0이 정상이고, 0이 아니면
        # 일정이 길어졌거나 상한 설정이 어긋났다는 뜻이다.
        "budget_exceeded": budget_exceeded,
        "by_mode": dict(sorted(Counter(edge.mode.value for edge in edges).items())),
        "by_source": dict(sorted(Counter(edge.source.value for edge in edges).items())),
        "warning_codes": dict(sorted(by_code.items())),
        "error_causes": dict(
            sorted(Counter(edge.error_code for edge in edges if edge.error_code).items())
        ),
        "level": level,
        # 마스킹을 타지 않는 자리(status_message)에 실을 한 줄.
        "headline": (
            f"구간 {len(edges)}개 · 실측 {measured} · 추정 {estimated}"
            + (f" · 상한초과 {budget_exceeded}" if budget_exceeded else "")
            + ("" if measure_attempted else " · 실측 미시도")
        ),
    }


def consecutive_pairs(place_ids: Sequence[str]) -> tuple[ScheduleTravelPair, ...]:
    """방문 순서에서 실제로 쓰이는 방향 구간만 뽑는다.

    후보 전체의 행렬을 만들지 않는다 — 후보 10곳이면 방향 간선이 90개이고,
    TP-191(D-113)이 정확히 그 비용 때문에 실측 대상을 좁혔다. 일정이 3~5곳이면
    구간은 2~4개다.
    """

    return tuple(
        ScheduleTravelPair(from_place_id=first, to_place_id=second)
        for first, second in zip(place_ids, place_ids[1:], strict=False)
        if first != second
    )


def travel_minutes_from_edges(edges: Sequence[ScheduleTravelEdge]) -> TravelMinutes:
    """구간 표를 읽는 콜러블. 표에 없는 구간은 None을 돌려준다.

    **방향을 지킨다.** 직선거리 행렬(`estimated_travel_minutes()`)은 방향이 없어
    양쪽 키를 다 봤지만, 실측은 왕복이 다를 수 있어 반대 방향으로 대신 답하지 않는다.
    없으면 None이고, 시간표가 폴백값(15분)으로 메운다.
    """

    by_pair = {(edge.from_place_id, edge.to_place_id): edge for edge in edges}

    def resolve(from_place_id: str, to_place_id: str) -> int | None:
        edge = by_pair.get((from_place_id, to_place_id))
        return None if edge is None else max(1, edge.duration_min)

    return resolve


async def resolve_schedule_travel_edges(
    *,
    candidates: Sequence[ScheduleTravelCandidate],
    place_ids: Sequence[str],
    conditions: UserConditions,
    weather: SegmentWeather | None = None,
    settings: Settings,
    travel_route_tool: TravelRouteTool | None,
    mode_judge: ModeJudge | None = None,
) -> tuple[ScheduleTravelEdge, ...]:
    """확정된 방문 순서의 구간 이동정보를 만든다. 실패해도 예외를 올리지 않는다.

    추정을 먼저 만들고, 경로 Tool이 주어졌을 때만 그 위를 실측으로 덮는다
    (TP-219의 `measure_schedule_travel_edges()`). 실측하지 못한 구간은 추정값 그대로
    남고 `source`·`confidence`에 그 사실이 실린다 — 조용히 사라지거나 0분이 되지
    않는다.

    좌표가 없으면 빈 결과를 돌려준다. 그러면 시간표가 구간마다 폴백값을 쓰므로
    편성 자체는 막히지 않는다(기존 동작과 같다).
    """

    if not candidates or len(place_ids) < 2:
        return ()

    pairs = consecutive_pairs(place_ids)
    if not pairs:
        return ()

    with observe_step("schedule_travel") as step:
        edges, warnings, measure_attempted = await _resolve_edges(
            candidates=candidates,
            pairs=pairs,
            conditions=conditions,
            weather=weather,
            settings=settings,
            travel_route_tool=travel_route_tool,
            mode_judge=mode_judge,
        )
        try:
            summary = summarize_schedule_travel(
                edges=edges,
                warnings=warnings,
                measure_attempted=measure_attempted,
            )
            step.record(
                output=summary,
                level=summary["level"],
                status_message=summary["headline"],
            )
            # 추세로 봐야 하는 두 값만 Score로 올린다. span의 output은 그 턴을
            # 열어봤을 때 읽는 값이고, 실측 성공률이 언제부터 떨어졌는지는
            # 여러 턴에 걸쳐 집계돼야 보인다(`record_score` docstring).
            if summary["measured_ratio"] is not None:
                record_score(
                    "schedule_travel_measured_ratio", float(summary["measured_ratio"])
                )
            if measure_attempted:
                record_score(
                    "schedule_travel_budget_exceeded", bool(summary["budget_exceeded"])
                )
        except Exception:
            logger.warning(
                "일정 구간 이동정보 관측 요약 실패(응답 흐름에는 영향 없음)", exc_info=True
            )
        return edges


async def _resolve_edges(
    *,
    candidates: Sequence[ScheduleTravelCandidate],
    pairs: Sequence[ScheduleTravelPair],
    conditions: UserConditions,
    weather: SegmentWeather | None = None,
    settings: Settings,
    travel_route_tool: TravelRouteTool | None,
    mode_judge: ModeJudge | None = None,
) -> tuple[tuple[ScheduleTravelEdge, ...], tuple[ScheduleTravelWarning, ...], bool]:
    """추정·실측 본체. 관측에 쓸 경고와 "실측을 시도했나"까지 함께 돌려준다.

    실측 시도 여부를 결과로 남기는 이유는 지표 때문이다 — 경로 Tool이 주입되지
    않은 턴의 실측률 0%와 실측이 전부 실패한 턴의 0%는 같은 수가 아니다.
    """

    # 구간 이동수단을 먼저 정하고 그 표를 넘긴다(TP-226). 판정을 구간 루프 밖에서
    # 하는 이유는 누적 도보량처럼 구간 사이 관계를 보려면 전 구간이 한 번에
    # 손에 있어야 하기 때문이다(TP-225).
    #
    # `mode_judge`가 없으면 구간마다 기존 규칙을 그대로 부르므로 **결과가 배선
    # 전과 같다.** 실제 판정은 TP-224의 세 번째 PR에서 주입한다.
    try:
        segments, _ = build_segment_inputs(
            candidates, pairs, walking_speed_mps=WALKING_SPEED_MPS
        )
        judgment_context = ModeJudgmentContext(
            transport=conditions.transport,
            companion=conditions.companion,
            # 9개 중 이동에 관련되는 셋만 넘긴다(TP-227). 나머지는 "그 장소가
            # 어떤가"라 판정과 무관하다.
            accessibility_needs=narrow_accessibility_needs(
                conditions.accessibility_needs
            ),
            weather=weather,
        )
        try:
            modes = await select_modes_for_segments(
                segments,
                judgment_context,
                judge=mode_judge,
                walking_speed_mps=WALKING_SPEED_MPS,
                walk_transfer_threshold_min=(
                    settings.schedule_walk_transfer_threshold_min
                ),
            )
        except (AppError, ValueError):
            # **판정 실패는 편성 실패가 아니다.** 규칙으로 되돌아가면 결과가 예전과
            # 같아지므로 그 턴은 정상으로 끝난다. 다만 되돌아갔다는 사실을 남기지
            # 않으면 판정이 한 번도 안 도는 채로 정상처럼 보인다.
            #
            # **`Exception`으로 넓게 잡지 않는다.** 처음에 그렇게 뒀더니 판정
            # 메서드가 없는 LLM 이중체에서 AttributeError가 그대로 삼켜져, 판정이
            # 한 번도 안 도는데 테스트 6건이 통과했다. 없는 메서드는 실행 중 실패가
            # 아니라 배선이 끊긴 것이므로 시끄럽게 터져야 한다.
            #
            # 아래 `except ValueError`와 따로 잡는 이유가 이것이다. 저쪽은 후보
            # 목록이 잘못된 경우이고 이쪽은 판정이 실패한 경우라, 같은 로그로
            # 뭉뚱그리면 어느 쪽인지 알 수 없다(TP-226에서 넘어온 항목).
            logger.warning("schedule_travel.mode_judge_failed", exc_info=True)
            record_score("schedule_mode_judge_fallback", 1.0)
            modes = await select_modes_for_segments(
                segments,
                judgment_context,
                judge=None,
                walking_speed_mps=WALKING_SPEED_MPS,
                walk_transfer_threshold_min=(
                    settings.schedule_walk_transfer_threshold_min
                ),
            )
        estimated = estimate_schedule_travel_edges(
            candidates=candidates,
            pairs=pairs,
            transport=conditions.transport,
            modes=modes,
            walking_speed_mps=WALKING_SPEED_MPS,
            transit_speed_mps=NON_WALKING_SPEED_MPS,
            driving_speed_mps=NON_WALKING_SPEED_MPS,
            walk_transfer_threshold_min=settings.schedule_walk_transfer_threshold_min,
        )
    except ValueError:
        # 후보 목록이 잘못 만들어진 경우(중복 place_id 등)까지 편성을 막지 않는다.
        logger.warning("schedule_travel.estimate_failed", exc_info=True)
        return (), (), False

    if travel_route_tool is None or not estimated.edges:
        return estimated.edges, estimated.warnings, False

    try:
        measured = await measure_schedule_travel_edges(
            tool=travel_route_tool,
            candidates=candidates,
            estimated_edges=estimated.edges,
            max_measured_segments=settings.schedule_max_measured_segments,
        )
    except Exception:
        # 실측이 통째로 실패해도 추정값으로 일정을 낸다("구조적 보장 우선").
        logger.warning("schedule_travel.measure_failed", exc_info=True)
        return estimated.edges, estimated.warnings, True

    # 추정 단계의 경고(중복·자기 쌍·좌표 없음)는 실측 결과에 다시 담기지 않으므로
    # 두 단계를 합쳐 넘긴다 — 한쪽만 보면 건너뛴 구간의 사유가 사라진다.
    return measured.edges, (*estimated.warnings, *measured.warnings), True


__all__ = [
    "NON_WALKING_SPEED_MPS",
    "WALKING_SPEED_MPS",
    "consecutive_pairs",
    "is_measured",
    "resolve_schedule_travel_edges",
    "summarize_schedule_travel",
    "travel_minutes_from_edges",
]
