"""주차 안내 원문 정규화 규칙 테스트.

값은 전부 종로구 실측(2026-08-10, 활성 844건)에서 가져왔다.
"""

from __future__ import annotations

import pytest

from app.domain.parking import ParkingAvailability, normalize_parking


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 실측 상위 3개 값이 631건 중 579건(92%)을 차지한다.
        ("불가능", ParkingAvailability.UNAVAILABLE),
        ("가능", ParkingAvailability.AVAILABLE),
        ("불가", ParkingAvailability.UNAVAILABLE),
        ("없음", ParkingAvailability.UNAVAILABLE),
        ("있음", ParkingAvailability.AVAILABLE),
    ],
)
def test_single_token_values(raw: str, expected: ParkingAvailability) -> None:
    assert normalize_parking(raw).availability is expected


def test_unavailable_is_judged_before_available() -> None:
    """`불가능`이 `가능`을 부분문자열로 포함한다.

    이 순서가 뒤집히면 실측 631건 중 313건이 통째로 AVAILABLE이 된다. 포함 검사로
    구현하면 조용히 통과하므로 명시적으로 못 박는다.
    """
    assert normalize_parking("불가능").availability is ParkingAvailability.UNAVAILABLE
    assert "가능" in "불가능"


@pytest.mark.parametrize(
    "raw",
    [
        "불가 (인근 공영주차장 이용)",
        "불가(인근 유료 주차장 이용)",
        "불가 (인근 유료주차장 이용)",
    ],
)
def test_negative_values_containing_parking_lot_word(raw: str) -> None:
    """부정형 뒤에 `주차장`이 붙어도 판정은 뒤집히지 않는다."""
    assert normalize_parking(raw).availability is ParkingAvailability.UNAVAILABLE


@pytest.mark.parametrize(
    "raw",
    ["주차 가능", "주차가능", "주차 가능 (161대)"],
)
def test_subject_prefix_is_stripped(raw: str) -> None:
    """`주차`라는 주어가 붙은 표기도 같은 규칙으로 읽는다."""
    assert normalize_parking(raw).availability is ParkingAvailability.AVAILABLE


def test_trailing_negation_does_not_flip_judgment() -> None:
    """앞머리만 본다 — 뒤에 붙은 안내문의 `불가`에 걸리면 안 된다."""
    raw = "가능 (54대)<br>※ 대형버스 25인승 초과 차량 주차 불가"
    assert normalize_parking(raw).availability is ParkingAvailability.AVAILABLE


def test_html_break_is_cleaned_into_note() -> None:
    result = normalize_parking("가능<br>요금 (30분 1,500원)")
    assert result.availability is ParkingAvailability.AVAILABLE
    assert "<br>" not in (result.note or "")
    assert "30분 1,500원" in (result.note or "")


def test_note_preserves_capacity_detail() -> None:
    """enum만으로는 버려지는 수용대수를 note가 보존한다."""
    result = normalize_parking("가능 (버스 50대 / 승용차 240대)")
    assert result.availability is ParkingAvailability.AVAILABLE
    assert result.note == "가능 (버스 50대 / 승용차 240대)"


def test_public_lot_value_keeps_context_in_note() -> None:
    """`가능(공용주차장 이용)`은 판정상 가능이지만 자체 주차장이 아니다.

    배지만 보면 오해하므로 원문이 note에 남아 있어야 한다.
    """
    result = normalize_parking("가능(공용주차장 이용)")
    assert result.availability is ParkingAvailability.AVAILABLE
    assert result.note == "가능(공용주차장 이용)"


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_missing_value_is_unknown_not_unavailable(raw: str | None) -> None:
    """빈 값은 "주차 불가"가 아니라 "정보 없음"이다.

    실측 213건(25%)이 여기 해당하고, 축제(15)는 주차 필드 자체가 없어 전량 빈
    값이다. UNAVAILABLE로 합치면 카드가 없는 사실을 단정해 버린다.
    """
    result = normalize_parking(raw)
    assert result.availability is ParkingAvailability.UNKNOWN
    assert result.note is None


def test_sentence_final_availability_is_unknown() -> None:
    """접두어 규칙에 걸리지 않는 실측 1건은 UNKNOWN으로 둔다.

    문장 끝의 `가능`까지 주우려면 포함 검사가 필요한데, 그러면
    test_trailing_negation_does_not_flip_judgment가 깨진다.
    """
    result = normalize_parking("지하 3층 부터 5층 까지 이용 가능")
    assert result.availability is ParkingAvailability.UNKNOWN
    # 판정은 못 해도 원문은 남겨 카드에 표시할 수 있게 한다.
    assert result.note == "지하 3층 부터 5층 까지 이용 가능"
