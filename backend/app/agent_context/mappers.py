"""기존 Tool 결과를 A–C 상위 RecommendationContext 값으로 변환한다."""

from __future__ import annotations

from datetime import UTC
from typing import Any, Literal, TypeVar

from app.agent_context.schemas import (
    ContextError,
    ContextValue,
    ContextWarning,
    Coordinates,
    HolidayInfo,
    PlaceCandidate,
    ProviderMetadata,
    ResolvedLocation,
    WeatherForecast,
)
from app.domain.operating_hours import OperatingSchedule
from app.providers.contracts import ProviderMetadata as DomainProviderMetadata
from app.tools.contracts import ToolError, ToolStatus
from app.tools.holiday import HolidayToolResult
from app.tools.nearby_place_details import NearbyPlaceDetailsResult
from app.tools.resolve_location import ResolveLocationResult
from app.tools.weather_forecast import WeatherForecastToolResult

T = TypeVar("T")

_WARNING_MESSAGES = {
    "partial_data": "일부 데이터를 확인하지 못했습니다.",
    "stale_data": "최신성이 보장되지 않는 데이터를 사용했습니다.",
    "fallback_used": "대체 조회 방식으로 결과를 찾았습니다.",
    "exclude_tags_unmapped": "제외 조건 일부를 분류로 옮기지 못해 반영하지 못했습니다.",
    "candidate_pool_truncated": (
        "이미 본 장소가 많아 새 후보를 더 받아오지 못했습니다."
    ),
    "candidate_pool_exhausted": "이미 본 장소를 제외하면 새 후보가 남아 있지 않습니다.",
}
_WEEKDAY_NAMES = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


def map_location_context(
    result: ResolveLocationResult,
) -> ContextValue[ResolvedLocation]:
    """위치 Tool 결과를 좌표 중심의 A–C Context로 변환한다."""

    data = None
    if result.location is not None:
        data = ResolvedLocation(
            requested_query=result.location.requested_query,
            resolved_name=result.location.resolved_name,
            source=result.location.source,
            location=Coordinates(
                latitude=result.location.latitude,
                longitude=result.location.longitude,
            ),
            # 현재 위치 Tool 도메인에는 주소 필드가 없다.
            address=None,
        )
    return _context_value(
        status=result.status,
        data=data,
        empty_data=None,
        error=result.error,
        warnings=result.warnings,
        provider_metadata=result.provider_metadata,
    )


# 계약 필드와 같은 Literal로 둔다. dict 값 타입을 명시하지 않으면 str로 추론돼
# 타입 검사기가 대입을 거부한다(런타임은 Pydantic이 검증하므로 통과하지만
# 에디터에서 걸린다).
_Precipitation = Literal["none", "rain", "snow", "sleet", "shower"]
_Sky = Literal["clear", "cloudy", "overcast"]

# 기상청 PTY(강수형태)·SKY(하늘상태) 코드를 도메인 용어로 옮긴다. D가 기상청 코드표를
# 알지 않아도 되도록 C에서 번역한다 — 판정("좋다/나쁘다")은 하지 않는다.
#
# PTY는 세기까지 나누지만(빗방울/눈날림 등) 추천 판정에 그 정도 해상도는 필요 없어
# 비·눈·진눈깨비·소나기로 합친다. 구분이 필요해지면 여기만 늘리면 된다.
_PRECIPITATION_BY_PTY_CODE: dict[str, _Precipitation] = {
    "0": "none",
    "1": "rain",
    "2": "sleet",
    "3": "snow",
    "4": "shower",
    "5": "rain",  # 빗방울
    "6": "sleet",  # 빗방울눈날림
    "7": "snow",  # 눈날림
}
_SKY_BY_CODE: dict[str, _Sky] = {
    "1": "clear",
    "3": "cloudy",
    "4": "overcast",
}


def map_weather_context(
    result: WeatherForecastToolResult,
) -> ContextValue[WeatherForecast]:
    """선택된 초단기예보를 A–C의 날씨 사실 Context로 변환한다.

    D-051 이후 C는 판정을 내리지 않는다 — 강수/하늘/기온 사실만 넘기고
    good/neutral/bad 판정은 D의 resolve_weather_condition()이 사용자 의도까지
    반영해 수행한다. Tool 결과(`SelectedWeatherForecast`)에 남아 있던 레거시
    `condition`도 제거돼, 이제 판정값은 C 경로 어디에도 없다.
    """

    data = None
    if result.forecast is not None:
        forecast = result.forecast
        data = WeatherForecast(
            forecast_for=forecast.forecast_for,
            precipitation=_PRECIPITATION_BY_PTY_CODE.get(forecast.precipitation_type or ""),
            sky=_SKY_BY_CODE.get(forecast.sky_code or ""),
            temperature_celsius=forecast.temperature_celsius,
        )
    return _context_value(
        status=result.status,
        data=data,
        empty_data=None,
        error=result.error,
        warnings=result.warnings,
        provider_metadata=result.provider_metadata,
    )


