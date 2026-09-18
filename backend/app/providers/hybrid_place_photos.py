"""장소 사진을 저장소 먼저, 없으면 TourAPI로 채우는 Provider.

역할: `PlacePhotoRepository` 계약을 두 출처로 만족시킨다. 적재된 장소는
      `place_image_embeddings`에서 읽고, 적재되지 않은 장소만 detailImage2를
      부른다.
입력: content_id 목록.
출력: content_id를 키로 하는 PlacePhoto 튜플. 사진이 없는 장소는 키가 없다.
호출 시점: INFO 장소 상세 응답을 조립할 때(ContextService._fetch_place_photos).

## 저장소를 먼저 보는 이유

TourAPI를 먼저 부르는 안도 재봤지만 신선함 이득이 비용에 비해 작았다
(2026-08-31 실측). 적재 후 원본이 바뀐 장소가 5,465곳 중 4곳(0.07%)이고,
최근 90일 기준으로도 하루 3.4곳 꼴이다. 반면 API 우선은 **모든 요청**이
호출 1회와 수백 ms를 낸다. 신선함은 여기가 아니라 재적재 주기가 정한다.

## 캐시가 DB가 아닌 이유

받은 사진을 `place_image_embeddings`에 쓸 수 없다 — 그 테이블은
`embedding vector(768) not null`이라 SigLIP으로 벡터를 만들어야 행이 들어가고,
요청 경로에는 인코더가 없다. 표시용 테이블을 새로 만들지 않기로 해서, 재사용
수단은 이 프로세스 메모리 캐시뿐이다.

## 캐시와 래치가 모듈 수준인 이유

이 Provider는 요청마다 새로 만들어진다(`create_external_client()` 안에서
`get_context_provider()`가 조립한다). 인스턴스에 담으면 요청이 끝날 때 같이
사라져 한 번도 재사용되지 않는다. `service.py`의 `_stale_area_probe_cache`와
같은 이유다.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

from app.domain.models import PlacePhoto
from app.errors import AppError
from app.providers.protocols import PlaceImageProvider
from app.providers.upstream_errors import is_daily_quota_exceeded
from app.repositories.protocols import PlacePhotoRepository

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")

# content_id → (만료 시각(monotonic), 사진). 빈 튜플은 "API에 사진이 없다"는
# 사실이며 그것도 캐시한다 — 적재된 5,465곳 중 2,749곳(50%)이 detailImage2가
# 비어 있던 장소라, 이 빈 응답을 반복해서 받는 것이 호출의 절반을 차지한다.
_photo_cache: dict[str, tuple[float, tuple[PlacePhoto, ...]]] = {}

# 만료된 항목을 정리하는 크기 기준. 미적재 장소가 2,595곳이라 상한이 낮지만,
# 적재 범위가 바뀌면 이 숫자도 바뀌므로 크기로만 판단한다.
_CACHE_PURGE_THRESHOLD = 1000

# 일일 한도를 소진한 KST 날짜. 같은 날에는 다시 부르지 않는다.
#
# 래치가 없으면 소진 뒤 모든 상세 조회가 "실패한 호출의 지연"을 먼저 기다린 다음
# 대표 이미지를 그린다. place_sync.py가 같은 규칙을 쓴다 — 그날 안에는 무엇을 해도
# 실패하는데 계속 던지면 한도만 더 태운다.
_quota_exhausted_on: str | None = None


def _today() -> str:
    return datetime.now(_KST).date().isoformat()


def _quota_is_exhausted() -> bool:
    return _quota_exhausted_on == _today()


def _mark_quota_exhausted() -> None:
    global _quota_exhausted_on
    _quota_exhausted_on = _today()


def reset_photo_api_state() -> None:
    """캐시와 래치를 비운다. 테스트가 모듈 상태를 격리하는 데 쓴다."""
    global _quota_exhausted_on
    _photo_cache.clear()
    _quota_exhausted_on = None


class HybridPlacePhotoProvider:
    """적재된 사진을 우선 쓰고, 없는 장소만 TourAPI로 채운다."""

    def __init__(
        self,
        *,
        photo_repository: PlacePhotoRepository,
        image_provider: PlaceImageProvider,
        display_limit: int,
        cache_ttl_seconds: int,
    ) -> None:
        self._repository = photo_repository
        self._provider = image_provider
        self._display_limit = display_limit
        self._cache_ttl_seconds = cache_ttl_seconds

    async def find_place_photos(
        self,
        content_ids: Sequence[str],
    ) -> dict[str, tuple[PlacePhoto, ...]]:
        unique_ids = [content_id for content_id in dict.fromkeys(content_ids) if content_id]
        if not unique_ids:
            return {}

        # 저장소 조회 실패는 여기서 잡지 않는다. "저장소에 없다"와 "저장소를 못
        # 읽었다"를 뭉뚱그리면 저장소가 흔들릴 때 미적재로 오인해 적재된 장소
        # 전부에 API를 부르고, 한도를 그날치까지 태운다. 호출부가 사진 없음으로
        # 처리한다(ContextService._fetch_place_photos).
        stored = await self._repository.find_place_photos(unique_ids)

        found = {
            content_id: photos[: self._display_limit]
            for content_id, photos in stored.items()
            if photos
        }
        missing = [content_id for content_id in unique_ids if content_id not in found]
        for content_id in missing:
            photos = await self._photos_from_api(content_id)
            if photos:
                found[content_id] = photos
        return found

    async def _photos_from_api(self, content_id: str) -> tuple[PlacePhoto, ...]:
        """미적재 장소 한 곳을 detailImage2로 채운다.

        사진을 못 얻는 세 경우(사진 없음·한도 소진·일시 오류)를 서로 다르게
        다룬다. 화면은 세 경우 모두 대표 이미지 한 장으로 같지만, 다음 요청에서
        다시 부를지가 갈린다.
        """
        cached = self._cached(content_id)
        if cached is not None:
            return cached

        if _quota_is_exhausted():
            # 오늘은 무엇을 해도 실패한다. 캐시에도 적지 않는다 — 그렇게 적으면
            # 소진된 날 열린 장소가 TTL 동안 "사진 없음"으로 굳는다.
            return ()

        try:
            result = await self._provider.get_place_images(
                content_id, self._display_limit
            )
        except AppError as exc:
            if is_daily_quota_exceeded(str(exc.details or "")):
                _mark_quota_exhausted()
                logger.warning(
                    "detailImage2 일일 한도를 소진해 오늘은 사진 보충을 멈춥니다: "
                    "place_id=%s",
                    content_id,
                )
                return ()
            # 초당 한도(23)·타임아웃·5xx는 쉬었다 부르면 성공할 수 있다. 그 요청만
            # 사진 없이 넘어가고 캐시에는 적지 않는다.
            logger.info(
                "장소 사진을 TourAPI에서 받지 못했습니다(다음 요청에 다시 시도): "
                "place_id=%s code=%s",
                content_id,
                exc.code,
            )
            return ()

        photos = result.data[: self._display_limit]
        # 빈 결과도 캐시한다. "사진이 없다"는 확인된 사실이다.
        self._remember(content_id, photos)
        return photos

    def _cached(self, content_id: str) -> tuple[PlacePhoto, ...] | None:
        entry = _photo_cache.get(content_id)
        if entry is None:
            return None
        expires_at, photos = entry
        if expires_at <= time.monotonic():
            _photo_cache.pop(content_id, None)
            return None
        return photos

    def _remember(self, content_id: str, photos: tuple[PlacePhoto, ...]) -> None:
        if self._cache_ttl_seconds <= 0:
            return
        if len(_photo_cache) >= _CACHE_PURGE_THRESHOLD:
            self._purge_expired()
        _photo_cache[content_id] = (
            time.monotonic() + self._cache_ttl_seconds,
            photos,
        )

    @staticmethod
    def _purge_expired() -> None:
        now = time.monotonic()
        expired = [key for key, (expires_at, _) in _photo_cache.items() if expires_at <= now]
        for key in expired:
            _photo_cache.pop(key, None)


__all__ = ["HybridPlacePhotoProvider", "reset_photo_api_state"]
