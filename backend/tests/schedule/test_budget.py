"""예산 산수 단위 테스트. (TP-238)

`fit_durations_to_budget()`이 지켜야 하는 것은 넷이다 — 정책 범위를 안 넘고,
조절하면 안 되는 자리는 안 건드리고, 이미 허용 오차 안이면 아무것도 안 하고,
상대 크기를 유지한다.
"""

from __future__ import annotations

from app.schedule.budget import (
    MAX_SCHEDULE_ITEMS,
    SCHEDULE_CLUSTER_EXTRA_ITEMS,
    SCHEDULE_DEFAULT_TIME_BUDGET_MIN,
    SCHEDULE_TIME_TOLERANCE_MIN,
    DurationSlot,
    cap_item_count_to_budget,
    classify_budget,
    cluster_ids_in_order,
    derive_item_range,
    fit_durations_to_budget,
    required_candidate_count,
    travel_estimate_minutes,
    walkable_cluster_size,
)
from app.schedule.duration import VisitDurationPolicy
from app.schedule.schemas import SchedulePlanningRequest
from app.schemas import RecommendationItem, ScheduleBudgetStatus, UserConditions

_ATTRACTION = VisitDurationPolicy(60, 90, 120)


def _slots(*durations: int, policy: VisitDurationPolicy | None = _ATTRACTION) -> list[DurationSlot]:
    return [DurationSlot(current_min=d, policy=policy) for d in durations]


class TestClassifyBudget:
    def test_시간을_말하지_않았으면_판정하지_않는다(self) -> None:
        """None은 "지켰다"도 "넘었다"도 아니다. 0으로 뭉개면 화면이 지키지도 않은
        약속을 말하게 된다."""

        assert classify_budget(300, None) is None

    def test_허용_오차_경계는_지킨_것으로_친다(self) -> None:
        """딱 30분 초과까지는 WITHIN이다. 이 경계가 "3시간에 3곳"을 통과시키는
        지점이라 한 칸만 밀려도 곳 수가 줄어든다."""

        assert classify_budget(180 + SCHEDULE_TIME_TOLERANCE_MIN, 180) is (
            ScheduleBudgetStatus.WITHIN
        )
        assert classify_budget(180 + SCHEDULE_TIME_TOLERANCE_MIN + 1, 180) is (
            ScheduleBudgetStatus.OVER
        )

    def test_모자란_쪽도_같은_오차로_판정한다(self) -> None:
        assert classify_budget(180 - SCHEDULE_TIME_TOLERANCE_MIN, 180) is (
            ScheduleBudgetStatus.WITHIN
        )
        assert classify_budget(180 - SCHEDULE_TIME_TOLERANCE_MIN - 1, 180) is (
            ScheduleBudgetStatus.UNDER
        )


