"""Gemini Audio API 기반의 짧은 사용자 발화 전사 구현.

역할: 브라우저가 WAV로 변환해 보낸 짧은 음성 녹음을 한국어 텍스트로 바꾼다.
출력 텍스트는 추천 Agent에 바로 넣지 않고 프론트 입력창에 먼저 표시한다.
호출 시점: `POST /api/transcribe` 음성 입력 경로.
"""

from __future__ import annotations

import time

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from app.errors import AppError, ProviderTimeoutError, ProviderUnavailableError
from app.observability.api_usage import record_call

_TRANSCRIBE_INSTRUCTION = """당신은 한국어 음성 전사기입니다.
오디오 속 사용자의 발화를 한국어 텍스트로만 정확히 받아쓰세요.
설명, 요약, 화자 표기, 따옴표를 덧붙이지 마세요. 장소명·역명·숫자를 임의로 바꾸지 마세요.
"""


class GeminiAudioTranscriber:
    """Google Gemini Audio 입력을 사용하는 단발 음성→텍스트 변환기."""

    def __init__(self, *, api_key: str, model_name: str, timeout_seconds: float) -> None:
        self._client = genai.Client(
            api_key=api_key,
            http_options=genai_types.HttpOptions(timeout=int(timeout_seconds * 1000)),
        )
        self.model_name = model_name

    async def transcribe(self, *, audio_bytes: bytes, mime_type: str) -> tuple[str, int]:
        """오디오를 전사하고 `(텍스트, Gemini 왕복 시간ms)`를 반환한다."""
        started = time.perf_counter()
        try:
            response = await self._client.aio.models.generate_content(
                model=self.model_name,
                contents=[
                    _TRANSCRIBE_INSTRUCTION,
                    genai_types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
                ],
                config=genai_types.GenerateContentConfig(temperature=0.0),
            )
        except (httpx.TimeoutException, TimeoutError):
            # 전송 계층이 aiohttp일 수도 httpx일 수도 있어 둘 다 잡는다
            # (gemini.py의 `_TIMEOUT_ERRORS` 주석 참고).
            _record(self.model_name, started, ok=False, status="timeout")
            raise ProviderTimeoutError("Gemini 음성 인식") from None
        except genai_errors.APIError as exc:
            _record(self.model_name, started, ok=False, status=str(exc.code))
            detail = f"{exc.code}{f' {exc.status}' if hasattr(exc, 'status') else ''}"
            raise ProviderUnavailableError("Gemini 음성 인식", detail=detail) from None

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        _record(self.model_name, started, ok=True, status="ok")
        text = _normalize_transcript(response.text or "")
        if not text:
            raise AppError(
                code="transcription_empty",
                message="음성을 인식하지 못했어요. 조금 더 또렷하게 말씀해 주세요.",
                status_code=422,
                retryable=True,
                provider="Gemini",
            )
        return text, elapsed_ms


def _normalize_transcript(value: str) -> str:
    """Gemini가 실수로 넣은 줄바꿈·양끝 따옴표만 제거한다.

    문장의 뜻이나 고유명사는 수정하지 않는다. 음성 인식 결과를 사용자가 보고 편집할
    수 있게 하므로, 여기서 별도의 후처리·교정 모델을 태우지 않는 것이 원칙이다.
    """
    text = " ".join(value.strip().split())
    if (
        len(text) >= 2
        and text[0] in {'"', "'", "“", "‘"}
        and text[-1]
        in {
            '"',
            "'",
            "”",
            "’",
        }
    ):
        text = text[1:-1].strip()
    return text


def _record(model_name: str, started: float, *, ok: bool, status: str) -> None:
    record_call(
        "gemini",
        model_name,
        ok=ok,
        latency_ms=(time.perf_counter() - started) * 1000,
        status=f"audio_transcription:{status}",
    )


__all__ = ["GeminiAudioTranscriber"]
