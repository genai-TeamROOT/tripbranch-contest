"""Google Cloud Translation Basic(v2) 실제 구현.

영어 UI의 입력은 한국어 Runtime으로 보내고, Runtime이 만든 사용자 노출 문장은
다시 영어로 바꾼다. API 키는 서버에서만 사용하며, 장소 ID·좌표·Intent 같은
구조화 값은 이 provider에 전달하지 않는다.
"""

from __future__ import annotations

import html

import httpx

from app.errors import ProviderTimeoutError, ProviderUnavailableError

_TRANSLATE_URL = "https://translation.googleapis.com/language/translate/v2"


class GoogleTranslateProvider:
    """Cloud Translation Basic(v2)의 텍스트 묶음 번역 client."""

    def __init__(self, *, api_key: str, client: httpx.AsyncClient, timeout_seconds: float) -> None:
        self._api_key = api_key
        self._client = client
        self._timeout_seconds = timeout_seconds

    async def translate_many(
        self, texts: list[str], *, source_language: str, target_language: str
    ) -> list[str]:
        """순서를 보존한 채 텍스트를 한 번의 API 호출로 번역한다."""

        if not texts:
            return []
        try:
            response = await self._client.post(
                _TRANSLATE_URL,
                params={"key": self._api_key},
                json={
                    "q": texts,
                    "source": source_language,
                    "target": target_language,
                    "format": "text",
                },
                timeout=self._timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Google Cloud Translation") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError("Google Cloud Translation", detail=str(exc)) from exc

        if response.is_error:
            detail = ""
            try:
                payload = response.json()
                detail = str(payload.get("error", {}).get("message", ""))
            except ValueError:
                detail = response.text[:500]
            raise ProviderUnavailableError("Google Cloud Translation", detail=detail)

        try:
            translated = response.json()["data"]["translations"]
            values = [html.unescape(str(item["translatedText"])) for item in translated]
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderUnavailableError(
                "Google Cloud Translation", detail="응답 형식에 translations가 없습니다."
            ) from exc
        if len(values) != len(texts):
            raise ProviderUnavailableError(
                "Google Cloud Translation",
                detail=f"번역 개수 불일치: requested={len(texts)} received={len(values)}",
            )
        return values


__all__ = ["GoogleTranslateProvider"]
