"""INFO 관광지 혼잡도 차트용 다일 예측 선택 규칙을 검증한다."""

from datetime import date

from app.agent_context.enrichment_service import select_concentration_forecasts
from app.domain.models import ConcentrationForecast, ConcentrationResult


def _result(*forecasts: ConcentrationForecast) -> ConcentrationResult:
    return ConcentrationResult(
        area_code="11",
        district_code="11110",
        requested_place_name="경복궁",
        forecasts=forecasts,
        provider="test",
    )


def _forecast(place_name: str, forecast_date: str, rate: float) -> ConcentrationForecast:
    return ConcentrationForecast(
        place_name=place_name,
        forecast_date=forecast_date,
        concentration_rate=rate,
        raw_data={},
    )


def test_selects_seven_daily_forecasts_from_visit_date_for_exact_place() -> None:
    forecasts = [
        _forecast("경복궁", f"202608{day:02d}", float(day))
        for day in range(20, 29)
    ]
    forecasts.append(_forecast("경복궁역", "20260822", 99.0))

    selected = select_concentration_forecasts(
        _result(*forecasts),
        candidate_name="경복궁",
        start_date=date(2026, 8, 22),
    )

    assert [item.forecast_date for item in selected] == [
        "20260822",
        "20260823",
        "20260824",
        "20260825",
        "20260826",
        "20260827",
        "20260828",
    ]
    assert all(item.place_name == "경복궁" for item in selected)


def test_returns_only_available_dates_when_fewer_than_seven() -> None:
    selected = select_concentration_forecasts(
        _result(
            _forecast("경복궁", "20260822", 42.0),
            _forecast("경복궁", "20260823", 58.0),
        ),
        candidate_name="경복궁",
        start_date=date(2026, 8, 22),
    )

    assert len(selected) == 2
