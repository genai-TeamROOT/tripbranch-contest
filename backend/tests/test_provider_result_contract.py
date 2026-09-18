from datetime import UTC, datetime

import pytest

from app.providers.contracts import (
    ProviderMetadata,
    ProviderSource,
    ProviderStatus,
    provider_result,
)


def test_provider_result_uses_fixed_utc_clock() -> None:
    fixed = datetime(2026, 7, 24, 1, 2, 3, 456000, tzinfo=UTC)

    result = provider_result(
        ("item",),
        source=ProviderSource.FAKE_PLACE,
        status=ProviderStatus.SUCCESS,
        clock=lambda: fixed,
    )

    assert result.data == ("item",)
    assert result.metadata == ProviderMetadata(
        source=ProviderSource.FAKE_PLACE,
        status=ProviderStatus.SUCCESS,
        retrieved_at=fixed,
    )


def test_provider_metadata_rejects_naive_retrieved_at() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ProviderMetadata(
            source=ProviderSource.FAKE_WEATHER,
            status=ProviderStatus.NO_DATA,
            retrieved_at=datetime(2026, 7, 24),
        )


def test_provider_result_rejects_naive_clock() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        provider_result(
            None,
            source=ProviderSource.FAKE_HOLIDAY,
            clock=lambda: datetime(2026, 7, 24),
        )
