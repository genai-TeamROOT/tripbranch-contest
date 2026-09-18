"""위치 기반 장소 수집과 거리 계산이 공유하는 정책 상수."""

from app.domain.travel_route import TravelMode

# 성인의 MVP 도보 이동속도 가정값(km/분): 1분에 약 70m를 이동한다.
WALKING_SPEED_KM_PER_MINUTE = 0.07

# 도보가 아닌 이동수단의 MVP 이동속도 가정값(km/분) = 20km/h.
#
# **이 값은 실측 속도가 아니라 검색 반경을 만든 가정과 같은 값이어야 한다.**
# 반경 산정(runtime/recommendation_transform.py::to_search_radius_km)이 비도보
# 요청에 20km/h를 쓰므로, 시간 예산이 같은 값으로 나눠야 사용자가 말한 이동시간이
# 그대로 예산이 된다(scoring.py::_travel_minutes_budget 참고). 실측값을 넣으면
# 분모가 "사용자 약속"이 아니게 되어 그 설계가 깨진다.
#
# 같은 20km/h가 recommendation_transform._OTHER_KM_PER_MIN에도 있다. 두 값은
# 반드시 같아야 하므로 한쪽만 바꾸지 않는다. 파일이 A·C로 갈려 있어 아직 상수를
# 합치지 못했다.
NON_WALKING_SPEED_KM_PER_MINUTE = 20 / 60

# 이동수단별 이동속도 가정값(km/분). 거리 점수의 시간 예산이 이 값으로 검색
# 반경을 소요시간으로 되돌린다(domain/scoring.py::_travel_minutes_budget).
#
# 대중교통도 20km/h를 쓴다. 실측값(경복궁 기준 7개 구간에서 직선거리 기준 실효
# 4.6~19km/h)이 아니라 반경 산정과 같은 가정이어야 하기 때문이다 — 반경 산정
# (recommendation_transform.to_search_radius_km)이 비도보 요청을 이동수단으로
# 가르지 않고 _OTHER_KM_PER_MIN 하나로 처리한다. 여기에 실측을 넣으면 분자와
# 분모의 기준이 어긋난다.
#
# 새 이동수단을 넣을 때 함께 넓혀야 하는 곳이 하나 더 있다: scoring.py의
# _applied_travel_route()가 실측 여부를 RouteSource로 가리므로 새 이동수단의
# source도 허용해야 한다. 둘 중 하나만 바꾸면 채점이 속도를 못 찾아 KeyError로
# 멈춘다 — 조용히 도보 속도로 재는 것보다 이 편이 낫다.
#
# 자동차 가정(20km/h)과 실제의 차이(네이버 Directions 실 API, 경복궁 기준 종로
# 6개 지점, 2026-08-20 14시대 평일):
#
# - 우회 계수 평균 **1.31배**(직선 대비 실제 도로 경로), 범위 1.07~1.71
# - 도로 기준 실주행 속도 평균 **6.98km/h**, 범위 4.64~8.79
# - 직선거리 기준 실효 속도는 평균 **5.38km/h**(범위 3.47~6.68)로, 이 가정
#   20km/h의 **약 1/3.7**이다. 도심 정체 때문이며 도보 실효 2.31km/h와 비교하면
#   자동차가 도보보다 2.3배 빠른 정도에 그친다.
# - 그래서 거리 점수는 도보보다 더 쉽게 0이 된다. 도보와 같은 이유로 보정하지
#   않는다 — 사용자가 "30분"이라고 했으면 예산도 30분이어야 한다.
# - 표본이 종로 6개 지점·특정 시간대뿐이다. 시간대에 따라 크게 달라지므로 이
#   숫자로 반경 가정을 바꾸기 전에 표본을 넓혀야 한다.
TRAVEL_SPEED_KM_PER_MINUTE: dict[TravelMode, float] = {
    TravelMode.WALKING: WALKING_SPEED_KM_PER_MINUTE,
    TravelMode.DRIVING: NON_WALKING_SPEED_KM_PER_MINUTE,
    TravelMode.TRANSIT: NON_WALKING_SPEED_KM_PER_MINUTE,
}

# 직선거리로 잰 도보 예상시간을 실제 보행 경로 길이에 맞추는 우회 계수.
#
# 카카오 도보 실 API 실측값이다(2026-08-19, 종로 5개 지점): 직선 대비 실제 보행
# 경로가 평균 **1.65배**, 범위 1.37~2.13.
#
# **채점의 시간 예산(domain/scoring.py::_travel_minutes_budget)은 이 계수를 쓰지
# 않는다.** 거기서 보정하면 사용자가 말한 "30분"이 사실상 50분이 된다. 여기서만
# 쓰는 이유는 이동수단을 고르는 시점에 실거리를 아직 모르기 때문이다 — 실측을
# 부르기 전이라 가진 것이 직선거리뿐이다.
WALKING_DETOUR_FACTOR = 1.65


