from __future__ import annotations

from scripts.build_concentration_mappings import derive_search_keys

# 실제 종로구 집중률 장소명 일부(2026-08-08 수집). 유일성 판정이 목록에 의존하므로
# 대표적인 충돌 사례(종묘 ↔ 종묘광장공원, 청와대 ↔ 청와대 앞길)를 포함한다.
NAMES = (
    "서울 동대문 닭한마리 골목",
    "동대문 문구완구거리",
    "종묘 [유네스코 세계유산]",
    "종묘광장공원",
    "청와대 앞길",
    "청와대사랑채",
    "한국교회 100주년 기념관",
    "낙원동 아구찜 거리",
    "서울 운현궁",
    "승가사(서울)",
    "채석장 전망대",
)


def test_existing_key_stays_first() -> None:
    """기존 검색어는 1순위를 유지한다.

    지금 값들이 전부 정상 조회되는 것이 확인됐으므로 휴리스틱으로 재계산해 회귀를
    만들지 않는다. 토큰 추가는 능력 추가로만 둔다(D-057).
    """
    keys = derive_search_keys("낙원동 아구찜 거리", NAMES, "아구찜")

    assert keys[0] == "아구찜"


def test_other_tokens_follow_as_fallback() -> None:
    """정식 명칭과 어긋나는 발화를 받아내려고 나머지 토큰을 뒤에 붙인다."""
    keys = derive_search_keys("서울 동대문 닭한마리 골목", NAMES, "닭한마리")

    assert keys[0] == "닭한마리"
    assert "동대문" in keys
    # 변별력 없는 토큰도 마지막 수단으로는 남긴다. 앞선 토큰에서 결과가 나오면
    # 호출자가 멈추므로 실제로 쓰일 일은 드물다.
    assert keys.index("동대문") < keys.index("서울")


def test_bracket_fragments_are_not_search_keys() -> None:
    """부기를 자른 조각은 장소명이 아니므로 검색어에서 뺀다."""
    keys = derive_search_keys("종묘 [유네스코 세계유산]", NAMES, "종묘")

    assert keys == ["종묘"]
    assert all("[" not in key and "]" not in key for key in keys)


def test_no_key_contains_whitespace() -> None:
    """공백이 든 값을 tAtsNm에 넘기면 무엇을 넣든 0건이 돌아온다."""
    for name in NAMES:
        for key in derive_search_keys(name, NAMES, None):
            assert " " not in key, f"{name}: {key!r}"


def test_name_without_whitespace_is_used_as_is() -> None:
    """공백이 없으면 정식 명칭 그대로 조회한다. 괄호는 문제가 되지 않는다."""
    keys = derive_search_keys("승가사(서울)", NAMES, None)

    assert keys[0] == "승가사(서울)"


def test_unreachable_tokens_are_dropped() -> None:
    """집중률 목록의 어떤 이름에도 걸리지 않는 토큰은 호출해도 0건이라 뺀다."""
    keys = derive_search_keys("한국교회 100주년 기념관", NAMES, "100주년")

    assert keys[0] == "100주년"
    assert all(any(key in name for name in NAMES) for key in keys)


def test_keys_are_deduplicated_and_ordered_deterministically() -> None:
    """같은 입력은 항상 같은 순서를 낸다 — set을 쓰면 실행마다 흔들린다."""
    first = derive_search_keys("서울 문묘와 성균관", NAMES, "성균관")
    second = derive_search_keys("서울 문묘와 성균관", NAMES, "성균관")

    assert first == second
    assert len(first) == len(set(first))
