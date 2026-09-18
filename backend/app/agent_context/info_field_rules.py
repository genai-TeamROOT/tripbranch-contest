"""INFO question_type별로 PlaceDetails에서 답변에 쓸 필드를 뽑는 규칙.

TourAPI detailIntro2는 contentTypeId마다 같은 의미의 필드 이름이 다르다 —
운영시간 하나가 ``usetime``/``usetimeculture``/``opentime``/``playtime``으로
흩어져 있다. 이 모듈은 유형별 후보 키를 순서대로 훑어 처음 발견한 값을 쓰고,
소비 측에는 C가 고정한 정규화 키(INFO_FIELD_KEYS)로만 넘긴다.

real_place.py가 운영시간·휴무일에 대해 이미 같은 방식(_OPERATING_HOURS_KEYS)을
쓰고 있고, 이 모듈은 그 대상을 나머지 question_type으로 넓힌 것이다.

값 정제는 두 가지만 한다 — HTML 태그 제거와 공백 정리. 문구를 지어내거나
"정보 없음" 같은 기본값을 채우지 않는다. 값이 없으면 키 자체를 넣지 않는다.
"""

from __future__ import annotations

import html
import re

from app.agent_context.info_schemas import InfoQuestionType
from app.domain.models import PlaceDetails

# 소비 측(A)이 읽는 정규화 키. 이 이름이 계약이다 — TourAPI 원본 키를 그대로
# 노출하지 않는다.
INFO_FIELD_KEYS = {
    "operating_hours": "operating_hours",
    "rest_date": "rest_date",
    "fee": "fee",
    "parking": "parking",
    "parking_fee": "parking_fee",
    "baby_carriage": "baby_carriage",
    "pet": "pet",
    "credit_card": "credit_card",
    "restroom": "restroom",
    # 무장애 여행 정보(place_barrier_free, D-077). 무장애 목록에 등록된 장소만 값이
    # 있어 대부분의 장소에서는 이 키들이 아예 나가지 않는다(4개 구 실측 19%).
    #
    # 이름을 응답 키가 아니라 뜻으로 지었다. TourAPI의 `wheelchair`는 휠체어 출입이
    # 아니라 대여이고 `exit`는 출구가 아니라 주출입구라, 원래 키를 그대로 쓰면
    # 소비 측이 정반대로 읽는다.
    "wheelchair_access": "wheelchair_access",
    "accessible_restroom": "accessible_restroom",
    "accessible_parking": "accessible_parking",
    "wheelchair_rental": "wheelchair_rental",
    "stroller_rental": "stroller_rental",
    "nursing_room": "nursing_room",
    "guide_dog": "guide_dog",
    "braille_block": "braille_block",
    "braille_promotion": "braille_promotion",
    "audio_guide": "audio_guide",
    "public_transport": "public_transport",
    "infant_family_etc": "infant_family_etc",
    "disability_etc": "disability_etc",
    "address": "address",
    "telephone": "telephone",
    "overview": "overview",
    "homepage": "homepage",
}

# **이 모듈은 raw_intro를 더 이상 읽지 않는다.** provider가 contenttypeid별 키를 이미
# 정규화해 PlaceDetails의 fee/parking/parking_fee/baby_carriage/pet/credit_card/
# restroom에 담아두기 때문이다 — operating_hours가 진작부터 쓰던 방식이다.
#
# Supabase 캐시는 유형별 키를 한 컬럼으로 눌러 담아 raw_intro를 복원할 수 없다.
# 그 경로에서 값이 조용히 비는 것이 D-054의 원인이었고, D-060에서 이관했다.
# 옛 경로를 함께 두지 않는다 — 같은 질문이 provider에 따라 다르게 답한다.

_TAG_PATTERN = re.compile(r"<[^>]+>")
_WHITESPACE_PATTERN = re.compile(r"\s+")


