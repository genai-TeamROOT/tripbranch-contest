"""집중률 장소 매핑 CSV 적재 입력 검증 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.import_concentration_mappings import (
    latest_mapping_csv_by_district,
    load_mapping_payloads,
)

# 매핑 CSV는 구마다 한 장씩 쌓이고 생성일이 파일명에 붙어 재생성마다 바뀐다.
# 최신 한 장만 보면 다른 구의 CSV가 검증 없이 남으므로 구별 최신본을 전부 훑는다.
# 같은 구의 옛 날짜 파일은 이력이라 대상이 아니다 - 검색어 컬럼(D-057) 이전에
# 만든 CSV는 지금 형식으로 적재되지 않는다.
_MAPPING_CSVS = list(latest_mapping_csv_by_district().values())

# 청와대는 정확 일치를 살리면서 별칭도 인정해야 하는 대표 사례라 값을 못 박는다.
# 종로구 CSV에만 있으므로 그 파일을 찾아 확인한다(D-057).
_BLUE_HOUSE_CONTENT_ID = "126533"


def test_repository_has_mapping_csv() -> None:
    assert _MAPPING_CSVS, "supabase/data에 매핑 CSV가 없습니다."


@pytest.mark.parametrize("csv_path", _MAPPING_CSVS, ids=lambda path: path.stem)
def test_mapping_csv_builds_unique_matched_payloads(csv_path: Path) -> None:
    """저장소에 있는 매핑 CSV가 구별로 그대로 적재 가능한 형태인지 확인한다."""
    payloads, csv_row_count, unmatched_count = load_mapping_payloads(csv_path)

    assert payloads
    assert csv_row_count == len(payloads) + unmatched_count
    # content_id가 PK라 중복이 있으면 적재가 실패한다.
    assert len({str(payload["content_id"]) for payload in payloads}) == len(payloads)
    # 검색어는 공백이 있으면 조회가 0건이 된다(D-043). 목록의 모든 원소에 적용된다(D-057).
    assert all(
        not any(character.isspace() for character in str(key))
        for payload in payloads
        for key in payload["concentration_search_keys"]
    )
    # 조회할 값이 하나도 없으면 매핑이 있어도 혼잡도를 못 받는다.
    assert all(payload["concentration_search_keys"] for payload in payloads)


def test_blue_house_keeps_exact_match_with_alias() -> None:
    """종로구 매핑에서 청와대가 별칭까지 함께 적재되는지 확인한다."""
    for csv_path in _MAPPING_CSVS:
        payloads, _, _ = load_mapping_payloads(csv_path)
        blue_house = next(
            (
                payload
                for payload in payloads
                if payload["content_id"] == _BLUE_HOUSE_CONTENT_ID
            ),
            None,
        )
        if blue_house is None:
            continue
        assert blue_house["primary_concentration_name"] == "청와대 앞길"
        assert blue_house["concentration_aliases"] == ["청와대"]
        assert blue_house["match_method"] == "exact_with_alias"
        return
    pytest.fail(f"content_id {_BLUE_HOUSE_CONTENT_ID}를 담은 매핑 CSV가 없습니다.")


def test_duplicate_matched_content_id_is_rejected(tmp_path: Path) -> None:
    csv_path = tmp_path / "duplicate.csv"
    csv_path.write_text(
        "content_id,place_title,concentration_title,concentration_search_keys,"
        "concentration_aliases,match_status,match_method,confidence_score,verified_at\n"
        '1,장소,집중률 장소,"[""집중률""]",[],matched,exact,1.0000,\n'
        '1,장소,별칭 장소,"[""별칭""]",[],matched,manual,0.8000,\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="content_id가 중복"):
        load_mapping_payloads(csv_path)


def test_blank_alias_is_rejected(tmp_path: Path) -> None:
    csv_path = tmp_path / "blank-alias.csv"
    csv_path.write_text(
        "content_id,place_title,concentration_title,concentration_search_keys,"
        "concentration_aliases,match_status,match_method,confidence_score,verified_at\n"
        '1,장소,집중률 장소,"[""집중률""]","["" ""]",matched,exact_with_alias,1.0000,\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="빈 concentration alias"):
        load_mapping_payloads(csv_path)
