"""Supabase 공개키(JWKS) 조회와 캐시 (D-062 4-1절).

역할: 토큰 서명 검증에 쓸 공개키를 받아와 kid별로 캐시한다.
입력: 토큰 헤더의 kid.
출력: PyJWK(공개키).
호출 시점: verify.verify_access_token()이 서명을 확인하기 직전에 호출한다.
TODO: 키 회전이 잦아지면 만료 시간 기반 갱신도 함께 둔다. 지금은 kid 미스가
      유일한 갱신 계기다.

이 프로젝트 Auth는 비대칭 키(ES256)로 서명하고 공개키를 공개한다. 그래서 백엔드는
비밀값을 하나도 보관하지 않는다 — `SUPABASE_URL`만 있으면 된다.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
import jwt

from app.auth.errors import TokenVerificationError
from app.config import settings

logger = logging.getLogger(__name__)

_JWKS_PATH = "/auth/v1/.well-known/jwks.json"
_TIMEOUT_SECONDS = 5.0

_cache: dict[str, jwt.PyJWK] = {}
_lock = asyncio.Lock()


def is_configured() -> bool:
    return bool(settings.supabase_url)


def issuer() -> str:
    """토큰의 `iss`로 기대하는 값. 다른 프로젝트가 발급한 토큰을 막는다."""
    return f"{settings.supabase_url.rstrip('/')}/auth/v1"


def jwks_url() -> str:
    """공개키 주소는 설정값에서만 만든다.

    토큰이 들고 오는 URL(`jku` 등)을 따라가지 않는다 — 공격자가 자기 키를 심을 수 있다.
    """
    return f"{settings.supabase_url.rstrip('/')}{_JWKS_PATH}"


def reset_cache() -> None:
    """테스트가 키 상태를 초기화할 때 쓴다."""
    _cache.clear()


async def get_signing_key(kid: str) -> jwt.PyJWK:
    """kid에 해당하는 공개키를 돌려준다.

    캐시에 없으면 JWKS를 다시 받아온다. 키 회전 시 캐시만 붙들고 있으면 그날
    전 요청이 401이 되므로, kid 미스는 오류가 아니라 갱신 신호로 다룬다.
    """
    key = _cache.get(kid)
    if key is not None:
        return key

    async with _lock:
        # 잠금을 기다리는 동안 다른 요청이 이미 채웠을 수 있다.
        key = _cache.get(kid)
        if key is not None:
            return key
        await _refresh()

    key = _cache.get(kid)
    if key is None:
        raise TokenVerificationError(f"unknown_kid:{kid}")
    return key


async def _fetch_jwks() -> dict:
    """공개키 문서를 받아온다. 네트워크 경계를 여기 하나로 모은다."""
    url = jwks_url()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as exc:
        # 주소만 남기고 예외 본문은 로그로 보낸다. URL에 비밀값이 없다(공개 엔드포인트).
        logger.warning("JWKS 조회 실패: url=%s error=%s", url, exc)
        raise TokenVerificationError("jwks_fetch_failed") from exc


async def _refresh() -> None:
    if not is_configured():
        raise TokenVerificationError("supabase_url_not_configured")

    payload = await _fetch_jwks()

    try:
        key_set = jwt.PyJWKSet.from_dict(payload)
    except Exception as exc:  # PyJWKSetError 등 라이브러리 내부 예외를 모두 포함
        logger.warning("JWKS 파싱 실패: url=%s error=%s", jwks_url(), exc)
        raise TokenVerificationError("jwks_parse_failed") from exc

    refreshed = {key.key_id: key for key in key_set.keys if key.key_id}
    _cache.clear()
    _cache.update(refreshed)
    logger.info("JWKS 갱신: kids=%s", sorted(_cache))
