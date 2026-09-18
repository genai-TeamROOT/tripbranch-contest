"""upstream_error_detail이 실제 거절 응답 형식에서 사유를 뽑는지 검증한다.

각 본문은 실제 API를 잘못된 인증정보로 호출해 받은 응답을 그대로 옮긴 것이다.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from app.errors import ProviderUnavailableError
from app.providers.upstream_errors import upstream_error_detail
from app.tools.contracts import ToolStatus
from app.tools.holiday import GetHolidaysTool, HolidayQuery


def _response(text: str, status_code: int = 403) -> httpx.Response:
    return httpx.Response(status_code, text=text)


def test_extracts_data_go_kr_json_error() -> None:
    detail = upstream_error_detail(
        _response(
            '{"OpenAPI_ServiceResponse":{"cmmMsgHeader":{'
            '"errMsg":"SERVICE_KEY_IS_NOT_REGISTERED_ERROR",'
            '"returnAuthMsg":"등록되지 않은 서비스키","returnReasonCode":"30"}}}'
        )
    )

    assert "errMsg=SERVICE_KEY_IS_NOT_REGISTERED_ERROR" in detail
    assert "returnReasonCode=30" in detail


def test_extracts_data_go_kr_xml_error() -> None:
    detail = upstream_error_detail(
        _response(
            "<OpenAPI_ServiceResponse><cmmMsgHeader>"
            "<errMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</errMsg>"
            "<returnReasonCode>30</returnReasonCode>"
            "</cmmMsgHeader></OpenAPI_ServiceResponse>"
        )
    )

    assert "errMsg=SERVICE_KEY_IS_NOT_REGISTERED_ERROR" in detail
    assert "returnReasonCode=30" in detail


def test_extracts_naver_error() -> None:
    detail = upstream_error_detail(
        _response(
            '{"error":{"errorCode":"200","message":"Authentication Failed",'
            '"details":"Invalid authentication information."}}',
            status_code=401,
        )
    )

    assert "errorCode=200" in detail
    assert "message=Authentication Failed" in detail


def test_falls_back_to_truncated_body() -> None:
    detail = upstream_error_detail(_response("<html>\n  Service Unavailable\n</html>"))

    assert detail.startswith("body=")
    assert "Service Unavailable" in detail


def test_truncates_long_unknown_body() -> None:
    detail = upstream_error_detail(_response("x" * 5000))

    assert len(detail) < 300


class _StubHolidayProvider:
    """AppError를 그대로 던져 tool 계층의 로깅만 검증한다."""

    async def get_holidays(self, *args: object, **kwargs: object) -> object:
        raise ProviderUnavailableError("KASI Holiday", detail="HTTP 403, errMsg=X")


@pytest.mark.asyncio
async def test_swallowed_provider_error_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """tool이 삼킨 오류는 200 응답으로 나가므로 로그가 유일한 흔적이다."""
    tool = GetHolidaysTool(_StubHolidayProvider())  # type: ignore[arg-type]

    with caplog.at_level(logging.WARNING, logger="app.tools.holiday"):
        result = await tool.execute(HolidayQuery(year=2026))

    assert result.status is ToolStatus.UNAVAILABLE
    assert "공휴일 정보 없이 진행" in caplog.text
    assert "HTTP 403" in caplog.text
