"""집중률 검색어 추출 규칙을 고정한다.

tAtsNm은 부분 일치 검색이라 공백이 든 값을 넘기면 0건이 온다(2026-08-04 실측).
검색어를 잘못 뽑으면 다른 장소의 혼잡도를 조용히 답하게 되므로 규칙을 테스트로 묶는다.
"""

from __future__ import annotations

from scripts.build_concentration_mappings import (
    MappingRow,
    apply_search_keys,
    derive_search_key,
)

# 실제 집중률 API 목록(2026-08-04)에서 판정이 갈리는 이름만 추린 것.
NAMES = [
    "경복궁",
    "종묘 [유네스코 세계유산]",
    "종묘광장공원",
    "창덕궁과 후원 [유네스코 세계유산]",
    "서울 운현궁",
    "청와대",
    "청와대 앞길",
    "아름다운 차박물관",
    "북촌한옥마을",
]


def test_공백_없는_이름은_그대로_쓴다() -> None:
    assert derive_search_key("경복궁", NAMES) == ("경복궁", "as_is")


def test_짧고_흔한_토큰이_유일해도_이름_전체를_우선한다() -> None:
    """"한옥"도 목록 안에서는 유일하지만, 공백이 없으면 쪼갤 이유가 없다."""
    assert derive_search_key("북촌한옥마을", NAMES) == ("북촌한옥마을", "as_is")


def test_공백이_있으면_유일한_토큰을_고른다() -> None:
    assert derive_search_key("서울 운현궁", NAMES) == ("운현궁", "token")


def test_길이가_같으면_뒤쪽_토큰을_고른다() -> None:
    """한국어 장소명은 뒤가 핵심어다."""
    assert derive_search_key("아름다운 차박물관", NAMES) == ("차박물관", "token")


def test_다른_장소에도_걸리는_토큰은_고르지_않는다() -> None:
    """"청와대"는 "청와대 앞길"에도 걸려 두 장소를 끌어온다."""
    key, reason = derive_search_key("청와대 앞길", NAMES)
    assert reason == "token"
    assert key == "앞길"


def test_괄호_부기를_떼고_토큰을_뽑는다() -> None:
    """부기를 남기면 "세계유산]"이 뽑혀 창덕궁까지 끌어온다."""
    key, _ = derive_search_key("종묘 [유네스코 세계유산]", NAMES)
    assert key == "종묘"


def test_유일한_토큰이_없으면_모호로_표시한다() -> None:
    """"종묘"는 "종묘광장공원"과 겹친다. 0건이 되는 원본보다는 낫다."""
    assert derive_search_key("종묘 [유네스코 세계유산]", NAMES) == (
        "종묘",
        "token_ambiguous",
    )


def test_검색어는_따로_담고_정식_명칭은_그대로_둔다() -> None:
    """조회용(tAtsNm)과 대조용(응답 이름)은 역할이 다르다."""
    rows = [MappingRow("1", "운현궁", "서울 운현궁", "normalized", ("옛이름",))]
    applied, unresolved = apply_search_keys(rows, NAMES)
    assert applied[0].concentration_title == "서울 운현궁"
    assert applied[0].search_key == "운현궁"
    assert applied[0].aliases == ("옛이름",)
    assert applied[0].match_method == "normalized"
    assert not unresolved


def test_정식_명칭으로_조회되면_검색어를_비워_둔다() -> None:
    """호출자가 정식 명칭을 그대로 쓰게 한다."""
    rows = [MappingRow("2", "경복궁", "경복궁", "exact")]
    applied, unresolved = apply_search_keys(rows, NAMES)
    assert applied[0].concentration_title == "경복궁"
    assert applied[0].search_key is None
    assert not unresolved


def test_모호한_건도_검색어를_채우되_따로_보고한다() -> None:
    """조회는 되지만 응답에서 정식 명칭으로 골라내야 한다."""
    rows = [MappingRow("3", "종묘", "종묘 [유네스코 세계유산]", "normalized")]
    applied, unresolved = apply_search_keys(rows, NAMES)
    assert applied[0].concentration_title == "종묘 [유네스코 세계유산]"
    assert applied[0].search_key == "종묘"
    assert unresolved == applied


def test_유일성은_집중률_목록_안에서만_따진다() -> None:
    """places에 "한옥"을 쓰는 장소가 25건 있어도 tAtsNm 검색과는 무관하다."""
    assert derive_search_key("서울 운현궁", NAMES + ["운현궁 별관"]) != ("운현궁", "token")
