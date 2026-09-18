"""집중률 예보 선택 규칙을 고정한다.

tAtsNm은 부분 일치 검색이라 한 번에 여러 장소가 딸려 온다(2026-08-04 실측:
"종묘" → 종묘 [유네스코 세계유산] 67.69 + 종묘광장공원 35.28). 잘못 고르면
"많이 붐빔"과 "여유로움"이 뒤바뀐 채 정상 응답으로 나간다.
"""

from __future__ import annotations

from datetime import date

from app.agent_context.enrichment_service import select_concentration_forecast
from app.domain.models import ConcentrationForecast, ConcentrationResult

REFERENCE_DATE = date(2026, 8, 4)


def _forecast(place_name: str, rate: float | None, day: str = "20260804"):
    return ConcentrationForecast(
        place_name=place_name,
        forecast_date=day,
        concentration_rate=rate,
        raw_data={},
    )


def _result(*forecasts: ConcentrationForecast) -> ConcentrationResult:
    return ConcentrationResult(
        area_code="11",
        district_code="11110",
        requested_place_name=None,
        forecasts=forecasts,
        provider="tour_api",
    )


def test_이름이_일치하는_예보를_고른다() -> None:
    result = _result(
        _forecast("종묘광장공원", 35.28),
        _forecast("종묘 [유네스코 세계유산]", 67.69),
    )
    forecast = select_concentration_forecast(
        result,
        candidate_name="종묘 [유네스코 세계유산]",
        reference_date=REFERENCE_DATE,
    )
    assert forecast is not None
    assert forecast.concentration_rate == 67.69


def test_여러_장소가_왔는데_일치하는_이름이_없으면_포기한다() -> None:
    """첫 예보로 폴백하면 엉뚱한 장소의 값을 정상 응답처럼 답하게 된다."""
    result = _result(
        _forecast("서울 종로 낙지볶음 골목", 77.08),
        _forecast("세종로공원", 50.08),
        _forecast("종로6가 대학천 책방거리", 13.37),
    )
    forecast = select_concentration_forecast(
        result, candidate_name="종로", reference_date=REFERENCE_DATE
    )
    assert forecast is None


def test_한_곳만_왔으면_표기가_달라도_그대로_쓴다() -> None:
    """검색어와 정식 명칭이 달라도(운현궁 ↔ 서울 운현궁) 그 장소가 맞다."""
    result = _result(_forecast("서울 운현궁", 42.0))
    forecast = select_concentration_forecast(
        result, candidate_name="운현궁", reference_date=REFERENCE_DATE
    )
    assert forecast is not None
    assert forecast.concentration_rate == 42.0


def test_같은_장소의_날짜별_예보는_여러_장소로_보지_않는다() -> None:
    """날짜 필터를 통과한 뒤에도 같은 이름이 여럿이면 한 장소다."""
    result = _result(
        _forecast("서울 운현궁", 42.0),
        _forecast("서울 운현궁", 55.0),
    )
    forecast = select_concentration_forecast(
        result, candidate_name="운현궁", reference_date=REFERENCE_DATE
    )
    assert forecast is not None
    assert forecast.concentration_rate == 42.0


def test_기준일_예보가_없으면_None() -> None:
    result = _result(_forecast("경복궁", 60.0, day="20260805"))
    assert (
        select_concentration_forecast(
            result, candidate_name="경복궁", reference_date=REFERENCE_DATE
        )
        is None
    )


def test_유효하지_않은_집중률은_제외한다() -> None:
    """무효값을 뺀 뒤 남은 장소가 하나면 폴백이 성립한다."""
    result = _result(
        _forecast("종묘광장공원", None),
        _forecast("종묘 [유네스코 세계유산]", 67.69),
    )
    forecast = select_concentration_forecast(
        result, candidate_name="종묘", reference_date=REFERENCE_DATE
    )
    assert forecast is not None
    assert forecast.place_name == "종묘 [유네스코 세계유산]"


def test_결과가_없으면_None() -> None:
    assert (
        select_concentration_forecast(
            None, candidate_name="경복궁", reference_date=REFERENCE_DATE
        )
        is None
    )
