"""거절 목록과 적재 payload.

거절 목록은 사람이 "이건 붙이지 마라"고 판정한 것을 파일로 남긴다. 남기지 않으면
다음 생성 때 같은 후보가 다시 올라와 매번 같은 판정을 되풀이해야 한다.

payload는 CSV 경로(`scripts/import_concentration_mappings.py`)와 화면 경로가 같은
테이블에 쓰므로, 두 경로가 만드는 모양이 같은지를 여기서 묶어 둔다. 갈라지면 어느
쪽으로 적재했느냐에 따라 혼잡도 조회 결과가 달라진다.
"""

from __future__ import annotations

from pathlib import Path

from app.services.concentration_mapping import (
    ManualOverride,
    MappingRow,
    PlaceRow,
    Rejection,
    append_rejections,
    load_rejections,
    mapping_payload,
    match_places,
)
from scripts.import_concentration_mappings import load_mapping_payloads


def _rejection_file(tmp_path: Path) -> Path:
    return tmp_path / "concentration_rejections.csv"


def test_거절한_짝은_다음_매칭에서_빠진다() -> None:
    places = [PlaceRow("1", "북촌생활사박물관"), PlaceRow("2", "경복궁")]
    names = ["북촌생활사박물관", "경복궁"]

    matched, unmatched, _ = match_places(
        places,
        names,
        rejections={("북촌생활사박물관", "북촌생활사박물관"): Rejection(
            "북촌생활사박물관", "북촌생활사박물관"
        )},
    )

    assert [row.place_title for row in matched] == ["경복궁"]
    assert [place.title for place in unmatched] == ["북촌생활사박물관"]


def test_수동_지정은_거절보다_세다() -> None:
    """손으로 붙이라고 적어둔 것을 거절 목록이 뒤집으면 어느 쪽이 이기는지 알 수 없다."""
    places = [PlaceRow("1", "낙산묘각사")]
    names = ["묘각사(서울)"]

    matched, unmatched, _ = match_places(
        places,
        names,
        overrides={"낙산묘각사": ManualOverride(primary="묘각사(서울)", aliases=())},
        rejections={("낙산묘각사", "묘각사(서울)"): Rejection("낙산묘각사", "묘각사(서울)")},
    )

    assert [row.concentration_title for row in matched] == ["묘각사(서울)"]
    assert unmatched == []


def test_거절은_장소와_이름_짝으로_본다() -> None:
    """장소만으로 거절하면 그 장소는 영영 어떤 이름에도 못 붙는다.

    나중에 집중률 API에 맞는 이름이 생기면 그건 다시 후보로 올라와야 한다.
    """
    places = [PlaceRow("1", "종묘")]
    names = ["종묘 [유네스코 세계유산]"]

    matched, _, _ = match_places(
        places,
        names,
        # 다른 이름에 대한 거절이라 이번 매칭에는 걸리지 않는다.
        rejections={("종묘", "종묘광장공원"): Rejection("종묘", "종묘광장공원")},
    )

    assert [row.concentration_title for row in matched] == ["종묘 [유네스코 세계유산]"]


def test_거절_목록을_파일로_읽고_쓴다(tmp_path: Path) -> None:
    path = _rejection_file(tmp_path)

    added = append_rejections(
        [Rejection("북촌생활사박물관", "북촌", "북촌한옥마을과 다른 장소")], path
    )

    assert [rejection.place_title for rejection in added] == ["북촌생활사박물관"]
    loaded = load_rejections(path)
    assert loaded[("북촌생활사박물관", "북촌")].note == "북촌한옥마을과 다른 장소"


def test_같은_짝을_다시_거절해도_파일이_늘지_않는다(tmp_path: Path) -> None:
    path = _rejection_file(tmp_path)
    append_rejections([Rejection("가", "나")], path)

    added = append_rejections([Rejection("가", "나"), Rejection("다", "라")], path)

    assert [rejection.place_title for rejection in added] == ["다"]
    assert len(load_rejections(path)) == 2


def test_파일이_없으면_거절이_없는_것으로_본다(tmp_path: Path) -> None:
    assert load_rejections(tmp_path / "없는파일.csv") == {}


def test_화면_적재와_CSV_적재가_같은_행을_만든다(tmp_path: Path) -> None:
    """두 경로가 같은 테이블에 쓴다. 열이 갈라지면 적재 경로에 따라 조회가 달라진다."""
    row = MappingRow(
        content_id="126508",
        place_title="경복궁",
        concentration_title="경복궁",
        match_method="exact",
        aliases=("景福宮",),
        search_key=None,
        search_keys=("경복궁",),
    )
    from app.services.concentration_mapping import write_mapping_csv

    csv_path = tmp_path / "concentration_place_mapping_11110_20260830.csv"
    write_mapping_csv([row], csv_path)
    from_csv, _, _ = load_mapping_payloads(csv_path)

    assert mapping_payload(row) == from_csv[0]


def test_검색어가_없으면_정식_명칭을_쓴다() -> None:
    """조회할 값이 없으면 매핑이 있어도 혼잡도를 못 받는다."""
    row = MappingRow("1", "운현궁", "운현궁", "exact")

    assert mapping_payload(row)["concentration_search_keys"] == ["운현궁"]
