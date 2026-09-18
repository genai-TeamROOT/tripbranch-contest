"""통합 Chat API 라우터.

역할: 프론트 실사용 흐름(HomePage/ChatPage)이 호출하는 단일 진입점. Intent 분류부터
      B/C/D 조정과 챗봇 메시지 조립까지를 `run_agent()`에 그대로 위임한다.
입력: POST /api/chat JSON body의 AgentRequest(user_input, session_id, device_location).
출력: AgentResponse (LLMOutput + 병합된 SessionState + 추천 결과 + 챗봇 메시지).
호출 시점: 사용자가 HomePage에서 추천을 시작하거나 ChatPage에서 후속 발화를 보낼 때.

`/api/agent-debug`와 같은 구현을 공유하지만 용도가 다르다 — 이 라우트는 실사용
경로이고, agent-debug는 개발용 패널 전용으로 남긴다.
TODO: 응답을 공개용으로 좁힌다. 지금은 프론트 전환 비용을 줄이려고 AgentResponse를
      그대로 내보내지만, llm_output 전체와 B의 내부 state까지 공개 계약에 고정되는
      형태라 D-016 확정 시 필요한 필드만 남긴다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from sse_starlette import EventSourceResponse, ServerSentEvent

from app.agent_context.factory import get_context_provider
from app.agent_context.info_schemas import InfoContextRequest
from app.auth.dependency import OptionalPrincipal
from app.config import settings
from app.errors import AppError
from app.observability.api_usage import create_external_client
from app.providers.factory import (
    get_google_translate_provider,
    get_llm_provider,
    get_place_details_repository,
)
from app.schemas import (
    AgentRequest,
    AgentResponse,
    PlacePreferenceInsight,
    PlaceReasonRequest,
    PlaceReasonResponse,
    RecommendationPlaceDetailRequest,
    RecommendationPlaceDetailResponse,
)
from app.services.runtime.agent_runtime import run_agent
from app.services.runtime.follow_up_suggester import suggest_follow_ups
from app.services.runtime.info_response_transform import to_info_place_card
from app.services.runtime.localization import (
    localize_follow_ups_for_user,
    localize_request_for_runtime,
    localize_response_for_user,
)
from app.state.service import RecordSessionMessageRequest, record_session_message
from app.state.session import new_trace_id

router = APIRouter(tags=["chat"])
logger = logging.getLogger(__name__)


def _log_incoming_location(request: AgentRequest) -> None:
    """발화가 들어올 때 그 턴이 가진 위치를 한 줄로 남긴다.

    **위치가 화면 설정·발화·기기 좌표 중 어디서 온 것인지 사후에 알 방법이 없었다.**
    같은 설정으로 물어도 검색 기준이 GPS로 갔다가 검색지로 갔다가 출발지로 가는
    일이 있는데(2026-09-07 확인), 응답만 봐서는 요청에 무엇이 실려 왔는지 안 보여
    매번 재현부터 다시 해야 했다.

    **좌표는 찍지 않고 있고/없고만 남긴다.** 서버에 위치를 남기지 않으려는 작업과
    같은 이유다(docs/location-storage-removal.local.md) — 저장을 지우면서 로그로
    흘리면 옮겨 담는 것에 지나지 않는다. 장소 이름은 사용자가 화면에서 직접 고른
    값이고 어느 칸이 비어 결과가 갈렸는지 보려면 이름이 필요해 그대로 남긴다.
    """

    logger.info(
        "발화 수신 | 출발지=%s | 검색지=%s | GPS=%s | 세션=%s",
        request.selected_current_location or "-",
        request.selected_search_center or "-",
        "있음" if request.device_location else "없음",
        request.session_id or "새 세션",
    )


async def _request_for_runtime(request: AgentRequest) -> AgentRequest:
    """영어 입력만 한국어 Agent Runtime 사본으로 변환한다."""

    _log_incoming_location(request)
    if request.language != "en":
        return request
    async with create_external_client() as client:
        return await localize_request_for_runtime(request, get_google_translate_provider(client))


async def _response_for_user(response: AgentResponse, *, language: str) -> AgentResponse:
    """영어 화면에 보이는 답변·카드 문장을 번역한다."""

    if language != "en":
        return response
    async with create_external_client() as client:
        return await localize_response_for_user(
            response, language=language, translator=get_google_translate_provider(client)
        )


def _record_transcript(request: AgentRequest, response: AgentResponse) -> None:
    """그 턴에 화면으로 나간 것을 그대로 남긴다. (TP-222 후속 — 화면 기록)

    **Runtime이 아니라 라우트가 남기는 이유는 시점 때문이다.** Runtime을 빠져나온
    응답은 아직 사용자가 볼 모양이 아니다 — 후속 질문은 스트리밍 경로에서 done
    뒤에 붙고, 영어 화면의 번역도 그 뒤에 일어난다. 화면 기록은 "그때 화면에
    나갔던 것"이어야 하므로 그 둘이 모두 끝난 자리에서 부른다.

    실패는 삼킨다. 기록은 이미 사용자에게 다 보여준 답변의 부가 기능이라, 저장
    장애가 완결된 턴을 뒤집으면 안 된다.
    """
    payload = response.model_dump(mode="json")
    # **그 턴의 GPS 좌표는 담지 않는다.** 화면은 이 값을 읽지 않는데(복원은
    # 말풍선과 카드만 그린다), 그대로 두면 턴마다 좌표 사본이 쌓여 세션 하나가
    # 곧 이동 경로가 된다. 현재 위치는 agent_states.api_context에 이미 한 벌
    # 있고 그쪽은 갱신될 뿐 누적되지 않는다.
    payload.get("state", {}).pop("api_context", None)

    try:
        record_session_message(
            RecordSessionMessageRequest(
                session_id=response.state.session_id,
                run_id=response.state.run_id,
                user_input=request.user_input,
                payload=payload,
            )
        )
    except Exception:
        logger.warning("화면 기록 저장 실패(응답 흐름에는 영향 없음)", exc_info=True)


async def _follow_ups_for_user(
    request: AgentRequest, response: AgentResponse, *, runtime_request: AgentRequest
) -> list[str]:
    """`done` 뒤에 이어 보낼 후속 질문 문구를 만든다.

    Runtime 안이 아니라 여기서 부르는 이유는 순서 때문이다 — 이 호출은 답변이 이미
    화면에 다 뜬 뒤에 도는데, `done`보다 앞에 두면 그 시간만큼 턴이 안 끝나 답변과
    카드 아래에 로딩 말풍선이 한 번 더 뜬 것처럼 보인다(D-102).

    입력은 **한국어 사본**(`runtime_request`, `response`)이다. 화면에 나가는 문구는
    그 결과를 다시 영어로 옮긴 것이다 — Runtime이 한국어로만 도는 전제를 여기서도
    지킨다.
    """

    suggestions = await suggest_follow_ups(runtime_request, response, llm=get_llm_provider())
    if request.language != "en" or not suggestions:
        return suggestions
    async with create_external_client() as client:
        return await localize_follow_ups_for_user(
            suggestions,
            language=request.language,
            translator=get_google_translate_provider(client),
        )


@router.post("/chat", response_model=AgentResponse)
async def chat(request: AgentRequest, principal: OptionalPrincipal) -> AgentResponse:
    runtime_request = await _request_for_runtime(request)
    response = await run_agent(runtime_request, principal=principal)
    for_user = await _response_for_user(response, language=request.language)
    _record_transcript(request, for_user)
    return for_user


@router.post("/chat/place-details", response_model=RecommendationPlaceDetailResponse)
async def recommendation_place_details(
    request: RecommendationPlaceDetailRequest,
) -> RecommendationPlaceDetailResponse:
    """추천/INFO 카드 한 곳의 C PlaceDetails를 LLM 없이 조회한다.

    C의 INFO 경로는 이름으로 장소를 해석한다. 요청이 ``place_id``를 명시한
    경우(추천 카드 클릭)에만 응답의 ``place_id``와 대조해, 다르면 화면에 싣지
    않는다 — 동명 장소의 상세가 잘못 열리는 것보다 상세 정보 없음이 안전하다.
    혼잡도·행사 INFO 카드는 ``place_id`` 없이 이름으로만 조회하며, 이 경우 대조를
    건너뛴다(애초에 이름으로 해석된 장소라 이름 재해석이 일관된다).
    """

    async with create_external_client() as client:
        context_provider = get_context_provider(client)
        info_response = await context_provider.fetch_info_context(
            InfoContextRequest(
                request_id=new_trace_id(),
                place_name=request.place_name,
                place_context="from_recommendation",
                question_type="general_info",
            )
        )

    place_card = to_info_place_card(info_response)
    if info_response.status == "unavailable":
        return RecommendationPlaceDetailResponse(
            status="unavailable",
            requested_place_id=request.place_id,
        )
    if place_card is None:
        return RecommendationPlaceDetailResponse(
            status="no_data",
            requested_place_id=request.place_id,
        )
    # place_id를 명시한 요청(추천 카드 클릭)에만 동명 안전장치로 대조한다.
    if request.place_id is not None and place_card.place_id != request.place_id:
        logger.warning(
            "추천 카드 상세 ID 불일치: requested=%s resolved=%s name=%s",
            request.place_id,
            place_card.place_id,
            request.place_name,
        )
        return RecommendationPlaceDetailResponse(
            status="no_data",
            requested_place_id=request.place_id,
        )
    if place_card.place_id:
        place_card = place_card.model_copy(
            update={"preference_insights": await _place_preference_insights(place_card.place_id)}
        )
    return RecommendationPlaceDetailResponse(
        status="success",
        requested_place_id=request.place_id,
        place_card=place_card,
    )


async def _place_preference_insights(place_id: str) -> list[PlacePreferenceInsight]:
    """그 장소의 취향 태그와 태그별 후기 근거를 읽는다. 못 읽으면 빈 목록이다.

    조회 실패를 부르는 쪽의 실패로 만들지 않는다 — 취향 근거는 상세 카드에도
    추천 이유 문장에도 부가 정보라, 이것 때문에 카드 전체가 안 나가는 것이 훨씬
    나쁘다. 상세조회와 이유 문장 두 경로가 같은 값을 읽어 여기로 모았다.

    **취향 스위치(`taste_evidence_enabled`)가 꺼져 있으면 DB를 읽지 않고 빈
    목록이다.** 태그와 대표 후기는 블로그·리뷰에서 뽑은 값이라 임베딩 검색과
    같은 출처다. 두 경로가 여기로 모여 있어 한 줄로 둘 다 막힌다 — 상세 카드의
    "방문자 후기에 나타난 특징"이 비고, 이유 문장은 근거가 없어 LLM을 부르지
    않는다(아래 `recommendation_place_reason`의 `if not insights`).
    """

    if not settings.taste_evidence_enabled:
        return []
    async with create_external_client() as client:
        preference_repository = get_place_details_repository(client)
        if preference_repository is None:
            return []
        try:
            rows = await preference_repository.find_preference_insights(place_id)
            return [PlacePreferenceInsight.model_validate(row) for row in rows]
        except Exception:
            logger.exception("상세 카드 취향 근거 조회 실패 — 취향 근거 없이 응답한다")
            return []


@router.post("/chat/place-details/reason", response_model=PlaceReasonResponse)
async def recommendation_place_reason(request: PlaceReasonRequest) -> PlaceReasonResponse:
    """상세 카드의 "AI가 추천하는 이유" 문장을 만든다. 못 만들면 ai_reason이 None이다.

    **상세조회(`/chat/place-details`)와 나눈 별도 호출이다.** 처음에는 상세 응답에
    문장을 실었는데, 그러면 문장 생성에 드는 1~2초가 주소·운영시간·사진이 뜨는
    시각 전체를 뒤로 밀었다 — 모델이 느려지거나 폴백까지 가면 그만큼 상세 카드가
    통째로 멈춘다. 화면이 카드를 먼저 그리고 문장은 도착하는 대로 채우도록,
    기다리는 쪽을 이 호출 하나로 좁혔다.

    **사용자 취향과 맞은 태그를 먼저 말한다.** 화면이 추천 카드에서 이미 들고 있던
    일치 표시(`preference_tags[].is_query_match`)를 `matched_preference_codes`로
    받아, 저장소가 준 태그 중 어느 것을 넘길지 고르는 데 쓴다. 저장소 순서는 그
    장소에서 많이 언급된 순서라, 그대로 상위 3개만 자르면 사용자가 말한 취향에
    걸린 태그가 아예 빠질 수 있다. 문장에 실리는 라벨·후기는 여전히 전부 저장소
    조회 결과다 — 화면이 준 것은 고르는 기준뿐이다.

    **취향 태그가 있는 장소에만 만든다.** 근거로 쓸 것이 태그 집계와 후기 문장뿐이라
    태그가 없으면 쓸 재료가 없고, 그때 억지로 부르면 카드에 없는 사실을 지어낼 여지만
    준다. 태그 미수집 장소는 화면이 이 절을 통째로 접는다.

    **실패를 오류 응답으로 만들지 않는다.** 이 문장이 없어도 절이 성립하도록 화면을
    만들었으므로(문장이 없으면 화면이 절을 접는다), LLM이 죽어도 카드는 지금까지처럼
    그대로 있어야 한다. 추천
    요약(compose_recommendation_summary)이 같은 이유로 같은 선택을 한다.
    """

    # 스위치가 둘이다. `place_reason_enabled`는 이 문장만 끄고, 취향 스위치는
    # 근거(취향 태그·후기)부터 끊어 아래 `if not insights`에서 멈춘다.
    if not settings.place_reason_enabled or not settings.taste_evidence_enabled:
        return PlaceReasonResponse()
    insights = await _place_preference_insights(request.place_id)
    if not insights:
        return PlaceReasonResponse()
    try:
        result = await get_llm_provider().generate_place_reason(
            place_name=request.place_name,
            category_label=request.category_label,
            insights=insights,
            matched_preference_codes=request.matched_preference_codes,
        )
    except Exception:
        logger.warning("상세 카드 추천 이유 생성 실패 — 그 절을 접는다", exc_info=True)
        return PlaceReasonResponse()
    return PlaceReasonResponse(ai_reason=result.data.strip() or None)


@router.post("/chat/stream")
async def chat_stream(
    request: AgentRequest, http_request: Request, principal: OptionalPrincipal
) -> EventSourceResponse:
    """Agent의 진행 상태와 스트리밍 가능한 LLM 답변을 SSE로 순서대로 전달한다.

    기존 /chat 단발 JSON 계약은 유지한다. 스트리밍 도중에는 HTTP 예외 핸들러가 이미
    응답을 시작한 뒤라 상태 코드를 바꿀 수 없으므로, AppError는 error 이벤트의 기존
    code/message/retryable 형태로 전달한다.
    """

    async def event_stream() -> AsyncIterator[ServerSentEvent]:
        queue: asyncio.Queue[tuple[str, dict[str, object]]] = asyncio.Queue()
        started_at = time.monotonic()
        task: asyncio.Task[AgentResponse] | None = None
        # 화면 기록은 done을 내보낸 **뒤에** 남긴다(후속 질문이 그때 정해진다).
        # 그 사이에 사용자가 창을 닫으면 이 제너레이터가 그대로 닫혀 그 턴의
        # 기록만 빠지고, 그러면 그 대화는 영영 "온전하지 않음"으로 판정돼
        # 근사치로만 복원된다. finally에서 한 번 더 부를 수 있게 붙잡아 둔다.
        to_record: AgentResponse | None = None

        def record_once() -> None:
            nonlocal to_record
            if to_record is None:
                return
            recorded, to_record = to_record, None
            _record_transcript(request, recorded)

        async def emit(event: str, payload: dict[str, object]) -> None:
            # 영어 응답은 마지막에 문장 묶음을 한 번에 번역한다. Runtime의 한국어
            # message_delta/result를 먼저 내보내면 화면에 한국어가 잠깐 보이고 카드도
            # 번역 전 상태로 고정되므로, 진행 단계만 유지하고 이 세 이벤트는 숨긴다.
            if request.language == "en" and event in {"message_start", "message_delta", "result"}:
                return
            await queue.put(
                (
                    event,
                    {
                        "elapsed_ms": int((time.monotonic() - started_at) * 1000),
                        **payload,
                    },
                )
            )

        try:
            runtime_request = await _request_for_runtime(request)
            task = asyncio.create_task(
                run_agent(
                    runtime_request,
                    principal=principal,
                    stream_event_sink=emit,
                    stream_recommendation_summary=True,
                    # 후속 질문은 done 뒤에 따로 만든다(_follow_ups_for_user).
                    generate_follow_ups=False,
                )
            )
            while not task.done() or not queue.empty():
                if await http_request.is_disconnected():
                    task.cancel()
                    return
                try:
                    event, payload = await asyncio.wait_for(queue.get(), timeout=0.1)
                except TimeoutError:
                    continue
                yield ServerSentEvent(event=event, data=json.dumps(payload, ensure_ascii=False))

            try:
                # **한국어 사본을 따로 붙잡아 둔다.** 후속 질문을 만드는 입력이
                # 이쪽이어야 한다 — 번역본을 넘기면 한국어 지침에 영어 답변이
                # 들어가고, 그렇게 나온 문구를 다시 ko→en으로 한 번 더 번역하게
                # 된다. 한국어 요청이면 두 이름이 같은 객체를 가리킨다.
                runtime_response = await task
                response = await _response_for_user(
                    runtime_response, language=request.language
                )
            except AppError as exc:
                yield ServerSentEvent(
                    event="error",
                    data=json.dumps(
                        {
                            "elapsed_ms": int((time.monotonic() - started_at) * 1000),
                            "code": exc.code,
                            "message": exc.message,
                            "retryable": exc.retryable,
                            "details": {"provider": exc.provider, "upstream": exc.details},
                        },
                        ensure_ascii=False,
                    ),
                )
                return
            except Exception:
                # SSE는 이미 200 응답을 시작했으므로 전역 예외 핸들러가 JSON 오류로
                # 바꿀 수 없다. 서버 로그에는 원인을 남기고, 프론트에는 기존 공통
                # 오류 계약과 같은 형태의 error 이벤트를 보낸다.
                logger.exception("스트리밍 채팅 처리 실패")
                yield ServerSentEvent(
                    event="error",
                    data=json.dumps(
                        {
                            "elapsed_ms": int((time.monotonic() - started_at) * 1000),
                            "code": "internal_server_error",
                            "message": "요청을 처리하지 못했어요. 잠시 후 다시 시도해주세요.",
                            "retryable": True,
                            "details": None,
                        },
                        ensure_ascii=False,
                    ),
                )
                return

            # done을 내보내기 전에 잡아 둔다. 여기부터는 언제 끊겨도 finally가
            # 기록을 남긴다(후속 질문이 붙기 전 모양일 수는 있어도, 그 턴이
            # 통째로 빠지지는 않는다).
            to_record = response

            yield ServerSentEvent(
                event="done",
                data=json.dumps(
                    {
                        "elapsed_ms": int((time.monotonic() - started_at) * 1000),
                        "response": response.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                ),
            )

            # **done 뒤에 보낸다.** 화면은 done에서 턴을 끝내 로딩을 감추고 입력창을
            # 풀고, 버튼만 조금 늦게 붙는다. 만들지 못했으면 이벤트를 아예 보내지
            # 않는다 — 빈 목록을 보내도 화면이 할 일이 없다.
            try:
                suggestions = await _follow_ups_for_user(
                    request, runtime_response, runtime_request=runtime_request
                )
            except Exception:
                # suggest_follow_ups()가 자체적으로 삼키지만 번역까지 포함한 이
                # 구간 전체를 한 번 더 감싼다. 이미 done을 보낸 뒤라 여기서 예외가
                # 새면 완결된 턴이 스트림 오류로 뒤집힌다.
                logger.warning("후속 질문 전달 실패(답변에는 영향 없음)", exc_info=True)
                suggestions = []

            # 여기가 응답이 화면과 같아지는 지점이다 — 번역이 끝났고 후속 질문도
            # 정해졌다. 화면 기록은 이 모양으로 남긴다.
            response.suggested_follow_ups = suggestions
            record_once()

            if suggestions:
                yield ServerSentEvent(
                    event="follow_ups",
                    data=json.dumps(
                        {
                            "elapsed_ms": int((time.monotonic() - started_at) * 1000),
                            "suggestions": suggestions,
                        },
                        ensure_ascii=False,
                    ),
                )
        finally:
            # 정상 경로에서는 이미 남겼으므로 no-op이다. done 뒤에 끊긴 경우에만
            # 여기서 실제로 기록한다.
            record_once()
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    return EventSourceResponse(
        event_stream(),
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
