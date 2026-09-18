"""만료된 익명 계정(auth.users)을 정리한다.

역할: Supabase Auth Admin API로 auth.users를 순회해 익명 계정
(is_anonymous=true) 중 생성된 지 오래(기본 30일)된 계정을 삭제한다.
TP-134(D-074)가 정리한 B 소유 4개 테이블(agent_states 등)과는 완전히
별개인 정리 작업이다 — agent_states.user_id가 auth.users를 FK로 참조하지
않도록 설계돼 있어(D-063 결정 4) 두 정리는 서로 의존하지 않고 독립적으로
실행 가능하다.

기준 필드는 last_sign_in_at이 아니라 created_at을 쓴다. B 소유 테이블처럼
"마지막 활동 시각"을 이 레벨에서 알 방법이 없다(FK가 없어 join하지 않기로
했으므로) — created_at 기준이 Supabase 공식 문서가 권장하는 정리 쿼리
(`delete from auth.users where is_anonymous is true and created_at <
now() - interval '30 days'`)와 동일한 기준이라 그대로 따른다.

입력: --days(기준 일수, 기본 30), --dry-run(삭제 없이 대상만 출력).
출력: 정리한(또는 정리할) 계정 수, 실패한 계정 id.
호출 시점: `python -m scripts.cleanup_anonymous_users` (수동 실행). 자동
스케줄(cron 등)은 이번 범위 밖 — cleanup_expired_sessions.py(D-074)와 동일한
방침.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

import httpx

from app.config import settings
from app.state.schema import now_kst

# GoTrue admin listUsers 한 페이지당 최대 조회 수. 프로젝트 규모상 익명 계정이
# 수천 단위로 쌓일 일은 없다고 보되, 페이지네이션 자체는 방어적으로 구현한다.
_PER_PAGE = 1000


class AnonymousUserCleanupError(Exception):
    """Supabase Auth Admin API 호출 실패."""


class AuthAdminClient:
    """Supabase Auth Admin API(GoTrue) 최소 클라이언트.

    app/state/supabase_store.py의 PostgREST 클라이언트와 달리, GoTrue admin
    엔드포인트는 apikey 헤더만으로는 인증되지 않고 Authorization: Bearer
    헤더가 함께 있어야 한다 — 별도로 작게 구현한다.
    """

    def __init__(
        self,
        supabase_url: str,
        secret_key: str,
        client: httpx.Client,
        timeout_seconds: float = 10.0,
    ) -> None:
        normalized_url = supabase_url.strip().rstrip("/")
        if not normalized_url:
            raise ValueError("supabase_url이 필요합니다.")
        if not secret_key.strip():
            raise ValueError("secret_key가 필요합니다.")
        self._base_url = f"{normalized_url}/auth/v1/admin"
        self._secret_key = secret_key
        self._client = client
        self._timeout_seconds = timeout_seconds

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self._secret_key,
            "Authorization": f"Bearer {self._secret_key}",
        }

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._client.request(
                method,
                f"{self._base_url}{path}",
                headers=self._headers(),
                timeout=self._timeout_seconds,
                **kwargs,
            )
            response.raise_for_status()
            return response
        except httpx.TimeoutException:
            raise AnonymousUserCleanupError("request timeout") from None
        except httpx.HTTPStatusError as exc:
            raise AnonymousUserCleanupError(f"HTTP {exc.response.status_code}") from None
        except httpx.HTTPError:
            raise AnonymousUserCleanupError("request failed") from None

    def list_users_page(self, page: int, per_page: int = _PER_PAGE) -> list[dict[str, Any]]:
        response = self._request(
            "GET", "/users", params={"page": page, "per_page": per_page}
        )
        try:
            payload = response.json()
        except ValueError:
            raise AnonymousUserCleanupError("non-JSON response") from None
        users = payload.get("users") if isinstance(payload, dict) else None
        if not isinstance(users, list):
            raise AnonymousUserCleanupError("invalid list response")
        return users

    def delete_user(self, user_id: str) -> None:
        self._request("DELETE", f"/user/{user_id}")


def _parse_created_at(raw: str) -> datetime:
    """Supabase가 내려주는 RFC3339 문자열(예: ...Z)을 파싱한다."""
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def find_stale_anonymous_users(
    admin: AuthAdminClient, cutoff: datetime
) -> list[dict[str, Any]]:
    """cutoff보다 먼저 생성된 익명 계정을 전부 모아 반환한다."""
    stale: list[dict[str, Any]] = []
    page = 1
    while True:
        users = admin.list_users_page(page)
        if not users:
            break
        for user in users:
            if user.get("is_anonymous") is not True:
                continue
            created_at_raw = user.get("created_at")
            if not created_at_raw:
                continue
            if _parse_created_at(created_at_raw) < cutoff:
                stale.append(user)
        if len(users) < _PER_PAGE:
            break
        page += 1
    return stale


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="만료된 익명 계정(auth.users) 정리")
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="이 일수보다 오래 전에 생성된 익명 계정을 정리 대상으로 삼는다 (기본 30일)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="삭제하지 않고 정리 대상 계정 수·id만 출력한다",
    )
    return parser


def cleanup(days: int, dry_run: bool, admin: AuthAdminClient) -> int:
    """정리를 실행하고 실패한 계정 수를 반환한다."""
    cutoff = now_kst() - timedelta(days=days)
    stale_users = find_stale_anonymous_users(admin, cutoff)

    if not stale_users:
        print(f"{cutoff.isoformat()} 이전 생성된 익명 계정 없음. 정리할 것이 없습니다.")
        return 0

    if dry_run:
        print(
            f"[dry-run] {len(stale_users)}개 익명 계정이 정리 대상입니다 "
            f"(기준: {cutoff.isoformat()})."
        )
        for user in stale_users:
            print(f"  - {user.get('id')} (created_at={user.get('created_at')})")
        return 0

    failed: list[str] = []
    for user in stale_users:
        user_id = str(user.get("id"))
        try:
            admin.delete_user(user_id)
        except AnonymousUserCleanupError as exc:
            failed.append(user_id)
            print(f"  실패: {user_id} ({exc})")

    succeeded = len(stale_users) - len(failed)
    print(
        f"{succeeded}/{len(stale_users)}개 익명 계정을 정리했습니다 "
        f"(기준: {cutoff.isoformat()})."
    )
    if failed:
        print(f"{len(failed)}개 실패 — 다음 실행에서 다시 시도됩니다.")
    return len(failed)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not settings.supabase_url.strip() or not settings.supabase_secret_key.strip():
        raise SystemExit(
            "SUPABASE_URL / SUPABASE_SECRET_KEY가 없어 Auth Admin API를 호출할 수 없습니다."
        )

    client = httpx.Client()
    admin = AuthAdminClient(
        supabase_url=settings.supabase_url,
        secret_key=settings.supabase_secret_key,
        client=client,
        timeout_seconds=settings.external_api_timeout_seconds,
    )
    failed_count = cleanup(args.days, args.dry_run, admin)
    return 1 if failed_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
