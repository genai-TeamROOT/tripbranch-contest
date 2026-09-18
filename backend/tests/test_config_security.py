from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.errors import AppError
from app.providers.concentration import RealConcentrationProvider
from app.providers.geocoding import RealGeocodingProvider
from app.providers.holiday import RealHolidayProvider
from app.providers.local_search import RealLocalSearchProvider
from app.providers.real_place import RealPlaceProvider
from app.providers.weather import RealWeatherProvider


def _assert_provider_traceback_has_no_secret(exc: BaseException, secret: str) -> None:
    traceback = exc.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        module_name = str(frame.f_globals.get("__name__", ""))
        if module_name.startswith("app.providers"):
            assert secret not in repr(frame.f_locals)
        traceback = traceback.tb_next


def test_settings_repr_and_dump_exclude_provider_secrets(
    monkeypatch,
) -> None:
    secrets = {
        "LLM_API_KEY": "secret-llm",
        "WEATHER_API_KEY": "secret-weather",
        "TOUR_API_SERVICE_KEY": "secret-tour",
        "NAVER_MAP_CLIENT_ID": "secret-naver-id",
        "NAVER_MAP_CLIENT_SECRET": "secret-naver-key",
        "NAVER_LOCAL_SEARCH_CLIENT_ID": "secret-local-id",
        "NAVER_LOCAL_SEARCH_CLIENT_SECRET": "secret-local-key",
        "SUPABASE_SECRET_KEY": "sb_secret_test",
    }
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)

    settings = Settings(_env_file=None)
    rendered = repr(settings)
    dumped = settings.model_dump()

    assert all(value not in rendered for value in secrets.values())
    assert all(value not in dumped.values() for value in secrets.values())
    assert settings.tour_api_service_key == "secret-tour"
    assert settings.supabase_secret_key == "sb_secret_test"


@pytest.mark.asyncio
async def test_provider_error_tracebacks_clear_request_secrets() -> None:
    secret = "sensitive-provider-secret"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        calls = (
            RealPlaceProvider(secret, client).search_places(37.5, 127.0, [], 1.0),
            RealWeatherProvider(secret, client).get_forecast_slots(37.5, 127.0),
            RealConcentrationProvider(secret, client).get_forecast("11", "11110"),
            RealHolidayProvider(secret, client).get_holidays(2026),
            RealGeocodingProvider(secret, secret, client).geocode("서울역"),
            RealLocalSearchProvider(secret, secret, client).search_places_by_name("쌈지길"),
        )
        for call in calls:
            with pytest.raises(AppError) as exc_info:
                await call
            _assert_provider_traceback_has_no_secret(exc_info.value, secret)
