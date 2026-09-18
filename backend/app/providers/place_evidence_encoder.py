"""취향 질의를 벡터로 바꾸는 ko-sroberta 인코더.

적재와 **같은 모델·같은 정규화**를 써야 유사도가 의미를 갖는다. 적재는
`jhgan/ko-sroberta-multitask`(768차원)로 `normalize_embeddings=True` 상태에서
했다(scripts/measure_similarity_distribution.py와 동일).

`sentence-transformers`는 선택 의존성이다(`pip install -e ".[embeddings]"`).
torch를 끌고 와 설치가 무겁고, 모델을 서버에 상주시키면 RSS가 약 1.2GB 늘어
배포 인스턴스 메모리가 2GB 이상이어야 한다. 그래서 **import를 모듈 최상단이
아니라 로딩 시점에** 두고, 없으면 무엇을 설치해야 하는지 알려주며 멈춘다.

모델은 첫 encode() 때 한 번만 적재한다(지연 로딩). 서버 기동 시 미리 올리려면
lifespan에서 warmup()을 부르면 된다 — 안 부르면 첫 요청이 모델 적재 시간을
그대로 뒤집어쓴다.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - 타입 검사 전용
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

# 적재 때 쓴 모델. 바꾸면 이미 적재된 40,389조각을 전부 다시 임베딩해야 한다.
MODEL_NAME = "jhgan/ko-sroberta-multitask"

_INSTALL_HINT = (
    "취향 근거 검색에는 sentence-transformers가 필요합니다. "
    'backend에서 `pip install -e ".[embeddings]"`로 설치하세요.'
)


class KoSrobertaEncoder:
    """PlaceEvidenceEncoder 구현체 — 프로세스당 모델 한 벌을 상주시킨다."""

    def __init__(self, model_name: str = MODEL_NAME) -> None:
        self._model_name = model_name
        self._model: SentenceTransformer | None = None
        # 예열 스레드와 첫 요청이 동시에 적재하는 것을 막는다. 두 벌이 올라가면
        # 순간 RSS가 두 배가 된다.
        self._lock = threading.Lock()

    def warmup(self) -> None:
        """모델을 미리 적재한다. 서버 기동 시 부르면 첫 요청이 느려지지 않는다."""
        self._load()

    def warmup_in_background(self) -> threading.Thread:
        """적재를 백그라운드로 돌리고 즉시 돌아온다.

        적재가 실측 9.4초라(2026-08-19) 동기로 부르면 그만큼 부팅이 늦어진다.
        서버는 먼저 뜨고 모델은 뒤따라 올라온다 — 적재 중에 취향 요청이 오면
        `_load()`의 락에서 기다렸다가 처리된다.
        """
        thread = threading.Thread(
            target=self._warmup_quietly, name="taste-encoder-warmup", daemon=True
        )
        thread.start()
        return thread

    def _warmup_quietly(self) -> None:
        try:
            self._load()
        except Exception:
            logger.exception("취향 임베딩 모델 예열 실패 — 취향 Feature 없이 동작한다")

    def _load(self) -> SentenceTransformer:
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                try:
                    from sentence_transformers import SentenceTransformer
                except ImportError as error:  # pragma: no cover - 설치 환경 의존
                    raise RuntimeError(_INSTALL_HINT) from error
                logger.info("취향 임베딩 모델 적재 시작 (model=%s)", self._model_name)
                self._model = SentenceTransformer(self._model_name)
                logger.info("취향 임베딩 모델 적재 완료 (model=%s)", self._model_name)
        return self._model

    def encode(self, text: str) -> Sequence[float]:
        """적재와 같은 조건(정규화 임베딩)으로 인코딩한다."""
        model = self._load()
        return model.encode(text, normalize_embeddings=True).tolist()


# 프로세스에 하나만 둔다. 인코더를 요청마다 새로 만들면 모델도 매번 적재돼
# 요청 1건당 9.4초와 537MB가 더 붙는다(2026-08-19 실측). lifespan의 예열과
# 요청 경로가 **같은 인스턴스**를 봐야 예열이 의미를 갖는다.
_shared_encoder: KoSrobertaEncoder | None = None
_shared_encoder_lock = threading.Lock()


def get_shared_encoder() -> KoSrobertaEncoder:
    """프로세스 공용 인코더를 돌려준다(없으면 만든다)."""
    global _shared_encoder
    if _shared_encoder is None:
        with _shared_encoder_lock:
            if _shared_encoder is None:
                _shared_encoder = KoSrobertaEncoder()
    return _shared_encoder