class TestFitDurationsToBudget:
    def test_초과하면_최소값_방향으로_줄인다(self) -> None:
        """관광지 3곳 90분씩 + 이동 30분 = 300분을 180분 예산에 맞춘다.

        여유는 곳당 30분(90 -> 60)뿐이라 210분까지만 줄어든다. 그 210분이
        허용 오차 안이고, 이것이 "3시간에 3곳"이 성립하는 경로다.
        """

        fitted = fit_durations_to_budget(_slots(90, 90, 90), overhead_min=30, budget_min=180)

        assert fitted == [60, 60, 60]
        assert sum(fitted) + 30 == 210
        assert classify_budget(210, 180) is ScheduleBudgetStatus.WITHIN

    def test_예산에_한참_못_미치면_최대값_방향으로_늘린다(self) -> None:
        fitted = fit_durations_to_budget(_slots(60, 60), overhead_min=15, budget_min=300)

        assert fitted == [120, 120]

    def test_이미_허용_오차_안이면_아무것도_바꾸지_않는다(self) -> None:
        """**이 가드가 없으면 밴드 안의 일정까지 예산에 딱 맞게 늘어난다.**

        60+60+이동 15 = 135분은 150분 요청의 오차 안이다. 여기서 굳이 늘리는 것은
        "사용자가 말한 시간은 꽉 채워 다니겠다는 뜻"이라고 가정하는 것인데 팀이
        그렇게 정한 적이 없다. 실제로 이 가드를 빼면 자정 넘김 편성 테스트가
        깨진다(도착 시각이 8분 밀린다).
        """

        fitted = fit_durations_to_budget(_slots(60, 60), overhead_min=15, budget_min=150)

        assert fitted == [60, 60]

    def test_정책_범위를_넘기면서까지_예산을_맞추지_않는다(self) -> None:
        """여유를 다 써도 남는 차이는 그대로 둔다 — "관광지 10분"을 만들지 않는다."""

        fitted = fit_durations_to_budget(_slots(90, 90, 90), overhead_min=30, budget_min=60)

        assert fitted == [60, 60, 60]
        assert all(minutes >= _ATTRACTION.minimum_min for minutes in fitted)

    def test_여유에_비례해_나눠_상대_크기가_유지된다(self) -> None:
        """균등하게 깎으면 "이 곳은 더 오래"라는 판단이 사라진다.

        120/90/60은 줄일 여유가 60/30/0이다. 50분을 줄여야 하므로 5분씩 10칸을
        여유 비율대로 7칸/3칸/0칸으로 걷는다(정수 나눗셈에서 남는 1칸은 여유가
        가장 큰 자리로). 여유가 없는 셋째 자리는 안 줄어들고, 순서는 그대로다.

        **5분 단위 배분 이전에는 86/74/60이었다.** (TP-244) 화면에 그대로 뜨는
        값이라 배정값 자체를 5분 배수로 둔다.
        """

        fitted = fit_durations_to_budget(_slots(120, 90, 60), overhead_min=30, budget_min=250)

        assert fitted == [85, 75, 60]
        assert fitted[0] > fitted[1] > fitted[2]
        assert sum(fitted) + 30 == 250

    def test_여유를_다_써도_모자라면_전부_최소값까지_간다(self) -> None:
        """줄여야 할 양이 여유와 같거나 크면 상대 크기는 유지되지 않는다 —
        모두 자기 최소값에서 멈추기 때문이다. 이것이 정상이다."""

        fitted = fit_durations_to_budget(_slots(120, 90, 60), overhead_min=30, budget_min=210)

        assert fitted == [60, 60, 60]

    def test_정책이_없는_자리는_조절하지_않는다(self) -> None:
        """부분 재편성에서 사용자가 유지하기로 한 자리(pinned)가 이 경우다."""

        slots = [
            DurationSlot(current_min=90, policy=None),
            DurationSlot(current_min=90, policy=_ATTRACTION),
        ]

        fitted = fit_durations_to_budget(slots, overhead_min=15, budget_min=120)

        assert fitted[0] == 90
        assert fitted[1] == 60

    def test_시간을_말하지_않았으면_그대로_둔다(self) -> None:
        assert fit_durations_to_budget(_slots(90, 90), overhead_min=15, budget_min=None) == [
            90,
            90,
        ]

    def test_조절할_자리가_없으면_그대로_둔다(self) -> None:
        assert fit_durations_to_budget([], overhead_min=0, budget_min=180) == []


def _candidate(place_id: str, category: str) -> RecommendationItem:
    return RecommendationItem(
        place_id=place_id, name=f"장소 {place_id}", category=category, distance_km=0.3,
        remaining_minutes=120, operating_hours_display=None, environment_type="indoor",
        recommendation_reason="고정 후보", explanations=[], warnings=[], score=0.5,
        feature_scores={}, weights_used={},
    )


def _request(
    budget: int | None, *, count: int = 5, category: str = "attraction", km: float | None = None
) -> SchedulePlanningRequest:
    distances = (
        {}
        if km is None
        else {
            (f"place-{i}", f"place-{j}"): km
            for i in range(count)
            for j in range(i + 1, count)
        }
    )
    return SchedulePlanningRequest(
        candidates=[_candidate(f"place-{i}", category) for i in range(count)],
        conditions=UserConditions(time_available=budget),
        pairwise_distances_km=distances,
    )