def clean_text(value: object) -> str | None:
    """TourAPI 텍스트에서 HTML 태그를 벗기고 공백을 정리한다.

    overview는 <br>로 줄바꿈이 들어오고 일부 필드는 &amp; 같은 엔티티가 섞인다.
    태그를 지울 때 <br>을 공백으로 바꾸지 않으면 앞뒤 단어가 붙어버린다.
    """
    if not isinstance(value, str):
        return None
    unescaped = html.unescape(_TAG_PATTERN.sub(" ", value))
    normalized = _WHITESPACE_PATTERN.sub(" ", unescaped).strip()
    return normalized or None


def _normalized(*pairs: tuple[str, str | None]) -> dict[str, str]:
    """provider가 이미 정규화해둔 값을 계약 키로 옮긴다. 빈 값은 키 자체를 뺀다."""
    fields: dict[str, str] = {}
    for normalized_key, value in pairs:
        cleaned = clean_text(value)
        if cleaned is not None:
            fields[normalized_key] = cleaned
    return fields


_WHEELCHAIR_ACCESS_SEPARATOR = " / "

# 원문에 붙어 오는 출처 표시. 뜻이 없는 꼬리라 그대로 내보내면 화면에도 답변에도
# `"엘리베이터 있음_무장애 편의시설"`처럼 읽힌다. 원문 15종을 통틀어 835곳에 있고
# 종류는 세 가지다 — `_무장애 편의시설`(619) · `_시각장애인 편의시설`(208) ·
# `_무장애 편의정보`(8). 분류명을 열거하지 않고 자리로 찾는 이유는 새 분류가
# 붙어도 같은 모양이기 때문이다(2026-09-04 서울 25개 구 실측).
#
# 꼬리 뒤에 설명이 이어지는 값도 3곳 있다("…_무장애 편의시설지상 공터에 주차하는
# 것이 더 편리함"). 지우는 자리에 공백을 넣어 그 설명이 앞말과 붙지 않게 한다.
_SOURCE_TAG_PATTERN = re.compile(r"_[가-힣]+ 편의(?:시설|정보)")

# 두 문장이 구분자 없이 붙어 온 자리. `"영유아거치대 있음기저귀교환대 있음"`처럼
# 앞 문장의 `있음` 뒤에 곧바로 다음 문장이 시작한다(실측 18건, `있음에도` 같은
# 어미 활용은 한 건도 없어 문장 경계로 봐도 안전하다).
_GLUED_SENTENCE_PATTERN = re.compile(r"있음(?=[가-힣])")

# 수유실 값이 비어도 기저귀교환대는 `infant_family_etc_raw`에 들어 있다. 실측
# 70건이 이 필드에서만 기저귀를 말하고, 그중 48건은 수유실 값이 아예 없다 —
# 수유실만 보면 그 48곳에서 기저귀 갈 곳이 있다는 사실이 사라진다.
_DIAPER_KEYWORD = "기저귀"

# `disability_etc_raw`는 잡동사니 필드지만 231건 중 142건이 좌석 형태를 말한다.
# 나머지에는 `"공연장까지 이동하는 경로에 2~3단 정도의 계단이 있음"`처럼 단차
# 서술이 섞여 있어(19건) 통째로 내면 카드에서 뺀 축이 되돌아온다. 좌석을 말하는
# 값만 고르며, 그 값에 단차 서술이 함께 들어 있는 행은 실측 0건이었다.
_SEATING_KEYWORDS = ("의자식", "입식")

# 원문이 항목 구분에 파이프를 쓰는 곳이 있다(`"손잡이|등받이|비상 호출벨"`, 6곳).
# 같은 뜻인데 어떤 곳은 쉼표라 화면에서 톤이 갈린다. 쉼표로 맞춘다.
_PIPE_SEPARATOR_PATTERN = re.compile(r"\s*\|\s*")