def map_places_context(
    result: NearbyPlaceDetailsResult,
) -> ContextValue[list[PlaceCandidate]]:
    """검색 후보를 유지하면서 확인 가능한 상세·운영정보만 보완한다."""

    places = [
        PlaceCandidate(
            place_id=item.candidate.place_id,
            name=item.candidate.name,
            category=item.candidate.category,
            lcls_systm1=item.candidate.lcls_systm1,
            lcls_systm2=item.candidate.lcls_systm2,
            lcls_systm3=item.candidate.lcls_systm3,
            location=Coordinates(
                latitude=item.candidate.latitude,
                longitude=item.candidate.longitude,
            ),
            operating_hours_raw=(
                item.details.operating_hours
                if item.details is not None
                else item.candidate.operating_hours
            ),
            rest_date_raw=(item.details.rest_date if item.details is not None else None),
            operating_schedule=_operating_schedule(
                item.details.operating_schedule if item.details is not None else None
            ),
            accessibility_verdicts=(
                {
                    need.value: verdict.value
                    for need, verdict in item.accessibility_verdicts.items()
                }
                if item.accessibility_verdicts
                else None
            ),
        )
        for item in result.places
    ]
    return _context_value(
        status=result.status,
        data=places or None,
        empty_data=[],
        error=result.error,
        warnings=result.warnings,
        provider_metadata=result.provider_metadata,
    )


def map_holidays_context(
    result: HolidayToolResult,
) -> ContextValue[list[HolidayInfo]]:
    """공휴일 Provider 결과에서 실제 공휴일 항목만 전달한다."""

    holidays = (
        [HolidayInfo(date=item.date, name=item.name) for item in result.holidays.holidays]
        if result.holidays is not None
        else []
    )
    return _context_value(
        status=result.status,
        data=holidays or None,
        empty_data=[],
        error=result.error,
        warnings=result.warnings,
        provider_metadata=result.provider_metadata,
    )


def _context_value(
    *,
    status: ToolStatus,
    data: T | None,
    empty_data: T | None,
    error: ToolError | None,
    warnings: tuple[str, ...],
    provider_metadata: tuple[DomainProviderMetadata, ...],
) -> ContextValue[T]:
    mapped_error = _error(error)
    if status is ToolStatus.NO_DATA:
        data = empty_data
        # A–C 계약은 no_data를 정상적인 빈 결과로 표현한다.
        mapped_error = None
    elif status in {ToolStatus.UNSUPPORTED, ToolStatus.UNAVAILABLE}:
        data = None

    return ContextValue[T](
        status=status.value,
        data=data,
        error=mapped_error,
        warnings=_warnings(warnings),
        provider_metadata=_metadata(provider_metadata),
    )


def _metadata(
    values: tuple[DomainProviderMetadata, ...],
) -> list[ProviderMetadata]:
    return [
        ProviderMetadata(
            source=value.source.value,
            status=value.status.value,
            retrieved_at=value.retrieved_at.astimezone(UTC),
        )
        for value in values
    ]


def _error(error: ToolError | None) -> ContextError | None:
    if error is None:
        return None
    return ContextError(
        code=error.code,
        message=error.message,
        retryable=error.retryable,
    )


def _warnings(values: tuple[str, ...]) -> list[ContextWarning]:
    return [
        ContextWarning(
            code=value,
            message=_WARNING_MESSAGES.get(value, value),
        )
        for value in values
    ]


def _operating_schedule(
    schedule: OperatingSchedule | None,
) -> dict[str, Any] | None:
    if schedule is None:
        return None
    return {
        "availability": schedule.availability.value,
        # D가 방문 예정 월·요일에 맞는 시간 구간을 선택할 수 있도록 규칙 단위를 보존한다.
        "rules": [
            {
                "months": sorted(rule.months) if rule.months is not None else None,
                "weekdays": (
                    [_WEEKDAY_NAMES[index] for index in sorted(rule.weekdays)]
                    if rule.weekdays is not None
                    else None
                ),
                "time_ranges": [
                    {
                        "open_time": time_range.start.strftime("%H:%M"),
                        "close_time": time_range.end.strftime("%H:%M"),
                        "crosses_midnight": time_range.crosses_midnight,
                    }
                    for time_range in rule.time_ranges
                ],
            }
            for rule in schedule.rules
        ],
        # 기존 A–C draft-v0 소비자를 위한 평탄화 필드는 당분간 유지한다.
        "time_ranges": [
            {
                "open_time": time_range.start.strftime("%H:%M"),
                "close_time": time_range.end.strftime("%H:%M"),
                "crosses_midnight": time_range.crosses_midnight,
            }
            for rule in schedule.rules
            for time_range in rule.time_ranges
        ],
        "closure_rules": [
            {
                "weekdays": [_WEEKDAY_NAMES[index] for index in sorted(rule.weekdays)],
                "source_text": rule.source_text,
            }
            for rule in schedule.closure_rules
        ],
        "parse_status": schedule.parse_status.value,
        "assumption_reason": schedule.assumption_reason,
        "warnings": list(schedule.warnings),
    }
