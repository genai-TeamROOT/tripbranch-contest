from datetime import datetime

from app.services.runtime.info_display import (
    format_citydata_timestamp,
    format_parking_for_display,
    parse_citydata_timestamp,
)


def test_format_parking_for_display_keeps_only_car_capacity() -> None:
    assert format_parking_for_display("가능 (승용차 240대 / 버스 50대)") == "가능 (승용차 240대)"


def test_format_parking_for_display_keeps_unknown_shape_unchanged() -> None:
    assert format_parking_for_display("가능") == "가능"


def test_format_citydata_timestamp_normalizes_compact_and_iso_shapes() -> None:
    assert format_citydata_timestamp("20260820 1520") == "8월 20일 15:20"
    assert format_citydata_timestamp("2026-08-20 15:20") == "8월 20일 15:20"


def test_parse_citydata_timestamp_reads_compact_and_iso_shapes() -> None:
    assert parse_citydata_timestamp("20260820 1520") == datetime(2026, 8, 20, 15, 20)
    assert parse_citydata_timestamp("2026-08-20 15:20") == datetime(2026, 8, 20, 15, 20)


def test_parse_citydata_timestamp_returns_none_without_time() -> None:
    # 날짜만 있으면 시간 차이를 구할 수 없다.
    assert parse_citydata_timestamp("2026-08-20") is None


def test_parse_citydata_timestamp_returns_none_for_unparseable_value() -> None:
    assert parse_citydata_timestamp("모름") is None
    assert parse_citydata_timestamp(None) is None