# `_GLUED_SENTENCE_PATTERN`이 못 잡는 붙음. 앞말이 `있음`이 아니라 괄호나 숫자로
# 끝나면 그 패턴이 비켜간다(`"출입구 인근 1개소슬라이딩손잡이"`,
# `"…간이화장실)손잡이"`, `"화장실 있음자동버튼손잡이"`).
#
# **설비 어휘를 열거해 그 앞에서만 끊는다.** 일반 규칙으로 넓히면 멀쩡한 문장을
# 자른다 — 예를 들어 `"장애인 전용 화장실 있음(공용화장실,유아숲 맞은편)"`은
# 끊을 자리가 없는 한 문장이다. 사전에 없는 말은 붙은 채로 남긴다.
#
# 앞에 공백이 없을 때만 끊는다. `"수평수직 손잡이"`처럼 이미 띄어 쓴 곳은
# 건드리지 않기 위해서다.
_GLUED_FACILITY_TERMS = (
    "슬라이딩",
    "자동물내림",
    "자동버튼",
    "자동문",
    "미닫이",
    "손잡이",
    "등받이",
    "비상 호출벨",
    "점자표지판",
    "점형블록",
    "영유아 거치대",
    "기저귀 교환대",
    # 대중교통 안내에서 `"활터앞정류장저상버스 없음"`처럼 붙는다. 이 필드의 붙음
    # 의심 10건 중 8건은 정류장·건물 이름이 원래 붙어 있는 고유명사라
    # (`"서울역버스환승센터강우규의거터"`) 끊으면 안 되고, 진짜 붙음은 이 어휘뿐이다.
    "저상버스",
)
_GLUED_FACILITY_PATTERN = re.compile(
    r"(?<=[가-힣0-9)])(?=" + "|".join(_GLUED_FACILITY_TERMS) + ")"
)

# 항목을 다 떼어내고 나면 `"있음"` 하나만 남는 자리가 있다
# (`"있음 / 더 센터 5층…"`, `"…비상 호출벨 / 있음"`). 무엇이 있다는 것인지가
# 없어서 읽는 사람에게 뜻이 없다.
_EMPTY_ITEM_PATTERN = re.compile(r"^(있음|없음)$")

# 장애인 화장실 원문 안에 적혀 오는 영유아 설비. 그 화장실에 실제로 있는
# 설비라 틀린 값은 아니지만, 화면에는 "수유·기저귀" 줄이 따로 있어 같은 말이
# 두 번 나온다(실측 25곳 중 14곳). 항목 단위로 떼어내 그 줄로 옮긴다.
_INFANT_FACILITY_TERMS = ("영유아 거치대", "기저귀 교환대", "기저귀교환대", "유아용 거치대")

# 떼어낸 항목에서 이 꼬리를 지우고 나면 설비 이름만 남는다. 남는 것이 있으면
# 그 항목은 영유아 설비만 말하는 것이 아니므로 건드리지 않는다.
_ITEM_TAIL_PATTERN = re.compile(r"(있음|구비|설치(?:되어 있음|됨)?)$")


def clean_barrier_free_text(value: object) -> str | None:
    """무장애 원문을 사람이 읽을 수 있는 한 문장으로 다듬는다.

    `clean_text`가 하는 태그·공백 정리에 더해 두 가지를 고친다 — 출처 표시를 떼고,
    구분자 없이 붙어 온 문장을 나눈다. 둘 다 원문의 뜻은 바꾸지 않는다.

    적재 쪽(`providers/tour_barrier_free.py`)이 아니라 여기서 하는 이유는 그 모듈이
    원문을 그대로 저장하기로 정해 두었기 때문이다. 해석은 소비 측 몫이다.
    """
    cleaned = clean_text(value)
    if cleaned is None:
        return None
    without_tag = _SOURCE_TAG_PATTERN.sub(" ", cleaned)
    unified = _PIPE_SEPARATOR_PATTERN.sub(", ", without_tag)
    separated = _GLUED_SENTENCE_PATTERN.sub(
        f"있음{_WHEELCHAIR_ACCESS_SEPARATOR}", unified
    )
    separated = _GLUED_FACILITY_PATTERN.sub(_WHEELCHAIR_ACCESS_SEPARATOR, separated)
    normalized = _WHITESPACE_PATTERN.sub(" ", separated).strip()
    return _without_empty_items(normalized)


