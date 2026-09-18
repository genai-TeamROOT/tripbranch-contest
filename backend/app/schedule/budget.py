"""일정 예산 산수 — 개수와 체류시간이 같은 활동 가능 시간을 보게 만드는 곳. (TP-238)

**왜 이 모듈이 따로 있는가.** 같은 예산을 나눠 쓰는 규칙이 셋인데 서로를 몰랐다.
`target_item_range()`가 곳 수를 버킷 상수로 정하고, LLM이 장소별 체류시간을 제안하고,
`duration.resolve_visit_duration()`이 그 제안을 분류별 범위로 자른다. 셋 중
**어느 것도 활동 가능 시간을 보지 않는다.** `timeline`이 마지막에 합산할 뿐이고
합이 예산을 넘어도 조정하는 곳이 없어서, "3시간 코스"가 4시간 26분으로 나갔다.

`duration.py`가 이 함정을 미리 적어뒀다 — "두 곳이 서로 다른 가정을 쓰면 개수는
맞는데 시간이 안 맞는 일정이 나온다." 고치려면 **예산을 아는 자리가 하나** 있어야
하고, 그게 이 모듈이다.

**허용 오차가 왜 필요한가.** 관광지 최소 체류가 60분이고 구간 이동이 15분이면
3시간에 3곳은 아무리 줄여도 210분이라 정확히는 못 맞춘다. 오차를 안 두면 그런
요청이 2곳으로 떨어진다 — 시간은 지켜지지만 좋은 답이 아니다. 30분은 임의값이
아니라 **"3시간에 3곳"이 통과하는 최소 문턱**이다(60*3 + 15*2 - 180 = 30). 같은
계산으로 4시간에 4곳은 45분이 필요해서 자동으로 막힌다 — 오차를 키우면 "짧게
많이" 쪽으로 새어나간다는 뜻이라, 이 값은 밀도 상한을 겸한다.

**무엇을 고르는지는 건드리지 않는다.** 이미 고른 장소의 체류시간만 분류별 정책
범위 안에서 조절한다. 후보 선정에 근접도를 섞으면 좋은 장소가 밀리고, 구 단위
요청에서 거리 축 영향을 줄인 SCORING_VERSION 1.9.0 방향과도 어긋난다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.place_search_policy import WALKING_SPEED_KM_PER_MINUTE
from app.schedule.duration import (
    VISIT_DURATION_STEP_MIN,
    VisitDurationPolicy,
    policy_for,
)
from app.schedule.schemas import SchedulePartialFillRequest, SchedulePlanningRequest
from app.schedule.timeline import (
    FALLBACK_TRAVEL_MINUTES,
    estimated_travel_minutes,
    travel_speed_km_per_minute,
)
from app.schemas import ScheduleBudgetStatus

# 요청한 활동 가능 시간과 실제 편성 결과 사이에 허용하는 오차(분).
#
# 예전에는 이 값이 response_composer에 `_DURATION_MATCH_TOLERANCE_MIN`이라는
# 이름으로 있었고 **표시에만** 쓰였다 — 라벨을 요청값으로 쓸지, 초과 안내를
# 붙일지. 편성 쪽에는 목표가 없어서 판정만 있고 지킬 방법이 없었다. 이제 편성이
# 이 값을 목표로 삼고 표시가 그 판정을 읽는다. **상수를 두 벌 두지 않는다.**
SCHEDULE_TIME_TOLERANCE_MIN = 30

# 한 일정에 넣을 수 있는 항목 수의 하드 캡. `ScheduleLLMPlan.items`의 `max_length`와
# 같은 수여야 한다 — 유도한 상한이 이 값을 넘으면 LLM 응답이 검증에서 거부된다.
MAX_SCHEDULE_ITEMS = 5

# 활동 가능 시간을 말하지 않은 요청에 가정하는 예산(분).
#
# **새로 정한 값이 아니라 이미 있던 암묵적 기본값을 한곳으로 모은 것이다.**
# 근거가 셋이다.
#
# 1. `gemini_prompts.build_schedule_planning_instruction()`이 시간이 없을 때
#    "3~4시간 내외로 구성하세요"라고 이미 안내한다
# 2. 여기 있던 상수 `_ITEM_RANGE_WITHOUT_BUDGET = (3, 5)`의 주석이 그 프롬프트
#    문구를 근거로 만든 값이라고 밝히고 있었다
# 3. D가 "반나절"을 분으로 환산할 때 **같은 폴백에 맞춰 240으로 확정했다**
#    (`app/prompts/recommend/HISTORY.md` 2.5.0 항목)
#
# **문제는 개수만 그 기본값을 따르고 시간은 아무도 따르지 않았다는 것이다.**
# 상한이 상수 5로 고정되고 배분도 판정도 건너뛰어져서, 실측에서 258~420분
# 일정이 아무 안내 없이 나갔다(2026-09-07, 13턴 중 5턴).
SCHEDULE_DEFAULT_TIME_BUDGET_MIN = 240

# 시간을 말하지 않은 요청이 LLM을 부르기 위해 요구하는 **후보 수**. SCHEDULE-07
# 동작이라 기본 예산을 도입해도 그대로 둔다 — 이 값을 내리면 "후보가 부족해요"
# 대신 1곳짜리 일정이 나가기 시작하고, 그건 별개의 제품 판단이다.
#
# **개수 범위의 최솟값과 다른 것이다.** 예전에는 한 상수가 둘을 겸했는데, 기본
# 예산을 넣으면서 겸할 수 없게 됐다 — 상한이 예산에서 나오므로 문화시설 세 곳처럼
# 비싼 후보에서는 상한이 2가 되고, 그때 범위 최솟값까지 3이면 프롬프트에
# "3개 이상 2개 이하"라는 모순된 범위가 실린다.
_REQUIRED_CANDIDATES_WITHOUT_BUDGET = 3

# 묶음 기준 — 이웃한 두 자리를 도보로 잇는 시간의 상한(분). (TP-243)
#
# 사용자 문의 원문("5분 거리 이내인 세 장소")에서 온 값이다. 도보 속도는
# `place_search_policy.WALKING_SPEED_KM_PER_MINUTE`를 쓴다 — 실제 이동수단이
# 대중교통으로 잡히더라도 **묶음 판정은 걸어서 얼마인가**로 한다. "가까이 붙어
# 있으니 가볍게 둘러보는 코스"라는 근거가 도보 거리에서 나오기 때문이다.
SCHEDULE_CLUSTER_WALK_MINUTES = 5

# 묶음이 늘려줄 수 있는 항목 수의 상한(곳). (TP-243)
#
# **이 가드가 없으면 "짧게 머물기"가 "많이 넣기"로 새어나간다.** 묶음 최소
# 45분 + 허용 오차 30분이면 210분 요청에 5곳(각 45분 + 이동 3분)이 통과한다.
# 묶음의 목적은 골목 세 곳에 60분씩 앉히지 않는 것이지 곳 수를 채우는 것이
# 아니므로, 묶이지 않았을 때의 상한보다 한 자리만 더 준다.
SCHEDULE_CLUSTER_EXTRA_ITEMS = 1

# 묶음으로 인정하는 최소 자리 수. 한 곳짜리는 묶음이 아니다.
_MIN_CLUSTER_SIZE = 2


@dataclass(frozen=True)
class DurationSlot:
    """체류시간 조절 대상 한 자리.

    `policy`가 None이면 **이 자리는 조절하지 않는다.** 부분 재편성에서 사용자가
    유지하기로 한 항목(pinned)이 그렇다 — `_draft_from_schedule_item()` 주석의
    "그대로 뒀다는 약속"과 같은 근거다. 후보 목록에 없는 place_id가 곧 pinned라는
    기존 불변식(`_compose_items()` 주석)을 그대로 쓴다.
    """

    current_min: int
    policy: VisitDurationPolicy | None = None


def effective_budget_min(time_available_min: int | None) -> int:
    """편성 계산에 쓸 예산(분). 시간을 말하지 않았으면 기본값을 쓴다.

    **판정에는 쓰지 않는다.** `classify_budget()`은 None을 None으로 그대로 둔다 —
    사용자가 말하지 않은 시간을 "지켰다"·"넘었다"로 판정하면 화면이 하지도 않은
    약속을 말하게 된다(그 함수 주석의 팀 결정). 그래서 이 기본값은 **개수 상한과
    체류시간 배분에만** 쓰이고, 말풍선 문구는 시간을 말하지 않은 턴에 여전히
    아무 말도 하지 않는다.

    가정한 예산으로 되묻지 않는 것이 요점이다. "4시간으로 말씀하셨는데"라고
    말하면 안 한 말을 한 것으로 만든다.
    """

    if time_available_min is None:
        return SCHEDULE_DEFAULT_TIME_BUDGET_MIN
    return time_available_min


def classify_budget(
    total_duration_min: int, time_available_min: int | None
) -> ScheduleBudgetStatus | None:
    """편성 결과가 요청한 시간을 지켰는지 판정한다.

    사용자가 시간을 말하지 않았으면(None) 판정할 것이 없다 — "지켰다"도 "넘었다"도
    아니므로 None을 돌려준다. 0으로 뭉개면 화면이 지키지도 않은 약속을 말하게 된다.
    """

    if time_available_min is None:
        return None
    difference = total_duration_min - time_available_min
    if difference > SCHEDULE_TIME_TOLERANCE_MIN:
        return ScheduleBudgetStatus.OVER
    if difference < -SCHEDULE_TIME_TOLERANCE_MIN:
        return ScheduleBudgetStatus.UNDER
    return ScheduleBudgetStatus.WITHIN


def travel_estimate_minutes(sorted_travel_min: Sequence[int], hops: int) -> int:
    """구간 `hops`개를 이동하는 데 걸릴 시간 추정(분).

    **후보들 사이 이동시간 중 짧은 것부터 `hops`개를 더한다.** 좋은 동선은 가까운
    구간을 쓰므로 실제 이동시간의 하한에 가깝다. `FALLBACK_TRAVEL_MINUTES`를 그대로
    곱하면 상한이 지나치게 보수적이 되는데, 그 값은 좌표를 못 구했을 때의 폴백이지
    실측이 아니다.

    하한을 쓰면 곳 수가 살짝 많게 나올 수 있다. **그쪽이 낫다고 봤다** — 남는 초과는
    `fit_durations_to_budget()`이 체류시간으로 흡수하고, 그래도 남으면
    `classify_budget()`이 알린다. 반대로 상한을 보수적으로 잡아 곳 수를 깎으면
    되돌릴 곳이 없다.

    거리 정보가 없으면(과거 세션 재생·단위 테스트) 폴백을 `hops`배 한다.
    """

    if hops <= 0:
        return 0
    if not sorted_travel_min:
        return FALLBACK_TRAVEL_MINUTES * hops
    picked = list(sorted_travel_min[:hops])
    # 후보가 적어 쌍이 hops개보다 적으면 남는 구간은 가장 짧은 값으로 메운다.
    picked += [sorted_travel_min[0]] * (hops - len(picked))
    return sum(picked)


def pairwise_travel_minutes(request: SchedulePlanningRequest) -> list[int]:
    """이번 요청의 후보 쌍 이동시간(분)을 오름차순으로.

    **시간표와 같은 환산 규칙을 쓴다**(`estimated_travel_minutes`). 여기서 따로
    나눗셈을 적으면 상한을 정한 가정과 시간표가 실제로 쓴 값이 갈린다.

    단 시간표는 실측 경로를 쓸 수도 있어서(TP-216) 이 추정과 어긋날 수 있다.
    상한은 LLM을 부르기 전에 정해야 하고 그때는 실측이 아직 없다 — 그 차이는
    체류시간 조절이 흡수하고, 남으면 판정이 알린다.
    """

    resolve = estimated_travel_minutes(
        request.pairwise_distances_km,
        speed_km_per_minute=travel_speed_km_per_minute(request.conditions),
    )
    minutes = [
        resolved
        for from_id, to_id in request.pairwise_distances_km
        if (resolved := resolve(from_id, to_id)) is not None
    ]
    return sorted(minutes)


def walkable_cluster_size(request: SchedulePlanningRequest, *, within_min: int) -> int:
    """도보 `within_min`분 안에 함께 묶을 수 있는 후보 수의 **근사 최대치**. (TP-242)

    근접 묶기(TP-243)가 대부분의 요청에 영향이 있는 기능인지 미리 재기 위한 값이다.
    이 수가 3 미만인 요청이 대부분이면 그 카드는 범위를 줄일 근거가 생긴다.

    **정확한 최대 묶음이 아니다.** 서로 모두 가까운 최대 집합을 구하는 것은 최대
    클리크 문제라 후보 수가 늘면 비싸진다. 여기서는 **한 후보를 중심으로 놓고 그
    반경 안에 들어오는 후보 수 + 1**의 최댓값을 쓴다 — 중심에서는 가깝지만 서로는
    먼 조합을 과대 계산할 수 있다. 지표는 추세를 보는 값이고, 실제 묶음 규칙은
    TP-243이 자기 기준으로 다시 정한다. **근사라는 사실을 이름이 아니라 이 주석이
    말한다** — 지표를 읽는 사람이 정확값으로 오해하면 잘못된 결론을 낸다.

    거리 정보가 없으면 0을 돌려준다 — "묶을 수 없다"가 아니라 "알 수 없다"이지만,
    그 구분은 이 지표의 목적(빈도 추세)에 필요하지 않다.
    """

    distances = request.pairwise_distances_km
    if not distances:
        return 0
    resolve = estimated_travel_minutes(
        distances, speed_km_per_minute=WALKING_SPEED_KM_PER_MINUTE
    )
    place_ids = sorted({place_id for pair in distances for place_id in pair})
    best = 0
    for center in place_ids:
        near = sum(
            1
            for other in place_ids
            if other != center
            and (minutes := resolve(center, other)) is not None
            and minutes <= within_min
        )
        best = max(best, near + 1)
    return best


def cluster_ids_in_order(
    request: SchedulePlanningRequest | SchedulePartialFillRequest,
    place_ids: Sequence[str],
    *,
    within_min: int = SCHEDULE_CLUSTER_WALK_MINUTES,
) -> list[int | None]:
    """방문 순서대로 받은 자리들을 묶음으로 갈라 묶음 번호를 매긴다. (TP-243)

    묶이지 않은 자리는 None이다. 번호는 1부터, 앞에서 나온 묶음이 작은 번호다.

    **이웃한 구간만 본다 — 클리크를 풀지 않는다.** 일정은 순서가 있으므로
    "서로 모두 가까운 최대 집합"을 구할 필요가 없다. 지표용
    `walkable_cluster_size()`는 한 후보를 중심에 놓고 반경 안을 세는 근사라
    **중심에서는 가깝지만 서로는 먼 조합을 묶어버린다**(그 함수 주석이 스스로
    적어뒀고, 실제 묶음 규칙은 이 카드가 다시 정한다고 넘겨놨다). A-B 3분,
    A-C 3분, B-C 15분인 셋을 한 묶음으로 보면 사용자는 걷다 지친다. 여기서는
    실제로 걸어서 이어지는 구간만 묶으므로 그 조합에서 A-B만 묶인다.

    **거리를 모르는 구간은 묶지 않는다.** 좌표를 못 구했다는 뜻이라 "가깝다"의
    근거가 없다 — 모를 때 묶어주면 근거 없이 체류시간을 깎게 된다.
    """

    ids: list[int | None] = [None] * len(place_ids)
    if len(place_ids) < _MIN_CLUSTER_SIZE or not request.pairwise_distances_km:
        return ids

    resolve = estimated_travel_minutes(
        request.pairwise_distances_km, speed_km_per_minute=WALKING_SPEED_KM_PER_MINUTE
    )
    next_id = 1
    run_start = 0
    for index in range(1, len(place_ids) + 1):
        walk = (
            None
            if index == len(place_ids)
            else resolve(place_ids[index - 1], place_ids[index])
        )
        if walk is not None and walk <= within_min:
            continue
        if index - run_start >= _MIN_CLUSTER_SIZE:
            for position in range(run_start, index):
                ids[position] = next_id
            next_id += 1
        run_start = index
    return ids


def clusterable_slot_count(
    request: SchedulePlanningRequest, *, within_min: int = SCHEDULE_CLUSTER_WALK_MINUTES
) -> int:
    """LLM을 부르기 전에 **몇 자리까지 묶일 수 있는지**의 낙관적 추정. (TP-243)

    상한을 정하는 시점에는 방문 순서가 아직 없어서 `cluster_ids_in_order()`를
    쓸 수 없다. 대신 도보 기준 안에 드는 후보 쌍의 수 `k`를 세고 `k + 1`을
    돌려준다 — 그 쌍들이 한 줄로 이어질 때의 자리 수다.

    **낙관적인 값이라는 것을 알고 쓴다.** 쌍이 흩어져 있으면 실제로는 그만큼
    이어지지 않는다. `travel_estimate_minutes()`가 짧은 구간부터 세는 것과 같은
    방향이고 이유도 같다 — 상한을 보수적으로 깎으면 되돌릴 곳이 없다. 새어나가는
    쪽은 `SCHEDULE_CLUSTER_EXTRA_ITEMS` 가드와, 응답이 온 뒤 실제 순서로 다시
    재는 `cap_item_count_to_budget()`이 막는다.
    """

    distances = request.pairwise_distances_km
    if not distances:
        return 0
    resolve = estimated_travel_minutes(
        distances, speed_km_per_minute=WALKING_SPEED_KM_PER_MINUTE
    )
    walkable_pairs = sum(
        1
        for from_id, to_id in distances
        if (minutes := resolve(from_id, to_id)) is not None and minutes <= within_min
    )
    if walkable_pairs == 0:
        return 0
    # 자리 수라서 후보 수를 넘을 수 없다. 쌍의 수는 후보가 늘면 제곱으로 늘어난다.
    return min(walkable_pairs + 1, len(request.candidates))


def _max_items_within(
    stay_minimums: Sequence[int],
    travel_min: Sequence[int],
    *,
    allowance: int,
    hard_cap: int,
) -> int:
    """`stay_minimums`를 앞에서부터 쌓아 예산에 들어가는 최대 자리 수.

    체류·이동 모두 개수에 대해 단조 증가라 한 번 넘으면 더 큰 개수도 넘는다.
    0을 돌려주지 않는다 — 한 곳도 못 넣는 편성보다 한 곳이 넘는 편성이 낫고,
    넘었다는 사실은 `classify_budget()`이 알린다.
    """

    reachable = min(hard_cap, len(stay_minimums))
    best = 1
    for count in range(1, reachable + 1):
        needed = sum(stay_minimums[:count]) + travel_estimate_minutes(travel_min, count - 1)
        if needed > allowance:
            break
        best = count
    return best


def _stay_minimums(
    categories: Sequence[str | None], *, clustered_slots: int, ascending: bool
) -> list[int]:
    """체류 최소값 목록. 묶일 수 있는 자리 수만큼 완화된 값을 쓴다. (TP-243)

    **줄어드는 폭이 큰 자리부터 완화한다.** 어느 자리가 실제로 묶일지는 아직
    모르므로, 이동 추정이 짧은 구간부터 세는 것과 같은 낙관적 방향을 쓴다.
    완화 대상이 아닌 분류(문화시설·식당)는 `policy_for(clustered=True)`가 값을
    그대로 돌려주므로 폭이 0이고, 자연히 뒤로 밀린다.

    `ascending`이 참이면 오름차순으로 돌려준다 — 후보 풀에서 "값싼 것부터"
    세는 `derive_item_range()`용이다. 거짓이면 **받은 순서를 지킨다** — LLM이
    고른 항목을 앞에서부터 세는 `cap_item_count_to_budget()`용이고, 그쪽은
    `_cap_item_count()`가 뒤에서부터 자르는 것과 기준이 같아야 한다.
    """

    plain = [policy_for(category).minimum_min for category in categories]
    relaxed = [
        policy_for(category, clustered=True).minimum_min for category in categories
    ]
    reductions = sorted(
        range(len(plain)), key=lambda i: (plain[i] - relaxed[i], -i), reverse=True
    )
    eased = set(reductions[: max(0, clustered_slots)])
    minimums = [
        relaxed[index] if index in eased else plain[index]
        for index in range(len(plain))
    ]
    return sorted(minimums) if ascending else minimums


def derive_item_range(
    request: SchedulePlanningRequest, *, hard_cap: int = MAX_SCHEDULE_ITEMS
) -> tuple[int, int]:
    """이번 요청에 맞는 일정 항목 개수의 (최소, 최대)를 예산 산수로 구한다.

    예전에는 버킷 상수였다 — 120분 미만이면 1~2곳, 210분 미만이면 2~4곳, 그
    이상이면 3~5곳. **그 상한이 예산과 안 맞았다.** 관광지 최소 체류 60분·이동
    15분 기준으로 2시간에 4곳은 최소 285분이다. 그런데 프롬프트는 그 상한까지
    채우라고 시켰다.

    지금은 이 부등식을 만족하는 최대 n이다.

        n곳의 체류 최소 합 + (n-1)구간 이동 추정 <= 활동 가능 시간 + 허용 오차

    **체류 최소는 이번 후보들의 분류에서 온다.** 60분을 상수로 박으면 박물관
    (최소 90분)과 쇼핑(최소 30분)이 같은 취급을 받는다. 작은 것부터 n개를 쓰는
    것은 이동 추정과 같은 방향(하한)이다 — 위 `travel_estimate_minutes()` 주석에
    이유가 있다.

    **허용 오차가 곳 수를 가른다.** 30분이면 3시간에 3곳까지 통과하고(60*3 +
    15*2 - 180 = 30) 4시간에 4곳은 45분이 필요해 자동으로 막힌다. 오차를 키우면
    "짧게 머물며 많이 넣기"로 새어나간다.

    **시간을 말하지 않았으면 기본 예산을 쓴다**(`effective_budget_min()`).
    예전에는 여기서 상수 `(3, 5)`를 돌려줬는데, 그러면 상한이 예산과 무관해져
    문화시설 네 곳 360분이 그대로 통과했다 — 실측 258~420분(2026-09-07). 기본값을
    쓰면 상한도 배분도 같은 수를 보게 된다. 판정은 여전히 하지 않는다.

    **후보 부족 가드는 이 함수가 아니라 `required_candidate_count()`가 답한다.**
    시간을 말하지 않은 요청은 후보 3곳을 요구하는 옛 동작을 그대로 둔다 — 그
    가드를 함께 풀면 "후보가 부족해요" 대신 1곳짜리 일정이 나가기 시작하고, 그건
    이 변경의 목적(예산 폭주 막기)과 다른 제품 판단이다.

    최솟값은 후보 부족 가드에만 쓰인다("LLM을 부를 가치가 있는가"). 상한이 2곳
    이상이면 2, 아니면 1이다. 예전에는 예산이 길수록 최솟값도 3까지 올라가서
    4시간 요청에 후보가 2곳이면 편성을 아예 포기했는데, 그건 2곳을 보여주는
    것보다 나쁘다 — 이제 부족은 판정이 알리므로 조용히 나쁜 답이 나가지 않는다.

    **도보로 붙어 있는 자리는 체류 최소값을 낮게 잡는다**(TP-243). 몇 자리까지
    그럴 수 있는지는 `clusterable_slot_count()`의 낙관적 추정이고, 늘어나는 곳
    수는 `SCHEDULE_CLUSTER_EXTRA_ITEMS`(한 자리)로 막는다 — 그 가드가 없으면
    210분 요청에 45분짜리 5곳이 통과해서 "짧게 머물기"가 "많이 넣기"로
    새어나간다. 묶을 후보가 없으면 완화된 목록이 원래 목록과 같아 결과도 같다.
    """

    categories = [candidate.category for candidate in request.candidates]
    travel_min = pairwise_travel_minutes(request)
    allowance = (
        effective_budget_min(request.conditions.time_available)
        + SCHEDULE_TIME_TOLERANCE_MIN
    )

    plain_max = _max_items_within(
        _stay_minimums(categories, clustered_slots=0, ascending=True),
        travel_min,
        allowance=allowance,
        hard_cap=hard_cap,
    )
    clustered_max = _max_items_within(
        _stay_minimums(
            categories,
            clustered_slots=clusterable_slot_count(request),
            ascending=True,
        ),
        travel_min,
        allowance=allowance,
        hard_cap=hard_cap,
    )
    max_items = min(clustered_max, plain_max + SCHEDULE_CLUSTER_EXTRA_ITEMS, hard_cap)

    return min(2, max_items), max_items


def cap_item_count_to_budget(
    request: SchedulePlanningRequest,
    chosen_categories: Sequence[str | None],
    *,
    hard_cap: int,
    clustered_flags: Sequence[bool] | None = None,
) -> int:
    """LLM이 **실제로 고른** 항목의 분류로 개수 상한을 다시 잰다.

    **왜 다시 재나.** `derive_item_range()`는 후보들의 체류 최소값을 **작은 것부터**
    n개 써서 상한을 정한다. 의도된 하한 가정이고(그 함수 주석 참고) 상한을 너무
    깎지 않으려는 것이다. 그런데 후보 풀에 쇼핑(최소 30분)이 섞여 있으면 3시간에
    3곳이 통과하고, 정작 LLM은 문화시설(최소 90분) 세 곳을 고를 수 있다. 그러면
    270분 + 이동이 되는데 **되돌릴 곳이 없다** — `fit_durations_to_budget()`은
    정책 최소값에서 멈추므로 90분을 더 깎지 못한다.

    실측으로 확인된 모양이다(2026-09-07): 3시간 요청에 문화시설 3곳이 나와
    276분, 초과 96분. 그 민원이 TP-238·239로 닫힌 줄 알았는데 이 경로가 남아
    있었다.

    **상한 계산을 고치지 않고 다시 재는 이유.** 어느 분류가 뽑힐지는 LLM이 고르기
    전까지 알 수 없다. 가장 비싼 조합을 가정해 미리 깎으면 대부분의 요청에서
    곳 수가 실제보다 줄어든다. 그래서 유도값은 프롬프트에 주는 목표로 남기고,
    응답이 온 뒤 실제 분류로 계약을 확인한다 — 개수 상한을 지시가 아니라
    자르기로 보장한 TP-239와 같은 철학이다.

    **고른 순서의 앞에서부터 센다.** `_cap_item_count()`가 뒤에서부터 자르므로
    같은 기준이어야 결과가 일치한다. LLM은 점수가 높은 곳을 앞에 두므로 남는
    것도 그쪽이다.

    **0을 돌려주지 않는다.** 한 곳도 못 넣는 편성보다 한 곳이 예산을 넘는 편성이
    낫고, 넘었다는 사실은 `classify_budget()`이 알린다.

    **고른 것이 전부 예산에 들어가면 `hard_cap`을 그대로 돌려준다.** 실제로 자를
    것이 없을 때 상한을 고른 개수로 줄이면, 상한을 다시 읽는 곳
    (`_resolve_must_include()`)이 들어갈 수 있었던 보관함 장소를 "항목 수 상한
    초과"로 안내한다 — 상한이 줄어든 이유가 예산이 아니라 LLM의 선택이라 거짓
    안내다.

    **시간을 말하지 않은 요청도 잰다.** 가정한 기본 예산을 쓴다
    (`effective_budget_min()`) — 예전에는 여기서 그냥 돌아갔고, 그래서 개수 상한이
    상수이던 시절과 겹쳐 문화시설 네 곳 360분이 통과했다.

    **`clustered_flags`는 실제 방문 순서에서 나온 묶음이다**(TP-243). 상한을 정할
    때(`derive_item_range()`)는 순서가 없어 낙관적 추정을 썼지만, 여기서는
    `cluster_ids_in_order()`가 이웃 구간을 실제로 재서 준다. 묶인 자리만 최소값이
    낮아지고, 그렇게 늘어나는 곳 수는 여기서도 한 자리로 막는다.
    """

    if not chosen_categories:
        return hard_cap

    plain = [policy_for(category).minimum_min for category in chosen_categories]
    if clustered_flags is None:
        eased = plain
    else:
        eased = [
            policy_for(category, clustered=flag).minimum_min
            for category, flag in zip(chosen_categories, clustered_flags, strict=True)
        ]
    travel_min = pairwise_travel_minutes(request)
    allowance = (
        effective_budget_min(request.conditions.time_available)
        + SCHEDULE_TIME_TOLERANCE_MIN
    )

    reachable = min(hard_cap, len(plain))
    plain_cap = _max_items_within(
        plain, travel_min, allowance=allowance, hard_cap=hard_cap
    )
    eased_cap = _max_items_within(
        eased, travel_min, allowance=allowance, hard_cap=hard_cap
    )
    # 묶음은 묶이지 않았을 때보다 한 자리만 더 준다 — 여기서도 같은 가드를 건다.
    # 이 자리에서 빼면 "묶였으니 45분"이 곧 "그러니 한 곳 더"로 이어진다.
    capped = min(eased_cap, plain_cap + SCHEDULE_CLUSTER_EXTRA_ITEMS)

    if capped >= reachable:
        # **고른 것이 전부 들어가면 상한을 깎지 않는다.** 이 자리에서 `capped`를
        # 그대로 돌려주면 LLM이 상한보다 적게 골랐을 때 상한이 그 개수로 줄어든다
        # — 그러면 `_resolve_must_include()`가 다시 계산될 때 들어갈 수 있었던
        # 보관함 장소가 "항목 수 상한 초과"로 안내된다. 상한이 줄어든 이유가
        # 예산이 아니라 LLM의 선택이라 그 안내는 거짓이다.
        # (관광지 4곳 후보·상한 3에 LLM이 2곳만 준 경우로 회귀 테스트가 잡았다.)
        return hard_cap
    return capped


def required_candidate_count(
    request: SchedulePlanningRequest, *, min_items: int
) -> int:
    """LLM을 부를 가치가 있는 최소 후보 수.

    **개수 범위의 최솟값과 다른 질문이다.** 범위는 "몇 곳을 넣을 것인가"이고
    이 값은 "부를 가치가 있는가"다. 시간을 말한 요청은 둘이 같아도 되지만
    (`min_items`), 말하지 않은 요청은 후보 3곳을 요구한다 — SCHEDULE-07 동작이고
    회귀 방지 테스트가 잠그고 있다
    (`test_시간_제한이_없으면_여전히_3개_미만에서_스킵한다`).

    기본 예산을 넣으면서 두 뜻을 한 상수가 겸할 수 없게 됐다 — 상수 주석 참고.
    """

    if request.conditions.time_available is None:
        return _REQUIRED_CANDIDATES_WITHOUT_BUDGET
    return min_items


def fit_durations_to_budget(
    slots: Sequence[DurationSlot],
    *,
    overhead_min: int,
    budget_min: int | None,
    shrink_only: bool = False,
) -> list[int]:
    """체류시간을 활동 가능 시간에 맞춰 조절한 값을 돌려준다.

    `overhead_min`은 체류가 아닌 시간(구간 이동 + 개장 전 대기)의 합이다. 총
    소요시간에서 체류 합계를 뺀 값이라 호출부가 시간표에서 그대로 구한다 — 여기서
    다시 계산하지 않는다. 다시 계산하면 시간표가 쓴 이동시간과 갈릴 수 있다.

    **정책 범위를 넘지 않는다.** 예산을 맞추려고 "관광지 10분"을 만들지 않는다.
    여유를 다 써도 남는 차이는 그대로 두고, 그 사실은 `classify_budget()`이
    판정으로 알린다 — 조용히 어기지 않는다.

    **비례 배분이고 재배정이 아니다.** 자리마다 남은 여유에 비례해 나누므로 LLM이
    매긴 항목 간 상대 크기가 유지된다. 균등하게 깎으면 "국립박물관은 더 오래"라는
    판단이 사라진다.

    **5분 배수로 배정한다.** (TP-244) 옮기는 양을 5분의 배수로만 잡으므로 정책
    범위 안에 있던 값은 5분 배수로 남는다. 예산과 최대 4분이 어긋나는데, 그
    4분은 허용 오차 안에서 무해하고 판정은 정확값으로 내려진다.

    **`shrink_only`는 가정한 예산에 쓴다.** 사용자가 시간을 말하지 않아
    기본값을 쓰는 경우(`effective_budget_min()`), 넘치면 줄이지만 **모자라면
    늘리지 않는다.** 늘리는 것은 "가정한 4시간을 꽉 채워 다니겠다는 뜻"으로 읽는
    것이고, 말한 시간에 대해서도 팀이 그렇게 합의한 적이 없다(아래 문단) —
    말하지 않은 시간에 대해서는 예산과 채울 의사를 둘 다 지어내는 셈이다.

    **이미 허용 오차 안이면 아무것도 하지 않는다.** 판정이 곧 목표라서, 목표를
    만족한 편성을 굳이 예산에 딱 맞게 늘리거나 줄일 이유가 없다. 그리고 밴드 안에서
    체류를 늘리는 것은 "사용자가 말한 3시간은 꽉 채워 다니겠다는 뜻"이라고 가정하는
    것인데, 팀이 그렇게 합의한 기록이 없다 — 이 함수가 혼자 정할 문제가 아니다.
    """

    current = [slot.current_min for slot in slots]
    if budget_min is None or not slots:
        return current

    total_min = sum(current) + overhead_min
    if classify_budget(total_min, budget_min) is ScheduleBudgetStatus.WITHIN:
        return current

    delta = (budget_min - overhead_min) - sum(current)
    if delta == 0:
        return current
    if shrink_only and delta > 0:
        # 가정한 예산 쪽으로 늘리지 않는다 — 위 docstring 참고.
        return current

    if delta < 0:
        headroom = [
            max(0, slot.current_min - slot.policy.minimum_min) if slot.policy else 0
            for slot in slots
        ]
    else:
        headroom = [
            max(0, slot.policy.maximum_min - slot.current_min) if slot.policy else 0
            for slot in slots
        ]

    # **5분 단위로 옮긴다.** (TP-244) 1분 단위로 나누면 "67분"·"63분"처럼 화면에
    # 그대로 뜨는 값이 나온다. 표시할 때만 반올림하는 방법을 쓰지 않는 이유는
    # duration.VISIT_DURATION_STEP_MIN 주석에 있다.
    #
    # **내림이라 예산과 최대 4분이 남는다.** 올리면 여유(headroom)를 넘겨 정책
    # 범위를 깨거나 반대 방향으로 지나칠 수 있다. 남는 4분은 허용 오차 30분
    # 안에서 무해하고, 남았다는 사실은 classify_budget()이 정확값으로 판정한다.
    headroom_steps = [room // VISIT_DURATION_STEP_MIN for room in headroom]
    available_steps = sum(headroom_steps)
    if available_steps == 0:
        return current

    move_steps = min(abs(delta) // VISIT_DURATION_STEP_MIN, available_steps)
    if move_steps == 0:
        return current

    shares = [room * move_steps // available_steps for room in headroom_steps]
    # 정수 나눗셈에서 남는 몫은 여유가 큰 자리부터 한 칸씩 준다. 여유가 같으면 앞
    # 자리부터 — 같은 입력에 같은 결과가 나와야 테스트가 성립한다.
    remainder = move_steps - sum(shares)
    for index in sorted(range(len(slots)), key=lambda i: (-headroom_steps[i], i)):
        if remainder == 0:
            break
        if shares[index] < headroom_steps[index]:
            shares[index] += 1
            remainder -= 1

    sign = 1 if delta > 0 else -1
    return [
        value + sign * share * VISIT_DURATION_STEP_MIN
        for value, share in zip(current, shares, strict=True)
    ]


__all__ = [
    "MAX_SCHEDULE_ITEMS",
    "SCHEDULE_CLUSTER_EXTRA_ITEMS",
    "SCHEDULE_CLUSTER_WALK_MINUTES",
    "SCHEDULE_DEFAULT_TIME_BUDGET_MIN",
    "SCHEDULE_TIME_TOLERANCE_MIN",
    "DurationSlot",
    "cap_item_count_to_budget",
    "cluster_ids_in_order",
    "clusterable_slot_count",
    "classify_budget",
    "derive_item_range",
    "effective_budget_min",
    "fit_durations_to_budget",
    "pairwise_travel_minutes",
    "required_candidate_count",
    "travel_estimate_minutes",
    "walkable_cluster_size",
]
