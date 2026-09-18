"""신원 검증 테스트용 키·토큰 도구.

실제 Supabase를 부르지 않는다. 테스트 전용 EC 키쌍을 만들어 우리가 직접 서명하고,
그 공개키를 JWKS로 돌려주도록 조회 함수를 바꿔 끼운다.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.auth import jwks
from app.config import settings

TEST_KID = "test-kid-1"
TEST_SUPABASE_URL = "https://test.supabase.co"
TEST_ISSUER = f"{TEST_SUPABASE_URL}/auth/v1"


@pytest.fixture
def signing_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def public_jwk(key: ec.EllipticCurvePrivateKey, kid: str = TEST_KID) -> dict[str, Any]:
    jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": kid, "use": "sig", "alg": "ES256"})
    return jwk


def make_token(
    key: ec.EllipticCurvePrivateKey,
    *,
    kid: str | None = TEST_KID,
    algorithm: str = "ES256",
    **claim_overrides: Any,
) -> str:
    claims: dict[str, Any] = {
        "sub": "3f1a9c04-0000-4000-8000-000000000001",
        "iss": TEST_ISSUER,
        "aud": "authenticated",
        "exp": 4102444800,  # 2100년
        "is_anonymous": True,
    }
    claims.update(claim_overrides)
    headers = {"kid": kid} if kid else {}
    return jwt.encode(claims, key, algorithm=algorithm, headers=headers)


def public_key_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    """공개된 키 자료. 공격자가 JWKS에서 받아 갈 수 있는 값이다."""
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def forge_hs256_token(secret: bytes, claims: dict[str, Any], *, kid: str = TEST_KID) -> str:
    """공개키를 HMAC 비밀키로 써서 서명한 토큰을 손으로 만든다.

    PyJWT의 `encode()`를 쓰지 않는 이유가 두 가지다. 첫째, 최신 PyJWT는 JWK·PEM 모양
    입력을 HMAC 비밀키로 쓰는 것을 막아서 버전에 따라 토큰 생성 자체가 실패한다.
    둘째, 실제 공격자는 우리 라이브러리의 가드를 거쳐 토큰을 만들지 않는다 — 방어를
    검증하려면 가드 밖에서 만든 토큰을 넣어야 한다.
    """
    return _assemble(
        {"alg": "HS256", "typ": "JWT", "kid": kid},
        claims,
        lambda signing_input: hmac.new(secret, signing_input, hashlib.sha256).digest(),
    )


def forge_unsigned_token(claims: dict[str, Any], *, kid: str = TEST_KID) -> str:
    """서명을 아예 비운 `alg: none` 토큰. 알고리즘 혼동의 다른 변종이다."""
    return _assemble({"alg": "none", "typ": "JWT", "kid": kid}, claims, lambda _: b"")


def _assemble(header: dict[str, Any], claims: dict[str, Any], sign) -> str:
    signing_input = (
        f"{_b64url(json.dumps(header).encode())}.{_b64url(json.dumps(claims).encode())}"
    ).encode()
    return f"{signing_input.decode()}.{_b64url(sign(signing_input))}"


@pytest.fixture(autouse=True)
def configured_auth(
    monkeypatch: pytest.MonkeyPatch,
    signing_key: ec.EllipticCurvePrivateKey,
) -> None:
    """SUPABASE_URL을 테스트 값으로 두고, JWKS 조회를 테스트 공개키로 바꿔 끼운다."""
    monkeypatch.setattr(settings, "supabase_url", TEST_SUPABASE_URL)
    jwks.reset_cache()

    async def fake_fetch() -> dict[str, Any]:
        return {"keys": [public_jwk(signing_key)]}

    monkeypatch.setattr(jwks, "_fetch_jwks", fake_fetch)
    yield
    jwks.reset_cache()