def _split_items(value: str) -> list[str]:
    """정리된 값을 항목 단위로 쪼갠다. 구분자는 쉼표와 `/` 둘뿐이다.

    **읽기 전용이다.** 쪼갠 것을 다시 이어 붙이지 않는다 — 그렇게 하면 원문이
    쓰던 구분자가 통째로 바뀐다(`"대여 가능(1대/안내데스크)"`가
    `"대여 가능(1대, 안내데스크)"`가 됐다). 지울 때는 `_remove_item()`으로
    그 자리만 도려낸다.
    """
    items: list[str] = []
    for chunk in value.split(_WHEELCHAIR_ACCESS_SEPARATOR.strip()):
        items.extend(part.strip() for part in chunk.split(","))
    return [item for item in items if item]


def _join_items(items: list[str]) -> str | None:
    """새로 만든 항목 목록을 잇는다. 원문을 다시 잇는 데 쓰면 안 된다."""
    return ", ".join(items) or None


def _remove_item(value: str, item: str) -> str:
    """항목 하나를 앞 구분자와 함께 도려낸다. 나머지는 원문 그대로 둔다."""
    escaped = re.escape(item)
    without = re.sub(rf"\s*[,/]\s*{escaped}(?=\s*(?:[,/]|$))", "", value)
    if without == value:
        without = re.sub(rf"^{escaped}\s*[,/]\s*", "", value)
    return without.strip()


def _without_empty_items(value: str) -> str | None:
    """`"있음"`처럼 무엇에 대한 것인지 없는 조각을 뺀다.

    항목을 쪼개다 남은 찌꺼기라, 그대로 두면 `"있음 / 더 센터 5층 …"`처럼 읽는
    사람에게 뜻이 없는 말로 시작한다. 값 전체가 `"있음"` 하나뿐이면 그것은 답이므로
    그대로 둔다 — 뺄 대상은 다른 항목과 함께 있을 때다.
    """
    items = _split_items(value)
    if len(items) <= 1:
        return value or None
    result = value
    for item in items:
        if _EMPTY_ITEM_PATTERN.match(item):
            result = _remove_item(result, item)
    return result or None


def _is_infant_only_item(item: str) -> bool:
    """항목 전체가 영유아 설비만 말하는가.

    **단어가 아니라 항목으로 본다.** `"장애인 전용 화장실 있음(공용화장실,유아숲
    맞은편)"`의 `"유아숲"`은 공원 이름이라, 단어로 지우면 위치 안내가 잘린다.
    설비 이름과 `"있음"` 같은 꼬리를 걷어내고 남는 것이 없을 때만 참이다.
    """
    remainder = item
    for term in _INFANT_FACILITY_TERMS:
        remainder = remainder.replace(term, "")
    remainder = _ITEM_TAIL_PATTERN.sub("", remainder.strip()).strip()
    return remainder == "" and item != remainder


def _take_infant_items(value: str | None) -> tuple[str | None, list[str]]:
    """정리된 화장실 값에서 영유아 설비 항목만 떼어낸다.

    돌려주는 것은 (영유아 항목을 뺀 값, 떼어낸 항목들)이다. 뗄 것이 없으면
    원래 값을 그대로 돌려준다.
    """
    if value is None:
        return None, []
    items = _split_items(value)
    # 괄호 안에 든 항목은 건드리지 않는다. `"…(점자표지판, 손잡이, 영유아 거치대)"`
    # 에서 마지막 항목만 떼면 여는 괄호가 닫히지 않는다. 중복이 남는 편이
    # 문장이 깨지는 것보다 낫다.
    infant = [
        item
        for item in items
        if _is_infant_only_item(item) and "(" not in item and ")" not in item
    ]
    if not infant:
        return value, []
    kept = value
    for item in infant:
        kept = _remove_item(kept, item)
    return kept or None, infant


