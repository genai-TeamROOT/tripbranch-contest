"""TourAPI 계열 서비스가 함께 쓰는 HTTP 호출과 응답 파싱.

한국관광공사가 같은 인증키로 여러 서비스를 낸다(국문 관광정보 `KorService2`,
무장애 여행 `KorWithService2`). 경로와 응답 필드만 다를 뿐 인증 방식·공통
파라미터·`resultCode` 규약·오류 표현이 모두 같아서, 서비스마다 호출부를 따로
두면 같은 코드가 복제된다.

특히 복제하면 안 되는 것이 예외 처리다 — httpx 예외와 traceback에는 serviceKey가
담긴 전체 URL이 남을 수 있어 `request_params.clear()`로 지우는데, 한쪽만 고치면
다른 쪽은 키를 흘리는 채로 남는다(`weather.py`가 같은 처리를 하는 이유다).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

import httpx

from app.errors import ProviderTimeoutError, ProviderUnavailableError
from app.providers.upstream_errors import upstream_error_detail

logger = logging.getLogger(__name__)


def first_item(payload: Mapping[str, object]) -> dict[str, object]:
    """items.item의 첫 항목. 상세조회처럼 1건만 오는 응답에 쓴다."""
    response = payload.get("response")
    body = response.get("body") if isinstance(response, Mapping) else None
    items = body.get("items") if isinstance(body, Mapping) else None
    raw_items = items.get("item", []) if isinstance(items, Mapping) else []
    if isinstance(raw_items, Mapping):
        return dict(raw_items)
    if isinstance(raw_items, list) and raw_items and isinstance(raw_items[0], Mapping):
        return dict(raw_items[0])
    return {}


def first_text(item: Mapping[str, object], keys: tuple[str, ...]) -> str | None:
    """앞에서부터 훑어 먼저 걸리는 값. 유형마다 키 이름이 다른 필드에 쓴다."""
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def response_body(payload: Mapping[str, object]) -> Mapping[str, object]:
    response = payload.get("response")
    body = response.get("body") if isinstance(response, Mapping) else None
    return body if isinstance(body, Mapping) else {}


def response_items(payload: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    """items.item 전체. 결과가 0건이면 items가 빈 문자열로 와서 ()가 된다."""
    items = response_body(payload).get("items")
    raw_items = items.get("item", []) if isinstance(items, Mapping) else []
    if isinstance(raw_items, Mapping):
        return (raw_items,) if raw_items else ()
    if isinstance(raw_items, list):
        return tuple(item for item in raw_items if isinstance(item, Mapping))
    return ()


class TourApiClient:
    """서비스 하나(base_url)에 대한 인증·호출·오류 판정."""

    def __init__(
        self,
        api_key: str,
        client: httpx.AsyncClient,
        base_url: str,
        timeout_seconds: float = 10.0,
        service_name: str = "TourAPI",
    ) -> None:
        self._api_key = api_key
        self._client = client
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds
        # 오류 메시지에 쓸 이름. 어느 서비스가 실패했는지 로그에서 갈라 보려는 것이라
        # 기본값은 기존 메시지와 같은 "TourAPI"를 유지한다.
        self._service_name = service_name

    def base_params(self) -> dict[str, object]:
        return {
            "MobileOS": "ETC",
            "MobileApp": "TripBranch",
            "_type": "json",
        }

    async def request_json(
        self, path: str, params: dict[str, object]
    ) -> dict[str, object]:
        request_params = {"serviceKey": self._api_key, **params}
        try:
            response = await self._client.get(
                self._base_url + path,
                params=request_params,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException:
            # httpx 예외와 traceback에는 ServiceKey가 포함된 전체 URL이 남을 수 있다.
            request_params.clear()
            response = None
            logger.error("%s 호출 타임아웃 (path=%s)", self._service_name, path)
            raise ProviderTimeoutError(self._service_name) from None
        except httpx.HTTPStatusError as exc:
            # 상태 코드만으로는 인증 실패·쿼터 초과·기간 만료가 구분되지 않는다.
            detail = (
                f"HTTP {exc.response.status_code}, {upstream_error_detail(exc.response)}"
            )
            request_params.clear()
            response = None
            exc = None
            logger.error(
                "%s 호출 실패 (%s, path=%s)", self._service_name, detail, path
            )
            raise ProviderUnavailableError(self._service_name, detail=detail) from None
        except httpx.HTTPError as exc:
            request_params.clear()
            response = None
            logger.error(
                "%s 호출 실패 (%s, path=%s)",
                self._service_name,
                type(exc).__name__,
                path,
            )
            raise ProviderUnavailableError(self._service_name) from None
        except ValueError:
            request_params.clear()
            response = None
            logger.error(
                "%s 호출 실패 (non-JSON response, path=%s)", self._service_name, path
            )
            raise ProviderUnavailableError(
                self._service_name, detail="non-JSON response"
            ) from None

        header = payload.get("response", {}).get("header", {})
        result_code = str(header.get("resultCode", ""))
        if result_code not in {"", "00", "0000"}:
            logger.error(
                "%s 응답 오류 (resultCode=%s, resultMsg=%s, path=%s)",
                self._service_name,
                result_code,
                header.get("resultMsg", ""),
                path,
            )
            raise ProviderUnavailableError(
                self._service_name,
                detail=f"{result_code}: {header.get('resultMsg', '')}",
            )
        return payload


__all__ = [
    "TourApiClient",
    "first_item",
    "first_text",
    "response_body",
    "response_items",
]
