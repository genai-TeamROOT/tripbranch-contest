"""음성 입력 전사 API.

`POST /api/transcribe`는 WAV 본문을 Gemini Audio API에 전달해 텍스트만 돌려준다.
전사 결과를 Agent에 자동 전송하지 않으므로, 프론트가 입력창에 먼저 표시한 뒤 기존
`/api/chat` 경로로 동일하게 처리한다.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Request

from app.auth.dependency import OptionalPrincipal
from app.errors import AppError
from app.providers.factory import get_gemini_audio_transcriber
from app.schemas import TranscriptionResponse

router = APIRouter(tags=["transcription"])

# 브라우저가 WAV(16-bit mono)로 보내므로 MIME을 하나로 고정한다. MediaRecorder의
# webm/mp4를 그대로 Gemini에 넘기지 않아 브라우저마다 지원 형식이 달라지는 문제를 막는다.
_WAV_MIME_TYPES = frozenset({"audio/wav", "audio/x-wav"})
_MAX_AUDIO_BYTES = 10 * 1024 * 1024


@router.post("/transcribe", response_model=TranscriptionResponse)
async def transcribe(request: Request, principal: OptionalPrincipal) -> TranscriptionResponse:
    """짧은 WAV 녹음을 전사한다. 녹음 파일은 저장하지 않고 요청 메모리에서만 쓴다."""
    mime_type = request.headers.get("content-type", "").split(";", maxsplit=1)[0].strip()
    if mime_type not in _WAV_MIME_TYPES:
        raise AppError(
            code="unsupported_audio_format",
            message="지원하지 않는 음성 형식이에요. 다시 녹음해 주세요.",
            status_code=415,
        )

    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > _MAX_AUDIO_BYTES:
        raise _audio_too_large_error()

    audio_bytes = await request.body()
    if not audio_bytes:
        raise AppError(
            code="empty_audio",
            message="녹음된 음성이 없어요. 다시 말씀해 주세요.",
            status_code=422,
            retryable=True,
        )
    if len(audio_bytes) > _MAX_AUDIO_BYTES:
        raise _audio_too_large_error()

    started = time.perf_counter()
    transcriber = get_gemini_audio_transcriber()
    text, _ = await transcriber.transcribe(audio_bytes=audio_bytes, mime_type="audio/wav")
    return TranscriptionResponse(
        text=text,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        model=transcriber.model_name,
    )


def _audio_too_large_error() -> AppError:
    return AppError(
        code="audio_too_large",
        message="음성은 1분 이내로 녹음해 주세요.",
        status_code=413,
        retryable=False,
    )
