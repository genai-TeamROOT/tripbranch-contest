"""TripBranch 백엔드 공통 애플리케이션 오류 타입.

역할: 서비스/라우터에서 발생한 의도된 오류를 표준 API 오류 응답으로 전달한다.
입력: 오류 코드, 사용자 메시지, HTTP 상태 코드, 재시도 가능 여부.
출력: FastAPI 예외 핸들러가 처리할 수 있는 AppError 인스턴스.
호출 시점: 도메인 검증 실패나 외부 provider 오류를 API 응답으로 바꿀 때 사용한다.
TODO: 실제 provider가 추가되면 원인 예외와 세부 details 필드를 확장한다.
"""

from __future__ import annotations


class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        retryable: bool = False,
        provider: str | None = None,
        details: object | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.provider = provider
        self.details = details

class ProviderTimeoutError(AppError):
    """외부 provider 호출이 타임아웃됐을 때."""

    def __init__(self, provider_name: str) -> None:
        super().__init__(
            code="provider_timeout",
            message=f"{provider_name} 응답이 지연되고 있어요. 잠시 후 다시 시도해주세요.",
            status_code=504,
            retryable=True,
            provider=provider_name,
        )


class ProviderUnavailableError(AppError):
    """외부 provider가 에러를 반환했을 때 (5xx, 인증 실패 등)."""

    def __init__(self, provider_name: str, detail: str = "") -> None:
        super().__init__(
            code="provider_unavailable",
            message=f"{provider_name} 연동에 문제가 발생했어요.",
            status_code=502,
            retryable=True,
            provider=provider_name,
            details={"upstream_detail": detail} if detail else None,
        )
