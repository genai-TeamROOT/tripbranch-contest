"""체류시간 정책 (TP-215)."""

from __future__ import annotations

import pytest

from app.schedule.duration import (
    CLUSTERED_VISIT_MINIMUM_MIN,
    policy_for,
    resolve_visit_duration,
)
from app.schemas import PlaceType


def test_known_category_uses_its_preferred_value() -> None:
    """제안값이 없으면 분류 권장값으로 채운다."""

    assert resolve_visit_duration(category=PlaceType.ATTRACTION.value) == 90
    assert resolve_visit_duration(category=PlaceType.SHOPPING.value) == 60
    assert resolve_visit_duration(category=PlaceType.CULTURAL_FACILITY.value) == 120


def test_proposal_inside_the_range_is_kept() -> None:
    """범위 안의 LLM 제안은 그대로 살린다 — 엔진이 불필요하게 덮어쓰지 않는다."""

    assert resolve_visit_duration(category=PlaceType.ATTRACTION.value, proposed_min=75) == 75


def test_proposal_below_minimum_is_raised() -> None:
    """"개수를 맞추겠다고 카페 20분"이 이 경로로 막힌다(SCHEDULE-10에서 관측된 사례)."""

    assert resolve_visit_duration(category=PlaceType.RESTAURANT.value, proposed_min=20) == 60


def test_proposal_above_maximum_is_capped() -> None:
    assert resolve_visit_duration(category=PlaceType.SHOPPING.value, proposed_min=600) == 90


def test_unknown_category_falls_back_to_a_wide_range() -> None:
    """분류를 모르는 후보에는 엔진이 강한 주장을 하지 않는다."""

    assert resolve_visit_duration(category="unknown") == 60
    assert resolve_visit_duration(category=None, proposed_min=140) == 140
    assert resolve_visit_duration(category="처음 보는 분류", proposed_min=5) == 40


@pytest.mark.parametrize("proposed", [0, -30, None])
def test_missing_or_nonpositive_proposal_uses_preferred(proposed: int | None) -> None:
    """LLM이 0을 주는 것은 "머물지 않는다"가 아니라 값을 못 만들었다는 뜻이다."""

    assert resolve_visit_duration(category=PlaceType.ATTRACTION.value, proposed_min=proposed) == 90


def test_priority_is_user_then_stored_then_proposed() -> None:
    resolved = resolve_visit_duration(
        category=PlaceType.ATTRACTION.value,
        proposed_min=70,
        stored_min=80,
        user_specified_min=110,
    )
    assert resolved == 110

    without_user = resolve_visit_duration(
        category=PlaceType.ATTRACTION.value,
        proposed_min=70,
        stored_min=80,
    )
    assert without_user == 80


def test_user_specified_value_is_still_clamped() -> None:
    """사용자 지정값도 범위로 자른다 — 여기서 통과시키면 걸러줄 곳이 뒤에 없다."""

    assert (
        resolve_visit_duration(category=PlaceType.ATTRACTION.value, user_specified_min=10) == 60
    )


def test_restaurant_minimum_admits_the_cafe_guidance() -> None:
    """카페와 식당은 같은 분류라 가를 수 없다(RecommendationItem.category = PlaceType).

    프롬프트가 안내하는 "카페 60분"이 restaurant 범위 안에 들어오는지 잠근다 —
    최소값을 올리면 카페 제안이 조용히 90분으로 부풀려진다.
    """

    assert policy_for(PlaceType.RESTAURANT.value).minimum_min <= 60
    assert resolve_visit_duration(category=PlaceType.RESTAURANT.value, proposed_min=60) == 60


def test_LLM이_준_어중간한_값을_5분_배수로_맞춘다() -> None:
    """TP-244. 프롬프트가 라운드 숫자를 안내하지만 그건 부탁이고, 지키지 않은
    값을 막을 곳이 여기 말고 없다 — 67분이 그대로 오면 화면에 "67분"이 뜬다.

    **범위로 자르기 전에 맞춘다.** 순서를 뒤집으면 최대값 90분짜리 제안이
    95분으로 올라가 범위를 넘는다.
    """

    assert resolve_visit_duration(category=PlaceType.ATTRACTION.value, proposed_min=67) == 65
    assert resolve_visit_duration(category=PlaceType.ATTRACTION.value, proposed_min=63) == 65
    assert resolve_visit_duration(category=PlaceType.ATTRACTION.value, proposed_min=62) == 60