class TestTravelEstimateMinutes:
    def test_짧은_구간부터_고른다(self) -> None:
        """좋은 동선은 가까운 구간을 쓰므로 하한에 가깝게 잡는다."""

        assert travel_estimate_minutes([3, 5, 20, 40], hops=2) == 8

    def test_구간이_없으면_0분이다(self) -> None:
        assert travel_estimate_minutes([3, 5], hops=0) == 0

    def test_거리_정보가_없으면_폴백을_구간수만큼_쓴다(self) -> None:
        """과거 세션 재생과 단위 테스트 경로다."""

        assert travel_estimate_minutes([], hops=2) == 30

    def test_쌍이_구간수보다_적으면_가장_짧은_값으로_메운다(self) -> None:
        assert travel_estimate_minutes([4], hops=3) == 12


class TestDeriveItemRange:
    """TP-239 — 곳 수 상한을 예산 산수로 구한다."""

    def test_예산에서_유도한_상한이_버킷_상수를_대신한다(self) -> None:
        """옛 버킷은 2시간에 4곳까지 허용했다. 관광지 최소 60분·이동 15분이면
        2시간에 4곳은 최소 285분이라 애초에 불가능한 상한이었다."""

        assert derive_item_range(_request(90))[1] == 1
        assert derive_item_range(_request(120))[1] == 2
        assert derive_item_range(_request(150))[1] == 2
        assert derive_item_range(_request(180))[1] == 3
        assert derive_item_range(_request(240))[1] == 3
        assert derive_item_range(_request(300))[1] == 4
        assert derive_item_range(_request(360))[1] == 5

    def test_허용_오차가_곳_수를_가른다(self) -> None:
        """3시간에 3곳은 최소 210분이라 오차 30분에 딱 걸쳐 통과한다.

        **이 문턱이 허용 오차 값의 근거다.** 오차가 30분보다 작으면 3시간이 2곳으로
        떨어지고, 45분이면 4시간에 4곳까지 열려 "짧게 많이"로 새어나간다.
        """

        assert 60 * 3 + 15 * 2 - 180 == SCHEDULE_TIME_TOLERANCE_MIN
        assert derive_item_range(_request(180))[1] == 3
        assert derive_item_range(_request(180), hard_cap=2)[1] == 2

    def test_분류가_다르면_상한도_다르다(self) -> None:
        """체류 최소값을 60분 상수로 박으면 박물관과 쇼핑이 같은 취급을 받는다."""

        assert derive_item_range(_request(180, category="cultural_facility"))[1] == 2
        assert derive_item_range(_request(180, category="attraction"))[1] == 3
        assert derive_item_range(_request(180, category="shopping"))[1] == 5

    def test_후보가_멀면_상한이_줄어든다(self) -> None:
        """**폴백 15분이 아니라 이번 후보들의 실제 거리를 쓴다는 증거다.**

        2km씩 떨어져 있으면 도보 환산 이동이 커져 3시간에 3곳이 안 들어간다.

        가까운 쪽은 TP-243 이후 4곳이다(이 카드 전에는 3곳). 0.15km는 도보 3분이라
        묶음으로 잡혀 체류 최소값이 45분까지 내려간다. **거리가 상한을 가른다는 이
        테스트의 주장은 그대로다** — 오히려 두 값의 차이가 벌어졌다.
        """

        assert derive_item_range(_request(180, km=0.15))[1] == 4
        assert derive_item_range(_request(180, km=2.0))[1] == 2

    def test_후보_수가_상한을_넘지_못한다(self) -> None:
        assert derive_item_range(_request(360, count=2))[1] == 2

    def test_하드_캡을_넘지_않는다(self) -> None:
        """ScheduleLLMPlan.items의 max_length와 같은 수여야 검증에서 안 거부된다."""

        assert derive_item_range(_request(1000))[1] == MAX_SCHEDULE_ITEMS

    def test_예산이_아무리_짧아도_한_곳은_남긴다(self) -> None:
        """0곳을 돌려주면 편성 자체가 불가능해진다. 부족은 판정이 알린다."""

        assert derive_item_range(_request(10))[1] == 1

    def test_시간을_말하지_않으면_기본_예산으로_유도한다(self) -> None:
        """**예전에는 상수 `(3, 5)`였다.** 그러면 상한이 예산과 무관해져
        문화시설 네 곳 360분이 그대로 통과한다 — 실측 258~420분(2026-09-07).

        기본 예산 240분을 쓰면 관광지(최소 60분)·이동 15분 기준으로 3곳이
        상한이다(60x3 + 15x2 = 210 <= 240 + 30). 4곳은 285분이라 막힌다.

        기본값 240은 새로 정한 값이 아니다 — 프롬프트가 이미 "3~4시간 내외"로
        안내하고, D가 "반나절"을 그 폴백에 맞춰 240으로 확정한 이력이 있다
        (`app/prompts/recommend/HISTORY.md` 2.5.0). 상수 주석에 근거가 있다.
        """

        assert derive_item_range(_request(None)) == (2, 3)
        assert SCHEDULE_DEFAULT_TIME_BUDGET_MIN == 240

    def test_시간을_말하지_않아도_후보_3곳_미만이면_부르지_않는다(self) -> None:
        """**개수 범위의 최솟값과 다른 질문이다.** 범위는 "몇 곳을 넣을 것인가"이고
        이 값은 "LLM을 부를 가치가 있는가"다. 기본 예산을 넣으면서 상한이 예산에서
        나오게 됐으니, 한 상수가 둘을 겸하면 프롬프트에 "3개 이상 2개 이하" 같은
        모순된 범위가 실린다(문화시설 세 곳이 그 경우다).

        옛 동작(SCHEDULE-07)은 그대로 둔다 — 이 가드를 풀면 "후보가 부족해요"
        대신 1곳짜리 일정이 나가기 시작하고 그건 별개의 제품 판단이다.
        """

        no_budget = _request(None)
        assert required_candidate_count(no_budget, min_items=2) == 3
        assert required_candidate_count(_request(180), min_items=2) == 2

    def test_최솟값은_후보_부족_가드용이라_2를_넘지_않는다(self) -> None:
        """예전에는 예산이 길수록 최솟값도 3까지 올라가서, 4시간 요청에 후보가
        2곳이면 편성을 아예 포기했다. 2곳을 보여주는 것보다 나쁘다."""

        assert derive_item_range(_request(360))[0] == 2
        assert derive_item_range(_request(90))[0] == 1


