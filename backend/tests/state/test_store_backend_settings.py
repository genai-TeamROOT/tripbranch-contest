"""STATE_STORE_BACKEND 설정 해석과 get_store() 조립 테스트."""

from __future__ import annotations

import pytest

import app.state.store as store_module
from app.config import Settings
from app.providers.factory import validate_provider_config
from app.state.store import InMemoryStateStore, get_store
from app.state.supabase_store import SupabaseStateStore


@pytest.fixture(autouse=True)
def _reset_supabase_store_cache():
    """테스트 간 지연 생성 캐시가 새지 않도록 매번 초기화한다."""
    store_module._reset_supabase_store_for_tests()
    yield
    store_module._reset_supabase_store_for_tests()


def test_defaults_to_memory_backend() -> None:
    settings = Settings(_env_file=None)

    assert settings.state_store_backend == "memory"


def test_invalid_backend_is_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(_env_file=None, state_store_backend="mysql")


def test_supabase_backend_requires_credentials() -> None:
    settings = Settings(_env_file=None, state_store_backend="supabase")

    with pytest.raises(ValueError) as exc_info:
        validate_provider_config(settings)

    message = str(exc_info.value)
    assert "SUPABASE_URL" in message
    assert "SUPABASE_SECRET_KEY" in message


def test_supabase_backend_passes_validation_with_credentials() -> None:
    settings = Settings(
        _env_file=None,
        state_store_backend="supabase",
        supabase_url="https://example.supabase.co",
        supabase_secret_key="secret",
    )

    validate_provider_config(settings)


def test_get_store_returns_in_memory_by_default(monkeypatch) -> None:
    monkeypatch.setattr(store_module, "settings", Settings(_env_file=None))

    assert isinstance(get_store(), InMemoryStateStore)


def test_get_store_returns_same_in_memory_instance_across_calls(monkeypatch) -> None:
    monkeypatch.setattr(store_module, "settings", Settings(_env_file=None))

    assert get_store() is get_store()


def test_get_store_returns_supabase_store_when_selected(monkeypatch) -> None:
    monkeypatch.setattr(
        store_module,
        "settings",
        Settings(
            _env_file=None,
            state_store_backend="supabase",
            supabase_url="https://example.supabase.co",
            supabase_secret_key="secret",
        ),
    )

    assert isinstance(get_store(), SupabaseStateStore)


def test_get_store_reuses_supabase_client_across_calls(monkeypatch) -> None:
    monkeypatch.setattr(
        store_module,
        "settings",
        Settings(
            _env_file=None,
            state_store_backend="supabase",
            supabase_url="https://example.supabase.co",
            supabase_secret_key="secret",
        ),
    )

    assert get_store() is get_store()