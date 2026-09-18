"""집중률 응답 정규화에 사용하는 공통 정책."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeGuard

# 집중률이 20% 미만이면 한적한 상태로 본다.
CONCENTRATION_QUIET_MAX_EXCLUSIVE = 20.0

# 집중률이 50% 미만이면 보통 상태로 본다.
CONCENTRATION_NORMAL_MAX_EXCLUSIVE = 50.0

# 집중률이 70% 미만이면 다소 혼잡한 상태로 본다.
CONCENTRATION_SLIGHTLY_CROWDED_MAX_EXCLUSIVE = 70.0

# INFO 단일 장소 질의에서만 쓰는 대체 관광지 탐색 반경이다. 추천 후보 수집의
# 기본 반경과 구분하며, 실측 결과에 따라 조정할 수 있도록 정책 상수로 둔다.
INFO_CONCENTRATION_FALLBACK_RADIUS_KM = 0.5

# 집중률 API의 광역 코드. 지원 구가 전부 서울특별시라 고정이다 - 25개 구로 넓혀도
# 바뀌지 않는다. 구는 고정하지 않는다(장소마다 다르다).
CONCENTRATION_AREA_CODE = "11"


def concentration_signgu_code(district_code: str | None) -> str | None:
    """places.district_code(3자리 "140")를 집중률 API signguCd(5자리 "11140")로 바꾼다.

    두 값은 같은 법정동 코드의 다른 자리다 - TourAPI 응답의 lDongSignguCd는 시군구
    3자리만 담고, 집중률 API의 signguCd는 앞에 시도 2자리(lDongRegnCd)를 붙인 5자리다
    (app/service_area.py의 ServiceDistrict 주석 참고).

    구를 모르면 None을 돌려준다. 이때 종로구로 대신 물어보면 안 된다 - 다른 구
    장소를 종로구로 물으면 응답이 0건이라, 조회에 실패한 것이 "혼잡도 정보 없음"과
    구분되지 않고 조용히 섞인다(D-095).
    """
    if not district_code:
        return None
    return f"{CONCENTRATION_AREA_CODE}{district_code}"


# 대체 조회에서 순서대로 시도할 최대 장소 수다. 매핑에 이름이 있어도 집중률 API
# 조회가 실패할 수 있어(표기 차이·API 갱신) 한 곳만 보고 포기하지 않는다.
# 실측(2026-08-03): 매핑 100건 중 30건이 조회 실패, 안국역은 2번째 후보에서 성공.
INFO_CONCENTRATION_FALLBACK_ATTEMPT_LIMIT = 3


class ConcentrationLevel(StrEnum):
    """A–C 계약에서 사용하는 집중률 단계 코드."""

    QUIET = "quiet"
    NORMAL = "normal"
    SLIGHTLY_CROWDED = "slightly_crowded"
    CROWDED = "crowded"


class ConcentrationLabel(StrEnum):
    """사용자 응답에 표시할 집중률 단계명."""

    QUIET = "한적함"
    NORMAL = "보통"
    SLIGHTLY_CROWDED = "다소 혼잡"
    CROWDED = "혼잡"


@dataclass(frozen=True)
class NormalizedConcentration:
    level: ConcentrationLevel
    label: ConcentrationLabel


def is_valid_concentration_rate(rate: float | None) -> TypeGuard[float]:
    """음수가 아닌 유한 숫자만 상대 집중률로 사용한다."""

    return rate is not None and math.isfinite(rate) and rate >= 0


def normalize_concentration(rate: float) -> NormalizedConcentration:
    """평시 대비 상대 집중률을 합의된 네 단계로 변환한다."""

    if not is_valid_concentration_rate(rate):
        raise ValueError("집중률은 음수가 아닌 유한 숫자여야 합니다.")
    if rate < CONCENTRATION_QUIET_MAX_EXCLUSIVE:
        return NormalizedConcentration(
            ConcentrationLevel.QUIET,
            ConcentrationLabel.QUIET,
        )
    if rate < CONCENTRATION_NORMAL_MAX_EXCLUSIVE:
        return NormalizedConcentration(
            ConcentrationLevel.NORMAL,
            ConcentrationLabel.NORMAL,
        )
    if rate < CONCENTRATION_SLIGHTLY_CROWDED_MAX_EXCLUSIVE:
        return NormalizedConcentration(
            ConcentrationLevel.SLIGHTLY_CROWDED,
            ConcentrationLabel.SLIGHTLY_CROWDED,
        )
    return NormalizedConcentration(
        ConcentrationLevel.CROWDED,
        ConcentrationLabel.CROWDED,
    )


__all__ = [
    "CONCENTRATION_NORMAL_MAX_EXCLUSIVE",
    "CONCENTRATION_QUIET_MAX_EXCLUSIVE",
    "CONCENTRATION_SLIGHTLY_CROWDED_MAX_EXCLUSIVE",
    "INFO_CONCENTRATION_FALLBACK_RADIUS_KM",
    "ConcentrationLabel",
    "ConcentrationLevel",
    "NormalizedConcentration",
    "is_valid_concentration_rate",
    "normalize_concentration",
]
