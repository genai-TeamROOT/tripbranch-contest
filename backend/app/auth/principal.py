"""신원 표현 (D-062 4절).

역할: 서명 검증을 통과한 토큰에서 뽑아낸 신원을 담는다.
입력: 검증된 JWT claims.
출력: Principal.
호출 시점: verify.verify_access_token()이 검증에 성공했을 때 생성한다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    """요청을 보낸 사용자.

    `user_id`는 클라이언트가 주장한 값이 아니라 서명 검증을 통과한 토큰의 `sub`다.
    body로 오는 `session_id`와 달리 위조할 수 없어 소유권 판단에 쓸 수 있다.

    `is_anonymous`는 게스트 여부다. 계정을 연결해도 `user_id`는 그대로 유지되고
    이 값만 False가 된다(D-062 2절).
    """

    user_id: str
    is_anonymous: bool
