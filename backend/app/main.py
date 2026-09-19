"""TripBranch API 애플리케이션 조립 지점.

역할: FastAPI 인스턴스, 공통 예외 응답, CORS, 라우터 등록을 구성한다.
입력: ASGI 서버나 테스트 클라이언트가 전달하는 HTTP 요청.
출력: JSON API 응답과 FastAPI 앱 객체.
호출 시점: uvicorn 실행, 테스트 클라이언트 생성, 앱 부팅 시 호출된다.
TODO: 실제 운영 환경별 CORS/로깅/관측 설정이 필요해지면 여기에서 연결한다.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.auth.jwks import is_configured as auth_is_configured
from app.auth.jwks import issuer as auth_issuer
from app.config import settings
from app.errors import AppError
from app.observability.langfuse_tracing import (
    incoming_trace_context,
    validate_langfuse_config,
)
from app.observability.langfuse_tracing import (
    shutdown as shutdown_langfuse,
)
from app.providers.factory import validate_provider_config
from app.providers.place_evidence_encoder import get_shared_encoder
from app.providers.tour_category_registry import get_tour_category_registry
from app.rate_limit import RateLimiter, client_key, path_is_limited
from app.routes.account import router as account_router
from app.routes.agent import router as agent_router
from app.routes.chat import router as chat_router
from app.routes.dev import router as dev_router
from app.routes.favorites import router as favorites_router
from app.routes.features import router as features_router
from app.routes.feedback import router as feedback_router
from app.routes.health import router as health_router
from app.routes.interpret import router as interpret_router
from app.routes.photo_similar import router as photo_similar_router
from app.routes.place_search import router as place_search_router
from app.routes.preferences import router as preferences_router
from app.routes.recommendations import router as recommendations_router
from app.routes.state import router as state_router
from app.routes.trace import router as trace_router
from app.routes.transcribe import router as transcribe_router
from app.services.runtime.llm_execution import get_llm_execution_metadata

# uvicorn이 핸들러를 붙여둔 logger를 그대로 쓴다 — 앱 전용 logger를 만들면 별도
# 로깅 설정 없이는 서버 콘솔에 아무것도 보이지 않는다.
logger = logging.getLogger("uvicorn.error")


_APP_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def _running_under_uvicorn() -> bool:
    """uvicorn이 로깅 설정을 마친 상태인지 확인한다.

    uvicorn 기본 설정은 uvicorn.error에 level만 주고 핸들러는 부모인 uvicorn에
    두므로 두 곳을 함께 본다. root까지 올라가면 pytest가 붙인 핸들러를 uvicorn의
    것으로 오인해 테스트 로깅을 건드리게 되므로 root는 보지 않는다.
    """
    return any(
        logging.getLogger(name).handlers for name in ("uvicorn.error", "uvicorn")
    )


def _configure_app_logging() -> None:
    """app.* logger에 타임스탬프가 붙는 핸들러를 붙인다.

    uvicorn은 uvicorn/uvicorn.error/uvicorn.access에만 핸들러를 붙이고 root에는
    붙이지 않는다. 그래서 app.providers.* 로그는 표준 lastResort 핸들러로 떨어져
    레벨 표시 없이 맨 문자열로 나온다. 게다가 uvicorn 기본 포매터에는 시각이 없어
    핸들러를 그대로 빌려와도 "새벽 몇 시에 났는지"를 알 수 없다 — provider 장애
    추적에는 그게 핵심이라 시각을 포함한 자체 포매터를 쓴다.
    """
    app_logger = logging.getLogger("app")
    if app_logger.handlers or not _running_under_uvicorn():
        # uvicorn 없이 임포트된 경우(테스트 등)엔 기존 동작을 그대로 둔다.
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_APP_LOG_FORMAT))
    app_logger.addHandler(handler)
    app_logger.setLevel(logging.INFO)
    # root로 다시 올려보내면 같은 줄이 두 번 찍힐 수 있다.
    app_logger.propagate = False


def _log_provider_modes() -> None:
    """부팅 시 각 Provider의 Fake/Real 모드를 남긴다.

    .env를 읽지 못하면 오류 없이 전부 fake로 뜨기 때문에, 실데이터로 띄웠다고
    생각한 서버가 실은 stub 응답을 주는 상황을 로그만 보고 알 수 있게 한다.
    """
    modes = {
        "llm": settings.resolved_llm_provider,
        "place": settings.resolved_place_provider,
        "geocoding": settings.resolved_geocoding_provider,
        "weather": settings.resolved_weather_provider,
        "concentration": settings.resolved_concentration_provider,
        "holiday": settings.resolved_holiday_provider,
        # 공통 PROVIDER_MODE를 상속하지 않아 resolved_* 변형이 없다(config.py).
        "travel_route": settings.travel_route_provider,
    }
    summary = ", ".join(f"{name}={mode}" for name, mode in modes.items())
    logger.info(
        "Provider 모드: %s, place_details_source=%s",
        summary,
        settings.resolved_place_details_source,
    )
    if all(mode == "fake" for mode in modes.values()):
        logger.warning(
            "모든 Provider가 fake입니다. 실데이터로 띄우려면 backend/.env의 "
            "PROVIDER_MODE를 확인하세요(백엔드는 backend/ 에서 실행해야 합니다)."
        )


def _log_auth_mode() -> None:
    """신원 토큰을 검증할 수 있는 상태인지 부팅 시 남긴다 (D-062 Phase 2).

    Provider 모드를 부팅에 남기는 것과 같은 이유다 — 설정이 빠지면 프론트가 보낸
    토큰이 조용히 무시되고, 화면상으로는 아무 문제 없이 동작한다. 지금은 인증이
    optional이라 부팅을 막지 않지만, Phase 4에서 필수화하면 여기가 부팅 실패가
    되어야 한다.
    """
    if auth_is_configured():
        logger.info("Auth: 신원 토큰 검증 활성 (issuer=%s)", auth_issuer())
    else:
        logger.warning(
            "Auth: SUPABASE_URL이 없어 신원 토큰을 검증하지 않습니다. "
            "프론트가 보낸 토큰은 무시됩니다(D-062 Phase 2)."
        )


def _warmup_taste_encoder() -> None:
    """취향 임베딩 모델을 기동 시 미리 올린다.

    적재는 프로세스마다 한 번씩 필요하고 실측 9.4초가 걸린다(2026-08-19).
    여기서 안 올리면 그 시간을 첫 사용자가 그대로 기다린다.

    적재를 기다리지 않는다. 동기로 부르면 부팅이 9.4초 늦어지고, 앱을 띄우는
    테스트마다 그 비용을 문다. 서버가 먼저 뜨고 모델은 뒤따라 올라오며, 적재
    중에 취향 요청이 오면 인코더 락에서 기다렸다가 처리된다.

    실패해도 서버는 뜬다 — 모델이 없으면 취향 Feature만 빠지고 추천은
    그대로 동작한다. 부팅을 막을 만한 오설정이 아니다.
    """
    if not settings.taste_evidence_enabled:
        return
    get_shared_encoder().warmup_in_background()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # uvicorn의 로깅 설정이 끝난 뒤여야 핸들러를 빌려올 수 있다.
    _configure_app_logging()
    # 오설정을 첫 요청의 익명 500이 아니라 부팅 실패로 드러낸다.
    validate_provider_config()
    # 관측도 같은 이유로 부팅에서 검증한다 — 켠 줄 알았는데 아무것도 안 쌓이는
    # 상태는 조용해서 며칠씩 간다. 꺼져 있으면(기본값) 즉시 반환한다.
    validate_langfuse_config()
    _log_provider_modes()
    _log_auth_mode()
    app.state.tour_category_registry = get_tour_category_registry()
    _warmup_taste_encoder()
    yield
    # 대기 중인 span을 내보내고 백그라운드 스레드를 정리한다. 관측이 꺼져 있으면
    # 클라이언트 자체가 없어 아무 일도 하지 않는다.
    shutdown_langfuse()


def create_app() -> FastAPI:
    app = FastAPI(title="TripBranch API", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.resolved_cors_allow_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if settings.rate_limit_enabled:
        limiter = RateLimiter(
            max_requests=settings.rate_limit_requests,
            window_seconds=float(settings.rate_limit_window_seconds),
        )
        limited_prefixes = settings.resolved_rate_limit_path_prefixes

        @app.middleware("http")
        async def limit_request_rate(request: Request, call_next: Any) -> Any:
            """돈이 나가는 경로를 IP별로 제한한다(`app.rate_limit` 참고).

            **꺼져 있으면 미들웨어를 아예 등록하지 않는다.** 등록해두고 안에서
            분기하면 모든 요청이 쓸데없이 한 겹을 더 지난다. 설정은 기동 시점에
            정해지고 런타임에 바뀌지 않으므로 여기서 갈라도 된다.
            """
            if path_is_limited(request.url.path, limited_prefixes):
                client = client_key(
                    request.headers.get("x-forwarded-for"),
                    request.client.host if request.client else None,
                )
                allowed, retry_after = limiter.check(client)
                if not allowed:
                    logger.warning(
                        "rate limited: client=%s path=%s retry_after=%s",
                        client,
                        request.url.path,
                        retry_after,
                    )
                    response = _error_response(
                        "rate_limited",
                        "요청이 너무 잦아요. 잠시 후 다시 시도해주세요.",
                        429,
                        retryable=True,
                    )
                    response.headers["Retry-After"] = str(retry_after)
                    return response
            return await call_next(request)

    @app.middleware("http")
    async def join_incoming_trace(request: Request, call_next: Any) -> Any:
        """평가 스크립트가 연 trace에 이 요청의 span을 이어 붙인다.

        로컬에서 `traceparent` 헤더가 올 때만 동작한다(`incoming_trace_context`).
        평소 요청에는 아무 영향이 없다 — 헤더가 없으면 그대로 통과한다.
        """
        with incoming_trace_context(request.headers):
            return await call_next(request)

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        # AppError는 provider/도메인 오류가 모두 거쳐가는 단일 지점이다 — 여기서
        # 로그를 남기지 않으면 502 등이 클라이언트 응답만 나가고 서버 로그엔 아무
        # 흔적도 안 남는다(D-052에서 발견).
        logger.error(
            "AppError: code=%s provider=%s status=%s path=%s details=%s",
            exc.code,
            exc.provider,
            exc.status_code,
            request.url.path,
            exc.details,
        )
        details: dict[str, object] | None = None
        if exc.provider or exc.details:
            details = {"provider": exc.provider, "upstream": exc.details}
        llm_execution = get_llm_execution_metadata()
        if llm_execution is not None:
            if details is None:
                details = {}
            details["llm_execution"] = llm_execution.model_dump()
        return _error_response(
            exc.code, exc.message, exc.status_code, exc.retryable, details=details
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return _error_response("invalid_request", "요청 내용을 확인해주세요.", 422)

    @app.exception_handler(HTTPException)
    async def handle_http_error(request: Request, exc: HTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return _error_response("invalid_request", "요청한 API를 찾을 수 없어요.", 404)
        return _error_response("invalid_request", "요청 내용을 확인해주세요.", exc.status_code)

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        return _error_response("internal_server_error", "예상치 못한 오류가 발생했어요.", 500)

    app.include_router(health_router, prefix="/api")
    app.include_router(features_router, prefix="/api")
    app.include_router(interpret_router, prefix="/api")
    app.include_router(recommendations_router, prefix="/api")
    app.include_router(agent_router, prefix="/api")
    app.include_router(chat_router, prefix="/api")
    app.include_router(transcribe_router, prefix="/api")
    app.include_router(photo_similar_router, prefix="/api")
    app.include_router(place_search_router, prefix="/api")
    app.include_router(state_router, prefix="/api")
    app.include_router(preferences_router, prefix="/api")
    app.include_router(favorites_router, prefix="/api")
    app.include_router(account_router, prefix="/api")
    app.include_router(feedback_router, prefix="/api")
    app.include_router(trace_router, prefix="/api")
    # 개발자 Ops 패널은 DB 쓰기까지 하는 엔드포인트를 갖는다. 설정 플래그로
    # 막는 대신 로컬이 아니면 라우트를 아예 등록하지 않아 존재 자체를 없앤다.
    if settings.app_env == "local":
        app.include_router(dev_router, prefix="/api")
    return app


def _error_response(
    code: str,
    message: str,
    status_code: int,
    retryable: bool = False,
    details: object | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
                "details": details,
            }
        },
    )


app = create_app()
