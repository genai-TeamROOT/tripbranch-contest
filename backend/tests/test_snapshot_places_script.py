from __future__ import annotations

from datetime import datetime

from app.services.place_snapshot import (
    COMPARED_COLUMNS as _COMPARED_COLUMNS,
)
from app.services.place_snapshot import (
    KST,
    build_reconciliation_rows,
    comparable_columns,
    find_baseline,
    list_snapshots,
    snapshot_file_name,
    snapshot_regions,
    write_snapshot,
)


def _row(**overrides: str) -> dict[str, str]:
    row = {
        "content_id": "1",
        "content_type_id": "12",
        "title": "경복궁",
        "address": "서울특별시 종로구 사직로 161",
        "latitude": "37.5788222",
        "longitude": "126.9770162",
        "area_code": "11",
        "district_code": "110",
        "lcls_systm1": "VE",
        "lcls_systm2": "VE01",
        "lcls_systm3": "VE010100",
        # 실제 스냅샷은 provider가 파싱한 datetime을 ISO로 쓴다.
        "source_modified_at": "2026-07-23T15:30:45+09:00",
        "first_image_url": "https://example.test/a.jpg",
        "thumbnail_url": "https://example.test/a_thumb.jpg",
        "list_fetched_at": "2026-08-08T13:00:00+09:00",
    }
    row.update(overrides)
    return row


def test_old_baseline_without_image_columns_is_not_all_updated() -> None:
    """열을 추가해도 과거 스냅샷과의 대조가 전량 updated로 무너지지 않는다.

    이미지 2열은 D-056에서 추가됐다. 기준 스냅샷에 그 열이 없으면 비교에서 빼야
    실제로 바뀐 장소만 잡힌다.
    """
    baseline_row = _row()
    del baseline_row["first_image_url"]
    del baseline_row["thumbnail_url"]
    baseline = {"1": baseline_row}
    current = {"1": _row()}

    compared = comparable_columns(list(baseline_row))

    assert "first_image_url" not in compared
    assert build_reconciliation_rows(baseline, current, compared) == []


def test_skipped_columns_are_reported_not_silently_dropped() -> None:
    """건너뛴 열은 호출자가 출력할 수 있도록 드러나야 한다.

    조용히 빼면 결과 파일에서 "안 바뀌었다"와 "안 봤다"가 구분되지 않는다.
    """
    baseline_row = _row()
    del baseline_row["first_image_url"]
    del baseline_row["thumbnail_url"]

    compared = comparable_columns(list(baseline_row))
    skipped = [column for column in _COMPARED_COLUMNS if column not in compared]

    assert skipped == ["first_image_url", "thumbnail_url"]


def test_image_change_is_detected_when_baseline_has_the_columns() -> None:
    """열이 양쪽에 있으면 이미지 변경도 정상적으로 잡힌다."""
    baseline = {"1": _row()}
    current = {"1": _row(thumbnail_url="https://example.test/b_thumb.jpg")}

    compared = comparable_columns(list(baseline["1"]))
    rows = build_reconciliation_rows(baseline, current, compared)

    assert len(rows) == 1
    assert rows[0]["change_type"] == "updated"
    assert rows[0]["changed_columns"] == ["thumbnail_url"]


def test_list_fetched_at_never_counts_as_a_change() -> None:
    """조회 시각은 항상 달라지므로 비교 대상이 아니다."""
    baseline = {"1": _row()}
    current = {"1": _row(list_fetched_at="2026-08-09T09:00:00+09:00")}

    compared = comparable_columns(list(baseline["1"]))

    assert build_reconciliation_rows(baseline, current, compared) == []


