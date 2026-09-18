"""구 전체 후보 중에서 실제로 채점에 올릴 것을 고른다(D-119).

역할: 구 단위 요청("강남구에서 추천해줘")은 반경이 없어 후보 모집단이 구 전량이다.
1,100곳을 30곳으로 줄이는 일을 여기서 한다.
입력: 그 구의 활성 장소 전량, 이미 보여준 place_id, 뽑을 개수.
출력: 고른 후보.
호출 시점: 구 단위 후보 조회 직후, 상세 보완 전(tools/nearby_place_details.py).

**이건 채점이 아니라 선택이다.** 날씨·운영시간·취향은 보지 않는다 — 그건 전부 D가
한다. 여기서 보는 것은 분류와 위치 두 가지뿐이고, 목적은 하나다. **한쪽으로 쏠리지
않게 고르는 것.**

쏠림이 왜 문제인지는 실측이 말해준다(2026-09-01, Supabase 전량). 구 대표점에서
거리순으로 30곳을 자르면 마포구는 30곳 중 25곳이 쇼핑이고 전부 홍대 매장이었다
(웍스아웃·반스·올리브영·헤메코). 강남구는 관광지가 1곳이었다. 무작위로 바꿔도
원본 분포를 그대로 따라가 쇼핑이 17~18곳이 된다 — 강남구는 1,100곳 중 713곳이
쇼핑이고 그 실체가 성형외과·약국·브랜드 매장이기 때문이다.

## 두 축으로 고른다

- **분류 몫**(`_CATEGORY_QUOTA`) — 무엇을 몇 개씩 담을지. 쏠림을 막는 주 수단이다.
- **격자 분산**(`_GRID_SIZE`) — 같은 분류 안에서 누구를 먼저 집을지. 구를 4x4로
  나누고 덜 쓴 칸의 장소를 먼저 고른다.

격자는 칸마다 할당량을 주는 방식이 아니다. 개수를 정하는 것은 언제나 분류 몫이고,
격자는 그 몫을 어느 동네에서 채울지만 정한다. 칸별 할당으로 하면 채울 수 없는 칸
(장소가 1곳뿐인 칸이 실재한다)의 몫을 어디로 보낼지 또 정해야 하고, 무엇보다 그
칸에 쇼핑밖에 없으면 분류 균형이 깨진다.

두 축을 합쳐도 서로 손해를 보지 않는다는 것이 실측으로 확인됐다 — 격자 칸 수는
격자만 썼을 때와 같고(강남 12/16, 마포 11/16), 분류 구성은 몫만 썼을 때와 같다.

## 정해진 규칙 뒤의 이유

- **소진율 상한**(`_DEPLETION_CAP`) — 한 분류에서 그 구가 가진 것의 60%를 넘게 쓰지
  않는다. 없으면 얇은 구가 바닥을 긁는다(상한 없이 재면 금천구 문화시설 7/7,
  동작구 8/8, 강동구 7/7이 전부 100% 소진이었다).
- **쇼핑 절대 상한** — 소진율과 별개로 몫 6을 넘지 않는다. 상한만 걸면 다른 분류에서
  넘친 자리가 전부 쇼핑으로 흘러간다(금천구에서 쇼핑 15곳이 됐다).
- **모자라면 모자란 대로 준다** — 25개 구 전량 검증에서 중랑 29곳·도봉 28곳·금천
  21곳이 30을 못 채운다. 금천구가 21곳인 것은 이 규칙의 결함이 아니라 데이터가
  그만큼이기 때문이다. 251곳 중 224곳이 쇼핑이라 여행 추천에 쓸 만한 장소가
  27곳뿐이고, 억지로 30을 맞추면 쇼핑 15곳이 들어간다.

## 사용자가 분류를 말했으면 몫을 적용하지 않는다

"강남구 카페"는 조건이 이미 분류를 확정한 요청이다. 여기에 관광지 7곳을 넣으면
요청을 무시하는 것이 된다. 그래서 `has_category_condition`이 참이면 몫도 소진율
상한도 걸지 않고 격자 분산만 적용한다 — 상한은 "우리가 고른 분류를 다 긁지 않기"
위한 것인데, 사용자가 지목한 분류는 다 보여주는 편이 맞다.

## 무작위를 쓰지 않는다

같은 요청에 같은 답이 나와야 한다. 같은 칸 안에서 우열을 가릴 수 없을 때는
place_id 순으로 끊는다. 실측 스크립트는 난수를 썼지만 그건 표본을 흔들어 보려던
것이고, 운영에서는 같은 질문에 다른 목록이 나오는 편이 나쁘다.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence

from app.schemas import PlaceCandidate

# 구를 몇 x 몇으로 나눌지. 4x4에서 강남구 12/16칸·마포구 11/16칸에 흩어졌다
# (빈 칸은 구 경계 밖이라 장소가 아예 없는 자리다). 더 잘게 나눌수록 분산은
# 늘지만 칸당 장소가 줄어 분류 몫과 부딪힌다.
_GRID_SIZE = 4

# TourAPI contentTypeId. providers/mappers.py::_CONTENT_TYPE_TO_CATEGORY와 같은 값이다.
_ATTRACTION = "12"
_CULTURAL_FACILITY = "14"
_LEISURE = "28"
_SHOPPING = "38"
_RESTAURANT = "39"

# 분류별로 30자리를 어떻게 나눌지. 합이 30이다.
#
# 관광 목적이므로 관광지·문화시설을 가장 두껍게 둔다. 음식점은 데이터가 두꺼워
# (강남 260·마포 105·종로 191) 6곳이어도 질이 떨어지지 않는다. 쇼핑은 원본의
# 60%를 넘게 차지하지만(강남구 713/1,100) 실체가 여행 추천에 쓸 것이 아니라 6으로
# 누른다. 레포츠는 구마다 한 자릿수라 얇게 둔다 — 균등하게 5씩 주면 강남구 레포츠
# 6곳 중 5곳을 긁어 세븐럭카지노·청소년센터까지 올라온다.
#
# **축제공연행사(15)는 몫이 없다.** 후보 자체가 오지 않기 때문이다 —
# `providers/mappers.py::_UNSUPPORTED_CONTENT_TYPE_IDS`가 끝난 행사를 거를 수 없다는
# 이유로 이 유형을 뺐다(D-120). 그 몫 2를 관광지·문화시설에 1씩 얹어 7·7에서 8·8로
# 올렸다. 둘은 이미 가장 두껍게 두기로 한 분류이고 모집단도 충분해(강남 39·69,
# 금천 10·7) 몫을 늘려도 소진율 상한에 먼저 걸린다.
_CATEGORY_QUOTA: dict[str, int] = {
    _ATTRACTION: 8,
    _CULTURAL_FACILITY: 8,
    _RESTAURANT: 6,
    _SHOPPING: 6,
    _LEISURE: 2,
}

# 몫을 채우는 순서. 앞의 분류부터 담으므로 모집단이 얇아 서로 밀릴 때 앞이 살아남는다.
_FILL_ORDER: tuple[str, ...] = (
    _ATTRACTION,
    _CULTURAL_FACILITY,
    _RESTAURANT,
    _SHOPPING,
    _LEISURE,
)

# 한 분류에서 그 구가 가진 것의 이 비율을 넘게 쓰지 않는다.
_DEPLETION_CAP = 0.60

# 이번 턴 후보가 이 수보다 적으면 호출부가 소진으로 본다.
#
# 이 하한이 없으면 금천구가 쇼핑 6곳을 무한히 내보낸다 — 쇼핑이 224곳이라 절대
# 마르지 않기 때문이다. 10이 아니라 15인 것은, 후반 턴이 "음식점 6 + 쇼핑 6"으로
# 수렴해 쇼핑 비중이 20%에서 50%로 오르는 구간을 잘라내기 위해서다. 15로 두면
# 강남구는 7턴, 마포구는 5턴에서 끝난다.
MIN_TURN_CANDIDATES = 15

_Bounds = tuple[float, float, float, float]  # min_lat, max_lat, min_lon, max_lon
_Cell = tuple[int, int]


def _bounds_of(candidates: Sequence[PlaceCandidate]) -> _Bounds:
    """격자의 기준이 되는 구 전체의 좌표 범위.

    **남은 후보가 아니라 구 전량으로 잰다.** 더 보기 턴마다 다시 재면 칸의 경계가
    턴마다 움직여서, 이미 쓴 칸을 셀 수 없다.
    """
    latitudes = [candidate.latitude for candidate in candidates]
    longitudes = [candidate.longitude for candidate in candidates]
    return (min(latitudes), max(latitudes), min(longitudes), max(longitudes))


def _cell_of(candidate: PlaceCandidate, bounds: _Bounds) -> _Cell:
    min_lat, max_lat, min_lon, max_lon = bounds
    latitude_span = max_lat - min_lat
    longitude_span = max_lon - min_lon
    # 후보가 한 곳뿐이거나 모두 같은 좌표면 범위가 0이다. 나누지 않고 한 칸으로 본다.
    row = (
        0
        if latitude_span <= 0
        else min(_GRID_SIZE - 1, int((candidate.latitude - min_lat) / latitude_span * _GRID_SIZE))
    )
    column = (
        0
        if longitude_span <= 0
        else min(
            _GRID_SIZE - 1,
            int((candidate.longitude - min_lon) / longitude_span * _GRID_SIZE),
        )
    )
    return row, column


def select_district_candidates(
    district_places: Sequence[PlaceCandidate],
    *,
    excluded_place_ids: Iterable[str] = (),
    limit: int,
    has_category_condition: bool = False,
) -> tuple[PlaceCandidate, ...]:
    """구 전량에서 이번 턴에 쓸 후보를 고른다.

    `district_places`는 **그 구의 활성 장소 전량**이어야 한다. 남은 것만 넘기면
    소진율 상한의 분모와 격자 경계가 턴마다 달라진다.

    `excluded_place_ids`가 비어 있으면 첫 턴으로 본다. 첫 턴에서만 넘기기를
    허용한다 — 몫을 못 채운 자리를 다른 분류로 넘기는 동작을 더 보기 턴에서도
    허용하면 3~4턴째에 한 분류가 목록을 먹는다(강남구 4턴 문화시설 17곳, 마포구
    4턴 음식점 24곳). 첫 턴에만 허용하면 구성이 유지되고, 대신 턴이 갈수록
    후보 수가 줄어든다.
    """
    if limit <= 0 or not district_places:
        return ()

    excluded = set(excluded_place_ids)
    is_first_turn = not excluded
    bounds = _bounds_of(district_places)

    # 아직 안 보여준 후보만 담되, 분모(구 전량)와 이미 쓴 수는 전량 기준으로 센다.
    remaining: dict[str, list[PlaceCandidate]] = defaultdict(list)
    total_by_type: Counter[str] = Counter()
    shown_by_type: Counter[str] = Counter()
    used_cells: Counter[_Cell] = Counter()
    for place in district_places:
        content_type = place.content_type_id or ""
        total_by_type[content_type] += 1
        if place.place_id in excluded:
            shown_by_type[content_type] += 1
            # 이미 보여준 곳도 칸을 차지한 것으로 센다. 그래야 더 보기가 같은
            # 동네를 다시 파지 않는다.
            used_cells[_cell_of(place, bounds)] += 1
        else:
            remaining[content_type].append(place)

    # 같은 칸 안에서 우열을 가릴 수 없을 때 place_id로 끊기 위해 미리 정렬한다.
    for places in remaining.values():
        places.sort(key=lambda place: place.place_id)

    room = _room_by_type(
        total_by_type=total_by_type,
        shown_by_type=shown_by_type,
        remaining_types=set(remaining),
        has_category_condition=has_category_condition,
    )

    picked: list[PlaceCandidate] = []
    taken_by_type: Counter[str] = Counter()

    def take(content_type: str) -> bool:
        places = remaining.get(content_type)
        if not places or room[content_type] <= 0:
            return False
        # 덜 쓴 칸을 먼저 고른다. 같은 칸 수면 앞의 것(place_id 순)이 이긴다.
        best = min(places, key=lambda place: used_cells[_cell_of(place, bounds)])
        places.remove(best)
        used_cells[_cell_of(best, bounds)] += 1
        room[content_type] -= 1
        taken_by_type[content_type] += 1
        picked.append(best)
        return True

    if has_category_condition:
        # 사용자가 분류를 지목한 요청이다. 몫을 걸지 않고 격자 분산만 적용한다.
        _fill_remaining(take, room, picked, limit)
        return tuple(picked)

    # 1단계: 분류별 몫만큼 담는다.
    for content_type in _FILL_ORDER:
        while taken_by_type[content_type] < _CATEGORY_QUOTA[content_type] and len(picked) < limit:
            if not take(content_type):
                break

    # 2단계: 남은 자리를 다른 분류로 넘긴다. 첫 턴에만 한다.
    if is_first_turn:
        _fill_remaining(take, room, picked, limit)
    return tuple(picked)


def _room_by_type(
    *,
    total_by_type: Counter[str],
    shown_by_type: Counter[str],
    remaining_types: set[str],
    has_category_condition: bool,
) -> dict[str, int]:
    """분류마다 이번 턴에 더 쓸 수 있는 수.

    소진율 상한은 **턴 누적**으로 잰다. 턴마다 초기화하면 1턴에 60%, 2턴에 남은
    것의 60%를 또 써서 누적 84%가 되고 상한을 둔 뜻이 사라진다. 누적 기준을 따로
    저장할 필요는 없다 — 분모(구 전량)는 고정이고 이미 보여준 수는 제외 목록으로
    넘어오므로 그 둘로 계산된다.
    """
    room: dict[str, int] = defaultdict(int)
    for content_type in remaining_types | set(_CATEGORY_QUOTA):
        if has_category_condition:
            # 사용자가 지목한 분류는 다 보여줘도 된다. 상한을 걸지 않는다.
            room[content_type] = total_by_type[content_type]
            continue
        ceiling = int(total_by_type[content_type] * _DEPLETION_CAP)
        available = max(0, ceiling - shown_by_type[content_type])
        if content_type == _SHOPPING:
            # 소진율과 별개로 턴당 몫을 절대 넘기지 않는다. 이게 없으면 다른
            # 분류에서 넘친 자리가 전부 쇼핑으로 흘러간다.
            available = min(available, _CATEGORY_QUOTA[_SHOPPING])
        room[content_type] = available
    return room


def _fill_remaining(
    take,  # noqa: ANN001 - 위 클로저를 그대로 받는다.
    room: dict[str, int],
    picked: list[PlaceCandidate],
    limit: int,
) -> None:
    """남은 자리를 여유 있는 분류로 채운다. 한 바퀴에 한 곳씩 돌아가며 담는다.

    한 분류를 다 채우고 다음으로 넘어가면 앞의 분류가 자리를 독식한다.
    """
    while len(picked) < limit:
        progressed = False
        for content_type in _FILL_ORDER:
            if len(picked) >= limit:
                break
            if room[content_type] > 0 and take(content_type):
                progressed = True
        if not progressed:
            return


__all__ = ["MIN_TURN_CANDIDATES", "select_district_candidates"]
