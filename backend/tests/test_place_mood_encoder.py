"""SigLIP 인코더가 열 수 없는 사진을 어떻게 다루는지 검증한다.

**모델을 적재하지 않는다.** 열기 실패는 모델보다 앞 단계에서 나므로, 가짜
model/processor를 넣어 그 경로만 본다 — 진짜 SigLIP을 올리면 테스트 한 건에
44초가 붙는다.
"""

from __future__ import annotations

import io

import pytest

from app.providers.place_mood_encoder import SiglipImageEncoder, UnreadableImageError

pytest.importorskip("PIL", reason="pillow는 선택 의존성이다([mood]).")


def _encoder() -> SiglipImageEncoder:
    encoder = SiglipImageEncoder()
    # _load()가 모델을 적재하지 않게 미리 채운다. 열기 실패는 여기까지
    # 오기 전에 나므로 내용은 쓰이지 않는다.
    encoder._model = object()
    encoder._processor = object()
    return encoder


def test_non_image_bytes_raise_unreadable() -> None:
    """확장자만 사진인 파일. MIME 검사로는 못 걸러진다."""
    with pytest.raises(UnreadableImageError):
        _encoder().encode_image(b"this is not an image")


def test_truncated_image_raises_unreadable() -> None:
    """전송 중 잘린 사진. 헤더는 맞아 열리다가 깨진다."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (120, 120, 120)).save(buffer, format="JPEG")
    truncated = buffer.getvalue()[:80]

    with pytest.raises(UnreadableImageError):
        _encoder().encode_image(truncated)


def test_empty_bytes_raise_unreadable() -> None:
    with pytest.raises(UnreadableImageError):
        _encoder().encode_image(b"")
