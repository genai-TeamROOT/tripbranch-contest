"""
역할: 클라이언트 IP별로 특정 경로의 요청 빈도를 제한한다.
입력: 요청 경로와 클라이언트 IP.
출력: 허용 여부와, 막았을 때 다시 시도할 수 있는 초.
호출 시점: `app.main`의 HTTP 미들웨어가 요청마다 호출한다.

**왜 필요한가.** `/api/chat`은 인증 없이 호출할 수 있고 한 번 호출할 때마다
LLM 요금이 발생한다. 공개 배포에서는 이 조합이 그대로 비용 노출이 된다 —
2026-09-19 배포 직후 1.5시간 동안 40개 IP가 `/.env`, `/wp-config.php` 같은
경로를 훑고 갔다. 그 봇들이 `/api/chat`을 찾아내는 순간 막을 방법이 없었다.

**기본값이 꺼짐인 이유.** 켜는 쪽을 명시적 선택으로 둔다(`taste_evidence_enabled`와
같은 원칙). 무엇보다 테스트는 같은 클라이언트에서 채팅 요청을 연달아 보내는데,
기본이 켜짐이면 그 테스트들이 429를 맞는다. 배포 환경의 `.env`에서만 켠다.

**한계.** 상태를 프로세스 메모리에 둔다. 인스턴스 1대·워커 1개라 지금은 맞지만,
프로세스를 늘리거나 서버를 늘리면 한도가 프로세스 수만큼 늘어난 것처럼 동작한다.
그때는 Redis 같은 공유 저장소가 필요하다. 재배포하면 카운터가 초기화되는데,
이것은 의도된 동작이다 — 배포마다 상태를 옮길 만큼 정확할 필요가 없다.
"""

from __future__ import annotations

import threading
import time
from collections import deque

# IP 하나당 타임스탬프 deque 하나를 들고 있다. 방치하면 스캐너가 IP를 바꿔가며
# 찌를 때 메모리가 무한히 는다 — 오래된 항목을 주기적으로 걷어내고, 그래도 한도를
# 넘으면 가장 오래된 것부터 버린다.
_MAX_TRACKED_CLIENTS = 10_000
_CLEANUP_INTERVAL_SECONDS = 300


class RateLimiter:
    """고정 창이 아니라 미끄러지는 창으로 센다.

    고정 창(예: 매 분 0초에 초기화)은 경계에서 두 배가 통과한다 — 59초에 20개,
    61초에 20개를 보내면 2초 사이에 40개가 지나간다. 요청 시각을 들고 있다가
    창 밖으로 나간 것만 버리면 그 구멍이 없다. 한도가 작아서 메모리도 작다.
    """

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self._max_requests = max_requests
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._last_cleanup = time.monotonic()

    def check(self, client: str) -> tuple[bool, int]:
        """(허용 여부, 재시도까지 남은 초)를 돌려준다.

        허용일 때 두 번째 값은 0이다. 이 호출 자체가 카운트를 올린다 —
        확인과 기록을 나누면 그 사이에 다른 요청이 끼어들 수 있다.
        """
        now = time.monotonic()
        with self._lock:
            self._maybe_cleanup(now)
            hits = self._hits.setdefault(client, deque())
            cutoff = now - self._window
            while hits and hits[0] <= cutoff:
                hits.popleft()

            if len(hits) >= self._max_requests:
                # 가장 오래된 기록이 창 밖으로 나가는 순간 한 자리가 빈다.
                retry_after = max(1, int(hits[0] + self._window - now) + 1)
                return False, retry_after

            hits.append(now)
            return True, 0

    def _maybe_cleanup(self, now: float) -> None:
        """호출자가 락을 쥔 상태에서만 부른다."""
        if now - self._last_cleanup < _CLEANUP_INTERVAL_SECONDS:
            return
        self._last_cleanup = now
        cutoff = now - self._window
        stale = [client for client, hits in self._hits.items() if not hits or hits[-1] <= cutoff]
        for client in stale:
            del self._hits[client]

        # 청소하고도 많으면 활동이 오래된 순으로 버린다. 정확도보다 메모리 상한이
        # 중요한 지점이다 — 버려진 IP는 다음 요청에서 다시 0부터 센다.
        if len(self._hits) > _MAX_TRACKED_CLIENTS:
            ordered = sorted(self._hits.items(), key=lambda kv: kv[1][-1] if kv[1] else 0.0)
            for client, _ in ordered[: len(self._hits) - _MAX_TRACKED_CLIENTS]:
                del self._hits[client]


def client_key(forwarded_for: str | None, peer: str | None) -> str:
    """요청자를 식별할 문자열을 고른다.

    **`X-Forwarded-For`는 맨 오른쪽 값을 쓴다.** 이 헤더는 프록시를 지날 때마다
    뒤에 덧붙는 목록이고, 우리 앞단은 Caddy 하나다. 따라서 맨 오른쪽이 Caddy가
    실제로 본 접속 IP다. 왼쪽부터 읽으면 클라이언트가 가짜 값을 미리 넣어
    한도를 IP마다 새로 받는 식으로 우회할 수 있다.

    헤더가 없으면(프록시를 거치지 않은 로컬 호출) 소켓 peer를 쓴다.
    """
    if forwarded_for:
        parts = [part.strip() for part in forwarded_for.split(",") if part.strip()]
        if parts:
            return parts[-1]
    return peer or "unknown"


def path_is_limited(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path.startswith(prefix) for prefix in prefixes)
