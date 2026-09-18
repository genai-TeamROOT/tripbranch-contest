"""장소 사진 출처 조립 테스트.

역할: 설정에 따라 어떤 사진 출처가 붙는지 못 박는다. 배선이 조용히 빠지면
      "사진이 안 늘어난다"로만 드러나 원인을 찾기 어렵다.
입력: provider_mode와 Supabase 설정.
출력: get_place_photo_repository()가 돌려주는 객체의 타입.
호출 시점: 로컬 테스트와 CI에서 pytest 실행 시.
"""

from __future__ import annotations

import httpx
import pytest

import app.providers.factory as factory_module
from app.config import Settings
from app.providers.factory import get_place_photo_repository
from app.providers.hybrid_place_photos import HybridPlacePhotoProvider
from app.repositories.fake_places import FakePlacePhotoRepository


def _use_settings(monkeypatch, **overrides: object) -> None:
    monkeypatch.setattr(
        factory_module, "settings", Settings(_env_file=None, **overrides)
    )


@pytest.mark.asyncio
async def test_실환경은_저장소와_api를_함께_쓴다(monkeypatch) -> None:
    _use_settings(
        monkeypatch,
        provider_mode="real",
        tour_api_service_key="key",
        supabase_url="https://example.supabase.co",
        supabase_secret_key="secret",
    )

    async with httpx.AsyncClient() as client:
        source = get_place_photo_repository(client)

    assert isinstance(source, HybridPlacePhotoProvider)


@pytest.mark.asyncio
async def test_설정된_상한과_ttl이_그대로_전달된다(monkeypatch) -> None:
    """config 값이 안 넘어가면 기본값으로 조용히 도는데, 화면으로는 구분되지 않는다."""
    _use_settings(
        monkeypatch,
        provider_mode="real",
        tour_api_service_key="key",
        supabase_url="https://example.supabase.co",
        supabase_secret_key="secret",
        place_photo_display_limit=4,
        place_photo_api_cache_ttl_seconds=60,
    )

    async with httpx.AsyncClient() as client:
        source = get_place_photo_repository(client)

    assert isinstance(source, HybridPlacePhotoProvider)
    assert source._display_limit == 4
    assert source._cache_ttl_seconds == 60


@pytest.mark.asyncio
async def test_fake_환경은_api를_부르지_않는다(monkeypatch) -> None:
    """fake 환경에서 실 API를 부르면 개발 중에 한도를 태운다(D-042)."""
    _use_settings(monkeypatch, provider_mode="fake")

    async with httpx.AsyncClient() as client:
        source = get_place_photo_repository(client)

    assert isinstance(source, FakePlacePhotoRepository)


@pytest.mark.asyncio
async def test_supabase_설정이_없으면_fake로_간다(monkeypatch) -> None:
    _use_settings(monkeypatch, provider_mode="real", tour_api_service_key="key")

    async with httpx.AsyncClient() as client:
        source = get_place_photo_repository(client)

    assert isinstance(source, FakePlacePhotoRepository)
