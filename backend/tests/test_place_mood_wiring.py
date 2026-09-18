"""장소 분위기 Provider 조립 테스트 — 스위치와 인코더 부재 처리."""

from __future__ import annotations

import httpx
import pytest

from app.providers import factory
from app.providers.place_mood import PlaceMoodProvider


@pytest.fixture
def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))


def test_disabled_flag_returns_none(monkeypatch, client) -> None:
    monkeypatch.setattr(factory.settings, "place_mood_enabled", False)
    assert factory.get_place_mood_provider(client) is None


def test_missing_supabase_config_returns_none_without_crashing(
    monkeypatch, client, caplog
) -> None:
    """설정 하나 때문에 서버 전체가 안 뜨면 안 된다. 대신 이유는 남긴다."""
    monkeypatch.setattr(factory.settings, "place_mood_enabled", True)
    monkeypatch.setattr(factory.settings, "supabase_url", "")
    monkeypatch.setattr(factory.settings, "supabase_secret_key", "")

    with caplog.at_level("WARNING"):
        assert factory.get_place_mood_provider(client) is None

    assert "PLACE_MOOD_ENABLED" in caplog.text


def test_enabled_builds_provider(monkeypatch, client) -> None:
    monkeypatch.setattr(factory.settings, "place_mood_enabled", True)
    monkeypatch.setattr(factory.settings, "supabase_url", "https://x.supabase.co")
    monkeypatch.setattr(factory.settings, "supabase_secret_key", "secret")
    monkeypatch.setattr(factory.settings, "place_mood_warmup_enabled", False)
    monkeypatch.setattr(factory, "_get_mood_encoder", lambda: None)

    provider = factory.get_place_mood_provider(client)

    assert isinstance(provider, PlaceMoodProvider)


def test_provider_is_built_even_without_the_encoder(monkeypatch, client) -> None:
    """SigLIP이 없어도 발화 경로는 돌아야 하므로 Provider 자체는 만든다."""
    monkeypatch.setattr(factory.settings, "place_mood_enabled", True)
    monkeypatch.setattr(factory.settings, "supabase_url", "https://x.supabase.co")
    monkeypatch.setattr(factory.settings, "supabase_secret_key", "secret")
    monkeypatch.setattr(factory, "_get_mood_encoder", lambda: None)

    provider = factory.get_place_mood_provider(client)

    assert provider is not None
    assert provider.photo_search_available is False


def test_warmup_runs_only_when_enabled(monkeypatch) -> None:
    calls: list[str] = []

    class _Encoder:
        def warmup_in_background(self):
            calls.append("warmup")

    monkeypatch.setattr(
        "app.providers.place_mood_encoder.get_shared_encoder", lambda: _Encoder()
    )

    monkeypatch.setattr(factory.settings, "place_mood_warmup_enabled", False)
    factory._get_mood_encoder()
    assert calls == []

    monkeypatch.setattr(factory.settings, "place_mood_warmup_enabled", True)
    factory._get_mood_encoder()
    assert calls == ["warmup"]


def _allow_reranker(monkeypatch) -> None:
    """재랭커가 만들어지는 조건을 모두 갖춘 상태."""
    monkeypatch.setattr(factory.settings, "place_mood_rerank_enabled", True)
    monkeypatch.setattr(factory.settings, "llm_api_key", "key")
    monkeypatch.setattr(
        factory.settings.__class__, "resolved_llm_provider", property(lambda self: "real")
    )
    monkeypatch.setattr(
        "app.providers.gemini_vlm_rerank.genai.Client", lambda **kw: object()
    )


def test_reranker_is_none_when_disabled(client, monkeypatch) -> None:
    """기본은 꺼져 있다. 검색 한 번에 16~47원이 드는 기능이라 명시적으로 켠다."""
    monkeypatch.setattr(factory.settings, "place_mood_rerank_enabled", False)
    assert factory.get_place_mood_reranker(client) is None


def test_reranker_is_none_in_fake_llm_mode(client, monkeypatch) -> None:
    """Fake LLM에서는 만들지 않는다.

    가짜 응답으로 순서를 바꾸면 임베딩이 낸 순서가 근거 없이 뒤집히는데 오류가
    없어 알아채기 어렵다 — D-042가 나온 사건과 같은 성격이다.
    """
    _allow_reranker(monkeypatch)
    monkeypatch.setattr(
        factory.settings.__class__, "resolved_llm_provider", property(lambda self: "fake")
    )
    assert factory.get_place_mood_reranker(client) is None


def test_reranker_is_none_without_api_key(client, monkeypatch) -> None:
    """키가 없으면 만들지 않는다. 부팅은 막지 않되 왜 안 켜졌는지는 남긴다."""
    _allow_reranker(monkeypatch)
    monkeypatch.setattr(factory.settings, "llm_api_key", "")
    assert factory.get_place_mood_reranker(client) is None


def test_reranker_is_built_when_enabled(client, monkeypatch) -> None:
    _allow_reranker(monkeypatch)
    reranker = factory.get_place_mood_reranker(client)
    assert reranker is not None
    assert reranker.model_name == factory.settings.resolved_place_mood_rerank_model_name
