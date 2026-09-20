"""관광공사 이미지가 없는 장소의 대표 사진을 Google Places에서 채우는 Provider.

역할: 장소명·주소·좌표로 Google Places를 찾아 대표 사진 1장과 **그 사진을 표시할
      때 함께 내보내야 하는 출처 정보**를 준다.
입력: 장소명(필수)과 주소·좌표(있으면 정확도를 올린다).
출력: GooglePlaceCoverPhoto 1건. 찾지 못했거나 사진이 없으면 None.
호출 시점: 추천 카드를 조립할 때, `thumbnail_url`이 비어 있는 장소에만
          (RecommendationCardTool.get_cards).

## 왜 필요한가

places 8,067건 중 844건은 `first_image_url`·`thumbnail_url`이 둘 다 비어 있다.
그중 843건은 `place_image_embeddings`에도 행이 없어 추가 사진으로도 못 채운다
(2026-09-20 실측). 활성 상태이고 추천 후보로 쓰이는 유형만 세면 607곳이다. 이
장소들은 지금 카드에서 자리표시 아이콘만 나온다.

## 출처 정보를 함께 받는 이유

Google Maps Platform 정책은 사진을 표시할 때 **작성자를 반드시 밝히라고** 요구한다
("You must always credit the author when displaying photos or reviews"). 또 사용자가
`googleMapsUri`로 원본 사진을 Google 지도에서 볼 수 있어야 한다. 그래서 사진 주소만
받아오면 정책을 지킬 수 없다 — 작성자 이름·프로필 링크·원본 링크를 같이 받아
카드까지 들고 간다. 출처를 표시할 수 없으면 사진도 쓰지 않는다.

## 호출은 장소당 2회다

1. Text Search로 장소를 찾으면서 사진 메타데이터(이름·출처)까지 같이 받는다.
   우리 DB에 `google_place_id`가 없어 검색부터 해야 한다 — 예전에 적재했던
   `place_google_profiles`는 쓰이지 않아 2026-09-11에 삭제됐다.
2. 받은 사진 이름으로 표시용 주소를 받는다.

`skipHttpRedirect=true`를 쓴다. 이 값이 없으면 Google이 이미지 파일로 302
리다이렉트하므로 서버가 이미지 바이트를 받아 다시 내려보내야 한다. JSON으로
주소만 받아 프론트에 넘기면 이미지는 브라우저가 Google에서 직접 받는다.

## 받은 주소를 DB에 저장하지 않는 이유

정책이 Places 콘텐츠의 사전 수집·캐시·저장을 금지한다(place ID만 예외). 표시용
주소도 만료된다. 그래서 `hybrid_place_photos.py`와 같은 방식으로 프로세스
메모리에만 짧게 들고 있다가 TTL이 지나면 다시 받는다. 캐시와 래치가 모듈 수준인
이유도 같다 — 이 Provider는 요청마다 새로 만들어지므로 인스턴스에 담으면 요청이
끝날 때 함께 사라진다.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from app.errors import ProviderTimeoutError, ProviderUnavailableError

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")

_PROVIDER_LABEL = "Google Places"
_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
_PHOTO_URL_TEMPLATE = "https://places.googleapis.com/v1/{photo_name}/media"

# Text Search가 돌려줄 필드. 필드를 넓게 잡을수록 비싼 SKU로 과금되므로 사진을
# 표시하는 데 꼭 필요한 것만 받는다 — 사진 이름, 작성자 출처, 원본 링크다.
# 이 헤더가 없으면 요청 자체가 400으로 거절된다.
_SEARCH_FIELD_MASK = (
    "places.photos.name,places.photos.authorAttributions,places.photos.googleMapsUri"
)

# 검색 결과를 몇 건 받을지. 1건이면 충분하다 — 이름과 주소로 특정한 장소를
# 찾는 것이지 후보를 고르는 것이 아니다.
_MAX_RESULT_COUNT = 1

# 좌표를 줄 때 검색을 묶을 반경(m). 같은 이름의 가게가 여러 구에 있을 때
# 엉뚱한 지점의 사진을 가져오는 것을 막는다. 관광공사 좌표와 Google 좌표가
# 건물 단위로 어긋나는 경우가 있어 너무 좁히지 않는다.
_LOCATION_BIAS_RADIUS_M = 500.0

_CACHE_PURGE_THRESHOLD = 1000

# 한도·결제 문제로 막힌 KST 날짜. 같은 날에는 다시 부르지 않는다.
# TourAPI 사진 경로(hybrid_place_photos.py)와 같은 규칙이다 — 그날 안에는 무엇을
# 해도 실패하는데 계속 던지면 응답만 느려진다.
_blocked_on: str | None = None


@dataclass(frozen=True)
class GooglePlaceCoverPhoto:
    """카드에 걸 사진 1장과 정책이 요구하는 출처 정보.

    `author_name`이 없는 사진은 만들지 않는다. 출처를 밝힐 수 없는 사진은
    쓰지 않기로 했기 때문에, 이 객체가 있다는 것은 곧 표시해도 된다는 뜻이다.
    """

    url: str
    author_name: str
    # 작성자 프로필 링크. 정책은 이름과 함께 프로필 링크를 걸라고 한다.
    author_uri: str | None = None
    # 사용자가 원본 사진을 Google 지도에서 볼 수 있어야 한다는 요구를 채운다.
    google_maps_uri: str | None = None


# 캐시 키 → (만료 시각(monotonic), 사진). None은 "Google에도 쓸 사진이 없다"는
# 확인된 사실이며 그것도 캐시한다 — 그러지 않으면 사진 없는 장소가 카드에 뜰
# 때마다 2회씩 계속 호출된다.
_photo_cache: dict[str, tuple[float, GooglePlaceCoverPhoto | None]] = {}


def _today() -> str:
    return datetime.now(_KST).date().isoformat()


def _is_blocked() -> bool:
    return _blocked_on == _today()


def _mark_blocked() -> None:
    global _blocked_on
    _blocked_on = _today()


def reset_google_photo_state() -> None:
    """캐시와 래치를 비운다. 테스트가 모듈 상태를 격리하는 데 쓴다."""
    global _blocked_on
    _photo_cache.clear()
    _blocked_on = None


class FakeGooglePlacePhotoProvider:
    """호출 없이 정해진 답을 주는 Fake.

    실제 응답의 모양을 그대로 재현한다. 특히 **사진을 못 찾는 경우가 있어야
    한다** — 이 경로는 "못 찾으면 원래대로 자리표시를 그린다"가 정상 동작이라,
    Fake가 항상 사진을 주면 그 분기가 한 번도 실행되지 않은 채 테스트가 통과한다.
    """

    # 이름에 이 문자열이 들어간 장소는 사진이 없는 것으로 답한다.
    NO_PHOTO_MARKER = "사진없음"

    def __init__(self, photo: GooglePlaceCoverPhoto | None = None) -> None:
        self._photo = photo or GooglePlaceCoverPhoto(
            url="https://example.test/google-place-photo.jpg",
            author_name="가짜 작성자",
            author_uri="https://maps.google.com/maps/contrib/fake",
            google_maps_uri="https://www.google.com/maps/place/fake",
        )

    async def find_cover_photo(
        self,
        *,
        name: str,
        address: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> GooglePlaceCoverPhoto | None:
        if not name.strip():
            return None
        if self.NO_PHOTO_MARKER in name:
            return None
        return self._photo


class GooglePlacePhotoProvider:
    """Google Places API(New)로 대표 사진 1장과 그 출처를 찾는다."""

    def __init__(
        self,
        *,
        api_key: str,
        client: httpx.AsyncClient,
        timeout_seconds: float,
        max_width_px: int,
        cache_ttl_seconds: int,
    ) -> None:
        self._api_key = api_key
        self._client = client
        self._timeout_seconds = timeout_seconds
        self._max_width_px = max_width_px
        self._cache_ttl_seconds = cache_ttl_seconds

    async def find_cover_photo(
        self,
        *,
        name: str,
        address: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> GooglePlaceCoverPhoto | None:
        """장소 한 곳의 대표 사진을 찾는다. 쓸 사진이 없으면 None.

        예외를 밖으로 내보내지 않는다. 사진은 있으면 좋은 값이지 추천의 성립
        조건이 아니다 — 여기서 올린 예외가 추천 전체를 실패시키면 사진 하나
        때문에 응답을 통째로 잃는다.
        """
        query = self._build_query(name=name, address=address)
        if query is None:
            return None

        cache_key = self._cache_key(query=query, latitude=latitude, longitude=longitude)
        cached = self._cached(cache_key)
        if cached is not None:
            return cached[0]

        if _is_blocked():
            return None

        try:
            found = await self._find_photo(
                query=query, latitude=latitude, longitude=longitude
            )
            if found is None:
                # "쓸 사진이 없다"는 확인된 사실이라 캐시한다.
                self._remember(cache_key, None)
                return None
            photo_name, attribution = found
            photo_url = await self._resolve_photo_url(photo_name)
        except ProviderUnavailableError as exc:
            if self._is_blocking_failure(exc):
                _mark_blocked()
                logger.warning(
                    "Google Places 사진 보강이 오늘은 멈춥니다(한도·권한 문제): detail=%s",
                    exc.details,
                )
                return None
            # 일시 오류는 캐시하지 않는다. 다음 요청에서 다시 시도한다.
            logger.info("Google Places 사진을 받지 못했습니다(다음 요청에 재시도): name=%s", name)
            return None
        except ProviderTimeoutError:
            logger.info("Google Places 사진 조회가 시간 안에 끝나지 않았습니다: name=%s", name)
            return None

        if photo_url is None:
            self._remember(cache_key, None)
            return None

        photo = GooglePlaceCoverPhoto(
            url=photo_url,
            author_name=attribution.author_name,
            author_uri=attribution.author_uri,
            google_maps_uri=attribution.google_maps_uri,
        )
        self._remember(cache_key, photo)
        return photo

    async def _find_photo(
        self,
        *,
        query: str,
        latitude: float | None,
        longitude: float | None,
    ) -> tuple[str, GooglePlaceCoverPhoto] | None:
        """검색 결과에서 쓸 수 있는 첫 사진의 이름과 출처를 뽑는다.

        **출처를 못 밝히는 사진은 건너뛴다.** 정책이 작성자 표기를 요구하므로,
        작성자가 없는 사진은 화면에 걸 수 없어 없는 것과 같다. 첫 사진만 보고
        포기하지 않고 다음 사진을 본다 — 한 장소의 사진 여러 장 중 일부만
        작성자가 비어 있을 수 있다.
        """
        body: dict[str, object] = {
            "textQuery": query,
            "languageCode": "ko",
            "maxResultCount": _MAX_RESULT_COUNT,
        }
        if latitude is not None and longitude is not None:
            body["locationBias"] = {
                "circle": {
                    "center": {"latitude": latitude, "longitude": longitude},
                    "radius": _LOCATION_BIAS_RADIUS_M,
                }
            }

        payload = await self._post_json(
            _SEARCH_URL,
            json=body,
            headers={"X-Goog-FieldMask": _SEARCH_FIELD_MASK},
        )
        places = payload.get("places")
        if not isinstance(places, list) or not places:
            return None
        first = places[0]
        if not isinstance(first, dict):
            return None
        photos = first.get("photos")
        if not isinstance(photos, list):
            return None

        for photo in photos:
            if not isinstance(photo, dict):
                continue
            photo_name = photo.get("name")
            if not isinstance(photo_name, str) or not photo_name.strip():
                continue
            attribution = self._attribution_of(photo)
            if attribution is None:
                continue
            return photo_name, attribution
        return None

    @staticmethod
    def _attribution_of(photo: dict[str, object]) -> GooglePlaceCoverPhoto | None:
        """사진 한 장의 출처를 읽는다. 작성자를 못 찾으면 None.

        url은 아직 모르므로 빈 문자열로 둔다 — 호출부가 사진 주소를 받은 뒤
        채운다. 출처만 담는 별도 타입을 만들지 않은 이유는 필드가 같아서다.
        """
        attributions = photo.get("authorAttributions")
        if not isinstance(attributions, list) or not attributions:
            return None
        first = attributions[0]
        if not isinstance(first, dict):
            return None
        author_name = first.get("displayName")
        if not isinstance(author_name, str) or not author_name.strip():
            return None
        author_uri = first.get("uri")
        google_maps_uri = photo.get("googleMapsUri")
        return GooglePlaceCoverPhoto(
            url="",
            author_name=author_name.strip(),
            author_uri=author_uri if isinstance(author_uri, str) and author_uri else None,
            google_maps_uri=(
                google_maps_uri
                if isinstance(google_maps_uri, str) and google_maps_uri
                else None
            ),
        )

    async def _resolve_photo_url(self, photo_name: str) -> str | None:
        url = _PHOTO_URL_TEMPLATE.format(photo_name=photo_name)
        payload = await self._get_json(
            url,
            params={
                "maxWidthPx": self._max_width_px,
                # 이미지 파일로 리다이렉트하지 말고 주소를 JSON으로 달라는 뜻이다.
                # 서버가 이미지 바이트를 중계하지 않기 위한 값이라 빼면 안 된다.
                "skipHttpRedirect": "true",
            },
        )
        photo_uri = payload.get("photoUri")
        if not isinstance(photo_uri, str) or not photo_uri.strip():
            return None
        return photo_uri

    async def _post_json(
        self,
        url: str,
        *,
        json: dict[str, object],
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        try:
            response = await self._client.post(
                url,
                json=json,
                headers={**self._auth_headers(), **(headers or {})},
                timeout=self._timeout_seconds,
            )
        except httpx.TimeoutException:
            raise ProviderTimeoutError(_PROVIDER_LABEL) from None
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(_PROVIDER_LABEL, detail=str(exc)) from None
        return self._parse(response)

    async def _get_json(self, url: str, *, params: dict[str, object]) -> dict[str, object]:
        try:
            response = await self._client.get(
                url,
                params=params,
                headers=self._auth_headers(),
                timeout=self._timeout_seconds,
            )
        except httpx.TimeoutException:
            raise ProviderTimeoutError(_PROVIDER_LABEL) from None
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(_PROVIDER_LABEL, detail=str(exc)) from None
        return self._parse(response)

    def _auth_headers(self) -> dict[str, str]:
        # 키를 쿼리스트링이 아니라 헤더로 보낸다. 실패 로그·예외에 URL이 남는
        # 경로가 있어 쿼리로 실으면 키가 그대로 새어 나갈 수 있다.
        return {"X-Goog-Api-Key": self._api_key}

    def _parse(self, response: httpx.Response) -> dict[str, object]:
        if response.is_error:
            detail = ""
            try:
                payload = response.json()
            except ValueError:
                detail = response.text[:500]
            else:
                if isinstance(payload, dict):
                    error = payload.get("error")
                    if isinstance(error, dict):
                        detail = f"{error.get('status', '')} {error.get('message', '')}".strip()
            raise ProviderUnavailableError(
                _PROVIDER_LABEL,
                detail=detail or f"HTTP {response.status_code}",
            )
        try:
            payload = response.json()
        except ValueError:
            raise ProviderUnavailableError(
                _PROVIDER_LABEL, detail="응답이 JSON이 아닙니다."
            ) from None
        if not isinstance(payload, dict):
            raise ProviderUnavailableError(
                _PROVIDER_LABEL, detail="응답이 객체가 아닙니다."
            )
        return payload

    @staticmethod
    def _is_blocking_failure(exc: ProviderUnavailableError) -> bool:
        """오늘 안에는 다시 시도해도 소용없는 실패인지.

        한도 초과·권한 거부·결제 미설정이 여기 해당한다. 네트워크 오류나 5xx는
        쉬었다 부르면 성공할 수 있으므로 래치를 걸지 않는다.
        """
        detail = str(exc.details or "").upper()
        return any(
            marker in detail
            for marker in (
                "RESOURCE_EXHAUSTED",
                "PERMISSION_DENIED",
                "QUOTA",
                "BILLING",
                "HTTP 429",
            )
        )

    @staticmethod
    def _build_query(*, name: str, address: str | None) -> str | None:
        cleaned_name = name.strip()
        if not cleaned_name:
            return None
        cleaned_address = (address or "").strip()
        if not cleaned_address:
            return cleaned_name
        return f"{cleaned_name} {cleaned_address}"

    @staticmethod
    def _cache_key(*, query: str, latitude: float | None, longitude: float | None) -> str:
        if latitude is None or longitude is None:
            return query
        # 좌표가 검색 결과를 바꾸므로 키에 포함한다. 소수점 넷째 자리면 약 11m라
        # 같은 장소가 좌표 오차 때문에 다른 키로 갈리지 않는다.
        return f"{query}|{latitude:.4f},{longitude:.4f}"

    def _cached(self, key: str) -> tuple[GooglePlaceCoverPhoto | None] | None:
        entry = _photo_cache.get(key)
        if entry is None:
            return None
        expires_at, photo = entry
        if expires_at <= time.monotonic():
            _photo_cache.pop(key, None)
            return None
        # None도 정상 값(사진 없음)이라 튜플로 감싸 "캐시에 없음"과 구분한다.
        return (photo,)

    def _remember(self, key: str, photo: GooglePlaceCoverPhoto | None) -> None:
        if self._cache_ttl_seconds <= 0:
            return
        if len(_photo_cache) >= _CACHE_PURGE_THRESHOLD:
            self._purge_expired()
        _photo_cache[key] = (time.monotonic() + self._cache_ttl_seconds, photo)

    @staticmethod
    def _purge_expired() -> None:
        now = time.monotonic()
        expired = [key for key, (expires_at, _) in _photo_cache.items() if expires_at <= now]
        for key in expired:
            _photo_cache.pop(key, None)


__all__ = [
    "FakeGooglePlacePhotoProvider",
    "GooglePlaceCoverPhoto",
    "GooglePlacePhotoProvider",
    "reset_google_photo_state",
]