def compose_infant_family_etc(details: PlaceDetails) -> str | None:
    """영유아·가족 편의 값에 화장실에서 떼어낸 항목을 더한다.

    답변 경로(`extract_info_fields`)에는 영유아 전용 키가 따로 있어, 상세 카드가
    쓰는 `compose_nursing_room()`이 아니라 이쪽이 옮긴 것을 받는다. 받는 자리가
    없으면 화장실에서 뗀 항목이 그대로 사라진다.
    """
    base = clean_barrier_free_text(details.infant_family_etc_raw)
    _, moved = _take_infant_items(clean_barrier_free_text(details.accessible_restroom_raw))
    if not moved:
        return base
    if base is None:
        return _join_items(moved)
    additions = [item for item in moved if item not in base]
    if not additions:
        return base
    return _WHEELCHAIR_ACCESS_SEPARATOR.join([base, _join_items(additions) or ""])


def compose_accessible_restroom(details: PlaceDetails) -> str | None:
    """장애인 화장실 값에서 영유아 설비 항목을 뺀다.

    뺀 항목은 사라지지 않는다 — `compose_nursing_room()`이 "수유·기저귀" 줄로
    받아 간다. 두 함수가 같은 규칙을 봐야 해서 한 모듈에 둔다.
    """
    without_infant, _ = _take_infant_items(
        clean_barrier_free_text(details.accessible_restroom_raw)
    )
    return without_infant


def _joined_barrier_free(*values: str | None) -> str | None:
    """여러 원문을 한 값으로 잇는다. 전부 비면 None이다."""
    parts = [
        cleaned for cleaned in (clean_barrier_free_text(value) for value in values)
        if cleaned is not None
    ]
    if not parts:
        return None
    return _WHEELCHAIR_ACCESS_SEPARATOR.join(parts)


def compose_visual_guide(details: PlaceDetails) -> str | None:
    """점자블록·점자 안내물·음성 안내를 한 값으로 잇는다.

    셋을 따로 두지 않는 이유는 채움률이다. 개별로는 6~17%라 카드에서 세 줄 중
    두 줄이 늘 비지만, 합치면 무장애 정보가 있는 1,229곳 중 296곳(24%)에서 한
    줄이라도 나온다(2026-09-04 실측). 시각장애 동행에게는 세 값이 "안내를 받을
    수단이 있는가"라는 하나의 답을 이룬다.
    """
    return _joined_barrier_free(
        details.braille_block_raw,
        details.braille_promotion_raw,
        details.audio_guide_raw,
    )


def compose_nursing_room(details: PlaceDetails) -> str | None:
    """수유실과 기저귀교환대를 한 값으로 잇는다.

    기저귀교환대가 수유실 필드가 아니라 영유아·가족 편의 필드에 들어 있어서다.
    영유아 필드에 기저귀 언급이 없으면(유아용 식기 등) 빼고 수유실만 낸다 —
    "수유·기저귀" 줄에 식기 얘기가 붙으면 라벨과 값이 어긋난다.
    """
    infant_family = clean_barrier_free_text(details.infant_family_etc_raw)
    if infant_family is not None and _DIAPER_KEYWORD not in infant_family:
        infant_family = None
    composed = _joined_barrier_free(details.nursing_room_raw, infant_family)

    # 장애인 화장실 원문에서 떼어낸 영유아 설비를 여기서 받는다. 그냥 버리면
    # 두 필드가 모두 비어 있던 곳(실측 8곳)에서 영유아 정보가 화면에서 통째로
    # 사라진다. 이미 같은 말을 하고 있으면 더하지 않는다 — 옮기는 이유가 중복을
    # 없애는 것이라 여기서 다시 겹치면 뜻이 없다.
    _, moved = _take_infant_items(clean_barrier_free_text(details.accessible_restroom_raw))
    if not moved:
        return composed
    if composed is None:
        return _join_items(moved)
    additions = [item for item in moved if item not in composed]
    if not additions:
        return composed
    return _WHEELCHAIR_ACCESS_SEPARATOR.join([composed, _join_items(additions) or ""])


