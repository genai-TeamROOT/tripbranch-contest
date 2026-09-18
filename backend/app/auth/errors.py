"""신원 검증 실패 (D-062 4-1절).

역할: 토큰이 유효하지 않은 경우를 한 종류로 모아 라우터가 401로 바꾸게 한다.
입력: 실패 사유 문자열.
출력: TokenVerificationError.
호출 시점: verify/jwks가 검증을 진행할 수 없거나 실패했을 때 발생시킨다.
"""

from __future__ import annotations


class TokenVerificationError(Exception):
    """토큰을 신뢰할 수 없다.

    사유 문자열은 서버 로그용이다. 사용자에게는 세부 사유를 내려보내지 않는다 —
    어떤 검증에서 걸렸는지가 공격자에게 힌트가 된다.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
