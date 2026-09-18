"""장소 주차 안내 원문을 카드 표시용 판정과 보존 원문으로 정규화한다.

TourAPI `detailIntro2`의 주차 필드는 contenttypeid마다 이름이 다르지만 값의 모양은
같다(D-056). 종로구 실측(2026-08-10, 활성 844건) 기준으로 값이 채워진 631건 중
601건이 10자 이하 단문이고 평균 길이가 4자다. `불가능`·`가능`·`불가` 세 값만으로
579건(92%)이 설명된다. 그래서 문장 해석이 아니라 접두어 판정으로 충분하다.

원문을 버리지 않고 note로 함께 남긴다 — `가능(공용주차장 이용)`처럼 판정값만으로는
자체 주차장이 있는 것처럼 읽히는 값이 있고, `가능 (버스 50대 / 승용차 240대)`의
수용대수나 `가능<br>요금 (30분 1,500원)`의 요금도 카드에서 쓸 수 있다. 저장소의
`*_raw` 보존 관습과도 같은 방향이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.domain.operating_hours import clean_operating_text


class ParkingAvailability(StrEnum):
    """카드 배지에 쓰는 주차 가능 여부."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ParkingInfo:
    """판정값과 정리된 원문을 함께 담는다."""

    availability: ParkingAvailability
    note: str | None


# **검사 순서가 이 규칙의 전부다.** `불가능`은 `가능`을 부분문자열로 포함하므로
# 부정형을 먼저 보지 않으면 실측 631건 중 363건(`불가능` 313 + `불가` 50)이 통째로
# AVAILABLE로 뒤집힌다. 하필 가장 흔한 값이라 조용히 틀린다.
_UNAVAILABLE_PREFIXES = ("불가능", "불가", "없음")
_AVAILABLE_PREFIXES = ("가능", "있음")
# `주차 가능`/`주차가능`처럼 주어가 붙은 표기를 같은 규칙으로 읽기 위한 접두어.
_SUBJECT_PREFIX = "주차"
_WHITESPACE_PATTERN = re.compile(r"\s+")


def normalize_parking(parking_info_raw: str | None) -> ParkingInfo:
    """주차 안내 원문에서 가능 여부를 판정하고 정리된 원문을 함께 돌려준다.

    값이 비어 있으면 UNKNOWN이다. 실측에서 213건(25%)이 여기 해당하고, 축제(15)는
    주차 필드 자체가 없어 전량 비어 있다. 카드에서 "주차 불가"와 "정보 없음"을
    구분해 표시할 수 있도록 UNAVAILABLE과 섞지 않는다.
    """
    # `<br>`·entity 정리는 운영시간과 같은 함수를 쓴다. 두 값 모두 detailIntro2의
    # 같은 응답에서 오므로 정리 규칙이 갈릴 이유가 없다.
    note = clean_operating_text(parking_info_raw)
    if note is None:
        return ParkingInfo(ParkingAvailability.UNKNOWN, None)
    return ParkingInfo(_judge(note), note)


def _judge(note: str) -> ParkingAvailability:
    """공백을 지운 앞머리로 판정한다.

    접두어만 보므로 `가능 (54대) ※ 대형버스 25인승 초과 차량 주차 불가`처럼 뒤에
    부정어가 섞인 값도 앞의 `가능`으로 판정된다. 포함 검사로 바꾸면 이런 값이
    UNAVAILABLE로 뒤집히므로 startswith를 유지한다.
    """
    probe = _WHITESPACE_PATTERN.sub("", note)
    candidates = [probe]
    if probe.startswith(_SUBJECT_PREFIX):
        candidates.append(probe[len(_SUBJECT_PREFIX) :])
    for candidate in candidates:
        if candidate.startswith(_UNAVAILABLE_PREFIXES):
            return ParkingAvailability.UNAVAILABLE
        if candidate.startswith(_AVAILABLE_PREFIXES):
            return ParkingAvailability.AVAILABLE
    # 실측에서 여기로 떨어지는 값은 `지하 3층 부터 5층 까지 이용 가능` 1건뿐이다.
    # 문장 끝의 `가능`을 주워 담으려면 포함 검사가 필요한데, 그 대가로 위 주석의
    # 뒤집힘을 떠안게 되므로 1건은 UNKNOWN으로 둔다.
    return ParkingAvailability.UNKNOWN


__all__ = ["ParkingAvailability", "ParkingInfo", "normalize_parking"]