def test_맞춘_값이_정책_범위를_넘지_않는다() -> None:
    """쇼핑 최대는 90분이다. 88분을 올리면 90분이라 경계에 딱 걸리고, 그보다
    큰 제안은 범위로 잘린다 — 맞추기가 범위를 뚫는 구멍이 되면 안 된다."""

    assert resolve_visit_duration(category=PlaceType.SHOPPING.value, proposed_min=88) == 90
    assert resolve_visit_duration(category=PlaceType.SHOPPING.value, proposed_min=93) == 90
    assert resolve_visit_duration(category=PlaceType.SHOPPING.value, proposed_min=32) == 30


def test_분류_권장값은_이미_5분_배수다() -> None:
    """제안이 없으면 권장값이 나간다. 정책 표의 값이 하나라도 5분 배수가
    아니게 되면 여기서 잡힌다."""

    for category in (
        PlaceType.ATTRACTION,
        PlaceType.CULTURAL_FACILITY,
        PlaceType.FESTIVAL,
        PlaceType.LEISURE,
        PlaceType.SHOPPING,
        PlaceType.RESTAURANT,
    ):
        policy = policy_for(category.value)
        assert policy.minimum_min % 5 == 0
        assert policy.preferred_min % 5 == 0
        assert policy.maximum_min % 5 == 0


def test_묶이면_관광지_최소값이_45분까지_내려간다() -> None:
    """TP-243 — 사용자 문의 원문("5분 거리 세 곳을 1시간 반")이 이 값의 근거다."""

    assert policy_for(PlaceType.ATTRACTION.value).minimum_min == 60
    assert (
        policy_for(PlaceType.ATTRACTION.value, clustered=True).minimum_min
        == CLUSTERED_VISIT_MINIMUM_MIN
    )


def test_문화시설과_식당은_묶여도_그대로다() -> None:
    """분류 최소값은 그 장소를 보는(먹는) 데 필요한 시간이라 근접도와 무관하다.

    `min(분류최소, 묶음최소)`를 쓰면 이 둘까지 45분으로 내려간다 — 그래서 그
    식을 쓰지 않는다.
    """

    for category in (PlaceType.CULTURAL_FACILITY.value, PlaceType.RESTAURANT.value):
        assert (
            policy_for(category, clustered=True).minimum_min
            == policy_for(category).minimum_min
        )


def test_묶음은_권장값과_최대값을_안_건드린다() -> None:
    """묶음은 "짧게 머물러도 된다"는 근거지 "짧게 머물러야 한다"는 지시가 아니다.

    여유가 있으면 원래대로 오래 머문다 — 최대값을 함께 내리면 그 길이 막힌다.
    """

    plain = policy_for(PlaceType.ATTRACTION.value)
    clustered = policy_for(PlaceType.ATTRACTION.value, clustered=True)

    assert (clustered.preferred_min, clustered.maximum_min) == (
        plain.preferred_min,
        plain.maximum_min,
    )
    assert resolve_visit_duration(category=PlaceType.ATTRACTION.value, clustered=True) == 90


def test_묶이면_LLM이_준_45분을_끌어올리지_않는다() -> None:
    """묶이지 않은 자리에서는 60분으로 올린다 — 그 클램프가 골목 세 곳을 각각
    한 시간씩 앉아 있게 만들던 자리다."""

    attraction = PlaceType.ATTRACTION.value

    assert resolve_visit_duration(category=attraction, proposed_min=45) == 60
    assert resolve_visit_duration(category=attraction, proposed_min=45, clustered=True) == 45


def test_묶여도_45분_아래로는_안_내려간다() -> None:
    """완화는 최소값을 바꾸는 것이지 없애는 것이 아니다."""

    assert (
        resolve_visit_duration(
            category=PlaceType.ATTRACTION.value, proposed_min=20, clustered=True
        )
        == CLUSTERED_VISIT_MINIMUM_MIN
    )
