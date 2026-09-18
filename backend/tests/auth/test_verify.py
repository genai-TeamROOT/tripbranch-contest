"""토큰 검증 규칙 (D-062 4-1절)."""

# 이 모듈은 전부 async라 마커를 파일 단위로 적용한다.

from __future__ import annotations

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.auth import jwks
from app.auth.errors import TokenVerificationError
from app.auth.verify import verify_access_token
from tests.auth.conftest import (
    TEST_ISSUER,
    TEST_KID,
    forge_hs256_token,
    forge_unsigned_token,
    make_token,
    public_jwk,
    public_key_pem,
)

pytestmark = pytest.mark.asyncio


async def test_유효한_토큰은_신원을_돌려준다(signing_key) -> None:
    principal = await verify_access_token(make_token(signing_key))

    assert principal.user_id == "3f1a9c04-0000-4000-8000-000000000001"
    assert principal.is_anonymous is True


async def test_계정_연결된_토큰은_익명이_아니다(signing_key) -> None:
    principal = await verify_access_token(make_token(signing_key, is_anonymous=False))

    assert principal.is_anonymous is False


async def test_is_anonymous가_없으면_익명으로_보지_않는다(signing_key) -> None:
    token = make_token(signing_key)
    # 클레임을 지운 토큰을 다시 만든다.
    token = jwt.encode(
        {
            "sub": "u1",
            "iss": "https://test.supabase.co/auth/v1",
            "aud": "authenticated",
            "exp": 4102444800,
        },
        signing_key,
        algorithm="ES256",
        headers={"kid": TEST_KID},
    )

    principal = await verify_access_token(token)

    assert principal.is_anonymous is False


async def test_알고리즘_혼동_공격을_막는다(signing_key) -> None:
    """공개키를 HMAC 비밀키로 써서 서명한 토큰은 통과하면 안 된다.

    공개키는 누구나 가져갈 수 있으므로, 헤더의 alg를 그대로 따르는 검증기는 아무나
    토큰을 위조할 수 있게 된다. 이 테스트가 4-1절 방어의 핵심이다.
    """
    forged = forge_hs256_token(
        public_key_pem(signing_key),
        {
            "sub": "attacker",
            "iss": TEST_ISSUER,
            "aud": "authenticated",
            "exp": 4102444800,
        },
    )

    with pytest.raises(TokenVerificationError) as exc:
        await verify_access_token(forged)

    # kid는 실재하는 값이므로 키 조회 단계는 통과한다. 즉 이 테스트가 통과했다면
    # 그것은 알고리즘 고정이 막아낸 것이지, kid를 못 찾아서가 아니다.
    #
    # 다만 이 테스트가 "고정을 풀면 뚫린다"를 증명하지는 않는다. 확인해 보니 최신
    # PyJWT는 뒤에 자체 가드를 겹쳐 두어(PEM·JWK 모양 입력을 HMAC 비밀키로 거부),
    # 우리가 고정을 풀어도 다른 이유로 막힌다. 그러나 그 가드는 버전에 따라 없고
    # (2.12.1에는 JWK 가드가 없다) 라이브러리 선의에 기대는 것이므로, 우리 쪽 고정을
    # 1차 방어로 유지하고 이 테스트로 그것이 먼저 발동함을 고정한다.
    assert exc.value.reason == "invalid_signature_or_claims"


async def test_서명_없는_토큰을_막는다(signing_key) -> None:
    """`alg: none`으로 서명을 통째로 비운 토큰. 알고리즘 혼동의 다른 변종이다."""
    forged = forge_unsigned_token(
        {
            "sub": "attacker",
            "iss": TEST_ISSUER,
            "aud": "authenticated",
            "exp": 4102444800,
        }
    )

    with pytest.raises(TokenVerificationError):
        await verify_access_token(forged)


async def test_만료된_토큰을_거부한다(signing_key) -> None:
    with pytest.raises(TokenVerificationError) as exc:
        await verify_access_token(make_token(signing_key, exp=1000000000))

    assert exc.value.reason == "expired"


async def test_다른_프로젝트가_발급한_토큰을_거부한다(signing_key) -> None:
    with pytest.raises(TokenVerificationError) as exc:
        await verify_access_token(
            make_token(signing_key, iss="https://other.supabase.co/auth/v1")
        )

    assert exc.value.reason == "issuer_mismatch"


async def test_aud가_다르면_거부한다(signing_key) -> None:
    with pytest.raises(TokenVerificationError) as exc:
        await verify_access_token(make_token(signing_key, aud="anon"))

    assert exc.value.reason == "audience_mismatch"


async def test_다른_키로_서명한_토큰을_거부한다(signing_key) -> None:
    attacker_key = ec.generate_private_key(ec.SECP256R1())

    with pytest.raises(TokenVerificationError) as exc:
        await verify_access_token(make_token(attacker_key))

    assert exc.value.reason == "invalid_signature_or_claims"


async def test_kid가_없으면_거부한다(signing_key) -> None:
    with pytest.raises(TokenVerificationError) as exc:
        await verify_access_token(make_token(signing_key, kid=None))

    assert exc.value.reason == "missing_kid"


async def test_토큰_형식이_깨졌으면_거부한다() -> None:
    with pytest.raises(TokenVerificationError) as exc:
        await verify_access_token("not-a-jwt")

    assert exc.value.reason == "malformed_header"


async def test_모르는_kid면_JWKS를_다시_받아온다(
    signing_key, monkeypatch: pytest.MonkeyPatch
) -> None:
    """키 회전 대응. 캐시만 붙들고 있으면 회전한 날 전 요청이 401이 된다."""
    rotated_key = ec.generate_private_key(ec.SECP256R1())
    calls = {"count": 0}

    async def fake_fetch() -> dict:
        calls["count"] += 1
        # 첫 조회는 옛 키만, 두 번째부터는 회전된 키를 준다.
        key = signing_key if calls["count"] == 1 else rotated_key
        kid = TEST_KID if calls["count"] == 1 else "test-kid-2"
        return {"keys": [public_jwk(key, kid=kid)]}

    monkeypatch.setattr(jwks, "_fetch_jwks", fake_fetch)

    await verify_access_token(make_token(signing_key))
    assert calls["count"] == 1

    principal = await verify_access_token(make_token(rotated_key, kid="test-kid-2"))
    assert calls["count"] == 2
    assert principal.user_id == "3f1a9c04-0000-4000-8000-000000000001"


async def test_갱신해도_없는_kid는_거부한다(
    signing_key, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(TokenVerificationError) as exc:
        await verify_access_token(make_token(signing_key, kid="nope"))

    assert exc.value.reason.startswith("unknown_kid")