def compose_seating(details: PlaceDetails) -> str | None:
    """장애인 편의 기타에서 좌석 형태를 말하는 값만 고른다.

    의자식(입식) 테이블이 있다는 것은 좌식이 아니라는 뜻이라, 오래 앉아 있기
    어려운 동행에게 쓸모가 있다.
    """
    disability_etc = clean_barrier_free_text(details.disability_etc_raw)
    if disability_etc is None:
        return None
    if not any(keyword in disability_etc for keyword in _SEATING_KEYWORDS):
        return None
    return disability_etc


def resolve_stroller_rental(details: PlaceDetails) -> tuple[str | None, str | None]:
    """(무장애 유모차 대여, detailIntro2 유모차) 중 쓸 값을 정한다.

    두 필드가 같은 사실을 말하는데 서로 어긋난다 — 둘 다 값이 있는 34곳 중
    21곳(62%)에서 detailIntro2는 `"없음"`·`"불가"`인데 무장애 원문은
    `"대여가능"`이다(서울공예박물관·국립현대미술관 서울·스타필드 코엑스몰 등,
    2026-09-04 서울 25개 구 실측). 무장애 쪽이 대수·위치·조건까지 적어 더
    구체적이므로 그쪽이 있으면 그쪽만 쓰고, 없을 때만 detailIntro2 값을 남긴다.

    둘을 함께 내면 카드가 "유모차: 없음"과 "유모차 대여: 대여가능(10대)"을 나란히
    보여준다.
    """
    stroller_rental = clean_barrier_free_text(details.stroller_rental_raw)
    if stroller_rental is not None:
        return stroller_rental, None
    return None, clean_text(details.baby_carriage)


def _compose_wheelchair_access(details: PlaceDetails) -> str | None:
    """접근로·주출입구·승강기를 한 값으로 잇는다. 셋 다 비면 None이다.

    셋을 따로 내지 않고 합치는 이유는 두 가지다.

    첫째, 원문에서 접근로 설명과 출입구 설명이 서로 뒤바뀐 장소가 있다(가나아트센터는
    approach 자리에 출입구 서술이, entrance 자리에 접근로 서술이 들어 있다). 한
    값으로 합치면 그 뒤바뀜이 답변에 영향을 주지 않는다.

    둘째, "휠체어로 들어갈 수 있나요"라는 질문에는 세 값이 하나의 답을 이룬다.

    구분자를 슬래시로 둔 이유는 원문 대부분이 마침표로 끝나지 않아, 공백으로 이으면
    앞뒤 문장이 한 문장처럼 붙기 때문이다.
    """
    return _joined_barrier_free(
        details.approach_route_raw,
        details.entrance_access_raw,
        details.elevator_raw,
    )


