"""일정 편성 모듈의 실행 로직.

역할: SchedulePlanningRequest를 받아 LLM으로 일정을 편성하고 ScheduleResult를
반환한다. 상태 저장소(StateStore)에 의존하지 않는 순수 입력→출력 함수다 —
A(agent_runtime.py)가 D(RecommendationProvider)를 호출하는 것과 동일한 방식으로
이 모듈을 호출한다(docs/design/int-07-schedule.md 6.0절, B의 "판단하지 않는
기억 장치" 원칙과 무관하게 B 코드는 전혀 건드리지 않는다).

place_associations(D-088) 연동(co_visited_fetcher)은 opt-in이다 — 호출부가
넘기지 않으면 이 모듈은 그 데이터 소스를 전혀 건드리지 않고 기존 동작과
바이트 단위로 동일하다(app.schedule.associations 참고).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from time import perf_counter
from typing import TypeAlias, TypeVar

from app.config import Settings
from app.domain.schedule_travel import ScheduleTravelEdge
from app.errors import AppError
from app.providers.protocols import LLMProvider
from app.schedule.associations import CoVisitedHint
from app.schedule.budget import (
    DurationSlot,
    cap_item_count_to_budget,
    classify_budget,
    cluster_ids_in_order,
    derive_item_range,
    effective_budget_min,
    fit_durations_to_budget,
    required_candidate_count,
)
from app.schedule.duration import policy_for, resolve_visit_duration
from app.schedule.schemas import (
    ScheduleLLMItem,
    SchedulePartialFillRequest,
    SchedulePlanningRequest,
)
from app.schedule.timeline import (
    Timeline,
    TimelineStop,
    TravelMinutes,
    build_timeline,
    estimated_travel_minutes,
    travel_speed_km_per_minute,
)
from app.schedule.travel import (
    is_measured,
    resolve_schedule_travel_edges,
    travel_minutes_from_edges,
)
from app.schemas import RecommendationItem, ScheduleItem, ScheduleResult
from app.state.schema import now_kst
from app.tools.mode_judge import LlmModeJudge
from app.tools.schedule_travel import ModeJudge
from app.tools.travel_route import TravelRouteTool

logger = logging.getLogger(__name__)

Timer: TypeAlias = Callable[[], float]
CoVisitedFetcher: TypeAlias = Callable[
    [Sequence[str], Settings], Awaitable[list[CoVisitedHint]]
]

_NO_CANDIDATES_ROUTE_SUMMARY = (
    "일정을 짤 만한 곳을 충분히 찾지 못했어요. "
    "지역을 조금 넓히거나 다른 종류의 장소로 다시 말씀해 주세요."
)

# ScheduleLLMPlan.items의 최소 개수가 이번 요청의 time_available에 따라 달라지므로
# (budget.derive_item_range(), TP-239) 후보 부족 가드도 고정 3이 아니라 그 최솟값을
# 쓴다 — 예를 들어 "2시간 코스 짜줘"는 최소 1개면 충분한데, 후보가 2개뿐이라고
# 무조건 "충분히 찾지 못했다"고 안내하면 실제로는 만들 수 있는 일정도 막힌다.
# 후보가 이 최솟값보다 적으면 LLM이 애초에 그 개수를 만족시킬 방법이 없다 —
# 재시도를 줘도 똑같이 실패해 llm_output_invalid(502)만 두 번 반복하고 끝난다.
# 그래서 이 경우엔 LLM을 아예 부르지 않고 여기서 바로 정규화된 안내로 반환한다
# (SCHEDULE-07, 9절 "D 후보 3개 미만" 미결 사항 해소).


def _parse_hhmm_minutes(hhmm: str) -> int | None:
    """estimated_arrival("HH:MM")을 자정 기준 분 단위 정수로 파싱한다.

    형식이 안 맞으면(LLM이 지시를 안 지킨 방어적 상황) None을 돌려준다 —
    호출부가 이 경우 재계산을 포기하고 원본 값을 그대로 두도록 한다.
    """

    try:
        hour_str, minute_str = hhmm.split(":", 1)
        return int(hour_str) * 60 + int(minute_str)
    except (ValueError, AttributeError):
        return None


# 시작 시각만 10분 단위로 올린다(TP-215).
#
# 예전에는 항목마다 estimated_arrival을 10분 단위로 올렸다 — LLM이 준 값이
# 11:59처럼 가짜 정밀도를 달고 나와 그대로 보여주기 어색했기 때문이다(팀 제안,
# 2026-08-12). 지금은 도착시각이 시작 시각 + 누적(이동 + 대기 + 체류)으로
# 계산되므로, 항목마다 올리면 화면의 시각이 그 누적과 어긋난다 — 이 카드가
# 없애려던 바로 그 불일치가 표시 단계에서 다시 생긴다.
#
# 그래서 올림은 **시작 시각 한 번**만 한다. 기준 시각이 보통 now_kst()라 13:47
# 같은 값으로 시작하는데, 13:50으로 올려두면 이후 시각이 전부 그 위에서
# 정확하게 누적된다. 읽기 좋은 값은 유지하면서 합은 깨지지 않는다.
_START_ROUNDING_MIN = 10


def _round_up_start(moment: datetime) -> datetime:
    """방문 시작 시각을 다음 10분 단위로 올린다."""

    truncated = moment.replace(second=0, microsecond=0)
    if truncated != moment:
        truncated += timedelta(minutes=1)
    remainder = truncated.minute % _START_ROUNDING_MIN
    if remainder:
        truncated += timedelta(minutes=_START_ROUNDING_MIN - remainder)
    return truncated


# recommendation_pipeline._operating_hours_display()가 "상시 운영"을 나타낼 때 쓰는
# 표시값과 동일한 문자열이다. SCHEDULE은 D의 공개 응답(RecommendationItem)만 보고 D
# 내부 상수를 직접 import하지 않으므로(레이어 경계, SchedulePlanningRequest.candidates의
# 주석 참고) 여기서 같은 문자열을 별도로 둔다 — 화면 표시 문자열이라 자주 바뀌지 않는다.
_ALL_DAY_OPERATING_HOURS_DISPLAY = "24시간"

# 사용자가 카드에서 먼저 보는 것은 도착 시각이라 그 순서로 쓴다. "운영 중이 아닐 수
# 있어요"는 완곡 표현이 겹쳐 있어(아닐 + 수 있다) 사실만 남겼다.
_OPERATING_HOURS_WARNING_TEMPLATE = (
    "{arrival} 도착 예정인데 이곳은 {display} 운영이에요. 가시기 전에 한 번 확인해 주세요."
)


def _parse_operating_hours_range(display: str | None) -> tuple[int, int] | None:
    """operating_hours_display("09:00~18:00")를 (개장 분, 마감 분)으로 파싱한다.

    "24시간"(상시 운영)이나 None(운영시간 미확인)은 폐점 여부를 판단할 근거가
    없거나 애초에 폐점이 없는 경우라 검사 대상이 아니다 — None을 반환해 호출부가
    경고를 붙이지 않도록 한다. 형식이 예상과 다르면(방어적 상황) 마찬가지로
    None을 반환한다 — 화면 표시용 후처리가 원본 값을 망가뜨리면 안 된다는 기존
    원칙과 동일하다.

    "24:00" 마감(자정 종료)은 recommendation_pipeline._operating_hours_display()가
    쓰는 원문 표기와 짝을 맞춰 1440분으로 취급한다.
    """
    if display is None or display == _ALL_DAY_OPERATING_HOURS_DISPLAY:
        return None
    parts = display.split("~", 1)
    if len(parts) != 2:
        return None
    open_str, close_str = parts
    open_minutes = _parse_hhmm_minutes(open_str)
    close_minutes = 24 * 60 if close_str == "24:00" else _parse_hhmm_minutes(close_str)
    if open_minutes is None or close_minutes is None:
        return None
    return open_minutes, close_minutes


def _operating_hours_warning(display: str | None, estimated_arrival: str) -> str | None:
    """도착 예정 시각이 그 후보의 운영시간을 벗어나면 경고 문구를 만든다.

    LLM 프롬프트에도 운영시간을 함께 전달해 애초에 마감된 곳을 피하도록
    유도하지만(build_schedule_planning_instruction), LLM이 지시를 놓치는
    경우까지 대비해 이 함수가 응답을 받은 뒤 다시 결정적으로 검사한다
    ("구조적 보장 우선" 원칙 — LLM 지시 준수만 믿지 않는다).
    """
    hours_range = _parse_operating_hours_range(display)
    if hours_range is None:
        return None
    arrival_minutes = _parse_hhmm_minutes(estimated_arrival)
    if arrival_minutes is None:
        return None
    open_minutes, close_minutes = hours_range
    if open_minutes <= arrival_minutes < close_minutes:
        return None
    return _OPERATING_HOURS_WARNING_TEMPLATE.format(display=display, arrival=estimated_arrival)


@dataclass(frozen=True)
class _ItemDraft:
    """시간표 계산에 넣기 직전의 항목. 시각만 아직 없다."""

    order: int
    place_id: str
    place_name: str
    reason: str
    visit_duration_min: int


@dataclass(frozen=True)
class _ResolvedTravel:
    """이번 편성에 쓸 구간 이동시간과 그 근거. (TP-216)

    시간표는 콜러블(`minutes`)만 있으면 되지만, 화면은 그 값이 실측인지 추정인지와
    어떤 이동수단으로 잰 값인지를 함께 보여줘야 한다. 콜러블은 숫자 하나만
    돌려주므로 근거를 실을 자리가 없어 `edges`를 함께 들고 다닌다 — 시간표가
    쓴 값과 화면이 설명하는 근거가 같은 표에서 나오게 하려는 것이다.
    """

    minutes: TravelMinutes
    edges: tuple[ScheduleTravelEdge, ...]


def _draft_from_llm_item(
    item: ScheduleLLMItem,
    candidate: RecommendationItem | None,
    order: int,
    *,
    clustered: bool = False,
) -> _ItemDraft:
    """LLM이 제안한 항목을 체류시간 정책으로 클램프해 초안으로 만든다.

    `clustered`는 이 자리가 도보로 이웃과 붙어 있다는 뜻이다(TP-243). 최소값만
    낮아지므로, LLM이 45분을 주면 60분으로 끌어올리지 않는다 — 어느 분류가
    완화되는지는 `duration.policy_for()` 주석에 있다.
    """

    return _ItemDraft(
        order=order,
        place_id=item.place_id,
        place_name=item.place_name,
        reason=item.reason,
        visit_duration_min=resolve_visit_duration(
            category=candidate.category if candidate is not None else None,
            proposed_min=item.estimated_duration_min,
            clustered=clustered,
        ),
    )


def _draft_from_schedule_item(item: ScheduleItem, order: int) -> _ItemDraft:
    """이미 확정된 항목(부분 재편성의 pinned)을 초안으로 만든다.

    체류시간은 **다시 클램프하지 않는다.** 직전 편성에서 이미 정책을 통과한
    값이고, 사용자가 유지하기로 한 자리를 이번 턴에 조용히 줄이거나 늘리면
    "그대로 뒀다"는 약속이 깨진다. 정책이 바뀌어도 마찬가지다 — 새 정책은 새로
    고르는 자리에만 적용한다.
    """

    return _ItemDraft(
        order=order,
        place_id=item.place_id,
        place_name=item.place_name,
        reason=item.reason,
        visit_duration_min=item.estimated_duration_min,
    )


def _build_schedule_timeline(
    drafts: Sequence[_ItemDraft],
    *,
    start_at: datetime,
    travel_minutes: TravelMinutes,
    candidates: Iterable[RecommendationItem],
) -> Timeline:
    """초안 목록으로 시간표를 계산한다. 운영시간은 개장 전 대기 판정에만 쓴다."""

    display_by_place = {c.place_id: c.operating_hours_display for c in candidates}
    stops: list[TimelineStop] = []
    for draft in drafts:
        hours = _parse_operating_hours_range(display_by_place.get(draft.place_id))
        stops.append(
            TimelineStop(
                place_id=draft.place_id,
                visit_duration_min=draft.visit_duration_min,
                opens_at_min=hours[0] if hours is not None else None,
                closes_at_min=hours[1] if hours is not None else None,
            )
        )
    return build_timeline(stops, start_at=start_at, travel_minutes=travel_minutes)


def _fit_to_time_available(
    drafts: Sequence[_ItemDraft],
    timeline: Timeline,
    *,
    time_available_min: int | None,
    candidates: Sequence[RecommendationItem],
    start_at: datetime,
    travel_minutes: TravelMinutes,
    clustered_flags: Sequence[bool] | None = None,
) -> tuple[list[_ItemDraft], Timeline]:
    """체류시간을 활동 가능 시간에 맞춰 조절하고 시간표를 다시 계산한다. (TP-238)

    **시간표를 한 번만 다시 계산한다.** 체류시간을 줄이면 도착이 당겨져 개장 전
    대기가 늘어날 수 있어서, 총 소요시간이 줄인 만큼 그대로 줄지는 않는다. 그래서
    한 번 더 돌려 실제 값을 얻되 거기서 멈춘다 — 대기와 체류가 서로를 밀어내며
    수렴하지 않을 수 있고, 남는 오차는 감추지 말고 판정으로 알리는 것이 맞다.

    **후보 목록에 없는 항목은 조절하지 않는다.** 그건 부분 재편성에서 사용자가
    유지하기로 한 자리(pinned)다 — `_compose_items()`가 운영시간 경고를 건너뛸 때
    쓰는 것과 같은 불변식이고, 근거는 `_draft_from_schedule_item()` 주석에 있다.

    **시간을 말하지 않은 턴에도 조절한다.** 예전에는 여기서 그냥 돌아갔고, 그래서
    개수 상한이 상수이던 시절과 겹쳐 420분 일정이 그대로 나갔다. 가정한 예산
    (`effective_budget_min()`)으로 배분하되 **판정은 하지 않는다** — 아래
    `classify_budget()` 호출은 `request.conditions.time_available`을 그대로 받으므로
    말하지 않은 턴은 여전히 판정이 None이다.

    **묶인 자리는 더 줄일 수 있다**(TP-243). `clustered_flags`가 참인 자리만 체류
    최소값이 낮아진다 — 부분 재편성 경로는 이 인자를 주지 않으므로(기본 None)
    예전과 똑같이 분류 최소값에서 멈춘다.
    """

    if not drafts:
        return list(drafts), timeline

    category_by_id = {c.place_id: c.category for c in candidates}
    flags = list(clustered_flags) if clustered_flags is not None else [False] * len(drafts)
    slots = [
        DurationSlot(
            current_min=draft.visit_duration_min,
            policy=(
                policy_for(category_by_id[draft.place_id], clustered=flag)
                if draft.place_id in category_by_id
                else None
            ),
        )
        for draft, flag in zip(drafts, flags, strict=True)
    ]
    overhead_min = timeline.total_duration_min - sum(
        draft.visit_duration_min for draft in drafts
    )
    fitted = fit_durations_to_budget(
        slots,
        overhead_min=overhead_min,
        budget_min=effective_budget_min(time_available_min),
        # 가정한 예산이면 넘칠 때만 줄인다 — 말하지 않은 시간을 꽉 채우려고
        # 체류를 늘리는 것은 예산과 채울 의사를 둘 다 지어내는 것이다.
        shrink_only=time_available_min is None,
    )
    if fitted == [draft.visit_duration_min for draft in drafts]:
        return list(drafts), timeline

    adjusted = [
        replace(draft, visit_duration_min=minutes)
        for draft, minutes in zip(drafts, fitted, strict=True)
    ]
    return adjusted, _build_schedule_timeline(
        adjusted,
        start_at=start_at,
        travel_minutes=travel_minutes,
        candidates=candidates,
    )


def _compose_items(
    drafts: Sequence[_ItemDraft],
    timeline: Timeline,
    candidates: Iterable[RecommendationItem],
    travel_edges: Sequence[ScheduleTravelEdge] = (),
    cluster_ids: Sequence[int | None] | None = None,
    pinned_items: Sequence[ScheduleItem] = (),
) -> list[ScheduleItem]:
    """초안 + 시간표를 화면에 실리는 ScheduleItem으로 합친다.

    구간 이동정보(`travel_edges`)는 **시간표가 쓴 그 표를 그대로 읽는다**(TP-216).
    이동수단과 실측 여부를 여기서 다시 판정하지 않는다 — 다시 판정하면 화면이
    설명하는 근거와 도착시각을 만든 근거가 갈릴 수 있다. 표에 없는 구간(좌표를
    못 구해 시간표가 폴백값을 쓴 자리)은 두 필드가 기본값으로 남고, 화면은
    이동수단이 None인 것으로 그 경우를 구분한다.

    운영시간 경고는 **방문 시작 시각** 기준으로 판단한다(도착 시각이 아니라).
    개장 전에 도착하면 시간표가 이미 대기를 잡아 방문 시작을 개장 시각으로
    미뤄두므로, 도착 시각으로 검사하면 정상적으로 기다리는 일정에까지 "운영 중이
    아닐 수 있다"는 경고가 붙는다. 마감 이후 도착이나 자정을 넘겨 도착한 경우는
    대기가 잡히지 않아 방문 시작 = 도착이고, 그때는 예전과 똑같이 경고가 붙는다.

    candidates에 없는 place_id(부분 재편성의 pinned 항목)는 운영시간 정보 자체가
    없으므로 검사하지 않고 그대로 둔다 — 기존 동작과 같다.

    묶음 번호(`cluster_ids`)는 **화면이 한 묶음으로 그리기 위한 표시값이다**
    (TP-243). 체류시간 완화와 자리 수 계산은 이미 앞에서 끝났고 여기서는 그
    결과를 실어 보내기만 한다.
    """

    display_by_place = {c.place_id: c.operating_hours_display for c in candidates}
    clusters = list(cluster_ids) if cluster_ids is not None else [None] * len(drafts)
    # 후보가 이미 들고 있는 사진을 그대로 옮긴다. 후보에 없는 pinned 항목은
    # 호출부가 그 항목에 채워 보낸 사진을 쓴다 — 운영시간과 달리 사진은 편성 판단에
    # 쓰이지 않고 화면에만 실리므로, 유지한 자리라고 비워 보내면 사진이 빠진
    # 카드만 남는다. 같은 장소가 양쪽에 있으면 이번 턴 후보 값을 쓴다.
    image_by_place = {
        **{item.place_id: (item.image_url, item.image_url_fallback) for item in pinned_items},
        **{c.place_id: (c.image_url, c.image_url_fallback) for c in candidates},
    }
    edge_by_pair = {(edge.from_place_id, edge.to_place_id): edge for edge in travel_edges}
    items: list[ScheduleItem] = []
    for index, (draft, stop) in enumerate(zip(drafts, timeline.stops, strict=True)):
        visit_start = stop.visit_start_at.strftime("%H:%M")
        warning = _operating_hours_warning(display_by_place.get(draft.place_id), visit_start)
        next_draft = drafts[index + 1] if index + 1 < len(drafts) else None
        edge = (
            None
            if next_draft is None
            else edge_by_pair.get((draft.place_id, next_draft.place_id))
        )
        image_url, image_url_fallback = image_by_place.get(draft.place_id, (None, None))
        items.append(
            ScheduleItem(
                order=draft.order,
                place_id=draft.place_id,
                place_name=draft.place_name,
                estimated_arrival=stop.arrival_at.strftime("%H:%M"),
                estimated_duration_min=stop.visit_duration_min,
                travel_to_next_min=stop.travel_to_next_min,
                reason=draft.reason,
                warnings=[warning] if warning is not None else [],
                image_url=image_url,
                image_url_fallback=image_url_fallback,
                travel_to_next_mode=None if edge is None else edge.mode,
                travel_to_next_measured=edge is not None and is_measured(edge),
                cluster_id=clusters[index],
            )
        )
    return items


def _travel_minutes_for(
    request: SchedulePlanningRequest | SchedulePartialFillRequest,
) -> TravelMinutes:
    """이번 요청의 구간 이동시간 계산기.

    **좌표가 없을 때만 쓰는 폴백이다.** 좌표(`travel_candidates`)가 오면
    `_resolve_travel_minutes()`가 추정·실측 Edge로 계산한다(TP-216). 이 경로는
    좌표를 못 채운 호출부(과거 세션 재생, 단위 테스트 등)를 위해 남겨둔다 —
    직선거리를 가정 속도로 나눈 값이고 방향이 없다.
    """

    return estimated_travel_minutes(
        request.pairwise_distances_km,
        speed_km_per_minute=travel_speed_km_per_minute(request.conditions),
    )


async def _resolve_travel_minutes(
    request: SchedulePlanningRequest | SchedulePartialFillRequest,
    place_ids: Sequence[str],
    *,
    settings: Settings | None,
    travel_route_tool: TravelRouteTool | None,
    mode_judge: ModeJudge | None = None,
) -> _ResolvedTravel:
    """확정된 방문 순서의 구간 이동시간을 만든다. (TP-216)

    **순서가 정해진 뒤에 부른다.** 어느 구간을 잴지는 LLM이 순서를 고른 뒤에야
    정해지고, 실측은 비동기라 시간표의 동기 콜러블 안에서 할 수 없다. 그래서
    필요한 구간만 미리 확정해 표로 만들고, 시간표에는 그 표를 읽는 콜러블을 넘긴다.

    좌표(`travel_candidates`)가 없으면 예전처럼 직선거리 ÷ 가정 속도로 계산한다 —
    이 필드를 안 채우는 호출부는 동작이 바뀌지 않는다.
    """

    edges = await resolve_schedule_travel_edges(
        candidates=request.travel_candidates,
        place_ids=place_ids,
        conditions=request.conditions,
        mode_judge=mode_judge,
        # 구간 이동수단 판정에 쓴다(TP-226). 두 요청 스키마가 같은 필드를 가지므로
        # 전체 편성과 부분 재편성이 같은 값을 넘긴다.
        weather=request.weather,
        settings=settings or Settings(),
        travel_route_tool=travel_route_tool,
    )
    if edges:
        return _ResolvedTravel(minutes=travel_minutes_from_edges(edges), edges=edges)
    return _ResolvedTravel(minutes=_travel_minutes_for(request), edges=())


def _drop_unknown_places(
    items: Sequence[ScheduleLLMItem], candidate_ids: set[str]
) -> tuple[list[ScheduleLLMItem], list[str]]:
    """후보 목록에 없는 place_id를 가진 항목을 버린다. (TP-215)

    **지어낸 id가 통과하면 되돌릴 수 없다.** 그대로 record_recommendation()에
    들어가 "추천됨"으로 기록되고, 이후 턴의 제외 목록에 올라 실재하는 장소를
    영구히 가린다. find_recommended_item()을 타고 보관함에도 담긴다. 화면에는
    상세가 없는 카드가 뜬다.

    지금까지 이 결함이 드러나지 않은 건 FakeLLMProvider가 candidates 앞에서
    고르기 때문일 뿐이다 — 실제 모델이 지어내면 막는 것이 아무것도 없었다.

    반환값은 (남긴 항목, 버린 place_id)이다. order는 호출부가 다시 매긴다.
    """

    kept: list[ScheduleLLMItem] = []
    dropped: list[str] = []
    for item in items:
        if item.place_id in candidate_ids:
            kept.append(item)
        else:
            dropped.append(item.place_id)
    return kept, dropped


def _build_basis_note(visit_datetime: datetime) -> str:
    """D 피드백 반영 — 근거 데이터(운영시간·날씨)가 단일 시각 기준이라 뒷 순서
    스탑에는 부정확할 수 있다는 걸 사용자에게 알리는 고정 안내 문구.

    LLM이 생성하지 않고 이 함수가 결정적으로 채운다(docs/design/
    int-07-schedule.md 6.2.1절) — 스탑별 재계산은 이번 범위 밖.
    """

    formatted = visit_datetime.strftime("%H:%M")
    return (
        f"{formatted} 기준으로 짠 일정이에요. "
        "실제로 가시는 시간에는 운영시간이나 날씨가 달라질 수 있어요."
    )


_RequestT = TypeVar("_RequestT", SchedulePlanningRequest, SchedulePartialFillRequest)


async def _with_co_visited_hints(
    request: _RequestT,
    place_ids: Sequence[str],
    fetcher: CoVisitedFetcher | None,
    settings: Settings | None,
) -> _RequestT:
    """co_visited_fetcher가 주어졌을 때만 place_associations를 조회해
    request.co_visited_hints를 채운 사본을 돌려준다.

    place_ids는 호출부가 명시적으로 넘긴다 — plan_schedule()은 candidates만
    보면 되지만, plan_partial_schedule()은 새로 채울 자리(candidates)뿐 아니라
    이미 확정된 pinned_items도 "함께 방문" 판단 대상에 넣어야 하기 때문이다
    (예: pinned로 남은 경복궁과 새로 채울 후보 중 하나가 실제로 같이 다닌
    곳이면 그것도 신호로 쓴다).

    실패(네트워크·설정 문제 등)는 SCHEDULE 전체를 막을 이유가 아니다 — 이
    힌트는 LLM에게 주는 참고 정보일 뿐 필수 입력이 아니므로, 조회가
    실패하면 경고만 남기고 힌트 없이(기존 동작) 계속한다("구조적 보장 우선"
    원칙과 같은 이유 — 부가 정보 실패가 핵심 기능을 무너뜨리면 안 된다).
    """
    if fetcher is None:
        return request
    try:
        hints = await fetcher(list(place_ids), settings or Settings())
    except Exception:  # noqa: BLE001 — 부가 힌트 실패는 SCHEDULE 흐름을 막지 않는다.
        logger.warning("co_visited_hints 조회 실패 — 힌트 없이 계속합니다.", exc_info=True)
        return request
    if not hints:
        return request
    return request.model_copy(update={"co_visited_hints": hints})


def _names_of(
    place_ids: Iterable[str], candidates: Sequence[RecommendationItem]
) -> list[str]:
    """place_id를 후보 목록의 장소 이름으로 바꾼다. 후보에 없으면 건너뛴다.

    사용자에게 보여줄 문구에 쓰는 값이라 place_id를 그대로 노출하지 않는다 —
    후보에 없는 id는 이름을 알 방법이 없으므로 여기서 빼고, 그런 장소는
    호출부(agent_runtime)가 보관함에 저장된 이름으로 따로 채운다(SCHEDULE-12).
    """

    name_by_place_id = {c.place_id: c.name for c in candidates}
    return [
        name_by_place_id[place_id]
        for place_id in place_ids
        if place_id in name_by_place_id
    ]


# 밀려난 보관함 장소를 되돌릴 때 그 자리에 쓰는 이유. LLM이 쓴 문장은 원래 그 자리에
# 있던 다른 장소를 설명하는 글이라 그대로 둘 수 없고, 새로 지어내지도 않는다 —
# 사용자가 직접 담았다는 것은 지어낸 값이 아니라 사실이다.
_RESTORED_ITEM_REASON = "보관함에 담아두신 곳이에요."

# 자리를 되돌린 턴의 동선 요약. LLM이 쓴 요약은 빠진 장소를 이름으로 언급할 수 있어
# ("…덕수궁 돌담길로 마무리하는 동선이에요") 그대로 쓰면 화면에 없는 곳을 말하게 된다.
# 앞에 "N시간 코스를 짜봤어요."가 붙으므로 "짜봤어요"를 다시 쓰지 않는다.
_RESTORED_ROUTE_SUMMARY = "담아두신 곳들을 순서대로 이어봤어요."


def _restore_displaced_must_include(
    items: Sequence[ScheduleLLMItem],
    missing: set[str],
    *,
    must_include: Sequence[str],
    saved_place_ids: Sequence[str],
    candidates: Sequence[RecommendationItem],
) -> tuple[list[ScheduleLLMItem], set[str], int]:
    """담지 않은 장소가 차지한 자리를 밀려난 보관함 장소에게 되돌린다. (TP-223)

    **왜 필요한가.** LLM이 `[반드시 포함]`을 재시도 후에도 어기면, 담아둔 곳이
    빠진 자리에 담지 않은 곳이 들어앉는다. 항목 수가 상한 이하이므로 이 조합은
    "자리가 없어서 뺐다"가 아니라 **"자리를 남에게 줬다"**는 뜻이다. 실제로
    관측된 상태다(TP-223: 6곳을 담았는데 2곳이 빠지고 안 담은 곳이 하나 들어옴).

    지금까지는 그 사실을 로그와 안내 문구로 알리기만 했다. 이 저장소는 반대로
    해왔다 — `_drop_unknown_places()`는 지어낸 id를 버리고,
    `plan_partial_schedule()`은 pinned를 LLM echo 대신 구조적으로 병합한다
    ("LLM 지시 준수보다 구조적 보장을 우선한다", SCHEDULE-07).

    **자리 수는 그대로다.** place_id·이름·이유만 바꾼다. 도착시각은 시간표가
    좌표로 다시 계산하므로 저절로 맞는다.

    **대기 중인 장소는 담은 순서로 꺼낸다**(`must_include` 순). 자르기와 같은
    기준이라 "왜 그 곳이 먼저인지"를 같은 말로 설명할 수 있다. 낯선 자리는 앞에서
    부터 채운다 — 순서는 시간표가 아니라 동선의 문제이고, LLM이 정한 방문 순서를
    최소한으로 흔든다.

    **자리가 남아서 들어온 장소는 건드리지 않는다.** 밀려난 보관함 장소가 없으면
    (missing이 비면) 아무것도 바꾸지 않는다 — 그건 설계된 동작이다
    (`prompts/schedule/plan.md`, "남는 자리를 다른 후보로 채우세요").

    반환값은 (바뀐 items, 아직 못 되돌린 missing, 되돌린 자리 수)이다.
    """

    if not missing:
        return list(items), missing, 0

    waiting = [place_id for place_id in must_include if place_id in missing]
    saved = set(saved_place_ids)
    name_by_place_id = {c.place_id: c.name for c in candidates}

    restored: list[ScheduleLLMItem] = []
    swapped = 0
    for item in items:
        if item.place_id in saved or not waiting:
            restored.append(item)
            continue
        place_id = waiting.pop(0)
        restored.append(
            item.model_copy(
                update={
                    "place_id": place_id,
                    "place_name": name_by_place_id.get(place_id, item.place_name),
                    "reason": _RESTORED_ITEM_REASON,
                }
            )
        )
        swapped += 1

    return restored, set(waiting), swapped


def _added_place_names(
    request: SchedulePlanningRequest, items: Sequence[ScheduleItem]
) -> list[str]:
    """보관함에 없었는데 편성에 들어간 장소 이름. (TP-223)

    **자르기 전 원본 목록과 비교한다.** `_resolve_must_include()`가 상한 때문에
    줄인 목록과 비교하면, 잘린 보관함 장소를 LLM이 그래도 골랐을 때 그 곳이
    "새로 찾은 곳"으로 잘못 안내된다 — 사용자가 담아둔 곳인데도.

    보관함을 쓰지 않은 턴에는 빈 리스트다. 그때는 모든 장소가 새로 찾은 곳이라
    알릴 내용이 아니다.
    """

    if not request.must_include_place_ids:
        return []
    saved = set(request.must_include_place_ids)
    return [item.place_name for item in items if item.place_id not in saved]


def _resolve_must_include(
    request: SchedulePlanningRequest, max_items: int
) -> tuple[list[str], list[str]]:
    """이번 편성에서 실제로 강제할 place_id와, 상한 때문에 뺀 장소 이름을 가른다.
    (SCHEDULE-12)

    두 단계로 줄인다.

    1. 후보 목록에 없는 id는 강제할 수 없다(폐점 하드 필터 등으로 D가 걸러낸
       경우). 여기서는 이름도 알 수 없으므로 조용히 빼고, 호출부가 보관함에
       저장된 이름으로 안내를 채운다.
    2. 남은 것이 항목 수 상한(`derive_item_range()`의 max)을 넘으면 **담은
       순서대로** 앞에서부터 상한까지만 쓴다. 점수 순으로 자르지 않는 이유는
       "왜 그 곳이 빠졌는지" 사용자에게 설명할 수 있어야 하기 때문이다
       (SavedPlaceList.items docstring과 같은 근거).

    반환값은 (강제할 place_id, 상한 때문에 못 담은 장소 이름)이다.
    """

    candidate_ids = {c.place_id for c in request.candidates}
    present = [
        place_id
        for place_id in request.must_include_place_ids
        if place_id in candidate_ids
    ]
    if len(present) <= max_items:
        return present, []
    return present[:max_items], _names_of(present[max_items:], request.candidates)


def _cap_item_count(
    items: Sequence[ScheduleLLMItem], max_items: int
) -> list[ScheduleLLMItem]:
    """유도한 개수 상한을 넘겨 온 항목을 잘라낸다. (TP-239)

    **프롬프트 지시는 부탁이고 이 자르기가 계약이다**(SCHEDULE-07과 같은 철학).
    상한을 예산에서 유도해 프롬프트에 적어 줘도 LLM이 더 많이 골라 올 수 있고,
    그러면 시간이 다시 어긋난다 — 실측으로 2시간 요청(상한 2곳)에 4곳 285분이
    나왔다. `ScheduleLLMPlan.items`의 `max_length`는 하드 캡(5)이라 이걸 못 막는다.

    **뒤에서부터 자르고 그 뒤는 기존 기계에 맡긴다.** 잘린 자리에 보관함 장소가
    있었으면 이어지는 `_missing_must_include()`가 빠진 것으로 보고,
    `_restore_displaced_must_include()`가 남은 자리로 되돌린다. 자르기와 되돌리기를
    따로 만들면 두 규칙이 서로를 모르게 된다.
    """

    if len(items) <= max_items:
        return list(items)
    logger.info("schedule.item_count_capped from=%d to=%d", len(items), max_items)
    return list(items[:max_items])


def _rebudget_item_cap(
    request: SchedulePlanningRequest,
    items: Sequence[ScheduleLLMItem],
    *,
    derived_max: int,
) -> tuple[int, list[str], list[str]]:
    """LLM이 고른 항목의 실제 분류로 상한을 다시 재고 보관함 판정도 함께 고친다.

    `derive_item_range()`의 상한은 후보 중 체류 최소값이 **작은 것부터** 센 값이라
    (의도된 하한), LLM이 비싼 분류를 고르면 예산을 넘긴다. 근거와 실측은
    `budget.cap_item_count_to_budget()` docstring에 있다.

    **묶음도 여기서 확정된다.** 도보로 이웃과 붙은 자리는 체류 최소값이 낮아져
    같은 예산에 한 자리가 더 들어갈 수 있다 — 그 한 자리가 상한이다(TP-243).

    **상한이 줄면 보관함 판정을 다시 해야 한다.** `over_capacity_place_names`가
    처음 상한으로 계산된 값이면, 줄어든 자리 때문에 못 들어간 보관함 장소가
    아무 안내 없이 사라진다. `_resolve_must_include()`를 다시 부르는 것으로 두
    값이 같은 상한을 보게 한다 — 상한이 그대로면 같은 값이 다시 나온다.

    **재시도 프롬프트의 `[반드시 포함]` 목록은 건드리지 않는다.** 이미 LLM을
    부른 뒤이고, 줄어든 자리에 못 들어간 장소는 `over_capacity_place_names`가
    안내한다. 시도마다 프롬프트 입력이 달라지면 두 응답을 비교할 근거가 사라진다.
    """

    category_by_id = {c.place_id: c.category for c in request.candidates}
    # 방문 순서가 이제 있으므로 묶음을 실제로 잰다(TP-243). 상한을 처음 정할
    # 때(`derive_item_range()`)는 순서가 없어 낙관적 추정을 썼다.
    clusters = cluster_ids_in_order(request, [item.place_id for item in items])
    effective = cap_item_count_to_budget(
        request,
        [category_by_id.get(item.place_id) for item in items],
        hard_cap=derived_max,
        clustered_flags=[cluster_id is not None for cluster_id in clusters],
    )
    if effective < derived_max:
        logger.info(
            "schedule.item_capacity_rebudgeted from=%d to=%d", derived_max, effective
        )
    resolved, dropped = _resolve_must_include(request, effective)
    return effective, resolved, dropped


def _missing_must_include(
    must_include: Sequence[str], items: Sequence[ScheduleLLMItem]
) -> set[str]:
    """LLM 응답에서 빠진 강제 포함 place_id. 없으면 빈 집합. (SCHEDULE-12)

    후보에 없는 항목을 걸러낸 뒤(_drop_unknown_places) 판정한다 — must_include는
    이미 후보 안의 id만 남긴 것이라 결과는 같지만, 순서를 뒤집으면 지어낸 id가
    강제 포함을 만족시킨 것처럼 보일 여지가 생긴다.
    """

    if not must_include:
        return set()
    return set(must_include) - {item.place_id for item in items}


async def plan_schedule(
    request: SchedulePlanningRequest,
    llm: LLMProvider,
    *,
    timer: Timer = perf_counter,
    co_visited_fetcher: CoVisitedFetcher | None = None,
    settings: Settings | None = None,
    travel_route_tool: TravelRouteTool | None = None,
) -> ScheduleResult:
    """SchedulePlanningRequest로 LLM을 호출해 ScheduleResult를 만든다.

    visit_datetime이 없으면 현재 시각(KST)을 기준으로 삼는다 — LLM 호출과
    basis_note 둘 다 같은 시각을 쓰도록 여기서 한 번만 결정한다(design doc 9절
    "estimated_arrival 기준 시각" 미결 사항 해소: 상대 표현 대신 항상 구체적인
    시작 시각을 LLM에 준다).

    elapsed_ms는 이 함수 진입부터 결과 조립까지의 처리시간이다 —
    RecommendationResponse.elapsed_ms(app/services/recommendation_pipeline.py)와
    같은 패턴으로 재서, 개발자 화면(카드·감사 패널)이 RECOMMEND와 동일하게
    SCHEDULE의 서버 소요시간도 보여줄 수 있게 한다(RECOMMEND는 이 값이 있는데
    SCHEDULE만 없어 "서버 소요"가 항상 빈 값으로 보이던 걸 정리함).

    co_visited_fetcher는 opt-in이다(app.schedule.associations.fetch_co_visited_hints를
    호출부가 넘겨야 켜진다) — 기본값 None이면 이 함수는 기존과 완전히 동일하게
    동작한다.
    """

    started_at = timer()
    effective_visit_datetime = request.visit_datetime or now_kst()

    # 이번 요청의 예산에 맞는 개수 범위를 구한다(TP-239). 버킷 상수가 아니라
    # 체류 최소값과 이번 후보들의 실제 거리로 계산한다 — budget.derive_item_range().
    # 후보가 최솟값보다 적으면 LLM을 부르지 않는다 — ScheduleLLMPlan.items가 그
    # 개수를 애초에 만족시킬 수 없어 호출해도 재시도까지 실패로 끝날 뿐이다
    # (SCHEDULE-07의 가드를 동적 최솟값으로 확장). 상한은 보관함 개수 충돌
    # 판정에 쓴다(SCHEDULE-12).
    min_items, max_items = derive_item_range(request)
    # 후보 부족 가드는 개수 범위의 최솟값이 아니라 이 함수가 답한다 — 시간을
    # 말하지 않은 요청은 후보 3곳을 요구하는 옛 동작을 유지한다(SCHEDULE-07).
    required_candidates = required_candidate_count(request, min_items=min_items)
    # 프롬프트에 넣는 목표값이다. 응답이 온 뒤 실제 분류로 다시 재므로
    # (`_rebudget_item_cap()`) 원래 값을 따로 들고 있어야 한다.
    derived_max = max_items
    if len(request.candidates) < required_candidates:
        return ScheduleResult(
            items=[],
            total_duration_min=0,
            route_summary=_NO_CANDIDATES_ROUTE_SUMMARY,
            basis_note=_build_basis_note(effective_visit_datetime),
            item_capacity=max_items,
            elapsed_ms=round((timer() - started_at) * 1000, 2),
        )

    # 두 사유를 끝까지 갈라서 들고 간다(TP-223). 예전에는 여기서 나온 "상한 초과"와
    # 아래 "재시도 후에도 LLM이 빠뜨림"을 한 리스트에 합쳐 넘겨, 화면이 두 경우에
    # 같은 문장을 냈다 — 사용자가 할 수 있는 일이 서로 다른데도.
    must_include, over_capacity_names = _resolve_must_include(request, max_items)
    omitted_names: list[str] = []

    resolved_request = (
        request
        if request.visit_datetime is not None
        else request.model_copy(update={"visit_datetime": effective_visit_datetime})
    )
    if list(must_include) != list(request.must_include_place_ids):
        resolved_request = resolved_request.model_copy(
            update={"must_include_place_ids": list(must_include)}
        )
    resolved_request = await _with_co_visited_hints(
        resolved_request,
        [candidate.place_id for candidate in resolved_request.candidates],
        co_visited_fetcher,
        settings,
    )

    candidate_ids = {candidate.place_id for candidate in resolved_request.candidates}

    plan = (await llm.generate_schedule_plan(resolved_request)).data
    llm_items, hallucinated = _drop_unknown_places(plan.items, candidate_ids)
    max_items, must_include, over_capacity_names = _rebudget_item_cap(
        request, llm_items, derived_max=derived_max
    )
    llm_items = _cap_item_count(llm_items, max_items)
    missing = _missing_must_include(must_include, llm_items)
    if missing:
        # 프롬프트 지시는 부탁이고 이 검증이 계약이다(SCHEDULE-07과 같은 철학).
        # 한 번만 다시 부른다 — 같은 입력으로 무한히 조르지 않고, 두 번째도
        # 실패하면 그 사실을 사용자에게 알린다(아래 omitted_names).
        logger.info(
            "schedule.must_include_missing retrying place_ids=%s",
            sorted(missing),
        )
        plan = (await llm.generate_schedule_plan(resolved_request)).data
        llm_items, hallucinated = _drop_unknown_places(plan.items, candidate_ids)
        max_items, must_include, over_capacity_names = _rebudget_item_cap(
            request, llm_items, derived_max=derived_max
        )
        llm_items = _cap_item_count(llm_items, max_items)
        missing = _missing_must_include(must_include, llm_items)

    if hallucinated:
        logger.warning(
            "schedule.unknown_place_ids dropped=%s", sorted(set(hallucinated))
        )

    # LLM이 items는 빈 배열로 주면서 total_duration_min/route_summary는 그럴듯한
    # 문장으로 채워 보내는 비일관 응답이 실제로 관측됐다(2026-08-10 real Gemini
    # 수동 테스트, SCHEDULE-06 후속). SCHEDULE-07부터 ScheduleLLMPlan.items에
    # min_length=3 제약이 걸려 있어 실제 Gemini 경로에서는 이 분기가 원칙적으로
    # 발생하지 않지만(검증 실패 시 gemini.py의 재시도 후에도 실패하면 예외로
    # 올라간다), FakeLLMProvider 등 스키마 검증을 안 거치는 테스트 더블까지
    # 방어하기 위해 남겨둔다.
    if not llm_items:
        return ScheduleResult(
            items=[],
            total_duration_min=0,
            route_summary=_NO_CANDIDATES_ROUTE_SUMMARY,
            basis_note=_build_basis_note(effective_visit_datetime),
            # 일정을 못 짠 턴에도 상한 초과는 알린다 — 담아둔 곳이 왜 안 보이는지는
            # 일정이 나왔든 안 나왔든 사용자가 똑같이 궁금해한다
            # (response_composer.compose_schedule_message docstring과 같은 근거).
            over_capacity_place_names=over_capacity_names,
            item_capacity=max_items,
            elapsed_ms=round((timer() - started_at) * 1000, 2),
        )

    # 담지 않은 장소가 차지한 자리를 밀려난 보관함 장소에게 되돌린다(TP-223).
    # 재시도 뒤에 둔다 — 먼저 LLM에게 한 번 더 기회를 주고, 그래도 안 지키면 코드가
    # 고친다. 자리가 남아서 들어온 장소는 건드리지 않는다.
    llm_items, missing, restored_count = _restore_displaced_must_include(
        llm_items,
        missing,
        must_include=must_include,
        saved_place_ids=request.must_include_place_ids,
        candidates=resolved_request.candidates,
    )
    if restored_count:
        logger.info("schedule.must_include_restored slots=%d", restored_count)

    if missing:
        # 재시도 후에도 빠진 것은 502로 턴을 죽이지 않고 결과를 살린다 —
        # plan_partial_schedule()의 하드 실패와 다른 선택이다. 저쪽은 "유지해야
        # 할 기존 일정"이 걸려 있어 잘못된 응답이 기존 항목을 훼손하지만,
        # 보관함은 그렇지 않고 장바구니에서 "일부를 못 담았다"는 전체 실패보다
        # 낫다(SCHEDULE-12). 대신 조용히 빠뜨리지 않고 이름을 실어 보낸다.
        logger.warning(
            "schedule.must_include_missing after retry place_ids=%s",
            sorted(missing),
        )
        omitted_names = _names_of(missing, request.candidates)

    candidate_by_id = {c.place_id: c for c in resolved_request.candidates}
    # **최종 순서로 묶음을 다시 잰다**(TP-243). `_rebudget_item_cap()`도 같은
    # 함수를 쓰지만 그때는 밀려난 보관함 장소를 되돌리기 전이라 순서가 다를 수
    # 있다 — 체류시간에 쓰는 것은 화면에 나갈 이 순서다.
    cluster_ids = cluster_ids_in_order(request, [item.place_id for item in llm_items])
    clustered_flags = [cluster_id is not None for cluster_id in cluster_ids]
    drafts = [
        _draft_from_llm_item(
            item, candidate_by_id.get(item.place_id), order, clustered=clustered
        )
        for order, (item, clustered) in enumerate(
            zip(llm_items, clustered_flags, strict=True), start=1
        )
    ]
    travel = await _resolve_travel_minutes(
        resolved_request,
        [draft.place_id for draft in drafts],
        settings=settings,
        travel_route_tool=travel_route_tool,
        # 구간 이동수단을 거리뿐 아니라 날씨·동행·무장애까지 보고 정한다(TP-227).
        # 편성에 쓰는 LLM을 그대로 쓴다 — 판정만 다른 Provider로 두면 한 턴 안에서
        # 모델이 갈려 관측·비용이 두 곳으로 흩어진다.
        mode_judge=LlmModeJudge(llm),
    )
    schedule_start_at = _round_up_start(effective_visit_datetime)
    timeline = _build_schedule_timeline(
        drafts,
        start_at=schedule_start_at,
        travel_minutes=travel.minutes,
        candidates=resolved_request.candidates,
    )
    drafts, timeline = _fit_to_time_available(
        drafts,
        timeline,
        time_available_min=request.conditions.time_available,
        candidates=resolved_request.candidates,
        start_at=schedule_start_at,
        travel_minutes=travel.minutes,
        clustered_flags=clustered_flags,
    )

    items = _compose_items(
        drafts,
        timeline,
        resolved_request.candidates,
        travel.edges,
        cluster_ids=cluster_ids,
    )
    return ScheduleResult(
        items=items,
        total_duration_min=timeline.total_duration_min,
        # 자리를 되돌렸으면 LLM 요약을 쓰지 않는다 — 그 문장은 지금 일정에 없는
        # 장소를 이름으로 언급할 수 있다.
        route_summary=_RESTORED_ROUTE_SUMMARY if restored_count else plan.route_summary,
        basis_note=_build_basis_note(effective_visit_datetime),
        omitted_saved_place_names=omitted_names,
        over_capacity_place_names=over_capacity_names,
        added_place_names=_added_place_names(request, items),
        item_capacity=max_items,
        time_budget_status=classify_budget(
            timeline.total_duration_min, request.conditions.time_available
        ),
        elapsed_ms=round((timer() - started_at) * 1000, 2),
    )


# SCHEDULE-09 2단계 — 부분 재편성(REJECT_SPECIFIC) 전용.
_NO_FILL_CANDIDATES_ROUTE_SUMMARY = (
    "바꿀 만한 곳을 찾지 못해서 일정은 그대로 뒀어요. "
    "조건을 조금 넓혀서 다시 말씀해 주시겠어요?"
)


def _anchor_start(items: Sequence[ScheduleItem], fallback: datetime) -> datetime:
    """부분 재편성 시간표의 시작 시각.

    첫 항목이 유지되는 자리(pinned)라면 그 자리의 도착 시각이 이미 사용자에게
    보인 값이므로 그대로 기준점으로 쓴다 — 자리 하나를 바꿨다고 일정 전체가 앞뒤로
    움직이면 "나머지는 그대로 뒀다"는 말이 안 맞는다. 파싱에 실패하면(방어적
    상황) 편성 기준 시각으로 되돌아간다.
    """

    if not items:
        return _round_up_start(fallback)
    minutes = _parse_hhmm_minutes(items[0].estimated_arrival)
    if minutes is None:
        return _round_up_start(fallback)
    midnight = fallback.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight + timedelta(minutes=minutes)


async def _pinned_only_result(
    request: SchedulePartialFillRequest,
    visit_datetime: datetime,
    route_summary: str,
    elapsed_ms: float,
    *,
    settings: Settings | None = None,
    travel_route_tool: TravelRouteTool | None = None,
    # 전체 편성과 같은 판정을 쓴다(TP-227). 안 넘기면 같은 사용자가 전체 편성과
    # 부분 수정에서 다른 이동수단을 받는다.
    mode_judge: ModeJudge | None = None,
) -> ScheduleResult:
    """새로 채운 자리 없이 기존 항목만으로 결과를 만든다.

    유지 항목만 남아도 구간 이동시간은 같은 경로로 확정한다(TP-216) — 여기만
    직선거리 추정을 쓰면 같은 일정이 실패 여부에 따라 다른 시각을 갖게 된다.
    """

    ordered = sorted(request.pinned_items, key=lambda item: item.order)
    drafts = [
        _draft_from_schedule_item(item, order)
        for order, item in enumerate(ordered, start=1)
    ]
    travel = await _resolve_travel_minutes(
        request,
        [draft.place_id for draft in drafts],
        settings=settings,
        travel_route_tool=travel_route_tool,
        mode_judge=mode_judge,
    )
    timeline = _build_schedule_timeline(
        drafts,
        start_at=_anchor_start(ordered, visit_datetime),
        travel_minutes=travel.minutes,
        candidates=request.candidates,
    )
    return ScheduleResult(
        # 유지만 하는 턴에도 묶음 번호는 실어 보낸다(TP-243) — 화면이 같은 일정을
        # 어떤 턴에서 보느냐에 따라 다르게 그리면 안 된다. **체류시간은 여전히
        # 건드리지 않는다**(`_draft_from_schedule_item()`의 "그대로 뒀다는 약속").
        items=_compose_items(
            drafts,
            timeline,
            request.candidates,
            travel.edges,
            cluster_ids=cluster_ids_in_order(
                request, [draft.place_id for draft in drafts]
            ),
            pinned_items=request.pinned_items,
        ),
        total_duration_min=timeline.total_duration_min,
        route_summary=route_summary,
        basis_note=_build_basis_note(visit_datetime),
        elapsed_ms=elapsed_ms,
    )


async def plan_partial_schedule(
    request: SchedulePartialFillRequest,
    llm: LLMProvider,
    *,
    timer: Timer = perf_counter,
    co_visited_fetcher: CoVisitedFetcher | None = None,
    settings: Settings | None = None,
    travel_route_tool: TravelRouteTool | None = None,
) -> ScheduleResult:
    """SchedulePartialFillRequest로 일부 슬롯만 새로 채운 ScheduleResult를 만든다.

    (SCHEDULE-09 2단계, SCHEDULE-부분수정-해결방향-설계안.md 3절)

    pinned_items는 LLM에 echo를 요청하지 않고 이 함수가 구조적으로 최종
    결과에 병합한다 — LLM은 target_orders 자리에 들어갈 new_items만
    반환한다. 응답의 order 집합이 target_orders와 정확히 일치하는지 여기서
    직접 검증하고, 불일치하면 llm_output_invalid로 실패 처리한다(개수가
    요청마다 달라 ScheduleLLMPlan처럼 Pydantic Field로 정적 강제할 수 없다 —
    SchedulePartialLLMPlan 참고).

    elapsed_ms는 plan_schedule()과 같은 방식으로 이 함수 진입부터 결과 조립까지
    잰다(SCHEDULE-10 후속, RECOMMEND와 서버 소요시간 표시 방식을 맞춘다).

    co_visited_fetcher는 plan_schedule()과 같은 opt-in 계약이다 — 기본값
    None이면 기존과 완전히 동일하게 동작한다. 켜지면 pinned_items +
    candidates 전체의 place_id로 조회한다(둘 중 한쪽에만 있어서는 "함께
    방문" 쌍이 안 잡히므로).
    """

    started_at = timer()
    effective_visit_datetime = request.visit_datetime or now_kst()

    if not request.target_orders:
        # 파싱 단계(SCHEDULE-09 1단계)가 REJECT_SPECIFIC일 때 항상 target_indices를
        # 채우므로 정상 흐름에서는 발생하지 않는다 — 방어적으로만 처리한다.
        return await _pinned_only_result(
            request,
            effective_visit_datetime,
            _NO_FILL_CANDIDATES_ROUTE_SUMMARY,
            round((timer() - started_at) * 1000, 2),
            settings=settings,
            travel_route_tool=travel_route_tool,
            mode_judge=LlmModeJudge(llm),
        )

    # 유지 대상(pinned)이 후보에 섞여 있으면 그 자리에 같은 장소가 다시 뽑혀
    # 한 일정에 중복으로 들어간다. 프롬프트도 "pinned_items의 place_id를 다시
    # 고르지 마세요"라고 지시하지만(fill.md) LLM 지시는 구조적 보장이 아니다 —
    # 여기서 후보 자체에서 빼서 고를 수 없게 만든다(basis_note·도착시각을 LLM에
    # 맡기지 않는 것과 같은 기조).
    #
    # 지금까지는 호출부의 제외 목록(recommended ∪ rejected)이 pinned를 후보에서
    # 먼저 걸러내 이 문제가 드러나지 않았다. 그 목록이 무엇을 담는지에 편성
    # 정확성이 딸려 있으면 안 된다.
    pinned_place_ids = {item.place_id for item in request.pinned_items}
    fillable_candidates = [
        candidate
        for candidate in request.candidates
        if candidate.place_id not in pinned_place_ids
    ]
    if len(fillable_candidates) != len(request.candidates):
        request = request.model_copy(update={"candidates": fillable_candidates})

    if not request.candidates:
        # 유지 대상(pinned)과 거절 대상을 빼고 나니 채울 수 있는 새 후보가
        # 아예 없다 — "일정 전체 실패"가 아니라 "일부만 대체 실패"이므로 pinned은
        # 그대로 살리고 실패 사실만 안내한다(전체 재구성으로 덮어쓰지 않음).
        return await _pinned_only_result(
            request,
            effective_visit_datetime,
            _NO_FILL_CANDIDATES_ROUTE_SUMMARY,
            round((timer() - started_at) * 1000, 2),
            settings=settings,
            travel_route_tool=travel_route_tool,
            mode_judge=LlmModeJudge(llm),
        )

    resolved_request = (
        request
        if request.visit_datetime is not None
        else request.model_copy(update={"visit_datetime": effective_visit_datetime})
    )
    resolved_request = await _with_co_visited_hints(
        resolved_request,
        [
            *(item.place_id for item in resolved_request.pinned_items),
            *(candidate.place_id for candidate in resolved_request.candidates),
        ],
        co_visited_fetcher,
        settings,
    )

    plan = (await llm.generate_schedule_fill(resolved_request)).data

    expected_orders = set(request.target_orders)
    actual_orders = {item.order for item in plan.new_items}
    if actual_orders != expected_orders or len(plan.new_items) != len(request.target_orders):
        raise AppError(
            code="llm_output_invalid",
            message="일정 재구성 응답을 해석하지 못했습니다.",
            status_code=502,
            retryable=True,
            provider="Gemini",
            details={
                "expected_orders": sorted(expected_orders),
                "actual_orders": sorted(actual_orders),
            },
        )

    # 후보에 없는 place_id는 여기서 하드 실패다(TP-215). plan_schedule()은 그런
    # 항목만 버리고 나머지로 일정을 살리지만, 이쪽은 "유지해야 할 기존 일정"이
    # 걸려 있어 자리 하나를 못 채우면 order 집합이 어긋난다 — 조용히 자리를 비우면
    # 그 뒤 항목들의 순서가 밀려 사용자가 유지하기로 한 일정이 망가진다.
    candidate_ids = {candidate.place_id for candidate in resolved_request.candidates}
    unknown = [item.place_id for item in plan.new_items if item.place_id not in candidate_ids]
    if unknown:
        logger.warning("schedule.fill_unknown_place_ids place_ids=%s", sorted(set(unknown)))
        raise AppError(
            code="llm_output_invalid",
            message="일정 재구성 응답을 해석하지 못했습니다.",
            status_code=502,
            retryable=True,
            provider="Gemini",
            details={"unknown_place_ids": sorted(set(unknown))},
        )

    # 유지 항목과 새 항목을 order로 합치고, 시각은 전체를 한 번에 다시 계산한다.
    #
    # 예전에는 세 가지를 따로 손봤다 — 새 항목의 도착 시각은 LLM이 준 값을 앵커로
    # 믿고, 그 뒤 pinned 항목만 누적으로 다시 맞추고(_resync_downstream_arrivals),
    # 바뀐 자리 바로 앞 pinned의 travel_to_next_min은 stale이라 None으로 지웠다.
    # 셋 다 "LLM이 준 시각 일부는 맞다"는 전제 위에 서 있었다. 지금은 시간표를
    # 통째로 다시 계산하므로 앵커도 stale도 없다 — 구간 이동시간은 이번 순서
    # 기준으로 전부 새로 구한다.
    candidate_by_id = {c.place_id: c for c in resolved_request.candidates}
    new_by_order = {item.order: item for item in plan.new_items}
    pinned_by_order = {item.order: item for item in request.pinned_items}

    drafts: list[_ItemDraft] = []
    for order, source_order in enumerate(sorted([*new_by_order, *pinned_by_order]), start=1):
        new_item = new_by_order.get(source_order)
        if new_item is not None:
            drafts.append(
                _draft_from_llm_item(
                    new_item, candidate_by_id.get(new_item.place_id), order
                )
            )
        else:
            drafts.append(_draft_from_schedule_item(pinned_by_order[source_order], order))

    # 첫 자리가 그대로 유지되는 자리면 그 도착 시각을 기준점으로 삼는다. 첫 자리가
    # 이번에 교체됐다면 기준으로 삼을 값이 없으므로(그 자리의 옛 도착 시각은 이제
    # 다른 장소의 것이다) 편성 기준 시각에서 새로 시작한다.
    first_order = min([*new_by_order, *pinned_by_order])
    first_pinned = pinned_by_order.get(first_order)
    start_at = (
        _anchor_start([first_pinned], effective_visit_datetime)
        if first_pinned is not None
        else _round_up_start(effective_visit_datetime)
    )
    travel = await _resolve_travel_minutes(
        resolved_request,
        [draft.place_id for draft in drafts],
        settings=settings,
        travel_route_tool=travel_route_tool,
        # 구간 이동수단을 거리뿐 아니라 날씨·동행·무장애까지 보고 정한다(TP-227).
        # 편성에 쓰는 LLM을 그대로 쓴다 — 판정만 다른 Provider로 두면 한 턴 안에서
        # 모델이 갈려 관측·비용이 두 곳으로 흩어진다.
        mode_judge=LlmModeJudge(llm),
    )
    timeline = _build_schedule_timeline(
        drafts,
        start_at=start_at,
        travel_minutes=travel.minutes,
        candidates=resolved_request.candidates,
    )
    drafts, timeline = _fit_to_time_available(
        drafts,
        timeline,
        time_available_min=request.conditions.time_available,
        candidates=resolved_request.candidates,
        start_at=start_at,
        travel_minutes=travel.minutes,
    )

    kept = len(request.pinned_items)
    replaced = len(plan.new_items)
    route_summary = f"{kept}곳은 그대로 두고 {replaced}곳만 다른 곳으로 바꿨어요."

    return ScheduleResult(
        items=_compose_items(
            drafts,
            timeline,
            resolved_request.candidates,
            travel.edges,
            cluster_ids=cluster_ids_in_order(
                request, [draft.place_id for draft in drafts]
            ),
            pinned_items=request.pinned_items,
        ),
        total_duration_min=timeline.total_duration_min,
        route_summary=route_summary,
        basis_note=_build_basis_note(effective_visit_datetime),
        time_budget_status=classify_budget(
            timeline.total_duration_min, request.conditions.time_available
        ),
        elapsed_ms=round((timer() - started_at) * 1000, 2),
    )


__all__ = ["plan_schedule", "plan_partial_schedule"]
