"""실제 Provider 요청과 응답을 인증정보 없이 확인하는 수동 Inspection Test."""

from __future__ import annotations

import json
import os
from time import perf_counter
from typing import Any
from urllib.parse import quote, quote_plus

import httpx
import pytest

from app.config import Settings
from app.providers.concentration import RealConcentrationProvider
from app.providers.geocoding import RealGeocodingProvider
from app.providers.holiday import RealHolidayProvider
from app.providers.real_place import RealPlaceProvider
from app.providers.tour_category_registry import get_tour_category_registry
from app.providers.weather import RealWeatherProvider
from app.tools.nearby_place_details import (
    DetailStatus,
    NearbyPlaceDetailsQuery,
    NearbyPlaceDetailsTool,
)
from app.tools.weather_forecast import (
    GetWeatherForecastTool,
    WeatherForecastQuery,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.inspection,
    pytest.mark.skipif(
        os.getenv("RUN_REAL_PROVIDER_INSPECTION") != "true",
        reason="RUN_REAL_PROVIDER_INSPECTION=true일 때만 실제 요청·응답을 출력합니다.",
    ),
]

settings = Settings()
_REDACTED = "<redacted>"
_SENSITIVE_QUERY_KEYS = {"servicekey", "api_key", "apikey"}
_SENSITIVE_HEADER_KEYS = {
    "authorization",
    "x-ncp-apigw-api-key-id",
    "x-ncp-apigw-api-key",
}
_MAX_RESPONSE_CHARS = 30_000
_INSPECTION_STARTED_AT = "tripbranch_inspection_started_at"


def _known_secrets() -> tuple[str, ...]:
    values = (
        settings.tour_api_service_key,
        settings.weather_api_key,
        settings.naver_map_client_id,
        settings.naver_map_client_secret,
        settings.llm_api_key,
    )
    return tuple(value for value in values if value)


def _redact_known_secrets(value: str) -> str:
    redacted = value
    for secret in _known_secrets():
        variants = {secret, quote(secret, safe=""), quote_plus(secret, safe="")}
        for variant in variants:
            if variant:
                redacted = redacted.replace(variant, _REDACTED)
    return redacted


def _required_value(name: str, value: str) -> str:
    if not value:
        pytest.skip(f"{name} 환경변수가 없습니다.")
    return value


def _sanitized_query(request: httpx.Request) -> dict[str, str | list[str]]:
    result: dict[str, str | list[str]] = {}
    for key, value in request.url.params.multi_items():
        safe_value = _REDACTED if key.lower() in _SENSITIVE_QUERY_KEYS else value
        existing = result.get(key)
        if existing is None:
            result[key] = safe_value
        elif isinstance(existing, list):
            existing.append(safe_value)
        else:
            result[key] = [existing, safe_value]
    return result


def _sanitized_headers(request: httpx.Request) -> dict[str, str]:
    visible_headers: dict[str, str] = {}
    for key, value in request.headers.items():
        normalized = key.lower()
        if normalized in _SENSITIVE_HEADER_KEYS:
            visible_headers[key] = _REDACTED
        elif normalized in {"accept", "content-type", "user-agent"}:
            visible_headers[key] = value
    return visible_headers


def _format_body(payload: Any) -> str:
    rendered = _redact_known_secrets(
        json.dumps(payload, ensure_ascii=False, indent=2)
    )
    if len(rendered) <= _MAX_RESPONSE_CHARS:
        return rendered
    omitted = len(rendered) - _MAX_RESPONSE_CHARS
    return f"{rendered[:_MAX_RESPONSE_CHARS]}\n... <{omitted} chars omitted>"


async def _print_request(request: httpx.Request) -> None:
    request.extensions[_INSPECTION_STARTED_AT] = perf_counter()
    print("\n=== Provider Request ===")
    print(f"method: {request.method}")
    print(f"url: {request.url.copy_with(query=None)}")
    print(f"query: {_format_body(_sanitized_query(request))}")
    print(f"headers: {_format_body(_sanitized_headers(request))}")


async def _print_response(response: httpx.Response) -> None:
    await response.aread()
    started_at = response.request.extensions.get(_INSPECTION_STARTED_AT)
    elapsed_ms = (
        (perf_counter() - started_at) * 1000
        if isinstance(started_at, (int, float))
        else None
    )
    print("=== Provider Response ===")
    print(f"status: {response.status_code}")
    print(f"content-type: {response.headers.get('content-type', '')}")
    if elapsed_ms is not None:
        print(f"elapsed_ms: {elapsed_ms:.2f}")
    try:
        payload: Any = response.json()
    except ValueError:
        payload = response.text
    print(_format_body(payload))