class Test5분_단위_배분:
    """TP-244 완료 조건 — 배정값 자체가 5분 배수라 저장값·표시값·항목 합이 같다."""

    def test_배분_결과가_전부_5분_배수다(self) -> None:
        """**돌연변이가 잡히는 입력이다.** 5분 단위 이전에는 이 입력이
        86/74/60을 냈다 — 화면에 "86분"이 그대로 뜬다. 평범한 입력
        (90/90/90 -> 60/60/60)은 1분 단위로 나눠도 5분 배수가 나와서
        단위를 지워도 통과한다.
        """

        fitted = fit_durations_to_budget(_slots(120, 90, 60), overhead_min=30, budget_min=250)

        assert all(minutes % 5 == 0 for minutes in fitted)

    def test_여유가_5분에_못_미치는_자리는_건드리지_않는다(self) -> None:
        """한 칸(5분)을 못 채우는 여유는 안 쓴다. 반내림해서 쓰면 정책 최소값
        아래로 내려간다 — 63분짜리 자리의 여유는 3분뿐이다."""

        slots = [
            DurationSlot(current_min=63, policy=_ATTRACTION),
            DurationSlot(current_min=90, policy=_ATTRACTION),
        ]

        fitted = fit_durations_to_budget(slots, overhead_min=30, budget_min=100)

        assert fitted[0] == 63
        assert all(minutes >= _ATTRACTION.minimum_min for minutes in fitted)

    def test_5분_단위로_옮겨도_판정이_뒤집히지_않는다(self) -> None:
        """**내림 때문에 예산과 최대 4분이 남는다.** 그 4분이 허용 오차 30분을
        넘겨 WITHIN을 OVER로 뒤집으면 안 된다.

        여기서 남는 4분은 여유가 모자라서가 아니라 34분을 5로 나눈 나머지다.
        여유가 부족한 경우는 5분 단위와 무관하게 같은 자리에서 멈추므로(정책
        경계값이 전부 5분 배수다) 차이가 생기지 않는다.
        """

        fitted = fit_durations_to_budget(_slots(90, 90), overhead_min=30, budget_min=176)
        total = sum(fitted) + 30

        assert all(minutes % 5 == 0 for minutes in fitted)
        assert 0 <= total - 176 <= 4
        assert classify_budget(total, 176) is ScheduleBudgetStatus.WITHIN


