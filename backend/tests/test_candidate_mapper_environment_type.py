"""candidate_mapper._environment_type()의 중분류(lcls_systm2) 판정표를 고정한다.

판정 근거: package_D/feature-environment-type-classification.md §2 (D-046).
D가 다루는 6개 content_type(12/14/15/28/38/39)의 48개 중분류 전부를 대표
소분류(lcls_systm3) 1건씩으로 조회해 기대 environment_type과 비교한다. 이
표가 바뀌면(indoor/outdoor 재분류, 중분류 추가/삭제) 이 테스트가 실패로 잡는다.
"""

from __future__ import annotations

import pytest

from app.domain.candidate_mapper import _environment_type

# (content_type_id, lcls_systm2, 대표 lcls_systm3, 기대 environment_type)
_MIDDLE_CATEGORY_CASES: tuple[tuple[str, str, str, str], ...] = (
    # content_type=12 (attraction)
    ("12", "EX01", "EX010100", "unknown"),  # 전통체험
    ("12", "EX02", "EX020100", "indoor"),  # 공예체험
    ("12", "EX03", "EX030100", "outdoor"),  # 농.산.어촌 체험
    ("12", "EX04", "EX040100", "unknown"),  # 산사체험
    ("12", "EX05", "EX050100", "indoor"),  # 웰니스관광
    ("12", "EX06", "EX060100", "indoor"),  # 산업관광
    ("12", "EX07", "EX070100", "unknown"),  # 기타체험
    ("12", "HS01", "HS010100", "outdoor"),  # 역사유적지
    ("12", "HS02", "HS020100", "outdoor"),  # 역사유물
    ("12", "HS03", "HS030100", "unknown"),  # 종교성지
    ("12", "HS04", "HS040100", "outdoor"),  # 안보관광지
    ("12", "NA01", "NA010100", "outdoor"),  # 자연경관(산)
    ("12", "NA02", "NA020100", "outdoor"),  # 자연경관(하천‧해양)
    ("12", "NA03", "NA030100", "outdoor"),  # 자연생태
    ("12", "NA04", "NA040100", "outdoor"),  # 자연공원
    ("12", "NA05", "NA050100", "outdoor"),  # 기타자연관광
    ("12", "VE01", "VE010100", "outdoor"),  # 랜드마크관광
    ("12", "VE02", "VE020100", "unknown"),  # 테마공원
    ("12", "VE03", "VE030100", "outdoor"),  # 도시공원
    ("12", "VE04", "VE040100", "outdoor"),  # 도시.지역문화관광
    ("12", "VE05", "VE050100", "unknown"),  # 복합관광시설
    # content_type=14 (cultural_facility) — 전부 indoor
    ("14", "VE06", "VE060100", "indoor"),  # 공연시설
    ("14", "VE07", "VE070100", "indoor"),  # 전시시설
    ("14", "VE08", "VE080600", "indoor"),  # 행사시설
    ("14", "VE09", "VE090100", "indoor"),  # 교육시설
    ("14", "VE12", "VE120100", "indoor"),  # 기타문화관광지(서점)
    # content_type=15 (festival) — 전부 unknown
    ("15", "EV01", "EV010100", "unknown"),  # 축제
    ("15", "EV02", "EV020100", "unknown"),  # 공연
    ("15", "EV03", "EV030100", "unknown"),  # 행사
    # content_type=28 (leisure)
    ("28", "AC05", "AC050100", "outdoor"),  # 캠핑
    ("28", "LS01", "LS010100", "unknown"),  # 육상레저스포츠
    ("28", "LS02", "LS020100", "outdoor"),  # 수상레저스포츠
    ("28", "LS03", "LS030100", "outdoor"),  # 항공레저스포츠
    ("28", "LS04", "LS040100", "unknown"),  # 복합레저스포츠
    ("28", "VE10", "VE100100", "unknown"),  # 레저스포츠시설
    ("28", "VE12", "VE120200", "indoor"),  # 기타문화관광지(카지노)
    # content_type=38 (shopping)
    ("38", "SH01", "SH010100", "indoor"),  # 백화점
    ("38", "SH02", "SH020100", "unknown"),  # 쇼핑몰
    ("38", "SH03", "SH030100", "indoor"),  # 대형마트
    ("38", "SH04", "SH040100", "indoor"),  # 면세점
    ("38", "SH05", "SH050100", "indoor"),  # 전문매장/상가
    ("38", "SH06", "SH060100", "outdoor"),  # 시장
    ("38", "SH07", "SH070100", "indoor"),  # 기타쇼핑시설
    # content_type=39 (restaurant) — 전부 indoor
    ("39", "FD01", "FD010100", "indoor"),  # 한식
    ("39", "FD02", "FD020100", "indoor"),  # 외국식
    ("39", "FD03", "FD030100", "indoor"),  # 간이음식
    ("39", "FD04", "FD040100", "indoor"),  # 주점
    ("39", "FD05", "FD050100", "indoor"),  # 카페/찻집
)


@pytest.mark.parametrize(
    ("content_type_id", "lcls_systm2", "lcls_systm3", "expected"),
    _MIDDLE_CATEGORY_CASES,
    ids=[f"{c[0]}_{c[1]}" for c in _MIDDLE_CATEGORY_CASES],
)
def test_environment_type_matches_middle_category_table(
    content_type_id: str,
    lcls_systm2: str,
    lcls_systm3: str,
    expected: str,
) -> None:
    # category 인자는 lcls_systm3가 해석되면 쓰이지 않으므로 임의값으로 둔다.
    assert _environment_type("unknown", lcls_systm3) == expected


def test_middle_category_table_covers_all_48_supported_combinations() -> None:
    """리소스 파일의 D 대상 content_type 중분류 개수(48개)와 정확히 일치해야 한다."""
    assert len(_MIDDLE_CATEGORY_CASES) == 48
    assert len({(c[0], c[1]) for c in _MIDDLE_CATEGORY_CASES}) == 48
