"""IP별 요청 빈도 제한 테스트(`app.rate_limit`, `/api/chat`).

`/api/chat`은 인증 없이 호출할 수 있고 호출마다 LLM 요금이 발생한다. 공개 배포에서
이 경로가 열려 있으면 비용이 그대로 노출되므로, 막히는지와 **막히지 말아야 할 것이
안 막히는지**를 함께 본다 — 헬스체크가 걸리면 배포가 통째로 실패한다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import create_app
from app.rate_limit import RateLimiter, client_key, path_is_limited


def test_allows_up_to_limit_then_blocks() -> None:
    limiter = RateLimiter(max_requests=3, window_seconds=60)

    assert [limiter.check("1.1.1.1")[0] for _ in range(3)] == [True, True, True]

    allowed, retry_after = limiter.check("1.1.1.1")
    assert allowed is False
    # 언제 다시 되는지 알려주지 않으면 클라이언트는 무작정 재시도한다.
    assert retry_after > 0


def test_clients_are_counted_separately() -> None:
    limiter = RateLimiter(max_requests=1, window_seconds=60)

    assert limiter.check("1.1.1.1")[0] is True
    assert limiter.check("1.1.1.1")[0] is False
    # 한 사람이 한도를 채웠다고 다른 사람까지 막히면 안 된다.
    assert limiter.check("2.2.2.2")[0] is True


def test_window_slides(monkeypatch: pytest.MonkeyPatch) -> None:
    """창이 지나면 자리가 다시 난다.

    고정 창이 아니라 미끄러지는 창이라, 기록이 창 밖으로 나가는 시점에 한 자리씩
    열린다. 시간을 실제로 기다리지 않도록 단조 시계를 갈아 끼운다.
    """
    now = [1000.0]
    monkeypatch.setattr("app.rate_limit.time.monotonic", lambda: now[0])

    limiter = RateLimiter(max_requests=2, window_seconds=60)
    assert limiter.check("1.1.1.1")[0] is True
    assert limiter.check("1.1.1.1")[0] is True
    assert limiter.check("1.1.1.1")[0] is False

    now[0] += 61
    assert limiter.check("1.1.1.1")[0] is True


def test_client_key_uses_rightmost_forwarded_for() -> None:
    """맨 오른쪽이 프록시가 실제로 본 IP다.

    `X-Forwarded-For`는 프록시를 지날 때마다 뒤에 덧붙는 목록이다. 클라이언트가
    가짜 값을 미리 넣어 보내면 왼쪽에 그 값이 남는데, 그걸 신뢰하면 요청마다
    다른 IP인 척해서 한도를 무제한으로 받을 수 있다.
    """
    assert client_key("9.9.9.9", "10.0.0.1") == "9.9.9.9"
    assert client_key("203.0.113.7, 9.9.9.9", "10.0.0.1") == "9.9.9.9"
    assert client_key("  1.2.3.4 ,  9.9.9.9  ", None) == "9.9.9.9"


def test_client_key_falls_back_to_peer() -> None:
    # 프록시를 거치지 않는 호출(서버 안에서 127.0.0.1로 직접 치는 헬스체크 등).
    assert client_key(None, "10.0.0.1") == "10.0.0.1"
    assert client_key("", "10.0.0.1") == "10.0.0.1"
    assert client_key(None, None) == "unknown"


def test_path_is_limited() -> None:
    prefixes = ("/api/chat",)
    assert path_is_limited("/api/chat", prefixes) is True
    assert path_is_limited("/api/chat/place-details/reason", prefixes) is True
    # 헬스체크가 걸리면 배포 워크플로우의 30회 폴링이 막힌다.
    assert path_is_limited("/api/health", prefixes) is False
    assert path_is_limited("/api/features", prefixes) is False


def test_disabled_by_default() -> None:
    """기본값이 꺼짐이어야 한다.

    켜짐이 기본이면 같은 클라이언트에서 채팅 요청을 연달아 보내는 다른 테스트들이
    429를 맞는다. 배포 환경의 `.env`에서만 켠다.
    """
    assert settings.rate_limit_enabled is False


def test_chat_returns_429_over_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_requests", 2)
    monkeypatch.setattr(settings, "rate_limit_window_seconds", 60)
    # 미들웨어는 기동 시점에 등록 여부가 정해지므로 앱을 새로 만든다.
    client = TestClient(create_app())

    # 빈 body라 검증에서 422가 나지만, 제한은 그 전에 센다.
    first = client.post("/api/chat", json={})
    second = client.post("/api/chat", json={})
    assert first.status_code != 429
    assert second.status_code != 429

    blocked = client.post("/api/chat", json={})
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"]
    body = blocked.json()["error"]
    assert body["code"] == "rate_limited"
    # 잠시 후면 되는 오류라 재시도 가능으로 알린다.
    assert body["retryable"] is True


def test_health_is_not_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    """헬스체크는 제한 대상이 아니다.

    배포 워크플로우가 컨테이너를 올린 뒤 `/api/health`를 30회 폴링한다. 여기에
    한도가 걸리면 정상 배포가 실패한다.
    """
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_requests", 2)
    client = TestClient(create_app())

    codes = {client.get("/api/health").status_code for _ in range(10)}

    assert codes == {200}