class TestWalkableClusterSize:
    """TP-242 — 근접 묶기(TP-243)의 가치를 미리 재는 값."""

    def test_붙어_있으면_후보_전부가_한_묶음이다(self) -> None:
        assert walkable_cluster_size(_request(180, km=0.15), within_min=5) == 5

    def test_멀면_묶이지_않는다(self) -> None:
        """1.5km는 도보로 5분을 한참 넘는다. 1은 "자기 자신뿐"이라는 뜻이다."""

        assert walkable_cluster_size(_request(180, km=1.5), within_min=5) == 1

    def test_기준_분을_바꾸면_결과가_달라진다(self) -> None:
        """기준이 지표의 의미를 바꾼다 — 그래서 기록에 기준 값을 함께 남긴다."""

        request = _request(180, km=0.5)

        assert walkable_cluster_size(request, within_min=5) < 5
        assert walkable_cluster_size(request, within_min=30) == 5

    def test_거리_정보가_없으면_0이다(self) -> None:
        """"묶을 수 없다"가 아니라 "알 수 없다"다. 빈도 추세를 보는 값이라
        이 구분까지는 필요하지 않다."""

        assert walkable_cluster_size(_request(180), within_min=5) == 0


def _request_with(
    distances: dict[tuple[str, str], float],
    *,
    budget: int | None = 180,
    category: str = "attraction",
) -> SchedulePlanningRequest:
    """쌍마다 거리가 다른 요청. 묶음 판정은 어느 쌍이 가까운지가 요점이라 필요하다."""

    place_ids = sorted({place_id for pair in distances for place_id in pair})
    return SchedulePlanningRequest(
        candidates=[_candidate(place_id, category) for place_id in place_ids],
        conditions=UserConditions(time_available=budget),
        pairwise_distances_km=distances,
    )


class TestClusterIdsInOrder:
    """TP-243 — 방문 순서에서 **이웃한 구간만** 묶는다."""

    def test_이웃_구간이_도보_기준_안이면_한_묶음이다(self) -> None:
        """0.15km는 도보 3분이라 기준(5분) 안이다."""

        request = _request_with({("a", "b"): 0.15, ("b", "c"): 0.15, ("a", "c"): 0.3})

        assert cluster_ids_in_order(request, ["a", "b", "c"]) == [1, 1, 1]

    def test_중심에서만_가까운_조합은_묶지_않는다(self) -> None:
        """**이 카드가 지표용 근사를 그대로 쓰지 않는 이유다.**

        a는 b·c 둘 다에 가깝지만 b와 c는 서로 멀다. `walkable_cluster_size()`는
        a를 중심에 놓고 3을 돌려주는데(그 함수 주석이 밝힌 과대 계산),
        방문 순서가 a -> b -> c면 b -> c 구간을 실제로 걸어야 한다.
        """

        request = _request_with({("a", "b"): 0.15, ("a", "c"): 0.15, ("b", "c"): 1.5})

        assert walkable_cluster_size(request, within_min=5) == 3
        assert cluster_ids_in_order(request, ["a", "b", "c"]) == [1, 1, None]

    def test_묶음이_둘이면_번호가_다르다(self) -> None:
        """번호가 같으면 화면이 떨어진 두 묶음을 한 묶음으로 그린다."""

        request = _request_with(
            {
                ("a", "b"): 0.15,
                ("b", "c"): 2.0,
                ("c", "d"): 0.15,
                ("a", "c"): 2.0,
                ("a", "d"): 2.0,
                ("b", "d"): 2.0,
            }
        )

        assert cluster_ids_in_order(request, ["a", "b", "c", "d"]) == [1, 1, 2, 2]

    def test_한_곳짜리는_묶음이_아니다(self) -> None:
        """혼자 있는 자리에는 "가까이 붙어 있으니 짧게"라는 근거가 없다."""

        request = _request_with({("a", "b"): 2.0})

        assert cluster_ids_in_order(request, ["a", "b"]) == [None, None]
        assert cluster_ids_in_order(request, ["a"]) == [None]

    def test_거리를_모르는_구간은_묶지_않는다(self) -> None:
        """좌표를 못 구했다는 뜻이라 "가깝다"의 근거가 없다. 모를 때 묶어주면
        근거 없이 체류시간을 깎는다."""

        request = _request_with({("a", "b"): 0.15})

        assert cluster_ids_in_order(request, ["a", "b", "c"]) == [1, 1, None]


