"""TourAPI 법정동코드(시도·시군구)에 붙일 이름을 조회한다.

역할: `lDongRegnCd`/`lDongSignguCd` 코드 쌍을 사람이 읽는 이름으로 바꾼다.
입력: 지역 코드와 시군구 코드.
출력: 시군구 이름. 자료에 없는 코드면 None.
호출 시점: 개발자 Ops 패널이 구별 요약에 이름을 붙일 때.

이름은 `resources/tour_api/tour_api_ldong_codes.json`에서 읽는다. TourAPI가
인정하는 코드 집합이 행정표준코드와 1:1로 대응하지 않아 규칙으로 유추할 수
없다(그 차이는 같은 디렉터리의 README.md에 적혀 있다). 정적 자료라 프로세스당
한 번만 읽는다.

이름을 못 찾으면 예외를 던지지 않고 None을 준다 — 표시용 이름이 없다는 이유로
DB 상태 조회 전체가 실패할 이유는 없다. 화면은 코드를 그대로 보여주면 된다.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_RESOURCE_PATH = (
    Path(__file__).resolve().parents[2]
    / "resources"
    / "tour_api"
    / "tour_api_ldong_codes.json"
)


@lru_cache(maxsize=1)
def _district_names() -> dict[tuple[str, str], str]:
    with _RESOURCE_PATH.open(encoding="utf-8") as fp:
        payload = json.load(fp)
    names: dict[tuple[str, str], str] = {}
    for region in payload.get("regions", []):
        region_code = str(region.get("code") or "").strip()
        for district in region.get("districts", []):
            district_code = str(district.get("code") or "").strip()
            name = str(district.get("name") or "").strip()
            if region_code and district_code and name:
                names[(region_code, district_code)] = name
    return names


def find_district_name(area_code: str, district_code: str) -> str | None:
    """코드 쌍에 해당하는 시군구 이름. 자료에 없으면 None."""
    return _district_names().get((area_code.strip(), district_code.strip()))


def list_districts(area_code: str) -> list[dict[str, str]]:
    """한 시도의 시군구를 코드순으로. 개발자 패널이 고를 수 있는 구의 사전이다.

    화면이 이 목록으로 입력을 검증한다 — 없는 코드로 동기화를 걸면 TourAPI가 빈
    목록을 돌려주고, 그 결과는 "장소가 0건인 구"와 구분되지 않는다.
    """
    target = area_code.strip()
    return [
        {"area_code": area, "district_code": district, "district_name": name}
        for (area, district), name in sorted(_district_names().items())
        if area == target
    ]
