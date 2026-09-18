"""구 단위 후보 선택 규칙을 못 박는다(D-119).

기대값은 지어낸 것이 아니라 2026-09-01 Supabase 전량 실측에서 나온 수다. 그래서
모집단도 실측 구성을 그대로 흉내 낸다 — 강남구는 30곳을 채우고 금천구는 20곳에서
멈추는데, 그 20이 이 규칙이 데이터 앞에서 어떻게 행동하는지를 보여주는 수다.
"""

from __future__ import annotations

from collections import Counter

from app.agent_context.district_selection import (
    MIN_TURN_CANDIDATES,
    select_district_candidates,
)
from app.schemas import PlaceCandidate

# 실측 구성. (contentTypeId, 그 구의 활성 장소 수)
#
# 축제(15)는 넣지 않는다. `resolve_place_category()`가 후보로 올리지 않아(D-120)
# 여기까지 올 수 없다 — 강남구 13곳·금천구 3곳이 실측이지만 모집단에서 빠진다.
_GANGNAM = {"12": 39, "14": 69, "39": 260, "38": 713, "28": 6}
_GEUMCHEON = {"12": 10, "14": 7, "39": 7, "38": 224, "28": 0}


def _place(
    place_id: str, content_type_id: str, latitude: float, longitude: float
) -> PlaceCandidate:
    return PlaceCandidate(
        place_id=place_id,
        content_type_id=content_type_id,
        name=f"장소 {place_id}",
        category="test",
        latitude=latitude,
        longitude=longitude,
        raw_source="test",
    )


