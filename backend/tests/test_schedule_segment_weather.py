"""조회된 예보를 일정 구간 판정이 쓰는 사실로 옮기는 매핑. (TP-226)"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.agent_context.schemas import (
    ContextError,
    ContextValue,
    RecommendationContext,
    WeatherForecast,
)
from app.services.runtime.agent_runtime import _segment_weather

_FORECAST_FOR = datetime(2026, 9, 3, 12, tzinfo=UTC)


def _context(weather: ContextValue[WeatherForecast] | None) -> RecommendationContext:
    return RecommendationContext(weather=weather)


def _forecast(**kwargs: object) -> WeatherForecast:
    return WeatherForecast(forecast_for=_FORECAST_FOR, **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("status", ["success", "partial"])
def test_조회에_성공하면_사실_세_개를_그대로_옮긴다(status: str) -> None:
    weather = _segment_weather(
        _context(
            ContextValue[WeatherForecast](
                status=status,  # type: ignore[arg-type]
                data=_forecast(precipitation="rain", sky="overcast", temperature_celsius=8.0),
            )
        )
    )

    assert weather is not None
    assert (weather.precipitation, weather.sky, weather.temperature_celsius) == (
        "rain",
        "overcast",
        8.0,
    )


def test_날씨를_조회하지_않은_턴은_비어_있다() -> None:
    assert _segment_weather(_context(None)) is None


def test_예보가_없는_턴은_비운다() -> None:
    """no_data는 계약상 error를 함께 담지 못한다 — 상태만으로 판정한다."""
    context = _context(ContextValue[WeatherForecast](status="no_data"))
    assert _segment_weather(context) is None


@pytest.mark.parametrize("status", ["unsupported", "unavailable"])
def test_조회에_실패하면_비운다(status: str) -> None:
    """실패를 빈 값이 아니라 지어낸 날씨로 메우면 안 된다(D-042와 같은 이유)."""
    context = _context(
        ContextValue[WeatherForecast](
            status=status,  # type: ignore[arg-type]
            error=ContextError(code="upstream_error", message="조회 실패", retryable=True),
        )
    )
    assert _segment_weather(context) is None