def test_snapshot_csv_round_trips_image_columns(tmp_path) -> None:
    """스냅샷에 쓴 이미지 URL이 sync 쪽 로더까지 살아서 돌아오는지 본다.

    쓰기만 고치고 읽기를 빼면 CSV에는 값이 있는데 DB는 계속 비어 있다(2026-08-08에
    실제로 발생). 왕복으로 묶어 한쪽만 고치는 실수를 막는다.
    """
    from app.services.place_snapshot import records_from_snapshot, write_snapshot

    path = tmp_path / "snapshot.csv"
    write_snapshot({"1": _row()}, path)
    records = records_from_snapshot(path)

    assert records[0].first_image_url == "https://example.test/a.jpg"
    assert records[0].thumbnail_url == "https://example.test/a_thumb.jpg"


def test_loader_accepts_old_snapshot_without_image_columns(tmp_path) -> None:
    """이미지 열이 없던 옛 스냅샷도 그대로 읽힌다(값은 None)."""
    import csv as _csv

    from app.services.place_snapshot import records_from_snapshot

    row = _row()
    del row["first_image_url"]
    del row["thumbnail_url"]
    path = tmp_path / "old_snapshot.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = _csv.DictWriter(fp, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)

    records = records_from_snapshot(path)

    assert records[0].first_image_url is None
    assert records[0].thumbnail_url is None


def test_snapshot_file_name_puts_district_before_date() -> None:
    """구가 앞, 날짜가 뒤. 같은 구 안에서는 이름순이 곧 날짜순이어야 한다."""
    when = datetime(2026, 8, 21, tzinfo=KST)

    assert (
        snapshot_file_name("11", "170", when)
        == "places_api_snapshot_11-170_20260821.csv"
    )
    older = snapshot_file_name("11", "170", datetime(2026, 8, 20, tzinfo=KST))
    assert older < snapshot_file_name("11", "170", when)


def test_find_baseline_never_picks_another_district(tmp_path) -> None:
    """다른 구 스냅샷이 더 최신이어도 기준으로 잡히지 않는다.

    2026-08-20에 중구를 종로구 스냅샷과 대조해 "삭제 844건"이 나왔다. 그 844건은
    폐업이 아니라 전부 종로구 장소였고, 대조 결과 자체가 의미 없는 값이었다.
    """
    write_snapshot(
        {"1": _row()},
        tmp_path / snapshot_file_name("11", "110", datetime(2026, 8, 21, tzinfo=KST)),
    )
    junggu_old = tmp_path / snapshot_file_name(
        "11", "140", datetime(2026, 8, 10, tzinfo=KST)
    )
    write_snapshot({"2": _row(content_id="2", district_code="140")}, junggu_old)

    baseline = find_baseline(tmp_path, area_code="11", district_code="140")

    assert baseline == junggu_old


def test_find_baseline_returns_none_for_a_district_without_snapshots(tmp_path) -> None:
    """스냅샷이 없는 구는 '기준 없음'이 된다 — 남의 구를 끌어다 쓰지 않는다."""
    write_snapshot(
        {"1": _row()},
        tmp_path / snapshot_file_name("11", "110", datetime(2026, 8, 21, tzinfo=KST)),
    )

    assert find_baseline(tmp_path, area_code="11", district_code="170") is None


def test_old_snapshot_name_without_district_is_not_used_as_baseline(tmp_path) -> None:
    """구가 없는 옛 이름은 어느 구의 기준으로도 잡히지 않는다.

    파일이 남아 있어도 '기준 없음'이 될 뿐, 다른 구와 섞이지는 않는다.
    """
    write_snapshot({"1": _row()}, tmp_path / "places_api_snapshot_20260810.csv")

    assert find_baseline(tmp_path, area_code="11", district_code="110") is None
    # 목록 조회는 구를 안 주면 옛 파일도 그대로 보여준다(무엇이 있는지 알아야 한다).
    assert [path.name for path in list_snapshots(tmp_path)] == [
        "places_api_snapshot_20260810.csv"
    ]


def test_snapshot_regions_reads_content_not_file_name() -> None:
    """스냅샷이 무엇을 담고 있는지는 행의 district_code가 말한다."""
    snapshot = {
        "1": _row(),
        "2": _row(content_id="2", district_code="140"),
    }

    assert snapshot_regions(snapshot) == {("11", "110"), ("11", "140")}
