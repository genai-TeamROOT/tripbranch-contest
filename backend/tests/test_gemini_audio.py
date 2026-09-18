"""GeminiAudioTranscriber의 응답 정규화 단위 테스트."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.errors import AppError
from app.providers.gemini_audio import GeminiAudioTranscriber


@pytest.mark.asyncio
async def test_transcribe_returns_normalized_text(monkeypatch) -> None:
    provider = GeminiAudioTranscriber(
        api_key="dummy", model_name="gemini-audio-test", timeout_seconds=1.0
    )

    async def fake_generate_content(**_kwargs):
        return SimpleNamespace(text='  "경복궁 근처 카페 추천해줘"  ')

    monkeypatch.setattr(provider._client.aio.models, "generate_content", fake_generate_content)

    text, elapsed_ms = await provider.transcribe(audio_bytes=b"wav", mime_type="audio/wav")

    assert text == "경복궁 근처 카페 추천해줘"
    assert elapsed_ms >= 0


@pytest.mark.asyncio
async def test_transcribe_rejects_blank_model_text(monkeypatch) -> None:
    provider = GeminiAudioTranscriber(
        api_key="dummy", model_name="gemini-audio-test", timeout_seconds=1.0
    )

    async def fake_generate_content(**_kwargs):
        return SimpleNamespace(text="   \n ")

    monkeypatch.setattr(provider._client.aio.models, "generate_content", fake_generate_content)

    with pytest.raises(AppError, match="음성을 인식하지 못했어요") as raised:
        await provider.transcribe(audio_bytes=b"wav", mime_type="audio/wav")

    assert raised.value.code == "transcription_empty"
