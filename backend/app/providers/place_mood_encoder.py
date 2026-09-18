"""올린 사진을 벡터로 바꾸는 SigLIP 인코더.

적재와 **같은 모델·같은 정규화**를 써야 유사도가 뜻을 갖는다. 적재는
`google/siglip2-base-patch16-224`(768차원)로 길이 1 정규화 상태에서 했다
(scripts/import_mood_embeddings.py가 저장한 벡터와 같은 조건).

`transformers`·`torch`·`pillow`는 선택 의존성이다(`pip install -e ".[mood]"`).
텍스트 인코더(place_mood_encoder의 이웃인 place_evidence_encoder)와 같은 이유로
모듈 최상단이 아니라 **로딩 시점에** import하고, 없으면 무엇을 설치해야 하는지
알려주며 멈춘다. 조용히 넘어가면 이 Provider가 있는 줄 알고 켜 둔 배포에서
사진 검색만 소리 없이 사라진다.

모델은 첫 encode_image() 때 한 번만 적재한다(지연 로딩).

**checkpoint 이름이 siglip2인데 config의 model_type은 siglip이다.** 그래서
AutoModel이 Siglip2Model이 아니라 SiglipModel을 준다 — 정상이다. Siglip2Model은
해상도가 가변인 NaFlex 계열용이고, 우리가 쓰는 patch16-224는 고정 해상도라
기존 SigLIP 클래스를 그대로 탄다.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - 타입 검사 전용
    pass

logger = logging.getLogger(__name__)

# 적재 때 쓴 모델. 바꾸면 이미 적재된 사진 2,263장과 장소 631곳을 전부 다시
# 임베딩해야 한다. so400m 계열로 가면 1152차원이 되어 컬럼 타입까지 바뀐다.
MODEL_NAME = "google/siglip2-base-patch16-224"

class UnreadableImageError(Exception):
    """올린 파일을 사진으로 열 수 없다.

    사용자 입력 문제이지 서버 장애가 아니라는 것을 호출부가 구분할 수 있게
    전용 예외로 둔다 — Pillow 예외를 그대로 올리면 라우트가 500으로 감싼다.
    """


_INSTALL_HINT = (
    "사진 분위기 검색에는 transformers·torch·pillow가 필요합니다. "
    'backend에서 `pip install -e ".[mood]"`로 설치하세요.'
)


class SiglipImageEncoder:
    """PlaceMoodEncoder 구현체 — 프로세스당 모델 한 벌을 상주시킨다."""

    def __init__(self, model_name: str = MODEL_NAME) -> None:
        self._model_name = model_name
        self._model: Any | None = None
        self._processor: Any | None = None
        # 예열 스레드와 첫 요청이 동시에 적재하는 것을 막는다. 두 벌이 올라가면
        # 순간 RSS가 두 배가 된다.
        self._lock = threading.Lock()

    def warmup(self) -> None:
        """모델을 미리 적재한다. 서버 기동 시 부르면 첫 요청이 느려지지 않는다."""
        self._load()

    def warmup_in_background(self) -> threading.Thread:
        """적재를 백그라운드로 돌리고 즉시 돌아온다.

        서버는 먼저 뜨고 모델은 뒤따라 올라온다 — 적재 중에 사진 요청이 오면
        `_load()`의 락에서 기다렸다가 처리된다.
        """
        thread = threading.Thread(
            target=self._warmup_quietly, name="mood-encoder-warmup", daemon=True
        )
        thread.start()
        return thread

    def _warmup_quietly(self) -> None:
        try:
            self._load()
        except Exception:
            logger.exception("사진 임베딩 모델 예열 실패 — 사진 검색 없이 동작한다")

    def _load(self) -> tuple[Any, Any]:
        if self._model is not None and self._processor is not None:
            return self._model, self._processor
        with self._lock:
            if self._model is None or self._processor is None:
                try:
                    import torch  # noqa: F401
                    from transformers import AutoModel, AutoProcessor
                except ImportError as error:  # pragma: no cover - 설치 환경 의존
                    raise RuntimeError(_INSTALL_HINT) from error
                logger.info("사진 임베딩 모델 적재 시작 (model=%s)", self._model_name)
                self._processor = AutoProcessor.from_pretrained(self._model_name)
                model = AutoModel.from_pretrained(self._model_name)
                model.eval()
                self._model = model
                logger.info("사진 임베딩 모델 적재 완료 (model=%s)", self._model_name)
        return self._model, self._processor

    def encode_image(self, image_bytes: bytes) -> Sequence[float]:
        """적재와 같은 조건(RGB 변환 + 길이 1 정규화)으로 인코딩한다.

        열 수 없는 사진은 `UnreadableImageError`로 바꿔 던진다. 그대로 두면
        Pillow 예외가 라우트까지 올라가 500이 되는데, 그건 서버 잘못이라는
        뜻이라 사용자 입력 문제와 섞인다.
        """
        import io

        import torch
        from PIL import Image, UnidentifiedImageError

        model, processor = self._load()
        # 적재 때 RGB로 변환했다. 흑백(L)이나 투명 채널(RGBA)이 그대로 들어가면
        # 채널 수가 달라 processor가 실패하거나 다른 값을 낸다.
        try:
            with Image.open(io.BytesIO(image_bytes)) as raw:
                image = raw.convert("RGB")
                inputs = processor(images=image, return_tensors="pt")
        except UnidentifiedImageError as error:
            # 확장자만 사진인 파일, 전송 중 잘린 사진, 브라우저가 만든 형식 중
            # Pillow가 모르는 것. MIME 검사로는 못 걸러진다 — content-type은
            # 브라우저가 파일 이름으로 붙이는 값이라 내용과 무관하다.
            raise UnreadableImageError("사진 형식을 알아볼 수 없습니다.") from error
        except OSError as error:
            # 잘린 사진은 열리다가 여기서 깨진다("image file is truncated").
            raise UnreadableImageError("사진이 손상됐습니다.") from error

        with torch.no_grad():
            features = _as_tensor(model.get_image_features(**inputs))

        # 길이 1로 정규화한다. 적재 벡터가 정규화돼 있어 이쪽도 맞춰야 내적이
        # 코사인 유사도가 된다.
        normalized = features / features.norm(dim=-1, keepdim=True)
        return normalized[0].float().tolist()


def _as_tensor(output: Any) -> Any:
    """`get_image_features()`의 반환형을 텐서로 맞춘다.

    transformers 버전에 따라 텐서를 그대로 주기도 하고 출력 객체
    (`BaseModelOutputWithPooling`)를 주기도 한다. 적재에 쓴 코랩 노트북에서 먼저
    겪은 문제이며, 같은 처리를 여기에도 둔다 — 여기서 갈라지면 적재 벡터와
    질의 벡터가 다른 방식으로 만들어진다.
    """
    import torch

    if torch.is_tensor(output):
        return output
    for name in ("pooler_output", "image_embeds", "last_hidden_state"):
        value = getattr(output, name, None)
        if torch.is_tensor(value):
            # last_hidden_state는 토큰별 벡터라 평균으로 접는다.
            return value.mean(dim=1) if name == "last_hidden_state" else value
    raise RuntimeError(f"임베딩을 찾지 못했습니다: {type(output).__name__}")


# 프로세스에 하나만 둔다. 인코더를 요청마다 새로 만들면 모델도 매번 적재된다.
# lifespan의 예열과 요청 경로가 **같은 인스턴스**를 봐야 예열이 의미를 갖는다.
_shared_encoder: SiglipImageEncoder | None = None
_shared_encoder_lock = threading.Lock()


def get_shared_encoder() -> SiglipImageEncoder:
    """프로세스 공용 인코더를 돌려준다(없으면 만든다)."""
    global _shared_encoder
    if _shared_encoder is None:
        with _shared_encoder_lock:
            if _shared_encoder is None:
                _shared_encoder = SiglipImageEncoder()
    return _shared_encoder