def _district(composition: dict[str, int]) -> list[PlaceCandidate]:
    """분류별 개수를 주면 구 하나를 만든다. 좌표는 격자 16칸에 고르게 흩는다."""
    places: list[PlaceCandidate] = []
    index = 0
    for content_type_id, count in composition.items():
        for _ in range(count):
            # 4x4 격자를 채우도록 0.00~0.03을 순환시킨다.
            row = (index % 4) * 0.01
            column = ((index // 4) % 4) * 0.01
            places.append(
                _place(f"p{index:04d}", content_type_id, 37.50 + row, 127.00 + column)
            )
            index += 1
    return places


def _counts(places: tuple[PlaceCandidate, ...]) -> Counter[str]:
    return Counter(place.content_type_id or "" for place in places)


def test_분류_몫대로_담는다() -> None:
    """데이터가 넉넉한 구는 몫이 그대로 나온다(강남구: 8·8·6·6·2)."""
    selected = select_district_candidates(_district(_GANGNAM), limit=30)

    assert len(selected) == 30
    assert _counts(selected) == Counter({"12": 8, "14": 8, "39": 6, "38": 6, "28": 2})


def test_쇼핑은_다른_분류가_모자라도_6을_넘지_않는다() -> None:
    """쇼핑 절대 상한이 없으면 넘친 자리가 전부 쇼핑으로 간다(금천구 15곳이었다)."""
    selected = select_district_candidates(_district(_GEUMCHEON), limit=30)

    assert _counts(selected)["38"] == 6


def test_얇은_구는_30을_못_채우고_모자란_대로_준다() -> None:
    """금천구는 20곳에서 멈춘다. 248곳을 갖고도 여행에 쓸 만한 것이 24곳뿐이라 그렇다.

    몫이 8로 올랐어도 관광지는 6곳에서 멈춘다 — 소진율 상한이 몫보다 먼저 걸린다.
    """
    selected = select_district_candidates(_district(_GEUMCHEON), limit=30)

    assert len(selected) == 20
    # 관광지 10곳 중 6곳(60%), 문화시설·음식점 7곳 중 4곳, 쇼핑은 절대 상한 6곳.
    assert _counts(selected) == Counter({"12": 6, "14": 4, "39": 4, "38": 6})


def test_소진율_상한이_바닥_긁기를_막는다() -> None:
    """문화시설이 7곳뿐인 구에서 7곳을 다 쓰지 않는다."""
    selected = select_district_candidates(_district({"14": 7, "38": 100}), limit=30)

    # int(7 * 0.60) = 4.
    assert _counts(selected)["14"] == 4


def test_한쪽에_몰린_구에서도_여러_칸으로_흩어진다() -> None:
    """거리순으로 자르면 한 칸에서 다 나온다 — 그걸 막는 것이 격자다."""
    dense = [_place(f"d{i:03d}", "39", 37.5001 + i * 0.000001, 127.0001) for i in range(200)]
    sparse = [
        _place(f"s{i:03d}", "39", 37.50 + i * 0.01, 127.00 + i * 0.01) for i in range(1, 4)
    ]
    selected = select_district_candidates(dense + sparse, limit=6)

    # 밀집한 200곳이 자리를 독식하지 않고 흩어진 3곳이 모두 뽑힌다.
    assert {place.place_id for place in selected} >= {"s001", "s002", "s003"}


def test_사용자가_분류를_말하면_몫도_상한도_걸지_않는다() -> None:
    """"강남구 카페"에 관광지를 섞어 넣으면 요청을 무시하는 것이 된다."""
    only_restaurants = _district({"39": 40})

    selected = select_district_candidates(
        only_restaurants, limit=30, has_category_condition=True
    )

    assert len(selected) == 30
    assert _counts(selected) == Counter({"39": 30})


def test_사용자_분류_조건에는_소진율_상한을_걸지_않는다() -> None:
    """지목한 분류가 20곳뿐이면 12곳으로 깎지 않고 다 보여준다."""
    selected = select_district_candidates(
        _district({"39": 20}), limit=30, has_category_condition=True
    )

    assert len(selected) == 20


def test_더_보기는_이미_본_곳을_다시_주지_않는다() -> None:
    places = _district(_GANGNAM)
    first = select_district_candidates(places, limit=30)

    second = select_district_candidates(
        places, excluded_place_ids=[place.place_id for place in first], limit=30
    )

    assert not {place.place_id for place in second} & {place.place_id for place in first}


def test_넘기기는_첫_턴에만_한다() -> None:
    """더 보기 턴에서도 넘기면 한 분류가 목록을 먹는다(강남구 4턴 문화시설 17곳)."""
    places = _district(_GANGNAM)
    shown = [place.place_id for place in select_district_candidates(places, limit=30)]

    second = select_district_candidates(places, excluded_place_ids=shown, limit=30)

    # 몫을 넘어서는 분류가 없다 — 넘기기가 돌았다면 한 분류가 몫을 크게 넘는다.
    # 8은 가장 두꺼운 몫(관광지·문화시설)이다.
    assert all(count <= 8 for count in _counts(second).values())


def test_더_보기를_반복하면_결국_소진_하한_아래로_떨어진다() -> None:
    """무한히 이어지지 않는다는 것을 못 박는다 — 금천구가 쇼핑만 계속 내보내던 문제."""
    places = _district(_GEUMCHEON)
    shown: list[str] = []
    turns = 0

    while True:
        selected = select_district_candidates(
            places, excluded_place_ids=shown, limit=30
        )
        if len(selected) < MIN_TURN_CANDIDATES:
            break
        turns += 1
        shown.extend(place.place_id for place in selected)
        assert turns < 10, "소진되지 않고 계속 후보가 나온다"

    assert turns == 1


def test_같은_입력에_같은_결과를_준다() -> None:
    """무작위를 쓰지 않는다 — 같은 질문에 다른 목록이 나오면 안 된다."""
    places = _district(_GANGNAM)

    first = select_district_candidates(places, limit=30)
    second = select_district_candidates(places, limit=30)

    assert [place.place_id for place in first] == [place.place_id for place in second]


def test_후보가_없거나_limit이_0이면_빈_결과다() -> None:
    assert select_district_candidates([], limit=30) == ()
    assert select_district_candidates(_district(_GANGNAM), limit=0) == ()


def test_좌표가_모두_같아도_예외로_끊기지_않는다() -> None:
    """격자 범위가 0이 되는 경우다. 한 칸으로 보고 그대로 고른다."""
    same_spot = [_place(f"x{i:03d}", "39", 37.5, 127.0) for i in range(10)]

    selected = select_district_candidates(same_spot, limit=5)

    assert len(selected) == 5
