"""위경도 <-> 기상청 격자좌표(nx, ny) 변환.

KMA 단기예보 조회서비스(getUltraSrtNcst 등)는 위경도가 아니라 자체 5km 격자
좌표계(Lambert Conformal Conic 투영)를 파라미터로 받는다. 아래 상수와 변환식은
기상청이 공식 배포하는 격자변환 예제(다양한 언어로 이식되어 널리 쓰이는 구현)를
그대로 옮긴 것으로, 임의로 바꾸면 안 된다.
"""

from __future__ import annotations

import math

_RE = 6371.00877  # 지구 반경(km)
_GRID = 5.0  # 격자 간격(km)
_SLAT1 = 30.0  # 투영 위도1(degree)
_SLAT2 = 60.0  # 투영 위도2(degree)
_OLON = 126.0  # 기준점 경도(degree)
_OLAT = 38.0  # 기준점 위도(degree)
_XO = 43  # 기준점 X좌표(GRID)
_YO = 136  # 기준점 Y좌표(GRID)

_DEGRAD = math.pi / 180.0

_re = _RE / _GRID
_slat1 = _SLAT1 * _DEGRAD
_slat2 = _SLAT2 * _DEGRAD
_olon = _OLON * _DEGRAD
_olat = _OLAT * _DEGRAD

_sn = math.tan(math.pi * 0.25 + _slat2 * 0.5) / math.tan(math.pi * 0.25 + _slat1 * 0.5)
_sn = math.log(math.cos(_slat1) / math.cos(_slat2)) / math.log(_sn)
_sf = math.tan(math.pi * 0.25 + _slat1 * 0.5)
_sf = math.pow(_sf, _sn) * math.cos(_slat1) / _sn
_ro = math.tan(math.pi * 0.25 + _olat * 0.5)
_ro = _re * _sf / math.pow(_ro, _sn)


def latlon_to_grid(latitude: float, longitude: float) -> tuple[int, int]:
    """위도/경도를 KMA 격자좌표(nx, ny)로 변환한다."""
    ra = math.tan(math.pi * 0.25 + latitude * _DEGRAD * 0.5)
    ra = _re * _sf / math.pow(ra, _sn)
    theta = longitude * _DEGRAD - _olon
    if theta > math.pi:
        theta -= 2.0 * math.pi
    if theta < -math.pi:
        theta += 2.0 * math.pi
    theta *= _sn

    x = ra * math.sin(theta) + _XO + 0.5
    y = _ro - ra * math.cos(theta) + _YO + 0.5

    return int(x), int(y)