def transit_switch_straight_line_km(walk_transfer_threshold_min: int) -> float:
    """도보 전환 임계(분)를 이동수단 판정에 쓸 직선거리(km)로 바꾼다 (D-118).

    임계값 자체는 `Settings.schedule_walk_transfer_threshold_min`(기본 20분)이다.
    SCHEDULE의 구간 이동수단 판정(`tools/schedule_travel.py::_select_mode`)과 같은
    값을 쓴다 — 같은 사용자에게 "이 정도 거리는 걸어요"를 두 기능이 다르게 말하지
    않게 하려는 것이다.

    **직선거리로 환산할 때 우회 계수를 나눈다.** 20분 × 0.07km/분 = 1.4km인데 그건
    실제 보행 경로 길이고, 직선거리로는 1.4 ÷ 1.65 ≈ **0.85km**다. 나누지 않으면
    규칙에는 "도보 20분 초과"라고 적어놓고 실제로는 33분짜리부터 전환하게 된다.

    낮은 쪽을 택한 또 하나의 이유는 전환된 후보를 도보·대중교통 양쪽으로 조회해
    빠른 쪽을 쓰기 때문이다(D-118 결정 3). 임계를 낮게 잡아 걷는 게 빠른 구간까지
    대중교통에 물어봐도 틀린 답이 나오지 않는다 — 늘어나는 것은 호출 수뿐이다.
    """
    if walk_transfer_threshold_min <= 0:
        raise ValueError("도보 전환 임계값은 0보다 커야 합니다.")
    return walk_transfer_threshold_min * WALKING_SPEED_KM_PER_MINUTE / WALKING_DETOUR_FACTOR

# 이동시간 조건이 없을 때 A와 C가 공통으로 사용하는 기본 장소 검색 반경(km).
DEFAULT_PLACE_SEARCH_RADIUS_KM = 2.0

# 짧은 이동시간도 후보 수집이 가능하도록 보장하는 최소 장소 검색 반경(km).
MIN_PLACE_SEARCH_RADIUS_KM = 0.3

# 장소 검색 Tool과 Provider가 허용하는 최대 검색 반경(km).
MAX_PLACE_SEARCH_RADIUS_KM = 20.0

# TourAPI 법정동 코드 기준 장소 검색 지원 시도: 서울특별시.
# 집중률 API의 signguCd(종로구 11110)와 다른 코드 체계이므로 혼용하지 않는다.
#
# 구는 여기서 고정하지 않는다. 지원 구는 `app.service_area.SUPPORTED_DISTRICTS`
# 하나가 정하고, 검색은 요청을 시도까지만 좁힌 뒤 응답의 lDongSignguCd로 거른다
# (D-025). 구를 요청에 실으면 지원 구가 여럿일 때 구마다 호출해야 하고, 반경
# 안에 있는 옆 지원 구 후보가 잘린다.
PLACE_SEARCH_LDONG_REGION_CODE = "11"

# 장소 검색 한 번에 요청할 최대 행 수.
#
# **이 값은 TourAPI의 제약이 아니라 우리가 고른 값이다.** 이전 주석은 100을 두고
# "locationBasedList2가 한 페이지에 허용하는 최대"라고 적었는데 사실이 아니었다 —
# 실측하면 요청한 만큼 그대로 주고 totalCount에서 멈춘다(2026-08-31, 안국역
# 반경 10km, 전량 1,598곳):
#
#   요청  100행 -> 100건,   69KB, 321ms
#   요청  500행 -> 500건,  349KB, 379ms
#   요청 1000행 -> 1000건, 704KB, 424ms
#   요청 2000행 -> 1598건(전량), 1133KB, 464ms
#
# 행 수를 16배로 늘려도 지연은 44%만 는다. 고정 비용이 대부분이고 커지는 것은
# 응답 크기뿐이다.
#
# 300으로 잡은 근거는 "더 보기"가 몇 턴까지 요청한 개수를 채우는가다. 요청 행 수는
# (limit + 이미 본 곳) x CANDIDATE_OVERFETCH_FACTOR이므로 턴이 갈수록 커진다.
# 후보 한도 30 기준으로 1턴 90행, 2턴 180행, 3턴 270행이라 300이면 3턴까지 꽉
# 채운다(100이면 2턴부터 모자라 3턴에 15곳으로 떨어졌다).
#
# **Provider도 이 값을 참조한다**(real_place.py의 numOfRows). 예전에는 Provider가
# min(limit, 100)으로 따로 잘라, Tool 상한만 올리면 요청이 조용히 100행에서
# 멈췄다 — 요청은 270행인데 실제로는 100행이 나가고 잘렸다는 신호도 안 섰다.
# 상한은 한 곳에만 둔다.
#
# 더 올리면 더 깊이 갈 수 있지만 응답이 그만큼 커진다. 그 깊이를 실제로 쓰는
# 흐름이 관측되면 그때 근거를 갖고 올린다. **다만 이 방식은 앞에서부터 받아
# 건너뛰는 구조라 어떤 값을 넣어도 언젠가 벽에 닿는다** — 천장을 없애려면
# pageNo를 써야 하고, 그건 별도 설계다.
MAX_PLACE_PROVIDER_ROWS = 300

# 별도 조회 개수가 없을 때 Place Provider가 반환할 기본 최대 건수.
DEFAULT_PLACE_PROVIDER_RESULT_LIMIT = 20

# 두 위·경도 사이의 직선거리를 Haversine 공식으로 계산할 때 사용하는 지구 반지름(km).
EARTH_RADIUS_KM = 6371.0