def extract_info_fields(
    question_type: InfoQuestionType,
    details: PlaceDetails,
) -> dict[str, str]:
    """question_type이 필요로 하는 필드만 뽑는다. 없으면 빈 dict를 돌려준다.

    concentration은 이 경로를 타지 않는다(집중률 API 전용 경로).
    """

    if question_type == "operating_hours":
        # 운영시간·휴무일은 provider가 이미 유형별 키를 훑어 정규화해둔 값이 있다.
        # Supabase 경로도 이 두 필드는 채우므로 여기서 raw_intro를 다시 보지 않는다.
        fields: dict[str, str] = {}
        operating_hours = clean_text(details.operating_hours)
        if operating_hours is not None:
            fields["operating_hours"] = operating_hours
        rest_date = clean_text(details.rest_date)
        if rest_date is not None:
            fields["rest_date"] = rest_date
        return fields

    if question_type == "fee":
        return _normalized(("fee", details.fee))

    if question_type == "parking":
        return _normalized(
            ("parking", details.parking),
            ("parking_fee", details.parking_fee),
        )

    if question_type == "facility":
        # `없음`도 값이다 — "정보가 없다"가 아니라 "없다고 답했다"이므로 그대로 낸다.
        #
        # 무장애 값(D-077)도 여기서 함께 낸다. question_type을 새로 만들지 않은
        # 이유는 분류 규칙(prompts/info/question_type_rules.md)이 이미 "휠체어
        # 가능?"을 facility로 보내고 있어서다 — 타입을 쪼개면 "화장실 있어?"가
        # 어느 쪽인지 하는 경계만 새로 생긴다.
        #
        # 일반 화장실(restroom)과 장애인 화장실(accessible_restroom)은 뜻이 달라
        # 둘 다 낸다. 앞은 detailIntro2, 뒤는 detailWithTour2에서 온 값이다.
        #
        # 유모차는 반대다. detailIntro2와 detailWithTour2가 같은 사실을 말하는데
        # 값이 서로 어긋나 하나만 골라야 한다 — 규칙과 근거는
        # resolve_stroller_rental()에 있다.
        stroller_rental, baby_carriage = resolve_stroller_rental(details)
        fields = _normalized(
            ("baby_carriage", baby_carriage),
            ("pet", details.pet),
            ("credit_card", details.credit_card),
            ("restroom", details.restroom),
        )
        # 무장애 원문은 출처 꼬리·붙은 문장을 정리해 낸다. 답변과 상세 카드가 같은
        # 원문을 서로 다르게 다듬으면 같은 장소가 두 자리에서 다르게 읽힌다.
        fields.update(
            _normalized(
                ("wheelchair_access", _compose_wheelchair_access(details)),
                ("accessible_restroom", compose_accessible_restroom(details)),
                ("accessible_parking", clean_barrier_free_text(details.accessible_parking_raw)),
                ("wheelchair_rental", clean_barrier_free_text(details.wheelchair_rental_raw)),
                ("stroller_rental", stroller_rental),
                ("nursing_room", clean_barrier_free_text(details.nursing_room_raw)),
                ("guide_dog", clean_barrier_free_text(details.guide_dog_raw)),
                ("braille_block", clean_barrier_free_text(details.braille_block_raw)),
                ("braille_promotion", clean_barrier_free_text(details.braille_promotion_raw)),
                ("audio_guide", clean_barrier_free_text(details.audio_guide_raw)),
                ("public_transport", clean_barrier_free_text(details.public_transport_raw)),
                ("infant_family_etc", compose_infant_family_etc(details)),
                ("disability_etc", clean_barrier_free_text(details.disability_etc_raw)),
            )
        )
        return fields

    if question_type == "location_info":
        fields = {}
        address = clean_text(details.address)
        if address is not None:
            fields["address"] = address
        telephone = clean_text(details.telephone)
        if telephone is not None:
            fields["telephone"] = telephone
        return fields

    if question_type == "general_info":
        fields = {}
        overview = clean_text(details.overview)
        if overview is not None:
            fields["overview"] = overview
        homepage = clean_text(details.homepage)
        if homepage is not None:
            fields["homepage"] = homepage
        return fields

    # event(searchFestival2 별도 연동 필요)와 concentration은 호출부가 걸러낸다.
    return {}


__all__ = [
    "INFO_FIELD_KEYS",
    "clean_barrier_free_text",
    "clean_text",
    "compose_nursing_room",
    "compose_seating",
    "compose_visual_guide",
    "extract_info_fields",
    "resolve_stroller_rental",
]
