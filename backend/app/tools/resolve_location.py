"""사용자 위치 표현을 지원 지역 안의 좌표로 해석하는 내부 Tool."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from itertools import combinations

from app.domain.models import GeocodeResult, LocalSearchPlace, StoredPlaceLocation
from app.errors import AppError
from app.geo import haversine_km
from app.providers.contracts import (
    ProviderMetadata,
    ProviderSource,
    ProviderStatus,
)
from app.providers.geocoding import get_landmark_alias
from app.providers.protocols import GeocodingProvider, LocalSearchProvider
from app.repositories.protocols import PlaceLocationRepository
from app.service_area import is_within_service_area, supported_district_label
from app.tools.contracts import ToolError, ToolStatus

ResolveLocationStatus = ToolStatus

# 도로명 주소와 지번 주소를 보수적으로 감지한다. 장소명에 "길"이 포함돼도
# 번지수가 없으면 장소명 검색 흐름을 유지한다.
_ROAD_ADDRESS_PATTERN = re.compile(r"(?:로|길)\s*\d+(?:-\d+)?(?:\s|$)")
_LOT_ADDRESS_PATTERN = re.compile(r"(?:동|읍|면|리)\s*\d+(?:-\d+)?(?:\s|$)")
_ADMIN_ADDRESS_PATTERN = re.compile(
    r"(?:서울(?:특별시)?|부산(?:광역시)?|대구(?:광역시)?|인천(?:광역시)?|"
    r"광주(?:광역시)?|대전(?:광역시)?|울산(?:광역시)?|세종(?:특별자치시)?|"
    r"경기(?:도)?|강원(?:특별자치도|도)?|충북|충청북도|충남|충청남도|전북|"
    r"전라북도|전남|전라남도|경북|경상북도|경남|경상남도|제주(?:특별자치도)?)"
    r"\s+\S+(?:시|군|구)"
)


# 위치를 가리키는 수식어. 장소명 뒤에 붙어도 검색 대상은 앞의 장소다
# ("안국역 근처" → "안국역"). 실측(2026-08-03): "안국역 근처"로 지역 검색하면
# 엘리베이터·모텔·돈까스집이 나와 정답인 "안국역 3호선"이 후보에 없었다.
# 이 목록은 보수적으로 유지한다 — 단어를 늘릴수록 실제 장소명을 잘라낼 위험이 커진다.
_LOCATION_MODIFIER_TOKENS = frozenset({"근처", "주변", "인근", "부근"})


# 같은 역을 노선별로 나눠 반환하는 경우를 한 장소로 묶기 위한 기준.
#
# 지역 검색은 "종로3가역"에 1·3·5호선을 각각 돌려준다. 이름이 달라 정확 일치가 안 되고
# 첫 토큰은 셋 다 같아 못 좁히므로 되묻기로 빠지는데, 사용자에게 몇 호선인지 물어도
# 답이 될 수 없다 — 카페를 찾는 사람에게 무의미하고, 검색 반경이 2km라 어느 출입구를
# 골라도 결과가 같다.
#
# 실측(2026-08-04) 역별 후보 간 최대 거리: 청량리역 381m, 서울역 341m, 종로3가역 291m,
# 시청역 267m, 김포공항역 248m, 공덕역 187m, 왕십리역 137m, 충무로역 47m. 노선 수가
# 아니라 역사 구조가 거리를 결정한다(5개 노선인 왕십리역이 가장 좁다).
_SAME_PLACE_RADIUS_KM = 0.5

# 카테고리도 함께 본다. 거리만 보면 "종각역 김밥천국"처럼 역명을 그대로 앞에 붙인 상호가
# 섞여도 묶이고, 그러면 "첫 후보를 임의로 고르지 않는다"는 원칙이 깨진다(쌈지길 검색에서
# 정답이 3번째였다). 실측한 표기는 4종이었다 — 지하철·전철·기차역·정차역.
_TRANSIT_CATEGORY_MARKERS = ("지하철", "전철", "기차", "철도", "정차역")

# 정확/첫토큰 일치가 다 실패했을 때만 쓰는 마지막 보정 단계. 오탈자·조사 한 글자
# 차이만 흡수한다("성수 카페거리" vs 실제 "성수동카페거리", 동 삽입, 실측
# 2026-08-26). 2는 안 된다 — "경복궁"↔"덕수궁"처럼 둘 다 3글자에 편집거리 2인
# 서로 다른 실존 랜드마크가 있다.
_EDIT_DISTANCE_LIMIT = 1
# 2글자 질의는 이 단계를 아예 안 탄다 — "신촌"↔"신천"처럼 편집거리 1이 완전히
# 다른 동네를 가리킬 수 있다.
_MIN_QUERY_LEN_FOR_FUZZY_MATCH = 3

# 역 이름 줄임말("교대", "홍대") 재검색용 접미사. "교대"를 그대로 지역 검색하면
# 동명 대학·상호("서울교육대학교", "교대갈비집")에 밀려 역이 상위 5건에 아예
# 안 잡힌다(실측, 2026-09-09) — "교대역"으로 검색해야 잡힌다. 이미 "역"으로
# 끝나는 질의("안국역")에는 다시 붙이지 않는다("안국역역" 방지).
_STATION_SUFFIX = "역"


def _bounded_edit_distance(a: str, b: str, *, limit: int) -> int:
    """길이 차가 limit을 넘으면 즉시 limit+1(무조건 탈락)을 반환해 DP를 아낀다.

    이름이 최대 20자 안팎이라 전체 Levenshtein DP 자체도 비용이 미미하다 — 밴드
    매트릭스 같은 정교화는 이 규모에서 실익이 없다.
    """
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous_row = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current_row = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            current_row[j] = min(
                previous_row[j] + 1, current_row[j - 1] + 1, previous_row[j - 1] + cost
            )
        previous_row = current_row
    return previous_row[-1]


def strip_location_modifiers(value: str) -> str:
    """장소명 뒤의 위치 수식어를 떼어낸다. 남는 게 없으면 원문을 유지한다.

    공백으로 구분된 토큰 단위로만 지운다 — "역근처식당"처럼 이름에 붙어 있는
    경우는 건드리지 않는다. "근처 추천해줘"처럼 수식어만 있는 입력은 애초에
    장소 조건이 비어 A가 이 Tool을 호출하지 않지만, 방어적으로 원문을 돌려준다.
    """
    tokens = value.split()
    kept = [token for token in tokens if token not in _LOCATION_MODIFIER_TOKENS]
    if not kept:
        return value
    return " ".join(kept)


def is_address_query(value: str) -> bool:
    """주소 형태의 입력이면 장소명 검색보다 Geocoding을 먼저 사용한다."""
    normalized = " ".join(value.split())
    return bool(
        _ROAD_ADDRESS_PATTERN.search(normalized)
        or _LOT_ADDRESS_PATTERN.search(normalized)
        or _ADMIN_ADDRESS_PATTERN.search(normalized)
    )


def _normalize_name(value: str) -> str:
    return value.casefold().replace(" ", "")


def _head_token(name: str) -> str:
    """공백으로 구분된 첫 토큰. 정규화 전에 잘라야 토큰 경계가 남는다."""
    tokens = name.split()
    return _normalize_name(tokens[0] if tokens else name)


def _select_local_search_candidate(
    candidates: tuple[LocalSearchPlace, ...], requested_query: str
) -> LocalSearchPlace | None:
    """이름으로 유일하게 특정되는 후보만 고른다. 못 좁히면 None(재질문).

    Local Search는 연관도 순으로 주변 상호까지 함께 반환하므로 순위를 판정에 쓰지
    않는다 — 실제로 "쌈지길" 검색에서 정답이 3번째였다. 임의로 첫 후보를 고르면
    엉뚱한 음식점이 검색 중심이 된다.
    """
    normalized_query = _normalize_name(requested_query)

    # 1) 정확 일치. 동명 후보가 여러 건이면 그중 위치 후보로 쓸 수 있는 것만 남겨 본다.
    #
    # **이름이 같다고 다 같은 성격의 후보는 아니다.** "운현궁"을 지역 검색에 넣으면 이름이
    # 완전히 같은 후보가 3건 나오는데(2026-08-27 실측) 중식당·한식당·궁궐이고, 그중 위치
    # 후보로 쓸 수 있는 것은 궁궐 하나뿐이다. 식당·상점은 위치 후보로 부적절하다는 규칙은
    # 이미 있었지만(`_is_location_pickable`) 되묻기 목록을 만들 때만 쓰였고, 여기 고르는
    # 단계에서는 안 쓰여서 "못 좁혔다"로 끝났다.
    #
    # 그 결과가 **끝나지 않는 되묻기**였다. 되묻기 버튼에는 걸러낸 "운현궁" 하나만 실리고,
    # 사용자가 그걸 고르면 같은 이름으로 같은 조회를 다시 돌아 같은 3건이 나온다 — 답이
    # 질문과 같은 문자열이라 입력이 하나도 바뀌지 않는다. 이름이 같은 것들끼리의 모호함은
    # 이름을 되물어 풀 수 없다.
    #
    # 걸러낸 뒤에도 2건 이상이면 그때는 진짜로 못 고르는 것이라 그대로 재질문한다.
    exact = tuple(item for item in candidates if _normalize_name(item.name) == normalized_query)
    if exact:
        if len(exact) == 1:
            return exact[0]
        pickable_exact = tuple(item for item in exact if _is_location_pickable(item))
        return pickable_exact[0] if len(pickable_exact) == 1 else None

    # 2) 첫 토큰 일치. "안국역 3호선"은 잡고 "안국역사거리"는 배제하기 위해
    #    startswith가 아니라 토큰 단위로 비교한다.
    head_matched = tuple(item for item in candidates if _head_token(item.name) == normalized_query)
    if len(head_matched) == 1:
        return head_matched[0]

    # 3) 같은 역의 노선별 후보라면 하나로 본다. 전부 교통 시설이고 서로 붙어 있을 때만
    #    묶으므로, 상호가 하나라도 섞이면 여기서 걸러져 재질문으로 남는다.
    if len(head_matched) > 1 and _is_same_transit_place(head_matched):
        return head_matched[0]

    # 4) 편집 거리 근사 일치. 역/명소류로만 적용해 상호를 엉뚱하게 정답으로
    #    추정하지 않는다 — 식당·상점까지 넓히면 이 파일의 "부분 일치로 넓히지
    #    않는다" 원칙과 충돌한다("안국역"≠"안국역사거리"는 그 후보가 기본
    #    카테고리(음식점)라 여기서도 그대로 안전하다).
    if len(normalized_query) >= _MIN_QUERY_LEN_FOR_FUZZY_MATCH:
        near = tuple(
            item
            for item in candidates
            if _is_location_pickable(item)
            and _bounded_edit_distance(
                _normalize_name(item.name), normalized_query, limit=_EDIT_DISTANCE_LIMIT
            )
            <= _EDIT_DISTANCE_LIMIT
        )
        if len(near) == 1:
            return near[0]

    return None


# 되묻기 버튼 개수를 UI에서 감당할 만큼으로 제한한다(A2 종로구 대표 스팟 버튼도
# 4개 — docs/design/clarification-options.md 7절과 결을 맞춘다).
_MAX_LOCATION_CANDIDATES = 4


def _join_candidate_names(names: object) -> str:
    """되묻기 버튼용 후보 이름을 "|" 구분 문자열로 합친다.

    ToolError.details가 dict[str, str]라 리스트를 그대로 못 담아, agent_context/
    assembler.py가 다시 split("|")로 푼다. 중복은 순서를 지키며 제거하고 최대
    _MAX_LOCATION_CANDIDATES개까지만 남긴다.
    """
    return "|".join(list(dict.fromkeys(names))[:_MAX_LOCATION_CANDIDATES])


def _is_transit_place(place: LocalSearchPlace) -> bool:
    category = place.category or ""
    return any(marker in category for marker in _TRANSIT_CATEGORY_MARKERS)


# 되묻기 후보로는 지하철역/명소류만 적절하다 — 지역 검색은 주변 상호(식당·카페 등)도
# 같이 돌려주는데, "종각"에 "숙썽수산 종로본점"/"어망집" 같은 식당이 위치 후보로
# 뜨면 사용자가 혼란스럽다(실사용 피드백, 2026-08-13). 사용자가 실제로 궁금한 건
# 지역/랜드마크 이름이지 특정 가게가 아니다.
_LANDMARK_CATEGORY_MARKERS = ("관광", "명소")


def _is_location_pickable(place: LocalSearchPlace) -> bool:
    category = place.category or ""
    return _is_transit_place(place) or any(
        marker in category for marker in _LANDMARK_CATEGORY_MARKERS
    )


def _is_same_transit_place(places: tuple[LocalSearchPlace, ...]) -> bool:
    """모든 후보가 교통 시설이고 서로 _SAME_PLACE_RADIUS_KM 안이면 True."""
    if not all(_is_transit_place(place) for place in places):
        return False
    located = [
        place for place in places if place.latitude is not None and place.longitude is not None
    ]
    if len(located) != len(places):
        # 좌표가 없으면 거리를 확인할 수 없다. 묶지 않고 재질문한다.
        return False
    return all(
        haversine_km(first.latitude, first.longitude, second.latitude, second.longitude)
        <= _SAME_PLACE_RADIUS_KM
        for first, second in combinations(located, 2)
    )


def _split_district_hint(name: str) -> tuple[str, str | None]:
    """`"전주식당 (종로구)"`을 `("전주식당", "종로구")`로 가른다. 없으면 구는 None.

    되묻기 버튼 라벨이 그대로 다음 턴의 검색어가 되기 때문에 필요하다. 이름이 같은 다른
    가게를 가리려고 라벨에 구를 붙였는데(_stored_place_label), 그 문자열로 저장소를 찾으면
    그런 이름이 없어 해소가 실패한다. 여기서 다시 갈라 이름으로 찾고 구로 고른다.

    괄호 안이 자치구가 아니면 건드리지 않는다 — 상호 자체에 괄호가 들어간 이름
    ("광장(전통)시장")을 잘라내면 안 된다.
    """

    if not name.endswith(")") or "(" not in name:
        return name, None
    head, _, tail = name.rpartition("(")
    district = tail[:-1].strip()
    if not district.endswith(("구", "군")) or len(district) < 2:
        return name, None
    return head.strip(), district


def _stored_place_label(place: StoredPlaceLocation, *, add_address: bool) -> str:
    """되묻기 버튼에 실을 이름. 필요하면 주소를 덧붙여 서로 구분되게 한다.

    이름이 같은 서로 다른 가게가 실제로 있다 — "광양불고기"가 양천구와 송파구에, "전주식당"이
    종로구와 중구에 있다(2026-09-11 저장소 전량 확인). 이름만 실으면 두 버튼이 같은 글자라
    사용자가 고를 수가 없고, 어느 쪽을 눌러도 같은 이름으로 다시 조회돼 제자리로 돌아온다.

    **자치구까지만 붙인다.** 전체 주소를 실으면 버튼이 길어져 읽기 나쁘고, 같은 이름이 한 구
    안에 둘 있는 경우는 관측되지 않았다. 주소가 없거나 구를 못 찾으면 이름만 쓴다.

    시·도 토큰은 건너뛴다. "서울특별시 양천구 ..."에서 앞부터 훑어 "시"로 끝나는 것을 집으면
    둘 다 "(서울특별시)"가 되어 라벨이 여전히 같아진다 — 붙이는 뜻이 사라진다.
    """

    if not add_address or not place.address:
        return place.title
    district = next(
        (
            token
            for token in place.address.split()
            if token.endswith(("구", "군")) and len(token) > 1
        ),
        None,
    )
    return f"{place.title} ({district})" if district else place.title


def _is_same_stored_place(places: tuple[StoredPlaceLocation, ...]) -> bool:
    """이름이 같은 저장소 행들이 사실은 한 장소인가.

    TourAPI가 같은 가게를 분류만 달리해 두 번 주는 일이 있다. "오설록 티하우스 북촌점"은
    음식점(39)과 쇼핑(38)으로 각각 적재돼 있고 좌표는 8m 떨어져 있다. 그런 행이 둘이라고
    "여러 장소 중 어느 곳"이라 되물으면, 선택지가 같은 이름 둘이라 화면에는 하나로 보이고
    눌러도 같은 조회가 다시 돌아 영영 끝나지 않는다(2026-09-11 실측).

    **이름이 같다고 다 한 장소는 아니다.** 저장소 전량을 재보니 같은 이름 29건 중 22건은
    343m 안의 같은 곳이고, 7건은 진짜 다른 가게였다 — "광양불고기"가 양천구와 송파구에
    23.7km 떨어져 있고, "전주식당"은 종로구와 중구에 1.7km 떨어져 있다. 그래서 이름이
    아니라 거리로 가른다. 두 무리 사이가 343m와 1.7km로 넉넉히 벌어져 있다.

    기준 거리는 역 후보를 묶을 때 쓰는 값과 같다(_SAME_PLACE_RADIUS_KM). 좌표가 없으면
    거리를 확인할 수 없으므로 묶지 않는다 — 되묻는 편이 임의로 고르는 것보다 낫다.
    """

    if len(places) < 2:
        return True
    return all(
        haversine_km(first.latitude, first.longitude, second.latitude, second.longitude)
        <= _SAME_PLACE_RADIUS_KM
        for first, second in combinations(places, 2)
    )


class ResolutionMethod(StrEnum):
    DIRECT = "direct"
    ALIAS = "alias"
    FALLBACK = "fallback"
    DATABASE = "database"
    LOCAL_SEARCH = "local_search"


class ResolutionConfidence(StrEnum):
    EXACT = "exact"
    APPROXIMATE = "approximate"
    UNKNOWN = "unknown"


class LocationSource(StrEnum):
    """이 좌표가 어디서 왔는지. 판정이 아니라 사실이다(D-051).

    기준점을 사용자에게 뭐라고 부를지는 D가 이 값으로 정한다 — QUERY면 사용자가
    말한 이름을 쓰고, DEVICE_GPS면 "현재 위치"라고 한다. C는 출처만 싣고 문구를
    고르지 않는다.
    """

    QUERY = "query"
    DEVICE_GPS = "device_gps"


class LocationPurpose(StrEnum):
    """이 해석이 무엇에 쓰이는지. 사다리 순서를 가른다.

    두 목적이 필요로 하는 것이 다르다.

    - SEARCH_CENTER: 반경 검색의 기준 좌표만 있으면 된다. 사용자가 말한 곳이
      우리 코퍼스의 어느 장소인지는 알 필요가 없다.
    - PLACE_IDENTITY: "여기 혼잡해?"에 답하려면 좌표가 아니라 **집중률 매핑이
      걸린 그 장소**를 확정해야 한다. 좌표 최근접으로 고르면 틀린다 — 북촌
      한옥마을 좌표의 최근접은 가회민화박물관(27m)이고 정작 북촌한옥마을은
      293m로 밀린다(D-043 실측).

    세 목적 모두 저장소를 먼저 본다. 한때 SEARCH_CENTER만 건너뛰었는데(cc3da0ed),
    코퍼스에 없는 이름은 조회가 반드시 실패하는 데다 필터 사다리를 한 칸씩 던지느라
    `places`를 4회 버렸기 때문이다. 필터를 or= 하나로 합쳐 그 비용이 제목 1회 +
    별칭 1회로 줄면서 전제가 사라졌다. REALTIME_CITYDATA만 예외다(아래).

    저장소 우선순위와 "지원 25개 구(D-107, 서울 전역) 밖이면 막는다"는 지역 제한은
    원래 서로 다른 이유로 묶인 게 아니다 — REALTIME_CITYDATA가 둘 다 건너뛴 건
    "권역명은 코퍼스 밖이라 저장소 조회가 헛돈다"는 비용 논리 하나였다. 그런데
    명동성당처럼 **코퍼스 안에 있는 장소**가 이 purpose로 잘못 보내지면(TP-171,
    오늘 날짜 혼잡 질문) 저장소를 못 봐 위치 해석이 통째로 실패한다. 그래서
    `ResolveLocationQuery`에 `enforce_service_area` 명시 오버라이드를 따로 뒀다 —
    저장소는 보되(PLACE_IDENTITY) 지역 제한만 끄고 싶은 경우(서울대공원처럼 지원
    구 밖의 실시간 인구 허브)를 표현하기 위해서다. 아래 REALTIME_CITYDATA는 여전히
    "저장소도 건너뛴다"는 기존 동작 그대로다 — 이번 변경은 그 경로를 손대지 않는다.
    """

    SEARCH_CENTER = "search_center"
    PLACE_IDENTITY = "place_identity"
    # 서울시 실시간 상권은 TourAPI 코퍼스 밖의 서울 주요 지역도 제공한다. 이 목적은
    # 상호 좌표만 찾고, 이후 C가 서울시 제공 지역 반경을 따로 판정한다. 추천의 지원
    # 지역 제한을 여기서 재사용하면 용리단길 같은 정상 상권 질문이 위치 단계에서
    # 막힌다. 저장소 조회도 건너뛴다 — 권역명("광화문·덕수궁")이나 코퍼스 밖 지명이
    # 대상이라 실패가 정상이다.
    REALTIME_CITYDATA = "realtime_citydata"


@dataclass(frozen=True)
class ResolveLocationQuery:
    location_query: str
    # 기존 호출부와 CLI가 그대로 동작하도록 정체성 확정을 기본으로 둔다.
    purpose: LocationPurpose = LocationPurpose.PLACE_IDENTITY
    # None이면 purpose가 정하는 기본값을 그대로 쓴다(REALTIME_CITYDATA만 지역 제한을
    # 건너뜀). 명시하면 그 값이 purpose와 무관하게 우선한다 — TP-171: 오늘 날짜
    # 혼잡 질문은 저장소는 보되(PLACE_IDENTITY) 지역 제한만 끄고 싶어서 추가했다.
    enforce_service_area: bool | None = None
    # "서울특별시 종로구"처럼 행정구역 자체를 좌표로 풀 때는 지역 검색이 주변
    # 명소·역 후보를 여럿 돌려 불필요한 되묻기를 만들 수 있다. 이 호출만
    # Geocoding으로 바로 보내며, 기본값은 기존 위치 해석 사다리를 유지한다.
    skip_local_search: bool = False

    def __post_init__(self) -> None:
        normalized = self.location_query.strip()
        if not normalized:
            raise ValueError("location_query는 비어 있을 수 없습니다.")
        if len(normalized) > 200:
            raise ValueError("location_query는 200자 이하여야 합니다.")


@dataclass(frozen=True)
class ResolvedLocation:
    requested_query: str
    provider_query: str
    resolved_name: str
    latitude: float
    longitude: float
    resolution_method: ResolutionMethod
    confidence: ResolutionConfidence
    # 이 Tool은 언제나 사용자가 말한 문자열을 푼다. 기기 GPS로 만든 결과는 Tool을
    # 거치지 않고 service.py::_gps_location_result()가 직접 만들며 거기서만 뒤집는다.
    source: LocationSource = LocationSource.QUERY
    place_id: str | None = None
    address: str | None = None
    # Naver Local Search의 업종. INFO 현재 혼잡 질문에서 관광지 예측과 상권 활동을
    # 구분할 때만 사용하며, 없으면 기존 위치 해석 동작을 유지한다.
    place_category: str | None = None
    # 저장소에서 푼 장소의 구(lDongSignguCd, 종로구 "110"). 집중률 조회가 구를
    # 지정해야 해서 함께 나른다. 저장소를 거치지 않은 해석(지오코딩·GPS)에는
    # 값이 없는데, 그 경로는 concentration_name도 없어 집중률을 묻지 않는다.
    district_code: str | None = None
    # 집중률 응답에서 장소를 골라낼 때 대조할 정식 명칭.
    concentration_name: str | None = None
    # tAtsNm에 넣을 검색어 목록. 앞에서부터 시도한다. 비어 있으면
    # concentration_name을 그대로 쓴다(D-057).
    concentration_search_keys: tuple[str, ...] = ()


ResolveLocationError = ToolError


@dataclass(frozen=True)
class ResolveLocationResult:
    status: ResolveLocationStatus
    location: ResolvedLocation | None
    error: ResolveLocationError | None
    warnings: tuple[str, ...] = ()
    provider_metadata: tuple[ProviderMetadata, ...] = ()


class ResolveLocationTool:
    def __init__(
        self,
        provider: GeocodingProvider,
        place_repository: PlaceLocationRepository | None = None,
        local_search_provider: LocalSearchProvider | None = None,
    ) -> None:
        self._provider = provider
        self._place_repository = place_repository
        self._local_search_provider = local_search_provider

    async def execute(self, query: ResolveLocationQuery) -> ResolveLocationResult:
        # 수식어를 먼저 떼고 조회한다. 주소 판별도 정리된 값으로 해야 "인사동길 44
        # 근처"가 주소로 잡힌다.
        requested_query = strip_location_modifiers(query.location_query.strip())
        enforce_service_area = (
            query.enforce_service_area
            if query.enforce_service_area is not None
            else query.purpose is not LocationPurpose.REALTIME_CITYDATA
        )
        if is_address_query(requested_query):
            return await self._resolve_address(
                requested_query, enforce_service_area=enforce_service_area
            )

        # 검색 중심점도 저장소를 먼저 본다. 예전에는 "코퍼스에 없는 이름은
        # 그 조회가 반드시 실패하므로 왕복만 버린다"는 이유로 건너뛰었는데, 지원
        # 지역이 네 구로 늘면서 그 전제가 깨졌다 — "명동성당 근처"처럼 저장소에
        # 있는 장소를 검색 중심으로 쓰는 요청이 실제로 들어온다. 지역 검색은 그런
        # 이름을 못 좁혀 되묻기로 끝난다("르빵 명동성당점" 같은 주변 상호가 섞인다).
        #
        # 실시간 도시데이터는 그대로 건너뛴다. 그쪽은 코퍼스 밖의 서울 주요
        # 지역을 대상으로 해서 저장소 조회가 실패하는 게 정상이다.
        if query.purpose is not LocationPurpose.REALTIME_CITYDATA:
            stored_result = await self._lookup_stored_place(requested_query)
            if stored_result is not None:
                return stored_result

        if not query.skip_local_search:
            local_search_result = await self._lookup_local_search(
                requested_query,
                purpose=query.purpose,
                enforce_service_area=enforce_service_area,
            )
            if local_search_result is not None:
                return local_search_result

        alias = get_landmark_alias(requested_query)

        if alias:
            first = await self._lookup(alias)
            if isinstance(first, ResolveLocationResult):
                if first.status is not ResolveLocationStatus.NO_DATA:
                    return first
                fallback = await self._lookup(requested_query, use_alias=False)
                if isinstance(fallback, ResolveLocationResult):
                    return fallback
                fallback_data, fallback_metadata = fallback
                return self._success_or_policy_result(
                    result=fallback_data,
                    requested_query=requested_query,
                    provider_query=requested_query,
                    method=ResolutionMethod.FALLBACK,
                    warnings=("fallback_used",),
                    provider_metadata=(fallback_metadata,),
                    enforce_service_area=enforce_service_area,
                )
            first_data, first_metadata = first
            return self._success_or_policy_result(
                result=first_data,
                requested_query=requested_query,
                provider_query=alias,
                method=ResolutionMethod.ALIAS,
                provider_metadata=(first_metadata,),
                enforce_service_area=enforce_service_area,
            )

        direct = await self._lookup(requested_query, use_alias=False)
        if isinstance(direct, ResolveLocationResult):
            return direct
        direct_data, direct_metadata = direct
        return self._success_or_policy_result(
            result=direct_data,
            requested_query=requested_query,
            provider_query=requested_query,
            method=ResolutionMethod.DIRECT,
            provider_metadata=(direct_metadata,),
            enforce_service_area=enforce_service_area,
        )

    async def _resolve_address(
        self, requested_query: str, *, enforce_service_area: bool = True
    ) -> ResolveLocationResult:
        """주소는 DB·지역 검색을 건너뛰고 Geocoding으로 바로 해석한다."""
        direct = await self._lookup(requested_query, use_alias=False)
        if isinstance(direct, ResolveLocationResult):
            return direct
        direct_data, direct_metadata = direct
        return self._success_or_policy_result(
            result=direct_data,
            requested_query=requested_query,
            provider_query=requested_query,
            method=ResolutionMethod.DIRECT,
            provider_metadata=(direct_metadata,),
            enforce_service_area=enforce_service_area,
        )

    async def _lookup_local_search(
        self,
        requested_query: str,
        *,
        purpose: LocationPurpose = LocationPurpose.PLACE_IDENTITY,
        enforce_service_area: bool = True,
    ) -> ResolveLocationResult | None:
        """DB에 없는 상호명은 지역 검색으로 좌표를 보완한다."""
        if self._local_search_provider is None:
            return None
        try:
            result = await self._local_search_provider.search_places_by_name(requested_query)
        except AppError:
            # Local Search 장애가 주소 Geocoding fallback을 막지 않게 한다.
            return None
        candidates = tuple(
            item for item in result.data if item.latitude is not None and item.longitude is not None
        )
        if not candidates:
            return await self._lookup_local_search_as_station(
                requested_query, purpose=purpose, enforce_service_area=enforce_service_area
            )
        selected = _select_local_search_candidate(candidates, requested_query)
        if selected is None:
            # 정확히 같은 이름의 후보가 있으면(동명이인, 예: "쌈지길" 2건) 이건
            # 실제로 그 이름의 장소를 찾은 것이다 — Geocoding은 상호명을 인식하지
            # 못하므로(docs/api-samples.md) 폴백해봐야 소용없고, 어느 쪽인지
            # 되묻는 게 맞다. 아래 "역/명소 후보 없음→ Geocoding 폴백"은 이런
            # 정확 일치가 전혀 없을 때만 적용한다.
            normalized = _normalize_name(requested_query)
            has_exact_match = any(_normalize_name(item.name) == normalized for item in candidates)
            # 후보를 못 좁혔는데 찾은 것이 전부 지역 밖이면 되묻기가 아니라 지역 문제다.
            # "부산 해운대"에 "지원 구 안에서 어느 장소인지" 되묻는 일을 막는다.
            if enforce_service_area and not any(
                is_within_service_area(item.latitude, item.longitude)
                for item in candidates
                if item.latitude is not None and item.longitude is not None
            ):
                return self._error_result(
                    status=ResolveLocationStatus.UNSUPPORTED,
                    code="unsupported_region",
                    cause="outside_supported_region",
                    retryable=False,
                    details={"reason": "outside_supported_region"},
                    provider_metadata=(result.metadata,),
                )
            in_area = [
                item
                for item in candidates
                if item.latitude is not None
                and item.longitude is not None
                and is_within_service_area(item.latitude, item.longitude)
            ]
            # 지하철역/명소류로만 좁힌다. 식당·상점은 위치 후보로 부적절하니 안
            # 좁혀진 전체로 폴백하지 않는다(실사용 피드백, 2026-08-13: "그냥
            # 지하철역으로만 가자").
            names_source = [item for item in in_area if _is_location_pickable(item)]
            # 되묻기에 실을 후보를 여기서 먼저 정한다.
            #
            # names_source는 지하철역/명소로 좁힌 목록이라 비어 있을 수 있다 — 동명 후보가
            # 전부 식당·상점인 경우(예: "쌈지길" 2건, has_exact_match만 True)나, "강서구"처럼
            # 애초에 그 갈래가 아닌 지명이다. 그때는 전체 후보로 넓힌다.
            #
            # **아래 방어와 같은 목록을 본다.** 전에는 방어가 names_source만 보고 되묻기는
            # 넓힌 목록으로 만들어서, 좁은 쪽이 0개인데 넓은 쪽은 1개인 지명이 방어를 그냥
            # 지나쳤다 — "강서구"가 그랬다(2026-09-09). 같은 판단을 서로 다른 목록으로 하면
            # 언제든 다시 어긋난다.
            pickable_candidates = names_source or list(candidates)
            # **답이 질문과 같아지는 되묻기는 만들지 않는다.** 후보가 하나뿐이고 그 이름이
            # 방금 물어본 이름과 똑같다면, 사용자가 그 버튼을 눌러도 같은 문자열로 같은
            # 조회가 다시 돌아 같은 되묻기가 나온다 — 입력이 하나도 바뀌지 않으므로 영영
            # 끝나지 않는다(2026-08-27 "운현궁 → 공영주차장" 흐름에서 실제로 그랬다).
            #
            # **후보 이름이 질의와 다르면 그대로 되묻는다.** 예를 들어 "종각역"에 후보가
            # "종각역 1호선" 하나뿐이면, 그 버튼을 누른 답은 질의와 달라서 다음 턴에
            # 정확 일치로 풀린다. 그런 되묻기는 한 번 더 확인받는 값어치가 있고, 첫 후보를
            # 임의로 고르지 않는다는 이 파일의 원칙도 지켜진다.
            #
            # 넓힌 목록에서 고른 후보라도 지원 구 밖이면 아래 공통 성공 경로의
            # enforce_service_area 검사가 걸러낸다 — 여기서 다시 보지 않는 이유다.
            if (
                len(pickable_candidates) == 1
                and _normalize_name(pickable_candidates[0].name) == normalized
            ):
                # 아래 공통 성공 경로로 흘려보낸다 — 지원 구 검사와 저장소 재조회를
                # 여기서 다시 구현하지 않기 위해서다.
                selected = pickable_candidates[0]
            elif not names_source and not has_exact_match:
                # 역/명소가 하나도 없고 정확히 같은 이름의 후보도 없다 — 두 가지
                # 원인이 있다. (a) "성수동"처럼 동 이름이 지역 검색에서 카페·식당
                # 상호명으로만 잡힌 경우(실측, 2026-08-26), (b) "교대"처럼 역
                # 줄임말이 동명 대학·상호에 밀려 이 지역 검색 자체에 역이 후보로
                # 아예 안 잡힌 경우(실측, 2026-09-09). 먼저 (b)를 "역"을 붙인
                # 재검색으로 풀어보고, 그래도 안 풀리면 None을 돌려줘 execute()의
                # 별칭/Geocoding 사다리로 넘긴다 — Naver Geocoding은 행정동/법정동
                # 이름을 직접 인식하므로(docs/api-samples.md) "성수동"류는 거기서
                # 풀린다.
                station_result = await self._lookup_local_search_as_station(
                    requested_query, purpose=purpose, enforce_service_area=enforce_service_area
                )
                if station_result is not None:
                    return station_result
                return None
            else:
                # 후보를 하나로 못 좁혀도 대표 좌표(1순위 후보)는 실어 보낸다 —
                # 실시간 행사 등 좌표만으로 답할 수 있는 폴백이 이걸로 계속 조회할 수
                # 있게 한다(concentration의 이름 전용 폴백과 대칭).
                #
                fallback_candidates = pickable_candidates
                fallback = fallback_candidates[0]
                return self._error_result(
                    status=ResolveLocationStatus.NO_DATA,
                    code="no_data",
                    cause="ambiguous_location",
                    retryable=False,
                    details={
                        "reason": "ambiguous_location",
                        "candidate_names": _join_candidate_names(
                            item.name for item in fallback_candidates
                        ),
                        "fallback_latitude": str(fallback.latitude),
                        "fallback_longitude": str(fallback.longitude),
                    },
                    provider_metadata=(result.metadata,),
                )
        # 지역 검색이 알아낸 정식 상호명으로 저장소를 다시 찾는다. "북촌"은 저장소에
        # 없지만 지역 검색이 "북촌 한옥마을"을 주므로, 여기서 다시 찾으면 집중률
        # 매핑까지 이어진다. 재조회가 실패해도 지역 검색 결과는 그대로 쓴다.
        return await self._finalize_local_search_selection(
            requested_query,
            selected,
            result.metadata,
            purpose=purpose,
            enforce_service_area=enforce_service_area,
        )

    async def _lookup_local_search_as_station(
        self,
        requested_query: str,
        *,
        purpose: LocationPurpose,
        enforce_service_area: bool,
    ) -> ResolveLocationResult | None:
        """역/명소 후보를 하나도 못 찾았을 때 "역"을 붙여 한 번 더 지역 검색한다.

        "교대", "홍대"처럼 역 이름의 줄임말은 그 글자 그대로 지역 검색하면 동명
        대학·상호에 밀려 역이 상위 결과에 아예 안 잡힌다(실측, 2026-09-09:
        "교대" 검색 상위 5건은 서울교육대학교·식당뿐이고 "교대역"은 없다.
        "교대역"으로 검색하면 바로 잡힌다). 이미 "역"으로 끝나는 질의는 다시
        붙이지 않는다("안국역" → "안국역역" 방지).

        이름이 원 질의와 비슷한지는 보지 않는다 — "홍대"+"역"="홍대역"의 실제
        역명은 "홍대입구역"이라 기존 정확/첫토큰/편집거리 매칭이 전부 실패하지만
        (실측), "역"을 붙여 찾은 교통 카테고리 후보가 하나뿐이면(환승역이라
        노선별로 여럿이어도 전부 같은 자리면 `_is_same_transit_place`로 이미
        묶는다) 그게 정답이라고 본다. 서로 다른 역인데 못 좁히면(교통 후보가
        여럿인데 같은 자리가 아니면) 조용히 실패해 상위 호출부가 별칭/Geocoding
        사다리로 넘기게 둔다 — 엉뚱한 역을 임의로 고르지 않는다.
        """
        if self._local_search_provider is None or requested_query.endswith(_STATION_SUFFIX):
            return None
        try:
            result = await self._local_search_provider.search_places_by_name(
                f"{requested_query}{_STATION_SUFFIX}"
            )
        except AppError:
            return None
        transit_candidates = tuple(
            item
            for item in result.data
            if item.latitude is not None
            and item.longitude is not None
            and _is_transit_place(item)
        )
        if not transit_candidates:
            return None
        if len(transit_candidates) > 1 and not _is_same_transit_place(transit_candidates):
            return None
        selected = transit_candidates[0]
        return await self._finalize_local_search_selection(
            requested_query,
            selected,
            result.metadata,
            purpose=purpose,
            enforce_service_area=enforce_service_area,
        )

    async def _finalize_local_search_selection(
        self,
        requested_query: str,
        selected: LocalSearchPlace,
        metadata: ProviderMetadata,
        *,
        purpose: LocationPurpose,
        enforce_service_area: bool,
    ) -> ResolveLocationResult:
        """선택된 지역 검색 후보 하나를 공통 성공 경로로 마무리한다.

        원 후보 경로와 "역" 재검색 경로가 지원 구 검사·저장소 재조회·성공
        결과 조립을 여기 하나로 공유한다.
        """
        if selected.latitude is not None and selected.longitude is not None:
            if enforce_service_area:
                outside = self._outside_service_area_result(
                    selected.latitude, selected.longitude, (metadata,)
                )
                if outside is not None:
                    return outside
        # 재조회는 집중률 매핑을 붙이기 위한 것이다. 검색 중심점에는 그 필드가
        # 쓰이지 않으므로(consumer는 service.py의 INFO 혼잡도 한 곳뿐) 건너뛴다.
        if (
            purpose is LocationPurpose.PLACE_IDENTITY
            and selected.name.strip() != requested_query.strip()
        ):
            stored = await self._lookup_stored_place(requested_query, lookup_name=selected.name)
            if stored is not None and stored.status is ResolveLocationStatus.SUCCESS:
                return stored
        return self._local_search_success(requested_query, selected, metadata)

    @staticmethod
    def _local_search_success(
        requested_query: str,
        place: LocalSearchPlace,
        metadata: ProviderMetadata,
    ) -> ResolveLocationResult:
        # candidates 단계에서 좌표 존재 여부를 확인했으므로 여기서는 확정값이다.
        assert place.latitude is not None and place.longitude is not None
        return ResolveLocationResult(
            status=ResolveLocationStatus.SUCCESS,
            location=ResolvedLocation(
                requested_query=requested_query,
                provider_query=requested_query,
                resolved_name=place.name,
                latitude=place.latitude,
                longitude=place.longitude,
                resolution_method=ResolutionMethod.LOCAL_SEARCH,
                confidence=ResolutionConfidence.APPROXIMATE,
                address=place.road_address or place.address,
                place_category=place.category,
            ),
            error=None,
            warnings=("local_search_used",),
            provider_metadata=(metadata,),
        )

    async def _lookup_stored_place(
        self, requested_query: str, *, lookup_name: str | None = None
    ) -> ResolveLocationResult | None:
        """저장된 TourAPI 장소를 먼저 찾아 상호명 지오코딩 실패를 줄인다.

        lookup_name을 주면 그 이름으로 조회하되 요청 원문은 그대로 보고한다 — 지역
        검색이 알아낸 상호명으로 재조회할 때 쓴다.
        """
        if self._place_repository is None:
            return None
        # 되묻기 버튼으로 돌아온 이름은 "전주식당 (종로구)" 꼴이다. 이름으로 찾고 구로 고른다.
        search_name, district_hint = _split_district_hint(lookup_name or requested_query)
        try:
            matches = await self._place_repository.find_active_places_by_name(search_name)
        except AppError:
            # 저장소 장애만으로 주소 기반 지오코딩까지 막지는 않는다.
            return None
        if district_hint:
            narrowed = tuple(
                place
                for place in matches
                if place.address and district_hint in place.address
            )
            # 구로 좁혀 아무것도 안 남으면 좁히기 전으로 되돌린다 — 주소 표기가
            # 달라졌을 때 답을 통째로 잃는 것보다 낫다.
            matches = narrowed or matches
        if not matches:
            return None
        metadata = (
            ProviderMetadata(
                # fake 저장소가 실저장소로 보이면 안 된다(D-042).
                source=getattr(
                    self._place_repository,
                    "provider_source",
                    ProviderSource.SUPABASE_PLACES,
                ),
                status=ProviderStatus.SUCCESS,
                retrieved_at=datetime.now(UTC),
            ),
        )
        # 같은 장소가 분류만 달리해 두 번 적재된 경우는 되묻지 않는다(_is_same_stored_place).
        # 그때 되물어 봐야 선택지가 같은 이름 둘이라 화면에는 하나로 보이고, 눌러도 같은
        # 조회가 다시 돌아 빠져나갈 수 없다.
        if len(matches) > 1 and not _is_same_stored_place(matches):
            return self._error_result(
                status=ResolveLocationStatus.NO_DATA,
                code="no_data",
                cause="ambiguous_location",
                retryable=False,
                details={
                    "reason": "ambiguous_location",
                    # 이름이 같은 다른 가게라 주소를 붙여 구분한다(_stored_place_label).
                    "candidate_names": _join_candidate_names(
                        _stored_place_label(place, add_address=True) for place in matches
                    ),
                },
                provider_metadata=metadata,
            )
        place = matches[0]
        return ResolveLocationResult(
            status=ResolveLocationStatus.SUCCESS,
            location=ResolvedLocation(
                requested_query=requested_query,
                provider_query=place.title,
                resolved_name=place.title,
                latitude=place.latitude,
                longitude=place.longitude,
                resolution_method=ResolutionMethod.DATABASE,
                confidence=ResolutionConfidence.EXACT,
                place_id=place.content_id,
                address=place.address,
                district_code=place.district_code,
                concentration_name=place.concentration_name,
                concentration_search_keys=place.concentration_search_keys,
            ),
            error=None,
            provider_metadata=metadata,
        )

    async def _lookup(
        self, provider_query: str, *, use_alias: bool = False
    ) -> tuple[GeocodeResult, ProviderMetadata] | ResolveLocationResult:
        try:
            result = await self._provider.geocode(
                provider_query,
                use_alias=use_alias,
            )
            return result.data, result.metadata
        except AppError as exc:
            if exc.code == "location_not_found":
                return self._error_result(
                    status=ResolveLocationStatus.NO_DATA,
                    code="no_data",
                    cause="location_not_found",
                    retryable=False,
                )
            return self._error_result(
                status=ResolveLocationStatus.UNAVAILABLE,
                code="unavailable",
                cause=_map_unavailable_cause(exc.code),
                retryable=exc.retryable,
            )

    def _success_or_policy_result(
        self,
        *,
        result: GeocodeResult,
        requested_query: str,
        provider_query: str,
        method: ResolutionMethod,
        warnings: tuple[str, ...] = (),
        provider_metadata: tuple[ProviderMetadata, ...] = (),
        enforce_service_area: bool = True,
    ) -> ResolveLocationResult:
        # 지역 판정을 먼저 한다 - 지원 범위 밖이면 "어느 장소인지" 되물어도 소용없다.
        if enforce_service_area:
            outside = self._outside_service_area_result(
                result.latitude, result.longitude, provider_metadata
            )
            if outside is not None:
                return outside
        # **사용자가 구분할 수 없는 후보로는 되묻지 않는다.** 되묻기의 목적은 고르게
        # 하는 것인데, 이름표가 전부 같으면 어느 버튼을 눌러도 같은 문자열이 다시
        # 들어가 같은 되묻기로 돌아온다 — 사용자가 빠져나갈 길이 없다.
        #
        # "강서구"가 그랬다(2026-09-09). 네이버 지오코딩이 부산 강서구까지 2건을 주는데
        # 이름표는 둘 다 "강서구"라, 되묻기 선택지가 하나로 합쳐졌다. 그 하나를 눌러도
        # 제자리였다. 첫 후보는 이미 서울 강서구였고 위의 지원 구 검사도 통과한
        # 상태였으므로, 되묻기가 알아내는 것이 하나도 없었다.
        #
        # 이름표가 서로 다르면 지금처럼 되묻는다 — 그때는 고르는 행위에 뜻이 있다.
        # 좌표로 가려낼 수는 없다. 응답에 후보별 좌표가 없어 어느 쪽이 서울인지
        # 알 방법이 없다(2026-09-09 실측: candidate_labels만 온다).
        #
        # **이름표가 아예 없을 때는 지금까지처럼 되묻는다.** 선택지 없는 되묻기는 막다른
        # 길이 아니다 — 사용자가 더 구체적인 이름을 직접 칠 수 있다. 가두는 것은 "고를
        # 수 있는데 골라도 그대로인" 경우뿐이다.
        distinct_labels = {label.strip() for label in result.candidate_labels if label.strip()}
        indistinguishable = bool(distinct_labels) and len(distinct_labels) == 1
        if (
            method is not ResolutionMethod.ALIAS
            and result.candidate_count > 1
            and not indistinguishable
        ):
            # 후보를 함께 싣는다(TP-182). 안 실으면 그 위층이 GPS로 짐작한 구의
            # 대표 스팟으로 버튼을 메워, "익선동"을 물은 사람에게 강서구 장소가
            # 나간다 — 진짜 답을 손에 들고도 짐작을 보여주는 셈이다.
            details = {"reason": "ambiguous_location"}
            if distinct_labels:
                details["candidate_names"] = _join_candidate_names(sorted(distinct_labels))
            return self._error_result(
                status=ResolveLocationStatus.NO_DATA,
                code="no_data",
                cause="ambiguous_location",
                retryable=False,
                details=details,
                provider_metadata=provider_metadata,
            )
        return ResolveLocationResult(
            status=ResolveLocationStatus.SUCCESS,
            location=ResolvedLocation(
                requested_query=requested_query,
                provider_query=provider_query,
                resolved_name=result.resolved_name,
                latitude=result.latitude,
                longitude=result.longitude,
                resolution_method=method,
                confidence=(
                    ResolutionConfidence.EXACT
                    if method in (ResolutionMethod.ALIAS, ResolutionMethod.DATABASE)
                    else ResolutionConfidence.APPROXIMATE
                ),
                # Geocoding 결과의 resolved_name은 도로명/지번 주소다. INFO의 공영
                # 주차장 구 단위 조회처럼 주소가 필요한 소비자가 있어 함께 보존한다.
                address=result.resolved_name,
            ),
            error=None,
            warnings=warnings,
            provider_metadata=provider_metadata,
        )

    @classmethod
    def _outside_service_area_result(
        cls,
        latitude: float,
        longitude: float,
        provider_metadata: tuple[ProviderMetadata, ...],
    ) -> ResolveLocationResult | None:
        """지원 지역 밖이면 unsupported, 안이면 None.

        저장소에서 해석된 장소에는 쓰지 않는다 — 이미 지원 구 장소로 등록된 것이라
        경계선에 붙어 있어도 지원 대상이 맞다(D-044).
        """
        if is_within_service_area(latitude, longitude):
            return None
        return cls._error_result(
            status=ResolveLocationStatus.UNSUPPORTED,
            code="unsupported_region",
            cause="outside_supported_region",
            retryable=False,
            details={"reason": "outside_supported_region"},
            provider_metadata=provider_metadata,
        )

    @staticmethod
    def _error_result(
        *,
        status: ResolveLocationStatus,
        code: str,
        cause: str,
        retryable: bool,
        details: dict[str, str] | None = None,
        provider_metadata: tuple[ProviderMetadata, ...] = (),
    ) -> ResolveLocationResult:
        """Provider 조회 뒤 정책 판정이 실패해도 조회 메타데이터는 보존한다."""

        return ResolveLocationResult(
            status=status,
            location=None,
            error=ResolveLocationError(
                code=code,
                message=_error_message(code, cause),
                cause=cause,
                retryable=retryable,
                details=details or {},
            ),
            provider_metadata=provider_metadata,
        )


def _map_unavailable_cause(provider_code: str) -> str:
    if provider_code == "provider_timeout":
        return "timeout"
    return "upstream_error"


def _error_message(code: str, cause: str) -> str:
    # 지원 범위를 문구에 직접 쓰지 않는다 - 구가 늘 때 SUPPORTED_DISTRICTS만
    # 고치면 여기도 따라오게 한다.
    if cause == "ambiguous_location":
        return (
            f"{supported_district_label()} 안에서 어느 장소인지 "
            "조금 더 구체적으로 알려주세요."
        )
    if cause == "outside_supported_region":
        return (
            f"현재는 {supported_district_label(with_city=True)} 안에서만 "
            "찾아드릴 수 있어요."
        )
    if code == "no_data":
        return "입력한 위치를 찾을 수 없습니다. 주소나 장소명을 확인해주세요."
    return "위치 검색 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해주세요."
