"""장소별 체류시간 정책. (TP-215)

**왜 필요한가.** 지금까지 `estimated_duration_min`은 프롬프트 예시("카페 60분,
관광지 90분")를 보고 LLM이 추정한 값이었다. 예시일 뿐이라 범위를 벗어난 값이
와도 막을 곳이 없었고, "개수를 맞추겠다고 카페 20분"처럼 비현실적으로 줄어드는
사례가 SCHEDULE-10에서 실제로 관측됐다. 프롬프트로 부탁하는 대신 엔진이
범위로 확정한다(SCHEDULE-07의 "LLM 지시 준수보다 구조적 보장을 우선한다").

**분류 어휘.** `RecommendationItem.category`는 `PlaceType` 값이다
(`providers/mappers.py`의 contentTypeId → PlaceType 매핑). 그래서 카페와 식당을
가를 수 없다 — 둘 다 `restaurant`다. 세분화하려면 `lcls_systm2`를
`RecommendationItem`까지 올려야 하는데 그건 D 소유 스키마라 이번 범위 밖이다.
`restaurant`의 최소값을 60분으로 둬서 프롬프트가 안내하는 "카페 60분"이 범위
안에 들어오게 맞췄다.

**아직 쓰지 않는 것.** `place_enrichments.estimated_visit_minutes` 컬럼이 있지만
값이 채워져 있지 않아 이번에는 참조하지 않는다. 채워지면
`resolve_visit_duration()`의 `stored_min` 인자로 넣기만 하면 된다 — 우선순위는
이미 그 자리에 뚫어뒀다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from app.schemas import PlaceType

# 체류시간을 배정하는 단위(분). (TP-244)
#
# **왜 값 자체를 맞추나.** 표시할 때만 반올림하면 항목 표시의 합과 총합 표시가
# 어긋난다 — TP-215가 일부러 없앤 상태로 되돌아간다. 배정값이 이미 5분 배수면
# 저장값·표시값·항목 합이 전부 같은 수를 가리킨다.
VISIT_DURATION_STEP_MIN = 5


def snap_to_step(minutes: int) -> int:
    """가장 가까운 `VISIT_DURATION_STEP_MIN` 배수로 맞춘다. (TP-244)

    딱 중간이면 올린다 — 체류시간을 임의로 깎는 쪽보다 낫다. 정책 범위를 보지
    않으므로 호출부가 맞춘 뒤 `clamp()`한다. 순서를 뒤집으면 경계값(예: 최대
    90분)이 95분으로 올라가 범위를 넘는다.
    """

    step = VISIT_DURATION_STEP_MIN
    return (minutes + step // 2) // step * step


@dataclass(frozen=True)
class VisitDurationPolicy:
    """한 분류의 체류시간 범위(분)."""

    minimum_min: int
    preferred_min: int
    maximum_min: int

    def clamp(self, minutes: int) -> int:
        return max(self.minimum_min, min(minutes, self.maximum_min))


# 분류별 (최소, 권장, 최대). 근거는 프롬프트가 이미 안내하던 체류시간 예시다.
# **이 최소값이 개수 상한을 정하는 입력이다** — budget.derive_item_range()가
# 이 값을 읽어 몇 곳이 예산에 들어가는지 계산한다(TP-239). 예전에는 개수 쪽이
# 자기 가정(장소당 60~90분)을 따로 갖고 있어서 "개수는 맞는데 시간이 안 맞는"
# 일정이 나왔다. 이제 한 곳에서만 읽으므로 이 값을 바꾸면 상한도 함께 움직인다.
_POLICY_BY_CATEGORY: dict[str, VisitDurationPolicy] = {
    PlaceType.ATTRACTION.value: VisitDurationPolicy(60, 90, 120),
    PlaceType.CULTURAL_FACILITY.value: VisitDurationPolicy(90, 120, 180),
    PlaceType.FESTIVAL.value: VisitDurationPolicy(60, 90, 150),
    PlaceType.LEISURE.value: VisitDurationPolicy(60, 90, 150),
    PlaceType.SHOPPING.value: VisitDurationPolicy(30, 60, 90),
    PlaceType.RESTAURANT.value: VisitDurationPolicy(60, 90, 120),
}

# 분류를 모르는 후보("unknown" 또는 매핑에 없는 값)의 폴백. 범위를 넓게 두는 것은
# 모르는 장소에 엔진이 강한 주장을 하지 않기 위해서다 — LLM 제안을 그대로
# 살려주는 쪽에 가깝다.
_DEFAULT_POLICY = VisitDurationPolicy(40, 60, 150)

# 도보로 붙어 있는 자리(묶음)에서 허용하는 체류 최소값(분). (TP-243)
#
# 사용자 문의 원문이 근거다 — "5분 거리 이내인 세 장소는 묶어서 1시간 반으로
# 배치". 90분에서 이동 10분을 빼면 곳당 27분인데, 그건 분류 최소값을 통째로
# 무시하는 값이라 그대로 받지 않았다. 45분은 **이동 3분 기준으로 3시간에 3곳이
# 각 58분으로 들어가는 값**이고, 30분까지 내리면 3시간에 5곳이 되어 과하다.
CLUSTERED_VISIT_MINIMUM_MIN = 45

# 묶였다고 해서 최소값을 내려주는 분류. (TP-243)
#
# **문화시설(90분)은 넣지 않는다.** 분류 최소값은 "그 장소를 보는 데 필요한
# 시간"이고 근접도와 무관하다 — 박물관 옆에 갤러리가 있다고 박물관을 45분에
# 볼 수 있게 되지는 않는다. 식당(60분)도 같은 이유로 뺐다(밥 먹는 시간이다).
# 쇼핑은 이미 30분이라 내릴 것이 없다.
#
# 그래서 규칙은 `min(분류최소, 묶음최소)`가 아니다. 그 식을 쓰면 모든 분류가
# 45분까지 내려가고, 위 두 분류에서 "짧게 머물러도 되는 근거"가 근거 없이 쓰인다.
_CLUSTER_RELAXABLE_CATEGORIES = frozenset(
    {
        PlaceType.ATTRACTION.value,
        PlaceType.FESTIVAL.value,
        PlaceType.LEISURE.value,
    }
)


def policy_for(category: str | None, *, clustered: bool = False) -> VisitDurationPolicy:
    """분류에 맞는 체류시간 정책. 모르는 분류는 폴백을 돌려준다.

    `clustered`가 참이면 **최소값만** `CLUSTERED_VISIT_MINIMUM_MIN`까지 내린다
    (TP-243). 권장값과 최대값은 그대로다 — 묶음은 "짧게 머물러도 된다"는 근거지
    "짧게 머물러야 한다"는 지시가 아니다. 여유가 있으면 원래대로 오래 머문다.

    완화 대상이 아닌 분류(`_CLUSTER_RELAXABLE_CATEGORIES` 밖)는 `clustered`를
    참으로 줘도 값이 바뀌지 않는다 — 그 판단은 상수 주석에 있다.
    """

    if category is None:
        base = _DEFAULT_POLICY
        key = None
    else:
        key = category.strip().lower()
        base = _POLICY_BY_CATEGORY.get(key, _DEFAULT_POLICY)
    if not clustered or key not in _CLUSTER_RELAXABLE_CATEGORIES:
        return base
    # **낮추는 규칙이지 45분으로 맞추는 규칙이 아니다.** 지금 완화 대상 중에
    # 45분보다 낮은 분류는 없어서 분기로 적으면 어떤 테스트도 지나가지 않는
    # 죽은 가지가 된다 — 그래서 `min()`으로 적어 의도가 식에 남게 했다.
    return replace(
        base, minimum_min=min(base.minimum_min, CLUSTERED_VISIT_MINIMUM_MIN)
    )


def resolve_visit_duration(
    *,
    category: str | None,
    proposed_min: int | None = None,
    stored_min: int | None = None,
    user_specified_min: int | None = None,
    clustered: bool = False,
) -> int:
    """이 장소에 실제로 배정할 체류시간(분)을 확정한다.

    우선순위는 사용자 지정 → 저장된 장소별 값 → LLM 제안 → 분류 권장값이고,
    **어느 경로로 왔든 마지막에 분류 범위로 자른다.** 사용자 지정값까지 자르는
    것이 맞는지는 한 번 갈렸는데, 자르는 쪽으로 정했다 — 여기서 통과시키면
    시간표 계산은 맞아도 "관광지 10분"처럼 사람이 못 지키는 일정이 나가고,
    그걸 걸러줄 곳이 뒤에 없다.

    0 이하와 None은 같게 취급한다. LLM이 0을 주는 것은 값을 만들지 못했다는
    뜻이지 "머물지 않는다"는 뜻이 아니다.

    **어느 경로로 왔든 5분 배수로 맞춘다.** (TP-244) 프롬프트가 라운드 숫자를
    안내하지만 그건 부탁이고, 지키지 않은 값을 막을 곳이 여기 말고 없다 —
    LLM이 67분을 주면 화면에 "67분"이 그대로 뜬다. 범위로 자르는 것과 같은
    이유이고 같은 자리다.

    **`clustered`는 최소값만 낮춘다.** (TP-243) 도보로 붙어 있는 자리에서는
    LLM이 준 45분을 60분으로 끌어올리지 않는다 — 인사동 골목 세 곳을 각각 한
    시간씩 앉아 있게 만드는 것이 그 클램프였다. 어느 분류가 완화되는지는
    `policy_for()` 주석에 있다.
    """

    policy = policy_for(category, clustered=clustered)
    for proposal in (user_specified_min, stored_min, proposed_min):
        if proposal is not None and proposal > 0:
            return policy.clamp(snap_to_step(proposal))
    return policy.preferred_min


__all__ = [
    "CLUSTERED_VISIT_MINIMUM_MIN",
    "VISIT_DURATION_STEP_MIN",
    "VisitDurationPolicy",
    "policy_for",
    "resolve_visit_duration",
    "snap_to_step",
]