def _inspection_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        event_hooks={
            "request": [_print_request],
            "response": [_print_response],
        }
    )


def _operating_schedule_summary(details: Any) -> dict[str, Any] | None:
    schedule = details.operating_schedule if details else None
    if schedule is None:
        return None
    return {
        "availability": schedule.availability.value,
        "parse_status": schedule.parse_status.value,
        "assumption_reason": schedule.assumption_reason,
        "cleaned_operating_hours": schedule.cleaned_operating_hours,
        "cleaned_rest_date": schedule.cleaned_rest_date,
        "rules": [
            {
                "months": sorted(rule.months) if rule.months else None,
                "weekdays": sorted(rule.weekdays) if rule.weekdays else None,
                "time_ranges": [
                    {
                        "start": time_range.start.isoformat(timespec="minutes"),
                        "end": time_range.end.isoformat(timespec="minutes"),
                        "crosses_midnight": time_range.crosses_midnight,
                    }
                    for time_range in rule.time_ranges
                ],
                "last_admission": (
                    rule.last_admission.isoformat(timespec="minutes")
                    if rule.last_admission
                    else None
                ),
                "source_text": rule.source_text,
            }
            for rule in schedule.rules
        ],
        "closure_rules": [
            {
                "weekdays": sorted(rule.weekdays),
                "source_text": rule.source_text,
            }
            for rule in schedule.closure_rules
        ],
        "warnings": list(schedule.warnings),
    }


async def test_inspect_naver_geocoding_request_and_response() -> None:
    async with _inspection_client() as client:
        provider = RealGeocodingProvider(
            api_key_id=_required_value("NAVER_MAP_CLIENT_ID", settings.naver_map_client_id),
            api_key=_required_value(
                "NAVER_MAP_CLIENT_SECRET", settings.naver_map_client_secret
            ),
            client=client,
        )
        result = await provider.geocode("경복궁")
        result = result.data

    print(f"normalized: {result}")


async def test_inspect_kma_weather_request_and_response() -> None:
    async with _inspection_client() as client:
        provider = RealWeatherProvider(
            api_key=_required_value("WEATHER_API_KEY", settings.weather_api_key),
            client=client,
        )
        result = await GetWeatherForecastTool(provider).execute(
            WeatherForecastQuery(37.5788, 126.9770)
        )

    forecast = result.forecast
    summary = {
        "sky_code": forecast.sky_code if forecast else None,
        "precipitation_type": forecast.precipitation_type if forecast else None,
        "temperature_celsius": forecast.temperature_celsius if forecast else None,
        "requested_visit_at": (
            forecast.requested_visit_at.isoformat() if forecast else None
        ),
        "forecast_for": forecast.forecast_for.isoformat() if forecast else None,
        "retrieved_at": forecast.retrieved_at.isoformat() if forecast else None,
        "data_type": forecast.data_type if forecast else None,
        "observed_at": forecast.observed_at if forecast else None,
        "timezone": forecast.timezone if forecast else None,
        "timezone_assumed": forecast.timezone_assumed if forecast else None,
        "selection_method": (
            forecast.selection_method.value if forecast else None
        ),
    }
    print(f"normalized_status: {result.status.value}")
    print(f"normalized_forecast: {_format_body(summary)}")


async def test_inspect_tour_api_place_request_and_response() -> None:
    async with _inspection_client() as client:
        provider = RealPlaceProvider(
            api_key=_required_value("TOUR_API_SERVICE_KEY", settings.tour_api_service_key),
            client=client,
            timeout_seconds=30.0,
        )
        result = await provider.search_places(
            latitude=37.5788,
            longitude=126.9770,
            preferred_categories=[],
            search_radius_km=1.0,
        )
        result = result.data

    print(f"normalized_count: {len(result)}")
    print(f"normalized_samples: {[candidate.name for candidate in result[:3]]}")


