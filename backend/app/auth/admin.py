"""Supabase Auth Admin(GoTrue) 최소 클라이언트 — 계정 삭제용.

역할: `auth.users`에서 계정 한 건을 지운다.
입력: user_id.
출력: 없음(실패 시 AccountAdminError).
호출 시점: 회원 탈퇴(app/accounts/deletion.py).

**클라이언트가 자기 계정을 지울 수 없어서 필요하다.** supabase-js에도 REST에도
"내 계정 삭제"가 없고, GoTrue admin 엔드포인트만 계정을 지운다. 그 엔드포인트는
secret key를 요구하므로 브라우저에서 부를 수 없고, 서버가 대신 부른다.

그래서 **누구를 지울지는 절대 요청 본문에서 받지 않는다.** 서명 검증을 통과한
토큰의 `sub`만 쓴다(routes/account.py). 본문에서 받으면 남의 계정을 지울 수 있다.

PostgREST(app/state/supabase_store.py)와 달리 admin 엔드포인트는 apikey 헤더만으로는
인증되지 않고 Authorization: Bearer가 함께 있어야 한다.

scripts/cleanup_anonymous_users.py에도 같은 성격의 클라이언트가 있다. 합치지 않은
이유는 그쪽이 목록 조회와 페이지네이션을 함께 들고 있고 B 소유 스크립트이기 때문이다
— 여기는 삭제 하나만 필요해서 작게 따로 둔다. 둘 다 커지면 그때 합치는 게 맞다.
"""

from __future__ import annotations

import httpx

_TIMEOUT_SECONDS = 10.0


class AccountAdminError(Exception):
    """Supabase Auth Admin API 호출 실패."""


class AccountAdminClient:
    def __init__(self, supabase_url: str, secret_key: str) -> None:
        normalized_url = supabase_url.strip().rstrip("/")
        if not normalized_url:
            raise ValueError("supabase_url이 필요합니다.")
        if not secret_key.strip():
            raise ValueError("secret_key가 필요합니다.")
        self._base_url = f"{normalized_url}/auth/v1/admin"
        self._secret_key = secret_key

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self._secret_key,
            "Authorization": f"Bearer {self._secret_key}",
        }

    def delete_user(self, user_id: str) -> None:
        """계정을 지운다.

        **이미 없는 계정(404)은 성공으로 본다.** 탈퇴가 중간에 끊겨 사용자가 다시
        누르는 경우가 실제 경로이고, 그때 계정만 이미 지워졌을 수 있다. 여기서
        실패로 만들면 남은 데이터를 영영 못 지운다.
        """
        try:
            with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
                response = client.request(
                    "DELETE",
                    f"{self._base_url}/user/{user_id}",
                    headers=self._headers(),
                )
            if response.status_code == 404:
                return
            response.raise_for_status()
        except httpx.TimeoutException:
            raise AccountAdminError("request timeout") from None
        except httpx.HTTPStatusError as exc:
            raise AccountAdminError(f"HTTP {exc.response.status_code}") from None
        except httpx.HTTPError:
            raise AccountAdminError("request failed") from None
