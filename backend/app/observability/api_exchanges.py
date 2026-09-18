"""외부 API 요청·응답 원문을 개발자 패널용으로 잠시 보관한다.

역할: 어떤 요청을 보내 어떤 응답이 왔는지를 호출 단위로 남긴다. 호출량 집계
(api_usage)가 "몇 번 불렀나"라면 이쪽은 "무엇을 주고받았나"다.
입력: MeteredTransport가 넘기는 httpx 요청·응답.
출력: `snapshot()`이 돌려주는 최근 교환 목록(개발자 채팅 화면이 소비).
호출 시점: 캡처가 켜져 있을 때 외부 HTTP 요청마다.

**기본값은 꺼짐이다.** MeteredTransport는 배포 환경에서도 도는데 응답 본문을
버퍼링하면 메모리를 계속 먹는다. 켜는 경로는 `APP_ENV=local`에서만 등록되는
/api/dev 라우터뿐이라, 배포 환경에서는 켤 방법 자체가 없다.

보안: 자격증명이 실리는 자리는 provider마다 다르다 — data.go.kr은 쿼리의
serviceKey, 네이버·Supabase는 헤더다. 그래서 host별로 정책을 나누고, **분류되지
않은 host는 값 전부를 마스킹한다.** 새 provider가 낯선 이름의 키를 들고 와도
조용히 새지 않게 하려는 것이다(값이 한 번 화면에 뜨면 스크린샷으로 옮겨간다).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

from app.observability.api_usage import UNKNOWN_PROVIDER, classify

_KST = ZoneInfo("Asia/Seoul")

# 보관 건수. 추천 한 번이 외부 호출 5~15건을 만드니 최근 몇 번의 대화를 덮는다.
MAX_EXCHANGES = 50

# 본문 상한. TourAPI 목록 응답은 1000건짜리라 그대로 두면 한 건이 수 MB가 된다.
MAX_BODY_BYTES = 32 * 1024

MASK = "***"

# 값을 그대로 보여줄 헤더. 이 목록 밖은 전부 마스킹한다 — 인증 헤더 이름을
# 하나씩 막는 방식(denylist)은 새 provider가 들어올 때 뚫린다.
_SAFE_HEADERS = frozenset(
    {
        "accept",
        "accept-encoding",
        "connection",
        "content-length",
        "content-range",
        "content-type",
        "date",
        "host",
        "prefer",
        "range",
        "user-agent",
    }
)

# data.go.kr은 serviceKey가 쿼리에 실린다. 여기 적힌 이름만 값을 보여주고
# 나머지는 마스킹한다.
_SAFE_DATA_GO_KR_QUERY = frozenset(
    {
        "_type",
        "areaCd",
        "arrange",
        "base_date",
        "base_time",
        "contentId",
        "contentTypeId",
        "dataType",
        "eventStartDate",
        "keyword",
        "lDongRegnCd",
        "lDongSignguCd",
        "lclsSystm1",
        "mapX",
        "mapY",
        "MobileApp",
        "MobileOS",
        "numOfRows",
        "nx",
        "ny",
        "pageNo",
        "radius",
        "signguCd",
        "solMonth",
        "solYear",
        "tAtsNm",
    }
)

# 쿼리에 자격증명이 실리지 않는 provider. 네이버는 x-ncp-* 헤더, Supabase는
# apikey 헤더로 인증하므로 쿼리는 그대로 보여도 된다(헤더는 위 규칙이 막는다).
_QUERY_SAFE_PROVIDERS = frozenset(
    {
        "kakao_map",
        "naver_driving",
        "naver_geocoding",
        "naver_local_search",
        "supabase",
    }
)

_DATA_GO_KR_PROVIDERS = frozenset(
    {"tour_api", "concentration", "kma_weather", "kasi_holiday"}
)


@dataclass
class Exchange:
    id: str
    started_at: datetime
    provider: str
    operation: str
    method: str
    url: str
    query: dict[str, str] = field(default_factory=dict)
    request_headers: dict[str, str] = field(default_factory=dict)
    request_body: str | None = None
    request_body_truncated: bool = False
    status: str = ""
    ok: bool = False
    latency_ms: float = 0.0
    response_headers: dict[str, str] = field(default_factory=dict)
    response_body: str | None = None
    response_body_truncated: bool = False
    response_bytes: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "started_at": self.started_at.isoformat(),
            "provider": self.provider,
            "operation": self.operation,
            "method": self.method,
            "url": self.url,
            "query": self.query,
            "request_headers": self.request_headers,
            "request_body": self.request_body,
            "request_body_truncated": self.request_body_truncated,
            "status": self.status,
            "ok": self.ok,
            "latency_ms": round(self.latency_ms, 1),
            "response_headers": self.response_headers,
            "response_body": self.response_body,
            "response_body_truncated": self.response_body_truncated,
            "response_bytes": self.response_bytes,
            "error": self.error,
        }


def mask_query(provider: str, params: httpx.QueryParams) -> dict[str, str]:
    """쿼리 파라미터를 provider 정책에 맞춰 마스킹한다.

    분류되지 않은 host는 값 전부를 가린다 — 이름만 남겨도 무엇을 불렀는지는
    읽히고, 값이 무엇이든 새지 않는다.
    """
    if provider in _QUERY_SAFE_PROVIDERS:
        return {key: params[key] for key in params}
    if provider in _DATA_GO_KR_PROVIDERS:
        return {
            key: (params[key] if key in _SAFE_DATA_GO_KR_QUERY else MASK)
            for key in params
        }
    return {key: MASK for key in params}


def mask_headers(headers: httpx.Headers) -> dict[str, str]:
    return {
        name: (value if name.lower() in _SAFE_HEADERS else MASK)
        for name, value in headers.items()
    }


def decode_body(raw: bytes) -> tuple[str | None, bool]:
    """본문을 상한까지만 문자열로 만든다. (내용, 잘렸는지)"""
    if not raw:
        return None, False
    truncated = len(raw) > MAX_BODY_BYTES
    clipped = raw[:MAX_BODY_BYTES]
    try:
        return clipped.decode("utf-8"), truncated
    except UnicodeDecodeError:
        return f"<binary {len(raw)} bytes>", False


class ExchangeRecorder:
    """최근 요청·응답을 담는 링 버퍼. 기본은 꺼짐이다."""

    def __init__(self) -> None:
        self._enabled = False
        self._items: deque[Exchange] = deque(maxlen=MAX_EXCHANGES)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if not enabled:
            # 꺼면 본문도 함께 버린다 — 끈 뒤에도 메모리에 남아 있으면 끈 의미가 없다.
            self._items.clear()

    def clear(self) -> None:
        self._items.clear()

    def add(self, exchange: Exchange) -> None:
        self._items.append(exchange)

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "capacity": MAX_EXCHANGES,
            "max_body_bytes": MAX_BODY_BYTES,
            # 최신이 위로 오게 뒤집는다.
            "items": [item.to_dict() for item in reversed(self._items)],
        }


_recorder = ExchangeRecorder()


def get_recorder() -> ExchangeRecorder:
    return _recorder


def begin(request: httpx.Request) -> Exchange | None:
    """요청 쪽을 먼저 기록한다. 캡처가 꺼져 있으면 None."""
    if not _recorder.enabled:
        return None
    provider, operation = classify(request.url.host, request.url.path)
    body, truncated = decode_body(request.read())
    return Exchange(
        id=str(uuid4()),
        started_at=datetime.now(_KST),
        provider=provider,
        operation=operation,
        method=request.method,
        # 쿼리는 URL에서 떼어 마스킹한 dict로만 남긴다 — 원문 URL을 그대로 두면
        # serviceKey가 통째로 들어간다.
        url=str(request.url.copy_with(query=None)),
        query=mask_query(provider, request.url.params),
        request_headers=mask_headers(request.headers),
        request_body=body,
        request_body_truncated=truncated,
    )


def finish_ok(
    exchange: Exchange | None,
    response: httpx.Response,
    raw_body: bytes,
    latency_ms: float,
) -> None:
    if exchange is None:
        return
    body, truncated = decode_body(raw_body)
    exchange.status = str(response.status_code)
    exchange.ok = response.status_code < 400
    exchange.latency_ms = latency_ms
    exchange.response_headers = mask_headers(response.headers)
    exchange.response_body = body
    exchange.response_body_truncated = truncated
    exchange.response_bytes = len(raw_body)
    _recorder.add(exchange)


def finish_error(
    exchange: Exchange | None, exc: Exception, latency_ms: float
) -> None:
    if exchange is None:
        return
    # 예외 문자열에는 serviceKey가 붙은 URL이 들어갈 수 있어 타입 이름만 남긴다.
    exchange.status = type(exc).__name__
    exchange.ok = False
    exchange.latency_ms = latency_ms
    exchange.error = type(exc).__name__
    _recorder.add(exchange)


__all__ = [
    "MASK",
    "MAX_BODY_BYTES",
    "MAX_EXCHANGES",
    "Exchange",
    "ExchangeRecorder",
    "UNKNOWN_PROVIDER",
    "begin",
    "decode_body",
    "finish_error",
    "finish_ok",
    "get_recorder",
    "mask_headers",
    "mask_query",
]
