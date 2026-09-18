"""저장된 취향 칩 → 취향 질의 변환 규칙을 못 박는다(D 영역).

실제 저장 데이터(`user_preferences.items`)의 모양을 그대로 쓴다 — 칩은
`{label, source, codes}` 세 필드다.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from app.domain.saved_preference import (
    _COMPANION_CODES,
    _CONTRADICTIONS,
    contradicts,
    decided_values,
    is_companion_chip,
    to_taste_query,
    usable_chips,
)
from app.state.schema import UserPreference


def _chip(label: str, source: str, *codes: str) -> UserPreference:
    return UserPreference(label=label, source=source, codes=list(codes))


# DB에 실제로 저장돼 있던 값(2026-09-04 조회). 지어내지 않는다.
_SAVED_A = [
    _chip("힐링하기 좋은", "preference", "healing"),
    _chip("카페", "place_tag", "카페", "찻집"),
    _chip("혼자 가기 좋은", "preference", "alone"),
]
_SAVED_B = [
    _chip("혼자 가기 좋은", "preference", "alone"),
    _chip("사진 명소", "preference", "photo_spot"),
    _chip("힙한 분위기", "preference", "trendy_hotspot"),
]


def test_저장된_칩의_라벨을_공백으로_잇는다() -> None:
    assert to_taste_query(_SAVED_B) == "혼자 가기 좋은 사진 명소 힙한 분위기"


def test_고른_순서를_지킨다() -> None:
    """사용자가 고른 순서가 곧 우선순위로 읽힌다(state/schema.py 주석)."""
    reversed_chips = list(reversed(_SAVED_B))

    assert to_taste_query(reversed_chips) == "힙한 분위기 사진 명소 혼자 가기 좋은"


def test_고른_칩을_하나도_버리지_않는다() -> None:
    """화면이 최소 3개·최대 5개를 고르게 한다. 걸러내면 고른 수만큼 반영되지 않는다.

    분류 칩을 뺐을 때 5개짜리 저장값의 질의가 1개로 줄어 취향점수가 0.39 → 0.22로
    떨어졌다(종로·중구 500곳). 분류 칩을 **질의에 넣는 것**과 **하드 필터로 쓰는
    것**은 다르다 — 여기서는 순위만 다듬고 후보를 지우지 않는다.
    """
    five = [
        _chip("사진 명소", "preference", "photo_spot"),
        _chip("전시·문화", "place_tag", "박물관", "미술관"),
        _chip("시장·쇼핑", "place_tag", "시장"),
        _chip("전통·역사", "place_tag", "궁궐"),
        _chip("친구와 함께", "preference", "with_friends"),
    ]

    assert to_taste_query(five) == "사진 명소 전시·문화 시장·쇼핑 전통·역사 친구와 함께"
    assert len(usable_chips(five)) == 5


def test_발화_취향_문구는_보지_않는다() -> None:
    """중복을 안 빼므로 `taste_query`가 판정에 안 쓰인다.

    발화 취향이 있든 없든 결과가 같다 — 인자로 받지도 않는다.
    """
    assert to_taste_query(_SAVED_B) == "혼자 가기 좋은 사진 명소 힙한 분위기"


def test_부딪히지_않는_칩은_전부_남는다() -> None:
    """"조용한 곳" 칩이 없으면 혼잡도 발화로 빠지는 칩이 없다.

    동행 칩도 발화에 동행이 없어 남는다.
    """
    result = to_taste_query(_SAVED_B, concentration_intent="AVOID")

    assert result == "혼자 가기 좋은 사진 명소 힙한 분위기"


def test_place_tag_칩도_질의에_넣는다() -> None:
    """하드 필터로는 안 쓰지만 질의에는 넣는다 — 빼면 고른 수만큼 반영되지 않는다."""
    assert to_taste_query(_SAVED_A) == "힐링하기 좋은 카페 혼자 가기 좋은"


@pytest.mark.parametrize(
    "code", ["alone", "date", "with_friends", "with_kids", "with_parents", "group_gathering"]
)
def test_동행_칩도_질의에_넣는다(code: str) -> None:
    """단독 통과율이 2.4~10.0%로 낮고 좋은 조합에 붙이면 점수를 깎지만(0.46 →
    0.25~0.30), 화면이 동행 칩을 최대 1개로 제한할 예정이라 최악이 1개뿐이고
    동행만 고른 사용자가 취향을 아예 못 쓰는 것보다 낫다(2026-09-04 팀 결정).
    """
    chips = [_chip("분위기 칩", "preference", "cozy"), _chip("동행 칩", "preference", code)]

    assert to_taste_query(chips) == "분위기 칩 동행 칩"


def test_동행_칩만_저장해도_취향_축을_쓴다() -> None:
    chips = [_chip(label, "preference", code) for label, code in
             (("혼자 가기 좋은", "alone"), ("데이트 코스", "date"))]

    assert to_taste_query(chips) == "혼자 가기 좋은 데이트 코스"


def test_직접_입력한_키워드는_넣는다() -> None:
    """custom은 대응 코드가 없어 codes가 빈 배열이다."""
    chips = [_chip("루프탑", "custom")]

    assert to_taste_query(chips) == "루프탑"


def test_알_수_없는_source는_넣지_않는다() -> None:
    """화면이 칩 종류를 늘려도 채점이 조용히 그 값을 먹지 않게 한다."""
    chips = [_chip("무언가", "brand_new_source", "x")]

    assert to_taste_query(chips) is None


def test_라벨이_공백뿐인_칩은_버린다() -> None:
    chips = [_chip("   ", "preference", "cozy"), _chip("아늑한 공간", "preference", "cozy")]

    assert to_taste_query(chips) == "아늑한 공간"


def test_고른_적이_없으면_None() -> None:
    assert to_taste_query([]) is None


def test_usable_chips는_칩_객체를_그대로_돌려준다() -> None:
    """호출부가 라벨 말고 다른 필드도 볼 수 있게 원본을 유지한다."""
    picked = usable_chips(_SAVED_A)

    assert [chip.label for chip in picked] == ["힐링하기 좋은", "카페", "혼자 가기 좋은"]
    assert picked[0].codes == ["healing"]


# --- 동행: 발화가 말했으면 통째로 뺀다 ---------------------------------------
#
# 값을 비교하지 않는다. 동행 값 여섯은 서로 배타적이지 않아 "다르면 모순"이
# 성립하지 않고("부모님이랑"에 "아이와 함께"는 3대가 함께 가는 경우다),
# "단체 모임"은 `Companion` 어휘에 대응 값이 아예 없다.

_COMPANION_CHIPS = [
    _chip("데이트 코스", "preference", "date"),
    _chip("친구와 함께", "preference", "with_friends"),
    _chip("아이와 함께", "preference", "with_kids"),
    _chip("단체 모임", "preference", "group_gathering"),
    _chip("부모님과 함께", "preference", "with_parents"),
    _chip("혼자 가기 좋은", "preference", "alone"),
]
_PHOTO = _chip("사진 명소", "preference", "photo_spot")
_QUIET = _chip("조용한 곳", "preference", "quiet")
_TRENDY = _chip("힙한 분위기", "preference", "trendy_hotspot")
_NATURE = _chip("자연·공원", "place_tag", "공원", "산", "호수", "계곡", "수목원")
_ANY_WEATHER = _chip("날씨 상관없는 곳", "preference", "indoor")
_WALK = _chip("산책하기 좋은", "preference", "walk")


@pytest.mark.parametrize("chip", _COMPANION_CHIPS, ids=lambda c: c.label)
@pytest.mark.parametrize("spoken", ["solo", "couple", "friend", "parent", "child", "pet"])
def test_발화에_동행이_있으면_동행_칩은_값과_무관하게_뺀다(
    chip: UserPreference, spoken: str
) -> None:
    """같은 값이어도 뺀다 — 이번 턴에 말한 동행이 정본이다."""
    assert to_taste_query([chip, _PHOTO], companion=spoken) == "사진 명소"


@pytest.mark.parametrize("chip", _COMPANION_CHIPS, ids=lambda c: c.label)
def test_발화에_동행이_없으면_동행_칩을_그대로_쓴다(chip: UserPreference) -> None:
    """저장한 동행 취향은 "말하지 않았을 때의 기본값"이다."""
    assert to_taste_query([chip, _PHOTO]) == f"{chip.label} 사진 명소"


def test_동행_발화는_분위기_칩을_건드리지_않는다() -> None:
    """축이 다르면 안 뺀다. 동행 한마디로 취향 설정이 통째로 죽으면 안 된다."""
    result = to_taste_query([_QUIET, _NATURE, _PHOTO, *_COMPANION_CHIPS], companion="solo")

    assert result == "조용한 곳 자연·공원 사진 명소"


# --- 분위기: 혼잡도가 부딪힐 때만 뺀다 ----------------------------------------
#
# 표는 한 줄(`quiet × SEEK`)이다. 실내외 5칩분과 trendy_hotspot은 실측으로
# 걷어냈다 — 이유와 수치는 `_CONTRADICTIONS` 주석에 있다.


def test_조용한_곳_칩은_북적이는_발화에서_빠진다() -> None:
    """실측에서 결과가 통째로 뒤집혔던 유일한 조합이다.

    "북적이는 활기찬" 발화에 "조용한 곳" 칩을 합치면 순희네빈대떡·종로3가
    포장마차가 안국선원·북촌동양문화박물관·선화랑으로 바뀌었다(종로·중구 500곳).
    """
    assert to_taste_query([_QUIET, _PHOTO], concentration_intent="SEEK") == "사진 명소"


def test_조용한_곳_칩은_같은_방향_발화에서는_남는다() -> None:
    """중복 제거는 하지 않는다.

    실측 5/5에서 중복을 두는 쪽이 발화를 더 잘 반영했다(모듈 docstring 표).
    """
    assert (
        to_taste_query([_QUIET, _PHOTO], concentration_intent="AVOID") == "조용한 곳 사진 명소"
    )


@pytest.mark.parametrize("kwargs", [{"concentration_intent": "IGNORE"}, {}])
def test_상관없다고_말한_축은_칩을_빼지_않는다(kwargs: dict[str, str]) -> None:
    """`IGNORE`는 "확정"이 아니다.

    "사람 많아도 괜찮아"(IGNORE)는 조용한 곳을 원하지 않는다는 말이 아니다
    (`prompts/_shared/rules/concentration_intent.md`).
    """
    assert to_taste_query([_QUIET, _NATURE], **kwargs) == "조용한 곳 자연·공원"


@pytest.mark.parametrize(
    "chip",
    [_TRENDY, _NATURE, _WALK, _ANY_WEATHER],
    ids=["힙한분위기", "자연·공원", "산책하기좋은", "날씨상관없는곳"],
)
def test_실측으로_걷어낸_칩들은_이제_안_빠진다(chip: UserPreference) -> None:
    """실내외 칩과 "힙한 분위기"는 판정 대상이 아니다.

    - 실내외: 비 오는 날 `environment=indoor` 요청에서 "자연·공원" 칩을 두든
      빼든 상위 5곳의 야외 비율이 똑같이 4/5(종로)·2/5(중구)였다. 반경 경로는
      취향 최대폭 0.15 < 환경 축 차이 0.245라 산술로도 불가능하다.
    - 힙한 분위기: 조용한 힙플레이스가 모순이 아니다(2026-09-04 팀 결정).

    발화가 무엇을 말하든 남는다 — `environment`는 인자로 받지도 않는다.
    """
    result = to_taste_query([chip, _PHOTO], concentration_intent="AVOID")

    assert result == f"{chip.label} 사진 명소"


def test_place_tag_칩은_코드_하나만_걸려도_빠진다() -> None:
    """코드가 여럿인 칩은 하나라도 부딪히면 뺀다.

    **표에 걸리는 코드가 맨 앞이 아닌 경우까지 본다** — 첫 코드만 보는 구현으로도
    통과해 버리면, 화면이 코드 순서를 바꿀 때 조용히 깨진다.
    """
    tail_match = _chip("어떤 조용한 칩", "place_tag", "미술관", "찻집", "quiet")

    assert contradicts(tail_match, {"SEEK"}) is True
    assert contradicts(tail_match, {"AVOID"}) is False


def test_동행_칩도_뒤쪽_코드로_걸린다() -> None:
    """같은 이유로 동행 판정도 첫 코드만 보면 안 된다."""
    assert is_companion_chip(_chip("어떤 동행 칩", "place_tag", "카페", "alone"))


@pytest.mark.parametrize(
    "chip",
    [
        _PHOTO,
        _chip("아늑한 공간", "preference", "cozy"),
        _chip("카페", "place_tag", "카페", "찻집"),
        _chip("야경 명소", "preference", "night_visit"),
        _chip("루프탑", "custom"),
    ],
    ids=lambda c: c.label,
)
def test_대립하는_값이_없는_칩은_절대_안_빠진다(chip: UserPreference) -> None:
    """표에 없는 칩은 발화가 무엇을 말하든 남는다."""
    result = to_taste_query([chip], concentration_intent="AVOID", companion="solo")

    assert result == chip.label


def test_남는_칩이_없으면_None() -> None:
    """전부 빠지면 빈 문자열이 아니라 None이다.

    빈 문자열은 호출부가 취향 축을 켜서 전 후보가 0점인 축이 다른 축의 몫만
    깎는다(`scoring.py::_taste_score`).
    """
    assert to_taste_query([_QUIET], concentration_intent="SEEK") is None


def test_decided_values는_확정된_값만_담는다() -> None:
    assert decided_values(concentration_intent="IGNORE") == set()
    assert decided_values(concentration_intent="SEEK") == {"SEEK"}
    assert decided_values() == set()


# --- 화면 칩 목록과 판정 표가 어긋나지 않게 -----------------------------------


def test_화면_칩_코드가_전부_판정돼_있다() -> None:
    """칩이 늘면 여기서 깨진다.

    두 표(`_CONTRADICTIONS`·`_COMPANION_CODES`)는 화면 카탈로그
    (`frontend/src/pages/preferenceOptions.ts`)의 복제라 어긋날 수 있다. 백엔드가
    프론트 파일을 읽는 게 흔한 모양은 아니지만, **칩이 판정 없이 조용히 늘어나는
    것**을 잡을 방법이 이것뿐이다 — 판정이 없으면 부딪히는 칩이 그대로 질의에 섞인다.

    새 코드를 추가할 때는 표에 넣거나, 넣지 않기로 했으면 그 이유를
    `_NO_OPPOSITE_ON_PURPOSE`에 적는다.
    """
    catalog = (
        pathlib.Path(__file__).resolve().parents[2]
        / "frontend/src/pages/preferenceOptions.ts"
    )
    if not catalog.exists():  # 백엔드만 체크아웃한 경우
        pytest.skip(f"프론트 카탈로그 없음: {catalog}")

    codes = {
        code
        for block in re.findall(r"codes:\s*\[([^\]]*)\]", catalog.read_text())
        for code in re.findall(r'"([^"]+)"', block)
    }
    assert codes, "카탈로그에서 코드를 하나도 못 읽었다 — 파싱이 깨졌다"

    judged = set(_CONTRADICTIONS) | set(_COMPANION_CODES)
    unjudged = codes - judged - _NO_OPPOSITE_ON_PURPOSE
    assert not unjudged, f"판정이 없는 새 칩 코드: {sorted(unjudged)}"


def test_동행_칩은_모순_표에_넣지_않는다() -> None:
    """두 표가 겹치면 같은 칩을 두 규칙이 다르게 다룬다.

    동행은 값을 안 보고 통째로 빼는 쪽이므로 모순 표에 있으면 안 된다.
    """
    assert set(_CONTRADICTIONS).isdisjoint(_COMPANION_CODES)


# 대립하는 값이 없어 **일부러** 판정을 안 붙인 코드. 이유는 `_CONTRADICTIONS`
# 주석에 있다 — 요약하면 "그 축으로 기울 뿐 그 축을 말하는 칩은 아니다"이거나,
# 애초에 반대말이 없다(야경·사진·전망·카페).
_NO_OPPOSITE_ON_PURPOSE = frozenset(
    {
        # 분위기 — 조용한 쪽으로 기울지만 혼잡도를 말한 칩은 아니다
        "photo_spot", "healing", "unique", "cozy", "good_view", "night_visit", "spacious",
        # 테마 — 실내가 많을 뿐 실내외를 말한 칩이 아니다(테라스 카페·전통시장)
        "박물관", "미술관", "전시관", "전시회", "카페", "찻집",
        "시장", "쇼핑몰", "백화점",
        "궁궐", "사찰", "성곽", "전통체험", "마을",
        "experience", "food_exploration", "reading",
        # 실측으로 판정을 걷어낸 것들(`_CONTRADICTIONS` 주석)
        "trendy_hotspot",  # 조용한 힙플레이스가 모순이 아니다
        "indoor", "walk", "공원", "산", "호수", "계곡", "수목원",  # 실내외는 효과 0
    }
)


def test_모순_표에는_혼잡도_값만_넣는다() -> None:
    """`environment`를 더 이상 안 넘기므로, 실내외 값을 표에 넣으면 조용히 안 걸린다.

    되살릴 때는 `to_taste_query`·`decided_values`에 인자를 다시 붙여야 한다 —
    표에 한 줄 넣는 것만으로는 동작하지 않는다.
    """
    allowed = {"AVOID", "SEEK"}  # app.schemas.ConcentrationIntent - IGNORE
    used = {value for values in _CONTRADICTIONS.values() for value in values}

    assert used <= allowed, f"혼잡도 아닌 값이 표에 있다: {sorted(used - allowed)}"
