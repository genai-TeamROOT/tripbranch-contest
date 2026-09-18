"""외부 API 호출량을 프로세스 메모리에 집계한다.

역할: 어떤 외부 Provider의 어떤 오퍼레이션을 몇 번 불렀는지, 성공·실패·지연과
함께 누적한다. data.go.kr 계열은 오퍼레이션 단위로 일일 한도가 걸려 있어
(2026-08-07 `areaBasedList2` 소진) 합산이 아니라 오퍼레이션 단위로 센다.
입력: `create_external_client()`가 만든 httpx 클라이언트가 실제로 보낸 요청,
      그리고 SDK로 나가서 httpx를 거치지 않는 호출의 `record_call()` 직접 기록.
출력: `get_usage_snapshot()`이 돌려주는 집계 스냅샷(개발자 Ops 패널이 소비).
호출 시점: 외부 HTTP 요청이 나갈 때마다, 그리고 패널이 조회할 때.

관측 전용이다 — 여기 값이 없거나 틀려도 추천 판정은 달라지지 않는다.

보안: URL 쿼리스트링은 절대 저장하지 않는다. data.go.kr 계열은 `ServiceKey`가
쿼리에 실려 나가므로 host와 path만 분류에 쓴다(`providers/weather.py`가 예외
처리에서 `request_params.clear()`를 하는 것과 같은 이유).
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

from app.config import settings

_KST = ZoneInfo("Asia/Seoul")

# 알 수 없는 host를 조용히 버리지 않기 위한 표식. 분류표에 없는 외부 호출이
# 생기면 패널에 이 provider로 드러나 분류표를 갱신할 계기가 된다.
UNKNOWN_PROVIDER = "unknown"

# 날짜 버킷 보존 일수. 개발용 패널이라 최근 며칠이면 충분하고, 무한히 쌓이면
# 장시간 띄워둔 서버의 메모리를 갉아먹는다.
_RETAINED_DAYS = 7

# 지연 표본 보관 개수. 평균·최대만 쓰므로 합계와 최대값이면 되지만, 최근 추세를
# 보려면 표본이 있어야 해서 최근 것만 제한적으로 남긴다.
_LATENCY_SAMPLE_SIZE = 200

# 한 호스트에 여러 API가 붙어 있어 path 접두사로 갈라야 하는 경우.
# host → ((path 접두사, provider), ...) 이고, 접두사는 위에서부터 startswith로
# 첫 일치를 찾으므로 순서가 곧 우선순위다. `/B551011`처럼 접두사끼리 앞부분을
# 공유하는 경우가 있어 dict가 아니라 순서 있는 튜플로 둔다.
#
# 접두사에 하나도 안 걸리는 path는 UNKNOWN_PROVIDER로 보낸다. 임의로 그 호스트의
# 다른 provider에 붙이면 새 API를 추가했을 때 조용히 남의 집계에 섞인다 — 네이버
# 자동차 경로가 지오코딩으로 집계되던 것이 그 사례다(TP-131).
_HOST_PREFIX_PROVIDERS: dict[str, tuple[tuple[str, str], ...]] = {
    "apis.data.go.kr": (
        ("/B551011/TatsCnctrRateService", "concentration"),
        ("/B551011/KorService2", "tour_api"),
        ("/1360000/VilageFcstInfoService_2.0", "kma_weather"),
        ("/B090041/openapi/service/SpcdeInfoService", "kasi_holiday"),
    ),
    # 자동차 경로와 지오코딩이 같은 호스트를 쓴다.
    "maps.apigw.ntruss.com": (
        ("/map-direction", "naver_driving"),
        ("/map-geocode", "naver_geocoding"),
    ),
}

# host(정확히 일치) → provider. path를 보지 않으므로 호스트 하나에 API가 하나뿐이거나,
# 여러 개여도 operation 열로 충분히 갈리는 경우에만 쓴다.
_HOST_PROVIDERS: dict[str, str] = {
    "dapi.kakao.com": "kakao_map",
    "naverapihub.apigw.ntruss.com": "naver_local_search",
}


def _now() -> datetime:
    return datetime.now(_KST)


def _today() -> date:
    return _now().date()


@dataclass
class _Counts:
    """한 (provider, operation)의 누적 집계."""

    count: int = 0
    ok: int = 0
    error: int = 0
    latency_sum_ms: float = 0.0
    latency_max_ms: float = 0.0
    latency_samples: deque[float] = field(
        default_factory=lambda: deque(maxlen=_LATENCY_SAMPLE_SIZE)
    )
    last_called_at: datetime | None = None
    last_status: str | None = None

    def add(self, *, ok: bool, latency_ms: float, status: str, at: datetime) -> None:
        self.count += 1
        if ok:
            self.ok += 1
        else:
            self.error += 1
        self.latency_sum_ms += latency_ms
        self.latency_max_ms = max(self.latency_max_ms, latency_ms)
        self.latency_samples.append(latency_ms)
        self.last_called_at = at
        self.last_status = status


class ApiUsageRegistry:
    """프로세스 수명 동안의 외부 호출 집계.

    서버를 재시작하면 0으로 돌아간다. 일일 한도 추적이 아니라 "지금 띄운 서버가
    무엇을 얼마나 부르고 있는가"를 보는 용도다.
    """

    def __init__(self) -> None:
        self._started_at = _now()
        self._totals: dict[tuple[str, str], _Counts] = {}
        self._daily: dict[date, dict[tuple[str, str], _Counts]] = {}

    @property
    def started_at(self) -> datetime:
        return self._started_at

    def record(
        self,
        provider: str,
        operation: str,
        *,
        ok: bool,
        latency_ms: float,
        status: str,
        at: datetime | None = None,
    ) -> None:
        moment = at or _now()
        key = (provider, operation)
        self._totals.setdefault(key, _Counts()).add(
            ok=ok, latency_ms=latency_ms, status=status, at=moment
        )
        bucket = self._daily.setdefault(moment.date(), {})
        bucket.setdefault(key, _Counts()).add(
            ok=ok, latency_ms=latency_ms, status=status, at=moment
        )
        self._prune(moment.date())

    def _prune(self, today: date) -> None:
        if len(self._daily) <= _RETAINED_DAYS:
            return
        for stale in sorted(self._daily)[: len(self._daily) - _RETAINED_DAYS]:
            if stale != today:
                del self._daily[stale]

    def total_count(self, provider: str, operation: str) -> int:
        counts = self._totals.get((provider, operation))
        return counts.count if counts else 0

    def daily_count(self, provider: str, operation: str, on: date) -> int:
        counts = self._daily.get(on, {}).get((provider, operation))
        return counts.count if counts else 0

    def reset(self) -> None:
        self._started_at = _now()
        self._totals.clear()
        self._daily.clear()

    def snapshot(self) -> dict[str, Any]:
        today = _today()
        today_bucket = self._daily.get(today, {})
        entries = [
            _entry(provider, operation, counts, today_bucket.get((provider, operation)))
            for (provider, operation), counts in sorted(self._totals.items())
        ]
        return {
            "process_started_at": self._started_at.isoformat(),
            "generated_at": _now().isoformat(),
            "today": today.isoformat(),
            "timezone": "Asia/Seoul",
            "provider_modes": provider_modes(),
            "totals": {
                "count": sum(entry["count"] for entry in entries),
                "ok": sum(entry["ok"] for entry in entries),
                "error": sum(entry["error"] for entry in entries),
            },
            "today_totals": {
                "count": sum(entry["today_count"] for entry in entries),
                "ok": sum(entry["today_ok"] for entry in entries),
                "error": sum(entry["today_error"] for entry in entries),
            },
            "entries": entries,
        }


def _entry(
    provider: str,
    operation: str,
    counts: _Counts,
    today: _Counts | None,
) -> dict[str, Any]:
    today_count = today.count if today else 0
    return {
        "provider": provider,
        "operation": operation,
        "count": counts.count,
        "ok": counts.ok,
        "error": counts.error,
        "today_count": today_count,
        "today_ok": today.ok if today else 0,
        "today_error": today.error if today else 0,
        "daily_limit": daily_limit_for(provider),
        "avg_latency_ms": (
            round(counts.latency_sum_ms / counts.count, 1) if counts.count else None
        ),
        "max_latency_ms": round(counts.latency_max_ms, 1) if counts.count else None,
        "last_called_at": (
            counts.last_called_at.isoformat() if counts.last_called_at else None
        ),
        "last_status": counts.last_status,
    }


def daily_limit_for(provider: str) -> int | None:
    """오퍼레이션 단위 일일 한도. 한도를 모르는 provider는 None(게이지 없음)."""
    if provider == "tour_api":
        return settings.tour_api_daily_call_limit
    if provider == "concentration":
        return settings.concentration_daily_call_limit
    return None


def provider_modes() -> dict[str, str]:
    """부팅 시점의 Fake/Real 구성.

    Fake provider는 외부 HTTP를 아예 보내지 않으므로 집계표가 비어 있는 것이
    정상이다. 그 상태를 "호출이 없었다"로 오독하지 않도록 모드를 함께 낸다.
    """
    return {
        "llm": settings.resolved_llm_provider,
        "place": settings.resolved_place_provider,
        "geocoding": settings.resolved_geocoding_provider,
        "local_search": settings.resolved_local_search_provider,
        "weather": settings.resolved_weather_provider,
        "concentration": settings.resolved_concentration_provider,
        "holiday": settings.resolved_holiday_provider,
        "travel_route": settings.travel_route_provider,
    }


_registry = ApiUsageRegistry()


def get_registry() -> ApiUsageRegistry:
    return _registry


def get_usage_snapshot() -> dict[str, Any]:
    return _registry.snapshot()


def reset_usage() -> None:
    _registry.reset()


def record_call(
    provider: str,
    operation: str,
    *,
    ok: bool,
    latency_ms: float,
    status: str,
) -> None:
    """httpx를 거치지 않는 호출(Gemini SDK 등)을 직접 기록한다."""
    _registry.record(
        provider, operation, ok=ok, latency_ms=latency_ms, status=status
    )


def _supabase_host() -> str | None:
    if not settings.supabase_url:
        return None
    return urlsplit(settings.supabase_url).hostname


def classify(host: str, path: str) -> tuple[str, str]:
    """host와 path만으로 (provider, operation)을 정한다.

    쿼리스트링은 받지 않는다 — ServiceKey가 거기 실려 있다.
    """
    host = host.lower()
    prefixes = _HOST_PREFIX_PROVIDERS.get(host)
    if prefixes is not None:
        for prefix, provider in prefixes:
            if path.startswith(prefix):
                return provider, _last_segment(path) or prefix
        return UNKNOWN_PROVIDER, f"{host}{path}"
    if host in _HOST_PROVIDERS:
        return _HOST_PROVIDERS[host], _last_segment(path) or path
    supabase_host = _supabase_host()
    if supabase_host and host == supabase_host.lower():
        return "supabase", _supabase_operation(path)
    return UNKNOWN_PROVIDER, f"{host}{path}"


def _last_segment(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1]


def _supabase_operation(path: str) -> str:
    """`/rest/v1/places` → `places`, `/rest/v1/rpc/foo` → `rpc/foo`."""
    trimmed = path.removeprefix("/rest/v1").strip("/")
    if trimmed.startswith("rpc/"):
        return trimmed
    return trimmed or path


class MeteredTransport(httpx.AsyncBaseTransport):
    """요청 한 건마다 집계를 남기는 transport 래퍼.

    event_hooks가 아니라 transport를 감싸는 이유: 타임아웃·연결 실패는 응답 훅에
    도달하지 않는데, 그런 호출도 상대 서버의 일일 한도는 이미 소모했을 수 있다.
    실패를 못 세면 한도 게이지가 실제보다 낮게 보인다.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self._inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        from app.observability import api_exchanges

        provider, operation = classify(request.url.host, request.url.path)
        exchange = api_exchanges.begin(request)
        started = time.perf_counter()
        try:
            response = await self._inner.handle_async_request(request)
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            _registry.record(
                provider,
                operation,
                ok=False,
                latency_ms=elapsed_ms,
                # 예외 문자열에는 ServiceKey가 포함된 URL이 들어갈 수 있어
                # 타입 이름만 남긴다.
                status=type(exc).__name__,
            )
            api_exchanges.finish_error(exchange, exc, elapsed_ms)
            raise
        elapsed_ms = (time.perf_counter() - started) * 1000
        _registry.record(
            provider,
            operation,
            ok=response.status_code < 400,
            latency_ms=elapsed_ms,
            status=str(response.status_code),
        )
        if exchange is not None:
            # 본문을 여기서 읽어 버퍼에 올린다. Response.aread()는 두 번째부터
            # 캐시된 값을 돌려주므로 이후 httpx Client가 다시 읽어도 같은 내용이
            # 온다. app/ 어디에도 스트리밍 요청(client.stream 등)이 없어 미리 읽어도
            # 끊기는 경로가 없다 — 스트리밍이 생기면 이 가정이 깨진다.
            api_exchanges.finish_ok(
                exchange, response, await response.aread(), elapsed_ms
            )
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


def create_external_client(**kwargs: Any) -> httpx.AsyncClient:
    """집계가 붙은 httpx 클라이언트. 외부 호출은 전부 이걸 쓴다."""
    transport = kwargs.pop("transport", None) or httpx.AsyncHTTPTransport()
    return httpx.AsyncClient(transport=MeteredTransport(transport), **kwargs)


__all__ = [
    "ApiUsageRegistry",
    "MeteredTransport",
    "UNKNOWN_PROVIDER",
    "classify",
    "create_external_client",
    "daily_limit_for",
    "get_registry",
    "get_usage_snapshot",
    "provider_modes",
    "record_call",
    "reset_usage",
]
