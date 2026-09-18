"""PLACE_DETAILS_SOURCE 설정 해석과 Provider 조립 테스트."""

from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

import app.providers.factory as factory_module
from app.config import Settings
from app.providers.factory import get_place_details_provider, validate_provider_config
from app.providers.protocols import BatchPlaceDetailsProvider
from app.providers.real_place import RealPlaceProvider
from app.providers.stub import FakePlaceProvider
from app.providers.supabase_place_details import SupabasePlaceDetailsProvider


def test_defaults_to_supabase() -> None:
    """기본값은 supabase다 — 추천이 후보 전량의 상세를 받기 때문이다.

    tour_api로 두면 후보 1곳당 detailCommon2 + detailIntro2 2회가 나가 일일 한도를
    금방 태운다(config.py::place_details_source 주석).
    """
    settings = Settings(_env_file=None, provider_mode="real")

    assert settings.resolved_place_details_source == "supabase"


def test_supabase_source_is_resolved_when_place_provider_is_real() -> None:
    settings = Settings(
        _env_file=None, provider_mode="real", place_details_source="supabase"
    )

    assert settings.resolved_place_details_source == "supabase"


def test_fake_place_provider_keeps_fake_details_contract() -> None:
    # Fake 모드에서는 상세도 Fake Provider가 담당하므로 supabase 설정을 무시한다.
    settings = Settings(
        _env_file=None, provider_mode="fake", place_details_source="supabase"
    )

    assert settings.resolved_place_details_source == "tour_api"


def test_invalid_source_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, place_details_source="mysql")


def test_supabase_source_requires_credentials() -> None:
    settings = Settings(
        _env_file=None,
        provider_mode="fake",
        place_provider="real",
        tour_api_service_key="key",
        place_details_source="supabase",
    )

    with pytest.raises(ValueError) as exc_info:
        validate_provider_config(settings)

    message = str(exc_info.value)
    assert "SUPABASE_URL" in message
    assert "SUPABASE_SECRET_KEY" in message


def test_supabase_source_passes_validation_with_credentials() -> None:
    settings = Settings(
        _env_file=None,
        provider_mode="fake",
        place_provider="real",
        tour_api_service_key="key",
        place_details_source="supabase",
        supabase_url="https://example.supabase.co",
        supabase_secret_key="secret",
    )

    validate_provider_config(settings)


@pytest.mark.asyncio
async def test_factory_builds_supabase_details_provider(monkeypatch) -> None:
    monkeypatch.setattr(
        factory_module,
        "settings",
        Settings(
            _env_file=None,
            provider_mode="real",
            place_details_source="supabase",
            tour_api_service_key="key",
            supabase_url="https://example.supabase.co",
            supabase_secret_key="secret",
        ),
    )

    async with httpx.AsyncClient() as client:
        provider = get_place_details_provider(client)

    assert isinstance(provider, SupabasePlaceDetailsProvider)
    # Tool이 다건 경로를 고르려면 런타임 계약을 만족해야 한다.
    assert isinstance(provider, BatchPlaceDetailsProvider)


@pytest.mark.asyncio
async def test_factory_keeps_tour_api_details_provider(monkeypatch) -> None:
    monkeypatch.setattr(
        factory_module,
        "settings",
        Settings(
            _env_file=None,
            provider_mode="real",
            place_details_source="tour_api",
            tour_api_service_key="key",
        ),
    )

    async with httpx.AsyncClient() as client:
        provider = get_place_details_provider(client)

    assert isinstance(provider, RealPlaceProvider)
    # 기존 단건 Provider는 다건 계약을 만족하지 않아 병렬 단건 경로를 탄다.
    assert not isinstance(provider, BatchPlaceDetailsProvider)


@pytest.mark.asyncio
async def test_factory_keeps_fake_provider_contract(monkeypatch) -> None:
    monkeypatch.setattr(
        factory_module,
        "settings",
        Settings(
            _env_file=None, provider_mode="fake", place_details_source="supabase"
        ),
    )

    async with httpx.AsyncClient() as client:
        provider = get_place_details_provider(client)

    assert isinstance(provider, FakePlaceProvider)
    assert not isinstance(provider, BatchPlaceDetailsProvider)
