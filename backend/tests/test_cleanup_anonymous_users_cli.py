"""만료된 익명 계정(auth.users) 정리 스크립트 테스트."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.config import Settings
from scripts import cleanup_anonymous_users as script


def _admin(transport: httpx.MockTransport) -> script.AuthAdminClient:
    client = httpx.Client(transport=transport)
    return script.AuthAdminClient(
        supabase_url="https://project.supabase.co/",
        secret_key="sb_secret_test",
        client=client,
    )


def _user(
    user_id: str, *, is_anonymous: bool, created_at: str
) -> dict[str, object]:
    return {"id": user_id, "is_anonymous": is_anonymous, "created_at": created_at}


def _single_page_transport(users: list[dict[str, object]]) -> httpx.MockTransport:
    """1페이지에만 users를 채우고 그 이후 페이지는 빈 목록으로 응답한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page")
        return httpx.Response(200, json={"users": users if page == "1" else []})

    return httpx.MockTransport(handler)


# ------------------------------------------------------------ AuthAdminClient


def test_list_users_page_보낸_요청이_admin_users_엔드포인트를_때린다() -> None:
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"users": []})

    admin = _admin(httpx.MockTransport(handler))
    admin.list_users_page(1)

    request = captured["request"]
    assert request.url.path == "/auth/v1/admin/users"
    assert request.headers["apikey"] == "sb_secret_test"
    assert request.headers["Authorization"] == "Bearer sb_secret_test"
    assert request.url.params["page"] == "1"


def test_list_users_page_users_목록을_반환한다() -> None:
    row = _user("u1", is_anonymous=True, created_at="2026-01-01T00:00:00Z")
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"users": [row]}))
    assert _admin(transport).list_users_page(1) == [row]


def test_list_users_page_형식이_이상하면_에러를_던진다() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"nope": []}))
    with pytest.raises(script.AnonymousUserCleanupError):
        _admin(transport).list_users_page(1)


def test_delete_user_admin_user_엔드포인트에_DELETE를_보낸다() -> None:
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={})

    admin = _admin(httpx.MockTransport(handler))
    admin.delete_user("u1")

    request = captured["request"]
    assert request.method == "DELETE"
    assert request.url.path == "/auth/v1/admin/user/u1"


def test_실패_응답이면_AnonymousUserCleanupError를_던진다() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(500, json={"message": "boom"}))
    with pytest.raises(script.AnonymousUserCleanupError):
        _admin(transport).delete_user("u1")


# ------------------------------------------------------------ find_stale_anonymous_users


def test_익명이_아닌_계정은_대상에서_제외한다() -> None:
    old = "2020-01-01T00:00:00Z"
    users = [
        _user("anon-old", is_anonymous=True, created_at=old),
        _user("real-old", is_anonymous=False, created_at=old),
    ]
    transport = _single_page_transport(users)
    cutoff = datetime.now(UTC) - timedelta(days=30)
    stale = script.find_stale_anonymous_users(_admin(transport), cutoff)
    assert [u["id"] for u in stale] == ["anon-old"]


def test_cutoff보다_최근에_생성된_익명_계정은_대상이_아니다() -> None:
    recent = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    users = [_user("anon-recent", is_anonymous=True, created_at=recent)]
    transport = _single_page_transport(users)
    cutoff = datetime.now(UTC) - timedelta(days=30)
    stale = script.find_stale_anonymous_users(_admin(transport), cutoff)
    assert stale == []


def test_created_at이_없는_행은_건너뛴다() -> None:
    users = [{"id": "weird", "is_anonymous": True}]
    transport = _single_page_transport(users)
    cutoff = datetime.now(UTC) - timedelta(days=30)
    stale = script.find_stale_anonymous_users(_admin(transport), cutoff)
    assert stale == []


def test_여러_페이지를_전부_순회한다() -> None:
    old = "2020-01-01T00:00:00Z"
    page1 = [_user(f"anon-{i}", is_anonymous=True, created_at=old) for i in range(script._PER_PAGE)]
    page2 = [_user("anon-last", is_anonymous=True, created_at=old)]

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page")
        if page == "1":
            return httpx.Response(200, json={"users": page1})
        if page == "2":
            return httpx.Response(200, json={"users": page2})
        return httpx.Response(200, json={"users": []})

    transport = httpx.MockTransport(handler)
    cutoff = datetime.now(UTC) - timedelta(days=30)
    stale = script.find_stale_anonymous_users(_admin(transport), cutoff)
    assert len(stale) == script._PER_PAGE + 1
    assert stale[-1]["id"] == "anon-last"


# ------------------------------------------------------------ cleanup()


def test_cleanup_대상이_없으면_0을_반환한다() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"users": []}))
    assert script.cleanup(days=30, dry_run=False, admin=_admin(transport)) == 0


def test_cleanup_dry_run은_삭제_요청을_보내지_않는다() -> None:
    old = "2020-01-01T00:00:00Z"
    users = [_user("anon-1", is_anonymous=True, created_at=old)]
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(
                200, json={"users": users if request.url.params.get("page") == "1" else []}
            )
        return httpx.Response(200, json={})

    admin = _admin(httpx.MockTransport(handler))
    failed = script.cleanup(days=30, dry_run=True, admin=admin)

    assert failed == 0
    assert "DELETE" not in calls


def test_cleanup_대상_전부_삭제하고_0을_반환한다() -> None:
    old = "2020-01-01T00:00:00Z"
    users = [
        _user("anon-1", is_anonymous=True, created_at=old),
        _user("anon-2", is_anonymous=True, created_at=old),
    ]
    deleted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200, json={"users": users if request.url.params.get("page") == "1" else []}
            )
        deleted.append(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, json={})

    admin = _admin(httpx.MockTransport(handler))
    failed = script.cleanup(days=30, dry_run=False, admin=admin)

    assert failed == 0
    assert sorted(deleted) == ["anon-1", "anon-2"]


def test_cleanup_일부_삭제_실패시_실패_개수를_반환한다() -> None:
    old = "2020-01-01T00:00:00Z"
    users = [
        _user("anon-ok", is_anonymous=True, created_at=old),
        _user("anon-fail", is_anonymous=True, created_at=old),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200, json={"users": users if request.url.params.get("page") == "1" else []}
            )
        target = request.url.path.rsplit("/", 1)[-1]
        if target == "anon-fail":
            return httpx.Response(500, json={"message": "boom"})
        return httpx.Response(200, json={})

    admin = _admin(httpx.MockTransport(handler))
    failed = script.cleanup(days=30, dry_run=False, admin=admin)

    assert failed == 1


# ------------------------------------------------------------ main()


def test_main_설정이_없으면_SystemExit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        script,
        "settings",
        Settings(_env_file=None, supabase_url="", supabase_secret_key=""),
    )
    with pytest.raises(SystemExit):
        script.main([])
