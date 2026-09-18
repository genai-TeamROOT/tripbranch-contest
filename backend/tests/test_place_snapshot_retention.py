"""스냅샷 보관 개수 정리와 갱신 이력.

`select_prunable`은 "무엇을 지울 수 있는가"를 정하는 유일한 자리다. 미리보기와
실제 정리가 같은 함수를 쓰므로, 화면이 보여준 목록과 지워진 파일이 갈라지지
않는다. 그래서 여기서 못 박아야 할 것은 개수보다 **후보에 무엇이 올라오지
않는가**다 — 같은 디렉터리에 이름 규칙 밖의 자료가 섞여 있다.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.services import place_snapshot
from app.services.place_snapshot import KST


def _touch(directory: Path, name: str) -> Path:
    path = directory / name
    path.write_text("content_id\n", encoding="utf-8")
    return path


def _snapshots(directory: Path, region: str, dates: list[str]) -> None:
    for date in dates:
        _touch(directory, f"places_api_snapshot_{region}_{date}.csv")


def test_오래된_것부터_돌려주고_최근_것은_남긴다(tmp_path: Path) -> None:
    _snapshots(tmp_path, "11-110", ["20260820", "20260822", "20260825", "20260828"])

    prunable = place_snapshot.select_prunable(
        tmp_path, area_code="11", district_code="110", keep=2
    )

    assert [path.name for path in prunable] == [
        "places_api_snapshot_11-110_20260820.csv",
        "places_api_snapshot_11-110_20260822.csv",
    ]


def test_보관_개수보다_적으면_지울_것이_없다(tmp_path: Path) -> None:
    _snapshots(tmp_path, "11-110", ["20260828"])

    assert (
        place_snapshot.select_prunable(
            tmp_path, area_code="11", district_code="110", keep=2
        )
        == []
    )


def test_keep이_0이면_아무것도_지우지_않는다(tmp_path: Path) -> None:
    """스냅샷이 0개가 되면 다음 대조가 기준을 잃고 전량을 신규로 잡는다.

    용산구 486건이면 그 한 번으로 하루 한도 1,000회의 절반을 쓴다. 0을 받아
    비우는 것보다 아무것도 안 하는 편이 낫다.
    """
    _snapshots(tmp_path, "11-110", ["20260820", "20260822", "20260825"])

    assert (
        place_snapshot.select_prunable(
            tmp_path, area_code="11", district_code="110", keep=0
        )
        == []
    )


def test_다른_구와_이름_규칙_밖의_파일은_후보에_오르지_않는다(tmp_path: Path) -> None:
    _snapshots(tmp_path, "11-110", ["20260820", "20260822", "20260825"])
    # 다른 구. 섞이면 남의 구 기준을 지운다.
    _snapshots(tmp_path, "11-140", ["20260820", "20260822"])
    # 이름 규칙 밖의 자료가 같은 디렉터리에 있다.
    _touch(tmp_path, "seongdong_places.csv")
    _touch(tmp_path, "yongsan_places.csv")
    _touch(tmp_path, "concentration_place_mapping_11110_20260821.csv")
    # 구가 이름에 없는 옛 스냅샷. 어느 구 것인지 알 수 없다.
    _touch(tmp_path, "places_api_snapshot_20260810.csv")

    prunable = place_snapshot.select_prunable(
        tmp_path, area_code="11", district_code="110", keep=1
    )

    assert [path.name for path in prunable] == [
        "places_api_snapshot_11-110_20260820.csv",
        "places_api_snapshot_11-110_20260822.csv",
    ]


def test_대조_결과도_같은_규칙으로_고른다(tmp_path: Path) -> None:
    for date in ["20260820", "20260822", "20260825"]:
        _touch(tmp_path, f"places_reconciliation_11-110_{date}.csv")
    # 스냅샷이 섞이면 안 된다 — 대조 결과를 지우려다 기준을 지우게 된다.
    _snapshots(tmp_path, "11-110", ["20260820", "20260822", "20260825"])

    prunable = place_snapshot.select_prunable(
        tmp_path,
        area_code="11",
        district_code="110",
        keep=1,
        prefix=place_snapshot.RECONCILIATION_PREFIX,
    )

    assert [path.name for path in prunable] == [
        "places_reconciliation_11-110_20260820.csv",
        "places_reconciliation_11-110_20260822.csv",
    ]


def test_이력_파일이_없으면_설명과_표_머리를_함께_만든다(tmp_path: Path) -> None:
    path = place_snapshot.append_history_row(
        {
            "일시": f"{datetime(2026, 8, 30, 9, 12, tzinfo=KST):%Y-%m-%d %H:%M}",
            "구": "종로구 11-110",
            "종류": "대조",
            "기준 스냅샷": "places_api_snapshot_11-110_20260825.csv",
            "신규": 3,
            "수정": 12,
            "삭제": 1,
            "상세조회": 15,
            "비고": "새 스냅샷 places_api_snapshot_11-110_20260830.csv (843건)",
        },
        tmp_path,
    )

    text = path.read_text(encoding="utf-8")
    assert path.name == place_snapshot.HISTORY_FILE_NAME
    assert text.startswith("# 스냅샷 갱신 이력")
    assert "| 일시 | 구 | 종류 |" in text
    assert text.rstrip().endswith(
        "| 2026-08-30 09:12 | 종로구 11-110 | 대조 | "
        "places_api_snapshot_11-110_20260825.csv | 3 | 12 | 1 | 15 | "
        "새 스냅샷 places_api_snapshot_11-110_20260830.csv (843건) |"
    )


def test_이력은_끝에_덧붙고_앞_줄을_건드리지_않는다(tmp_path: Path) -> None:
    for index in range(3):
        place_snapshot.append_history_row(
            {"일시": "2026-08-30 09:12", "구": f"구{index}", "종류": "대조"},
            tmp_path,
        )

    lines = (tmp_path / place_snapshot.HISTORY_FILE_NAME).read_text(
        encoding="utf-8"
    ).splitlines()
    rows = [line for line in lines if line.startswith("| 2026-08-30")]
    assert [row.split(" | ")[1] for row in rows] == ["구0", "구1", "구2"]


def test_파이프가_든_값은_표를_깨뜨리지_않는다(tmp_path: Path) -> None:
    """장소 제목에 파이프가 들어오면 표의 열 수가 어긋난다."""
    place_snapshot.append_history_row(
        {"일시": "2026-08-30 09:12", "구": "종로구", "비고": "a | b"},
        tmp_path,
    )

    row = [
        line
        for line in (tmp_path / place_snapshot.HISTORY_FILE_NAME)
        .read_text(encoding="utf-8")
        .splitlines()
        if line.startswith("| 2026-08-30")
    ][0]
    assert row.count(" | ") == len(place_snapshot.HISTORY_COLUMNS) - 1
    assert r"a \| b" in row
