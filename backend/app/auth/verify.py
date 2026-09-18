"""신원 토큰 검증 (D-062 4-1절).

역할: Supabase access token의 서명과 클레임을 확인해 Principal로 바꾼다.
입력: Bearer 토큰 문자열.
출력: Principal 또는 TokenVerificationError.
호출 시점: dependency.get_principal()이 Authorization 헤더를 받았을 때 호출한다.

이 방식의 위험은 공개키가 새는 것이 아니라 검증 코드를 잘못 짜는 것이다.
아래 세 가지를 지우면 검증이 무력화된다.
  1. algorithms를 우리가 고정한다. 토큰 헤더의 alg를 그대로 따르면 알고리즘 혼동
     공격이 성립한다 — 공격자가 alg를 HS256으로 바꾸고 공개된 공개키를 HMAC
     비밀키로 써서 서명하면 순진한 검증기는 통과시킨다.
  2. issuer를 확인한다. 서명이 유효한 다른 Supabase 프로젝트의 토큰을 막는다.
  3. exp/aud를 확인한다.
"""

from __future__ import annotations

import jwt

from app.auth.errors import TokenVerificationError
from app.auth.jwks import get_signing_key, issuer
from app.auth.principal import Principal

# 헤더의 alg를 믿지 않는다. 여기 적힌 것만 허용한다.
_ALGORITHMS = ["ES256"]
_AUDIENCE = "authenticated"
_REQUIRED_CLAIMS = ["exp", "sub", "aud", "iss"]


async def verify_access_token(token: str) -> Principal:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise TokenVerificationError("malformed_header") from exc

    kid = header.get("kid")
    if not kid:
        raise TokenVerificationError("missing_kid")

    signing_key = await get_signing_key(kid)

    try:
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=_ALGORITHMS,
            issuer=issuer(),
            audience=_AUDIENCE,
            options={"require": _REQUIRED_CLAIMS},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenVerificationError("expired") from exc
    except jwt.InvalidIssuerError as exc:
        raise TokenVerificationError("issuer_mismatch") from exc
    except jwt.InvalidAudienceError as exc:
        raise TokenVerificationError("audience_mismatch") from exc
    except jwt.PyJWTError as exc:
        raise TokenVerificationError("invalid_signature_or_claims") from exc

    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise TokenVerificationError("missing_subject")

    # is_anonymous가 없는 토큰은 익명이 아닌 것으로 본다. 게스트를 정식 사용자로
    # 오인하는 방향이 반대보다 안전하다 — 게스트 전용 완화를 잘못 적용하지 않는다.
    return Principal(
        user_id=subject,
        is_anonymous=claims.get("is_anonymous") is True,
    )
