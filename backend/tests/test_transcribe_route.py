"""음성 전사 라우팅·입력 검증 테스트.

실제 Gemini API는 호출하지 않는다. 전사기 구현은 provider 테스트에서, 여기서는 WAV
본문이 전사기에 전달되고 결과가 프론트 계약으로 반환되는지만 검증한다.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import app.routes.transcribe as transcribe_route
from app.main import app


def _client_with_transcriber(monkeypatch) -> tuple[TestClient, list[tuple[bytes, str]]]:
    calls: list[tuple[bytes, str]] = []

    class _FakeTranscriber:
        model_name = "gemini-audio-test"

        async def transcribe(self, *, audio_bytes: bytes, mime_type: str) -> tuple[str, int]:
            calls.append((audio_bytes, mime_type))
            return "경복궁 근처 카페 추천해줘", 123

    monkeypatch.setattr(transcribe_route, "get_gemini_audio_transcriber", _FakeTranscriber)
    return TestClient(app), calls


def test_transcribe_returns_text_for_wav_body(monkeypatch) -> None:
    client, calls = _client_with_transcriber(monkeypatch)

    response = client.post(
        "/api/transcribe",
        content=b"RIFF-test-wav",
        headers={"Content-Type": "audio/wav"},
    )

    assert response.status_code == 200
    assert response.json()["text"] == "경복궁 근처 카페 추천해줘"
    assert response.json()["model"] == "gemini-audio-test"
    assert calls == [(b"RIFF-test-wav", "audio/wav")]


def test_transcribe_rejects_non_wav_body(monkeypatch) -> None:
    client, calls = _client_with_transcriber(monkeypatch)

    response = client.post(
        "/api/transcribe",
        content=b"webm",
        headers={"Content-Type": "audio/webm"},
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_audio_format"
    assert calls == []


def test_transcribe_rejects_empty_audio(monkeypatch) -> None:
    client, calls = _client_with_transcriber(monkeypatch)

    response = client.post("/api/transcribe", content=b"", headers={"Content-Type": "audio/wav"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "empty_audio"
    assert calls == []