async def test_inspect_tour_api_cafe_category_request_and_response() -> None:
    cafe_matches = get_tour_category_registry().find_by_small_name("카페")
    assert len(cafe_matches) == 1

    async with _inspection_client() as client:
        provider = RealPlaceProvider(
            api_key=_required_value("TOUR_API_SERVICE_KEY", settings.tour_api_service_key),
            client=client,
            timeout_seconds=30.0,
        )
        result = await provider.search_places(
            latitude=37.5788,
            longitude=126.9770,
            preferred_categories=["cafe"],
            search_radius_km=5.0,
            category_filter=cafe_matches[0].to_filter(),
        )
        result = result.data

    assert result
    assert all(candidate.content_type_id == "39" for candidate in result)
    assert all(candidate.lcls_systm1 == "FD" for candidate in result)
    assert all(candidate.lcls_systm2 == "FD05" for candidate in result)
    assert all(candidate.lcls_systm3 == "FD050100" for candidate in result)
    print(f"normalized_count: {len(result)}")
    samples = [
        (
            candidate.name,
            candidate.content_type_id,
            candidate.lcls_systm1,
            candidate.lcls_systm2,
            candidate.lcls_systm3,
        )
        for candidate in result[:10]
    ]
    print(
        "normalized_samples: "
        f"{samples}"
    )


async def test_inspect_tour_api_nearby_place_details_request_and_response() -> None:
    async with _inspection_client() as client:
        provider = RealPlaceProvider(
            api_key=_required_value("TOUR_API_SERVICE_KEY", settings.tour_api_service_key),
            client=client,
            timeout_seconds=30.0,
        )
        tool = NearbyPlaceDetailsTool(
            search_provider=provider,
            details_provider=provider,
            max_concurrency=3,
        )
        result = await tool.execute(
            NearbyPlaceDetailsQuery(
                latitude=37.5788,
                longitude=126.9770,
                search_radius_km=2.0,
                limit=10,
                excluded_place_ids=frozenset({"126508"}),
            )
        )

    summaries = [
        {
            "place_id": item.candidate.place_id,
            "content_type_id": item.candidate.content_type_id,
            "name": item.candidate.name,
            "address": item.candidate.address,
            "latitude": item.candidate.latitude,
            "longitude": item.candidate.longitude,
            "lcls_systm1": item.candidate.lcls_systm1,
            "lcls_systm2": item.candidate.lcls_systm2,
            "lcls_systm3": item.candidate.lcls_systm3,
            "detail_status": item.detail_status.value,
            "error_code": item.error_code,
            "title": item.details.title if item.details else None,
            "overview": item.details.overview if item.details else None,
            "homepage": item.details.homepage if item.details else None,
            "telephone": item.details.telephone if item.details else None,
            "operating_hours": (
                item.details.operating_hours if item.details else None
            ),
            "rest_date": item.details.rest_date if item.details else None,
            "operating_schedule": _operating_schedule_summary(item.details),
        }
        for item in result.places
    ]

    assert result.places
    assert len(result.places) <= 10
    assert any(
        item.detail_status is DetailStatus.SUCCESS for item in result.places
    )
    assert any(item.details and item.details.operating_hours for item in result.places)
    assert any(item.details and item.details.rest_date for item in result.places)
    print(f"normalized_nearby_count: {len(result.places)}")
    print(f"normalized_nearby_status: {result.status.value}")
    print(f"normalized_nearby_total_elapsed_ms: {result.elapsed_ms:.2f}")
    print(f"normalized_nearby_details: {_format_body(summaries)}")


async def test_inspect_tour_api_keyword_and_details_request_and_response() -> None:
    async with _inspection_client() as client:
        provider = RealPlaceProvider(
            api_key=_required_value("TOUR_API_SERVICE_KEY", settings.tour_api_service_key),
            client=client,
        )
        details = await provider.find_details_by_name(
            "경복궁", region_code="11", district_code="110"
        )
        details = details.data

    print(f"normalized_details: {details}")


async def test_inspect_tour_api_concentration_request_and_response() -> None:
    async with _inspection_client() as client:
        provider = RealConcentrationProvider(
            api_key=_required_value("TOUR_API_SERVICE_KEY", settings.tour_api_service_key),
            client=client,
        )
        result = await provider.get_forecast("11", "11110", "경복궁")
        result = result.data

    print(f"normalized_count: {len(result.forecasts)}")
    print(f"normalized_samples: {list(result.forecasts[:3])}")


async def test_inspect_kasi_holiday_request_and_response() -> None:
    async with _inspection_client() as client:
        provider = RealHolidayProvider(
            api_key=_required_value(
                "TOUR_API_SERVICE_KEY", settings.tour_api_service_key
            ),
            client=client,
        )
        result = await provider.get_holidays(2026)
        result = result.data

    print(f"normalized_count: {len(result.entries)}")
    print(f"normalized_holidays: {list(result.holidays)}")