class TestClusteredItemRange:
    """TP-243 — 묶이면 곳 수가 늘어나되 한 자리까지만."""

    def test_묶이면_같은_예산에_곳_수가_늘어난다(self) -> None:
        """3시간 요청, 관광지 5곳. 0.15km면 묶여서 45분까지 내려간다."""

        far = derive_item_range(_request(180, km=2.0))[1]
        near = derive_item_range(_request(180, km=0.15))[1]

        assert far == 2
        assert near == 4

    def test_묶여도_한_자리보다_더_늘지_않는다(self) -> None:
        """**이 가드가 없으면 "짧게 머물기"가 "많이 넣기"로 새어나간다.**

        210분 요청에 묶음 최소 45분 + 허용 오차 30분이면 5곳(각 45분 + 이동
        3분 = 237분)이 산수로는 통과한다. 카드가 미리 지목한 구멍이다.
        """

        request = _request(210, km=0.15)
        clustered_max = derive_item_range(request)[1]

        assert clustered_max == 4
        assert clustered_max < 5

    def test_문화시설은_묶여도_최소값이_안_내려간다(self) -> None:
        """분류 최소값은 그 장소를 보는 데 필요한 시간이고 근접도와 무관하다 —
        박물관 옆에 갤러리가 있다고 박물관을 45분에 볼 수 있지는 않다."""

        near = derive_item_range(_request(180, km=0.15, category="cultural_facility"))
        far = derive_item_range(_request(180, km=2.0, category="cultural_facility"))

        assert near[1] == far[1] == 2

    def test_거리를_모르면_이_카드_이전과_같다(self) -> None:
        """묶을 후보가 없으면 완화된 목록이 원래 목록과 같아 결과도 같다."""

        assert derive_item_range(_request(180))[1] == 3
        assert derive_item_range(_request(300))[1] == 4


class TestClusteredRebudget:
    """TP-243 — 응답이 온 뒤에는 실제 순서로 잰다."""

    def test_묶인_자리는_짧은_체류로_다시_잰다(self) -> None:
        """묶이지 않았다면 3곳에서 잘릴 편성이 묶이면 4곳까지 남는다."""

        request = _request(180, km=0.15)
        chosen = ["attraction"] * 4

        assert cap_item_count_to_budget(request, chosen, hard_cap=4) == 3
        assert (
            cap_item_count_to_budget(
                request, chosen, hard_cap=4, clustered_flags=[True] * 4
            )
            == 4
        )

    def test_묶여도_한_자리보다_더_늘지_않는다(self) -> None:
        """상한을 정할 때와 같은 가드가 여기에도 있어야 한다 — 없으면
        "묶였으니 45분"이 곧 "그러니 한 곳 더"로 이어진다."""

        request = _request(210, km=0.15)
        chosen = ["attraction"] * 5
        plain = cap_item_count_to_budget(request, chosen, hard_cap=5)

        capped = cap_item_count_to_budget(
            request, chosen, hard_cap=5, clustered_flags=[True] * 5
        )

        assert plain == 3
        assert capped == plain + SCHEDULE_CLUSTER_EXTRA_ITEMS
