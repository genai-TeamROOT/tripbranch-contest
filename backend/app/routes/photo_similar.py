"""사진으로 분위기가 닮은 장소를 찾는 API.

`POST /api/places/similar-by-photo`는 사진 한 장과 위치를 받아, 그 주변에서
분위기가 닮은 장소를 유사도 순으로 돌려준다.

**인텐트를 타지 않는다.** 인텐트는 "사용자 발화가 무엇을 원하는가"를 분류하는
장치인데, 사진은 발화가 아니라 이미 목적이 확정된 입력이다. 음성 전사
(`/api/transcribe`)가 같은 이유로 인텐트 밖에 있다.

**대화가 잡은 위치를 이어받는다.** 앞 턴에서 "안국역"이라고 말했으면 사진도 거기서
찾는다. 순서는 기존 추천과 같다 — `search_center` → `current_location` → 기기 GPS
(agent_context/service.py::fetch_context). 사진만 다른 규칙으로 위치를 정하면 같은
대화 안에서 "추천은 안국역인데 사진은 내 위치"가 된다.

**추천 채점을 타지 않는다.** 순위는 사진 유사도만으로 정한다. 거리·취향·혼잡도를
섞지 않으므로 `domain/scoring.py`를 건드리지 않는다(D-094 후속, TP-175).
다만 하드 필터는 태운다 — 지금 닫힌 가게가 1등으로 나오면 쓸모가 없다.
"""

from __future__ import annotations

import logging
import time
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile

from app.auth.dependency import OptionalPrincipal
from app.auth.principal import Principal
from app.errors import AppError
from app.observability.api_usage import create_external_client
from app.providers.factory import (
    get_geocoding_provider,
    get_local_search_provider,
    get_place_details_repository,
    get_place_location_repository,
    get_place_mood_provider,
    get_place_mood_reranker,
    get_place_provider,
)
from app.schemas import PhotoSimilarPlace, PhotoSimilarPlacesResponse
from app.services.photo_similar import PhotoSimilarQuery, build_photo_similar_places
from app.state import service as state_service
from app.state.schema import ConversationTurn

logger = logging.getLogger(__name__)

router = APIRouter(tags=["photo-similar"])

# 사진 한 장의 상한. 휴대폰 원본 사진이 10MB를 넘는 경우가 있어 넉넉히 두되,
# 임베딩은 224x224로 줄여 쓰므로 그보다 큰 파일을 받을 이유는 없다.
_MAX_IMAGE_BYTES = 10 * 1024 * 1024

# 브라우저가 보내는 형식만 받는다. SigLIP 인코더가 Pillow로 열어 RGB로 바꾸므로
# 형식 자체는 더 넓게 되지만, 받는 범위를 좁혀 두면 예상 못 한 입력이 모델까지
# 내려가지 않는다.
_ALLOWED_MIME_TYPES = frozenset(
    {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/heic", "image/heif"}
)

_DEFAULT_LIMIT = 10
_MAX_LIMIT = 30

# 대화에 남길 사용자 발화. **해석하지 않는다** — 사진 검색은 인텐트를 타지 않으므로
# 이 문장이 분류되는 일은 없다. 화면 말풍선과 최근 대화 맥락에 "무엇을 요청한
# 턴이었는지"를 남기려고 문장 모양으로 둔 것이다.
#
# 자리표시자("[사진으로 장소 찾기]")를 쓰지 않는 이유는 다음 턴 때문이다. 이 값은
# ConversationTurn.user_input으로 저장돼 다음 턴의 모델 맥락에 실리는데, 그때
# 사람이 할 법한 문장이어야 "그중에 첫 번째"처럼 이어지는 말이 풀린다.
#
# 화면에도 같은 문장이 나간다(PhotoSimilarResultMessage.tsx). 고칠 때 함께 고친다.
PHOTO_SEARCH_USER_INPUT = "이 사진과 비슷한 장소 추천해줘"

# 화면 기록(SessionMessage.payload)이 사진 검색임을 알리는 표시. 화면 기록은
# 원래 AgentResponse만 담았고 복원 화면이 그 모양을 가정한다 — 표시가 없으면
# 사진 검색 기록을 AgentResponse로 읽다가 터진다. 옛 기록에는 이 키가 없으므로
# **없으면 AgentResponse**로 읽는 쪽이 하위 호환이다.
PHOTO_SEARCH_RECORD_KIND = "photo_similar"


@router.post("/places/similar-by-photo", response_model=PhotoSimilarPlacesResponse)
async def similar_by_photo(
    principal: OptionalPrincipal,
    image: Annotated[UploadFile, File(description="분위기를 찾을 사진")],
    location_query: Annotated[str | None, Form(description='지역명. 예: "성수동"')] = None,
    session_id: Annotated[
        str | None, Form(description="대화 세션. 앞 턴이 잡은 위치를 이어받는다")
    ] = None,
    latitude: Annotated[float | None, Form(description="기기 GPS 위도")] = None,
    longitude: Annotated[float | None, Form(description="기기 GPS 경도")] = None,
    search_radius_km: Annotated[float | None, Form(description="검색 반경(km)")] = None,
    limit: Annotated[int, Form(description="돌려줄 장소 수")] = _DEFAULT_LIMIT,
) -> PhotoSimilarPlacesResponse:
    """사진과 위치를 받아 분위기가 닮은 장소를 찾는다.

    위치는 `location_query`가 있으면 그것으로 풀고, 없으면 좌표를 그대로 쓴다.
    **지역명이 좌표를 이긴다** — 사용자가 적은 쪽이 의도이고 좌표는 적지 않았을
    때의 기본값이다. 둘 다 없으면 `location_required`로 되묻는다.

    사진은 저장하지 않는다. 요청 메모리에서 임베딩만 하고 버린다. 다만 **이 턴이
    있었다는 사실은 대화에 남긴다** — 남기지 않으면 다음 턴의 모델 맥락에서
    사진 검색만 빠져 "그중에 첫 번째"가 안 풀리고, 지난 대화를 되돌릴 때도 이
    턴만 사라진다.
    """
    mime_type = (image.content_type or "").split(";", maxsplit=1)[0].strip().lower()
    if mime_type not in _ALLOWED_MIME_TYPES:
        raise AppError(
            code="unsupported_image_format",
            message="지원하지 않는 사진 형식이에요. JPG나 PNG로 올려 주세요.",
            status_code=415,
        )

    image_bytes = await image.read()
    if not image_bytes:
        raise AppError(
            code="empty_image",
            message="사진이 비어 있어요. 다시 올려 주세요.",
            status_code=422,
            retryable=True,
        )
    if len(image_bytes) > _MAX_IMAGE_BYTES:
        raise AppError(
            code="image_too_large",
            message="사진이 너무 커요. 10MB 이하로 올려 주세요.",
            status_code=413,
        )

    resolved_query = (location_query or "").strip() or _session_location(session_id, principal)
    # 위치를 어디서 얻었는지 남긴다. 사진 검색이 "어디서 찾을까요"로 끝났을 때
    # 좌표가 없었던 것인지 세션이 비었던 것인지 로그만 보고 갈릴 수 있어야 한다.
    logger.info(
        "사진 검색 위치 해석: query=%s session=%s gps=%s",
        resolved_query or "-",
        "있음" if session_id else "없음",
        "있음" if latitude is not None and longitude is not None else "없음",
    )

    started = time.perf_counter()
    async with create_external_client() as client:
        result = await build_photo_similar_places(
            PhotoSimilarQuery(
                image_bytes=image_bytes,
                location_query=resolved_query,
                latitude=latitude,
                longitude=longitude,
                search_radius_km=search_radius_km,
                limit=max(1, min(limit, _MAX_LIMIT)),
            ),
            geocoding_provider=get_geocoding_provider(client),
            place_provider=get_place_provider(client),
            mood_provider=get_place_mood_provider(client),
            # 채팅 경로(agent_context/factory.py)와 같은 조합이다. 지오코딩만
            # 넘기면 "안국역" 같은 장소명이 안 풀린다.
            details_repository=get_place_details_repository(client),
            place_repository=get_place_location_repository(client),
            local_search_provider=get_local_search_provider(client),
            # 꺼져 있으면 None이고, 그때는 임베딩 순서를 그대로 낸다.
            reranker=get_place_mood_reranker(client),
        )

    # 검색이 성사된 뒤에 세션을 확보한다. 위치를 못 잡아 위에서 422로 끝난 요청은
    # 여기 도달하지 않으므로 빈 대화가 남지 않는다 — 일반 발화도 해석이 끝난
    # 자리(agent_runtime의 apply 호출)에서 세션을 발급하는 것과 같은 규칙이다.
    session = state_service.ensure_session(
        state_service.EnsureSessionRequest(
            session_id=session_id,
            title=_session_title(result.center_name, used_location_query=bool(resolved_query)),
        ),
        principal=principal,
    )

    response = PhotoSimilarPlacesResponse(
        places=[
            PhotoSimilarPlace(
                content_id=row.content_id,
                title=row.name,
                similarity=row.similarity,
                photo_count=row.photo_count,
                image_url=row.image_url,
            )
            for row in result.places
        ],
        center_name=result.center_name,
        session_id=session.session_id,
        candidate_count=result.candidate_count,
        truncated_count=result.truncated_count,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )

    _record_turn(session.session_id, response)
    return response


def _session_title(center_name: str, *, used_location_query: bool) -> str:
    """사진으로 시작한 대화의 제목.

    **기준점 이름은 지역명으로 찾았을 때만 넣는다.** 좌표로만 찾은 경우 이름이
    "현재 위치"인데, 그 말은 목록에서 나중에 다시 볼 때 아무것도 가리키지 않는다.
    좌표를 동네 이름으로 바꿔 넣지 않는 이유는 그것이 검색지 이름을 DB에 적는
    일이라, 서버가 위치를 저장하지 않기로 한 방향과 반대이기 때문이다.

    발화(PHOTO_SEARCH_USER_INPUT)를 제목으로 쓰지 않는다 — 문장이 고정이라
    사진으로 시작한 대화가 목록에서 전부 같은 이름이 된다.
    """
    if used_location_query and center_name:
        return f"{center_name} 사진으로 찾은 곳"
    return "사진으로 찾은 곳"


def _record_turn(session_id: str, response: PhotoSimilarPlacesResponse) -> None:
    """사진 검색 한 턴을 대화에 남긴다.

    **두 기록을 함께 남긴다.** 최근 대화(모델 맥락)와 화면 기록은 목적이 다르지만,
    복원 판정이 "화면 기록 수 >= 최근 대화 수"라서(state/service.py의
    _to_session_detail) 한쪽만 남기면 그 세션이 통째로 옛 복원 방식으로 떨어진다.

    **실패는 삼킨다.** chat.py의 _record_transcript와 같은 이유다 — 기록은 이미
    사용자에게 다 보여준 결과의 부가 기능이라, 저장 장애가 완결된 턴을 뒤집으면
    안 된다.

    **사진은 담지 않는다.** 원본은 임베딩만 하고 버렸고 축소본은 브라우저에만
    있다. 복원 화면은 사진 없이 문구와 결과만 다시 그린다.
    """
    try:
        state_service.append_conversation_turn(
            state_service.AppendConversationTurnRequest(
                session_id=session_id,
                turn=ConversationTurn(
                    user_input=PHOTO_SEARCH_USER_INPUT,
                    assistant_message=_assistant_summary(response),
                    # intent는 비운다. 사진 검색은 인텐트를 타지 않아 분류된 값이
                    # 없고, 없는 값을 지어내면 다음 턴이 그것을 근거로 삼는다.
                    place_names=[place.title for place in response.places],
                ),
            )
        )
        state_service.record_session_message(
            state_service.RecordSessionMessageRequest(
                session_id=session_id,
                user_input=PHOTO_SEARCH_USER_INPUT,
                payload={
                    "kind": PHOTO_SEARCH_RECORD_KIND,
                    **response.model_dump(mode="json"),
                },
            )
        )
    except Exception:
        logger.warning("사진 검색 기록 저장 실패(응답 흐름에는 영향 없음)", exc_info=True)


def _assistant_summary(response: PhotoSimilarPlacesResponse) -> str:
    """그 턴에 화면으로 나간 답변을 한 문장으로 옮긴다.

    화면 문구(PhotoSimilarResultMessage.tsx)와 같은 갈래를 탄다 — 결과가 있을
    때와 후보 자체가 없었을 때, 후보는 있는데 비교할 사진이 없었을 때가 다르다.
    다음 턴의 모델이 "왜 아무것도 못 찾았는지"까지 알아야 이어지는 말을 푼다.
    """
    if response.places:
        return (
            f"{response.center_name} 주변에서 분위기가 닮은 곳 "
            f"{len(response.places)}곳을 찾았어요."
        )
    if response.candidate_count == 0:
        return f"{response.center_name} 주변에서 지금 갈 수 있는 곳을 찾지 못했어요."
    return (
        f"{response.center_name} 주변 {response.candidate_count}곳을 봤는데 "
        "사진과 비교할 수 있는 곳이 없었어요."
    )


def _session_location(session_id: str | None, principal: Principal | None) -> str | None:
    """대화가 이미 잡은 검색 중심점을 가져온다.

    순서는 기존 추천과 같다 — `search_center` → `current_location`. B가 병합한
    누적 조건이라 앞 턴에서 말한 위치가 그대로 살아 있다.

    **세션 조회 실패를 요청 실패로 만들지 않는다.** 위치를 못 가져오면 좌표로
    떨어지거나 되묻으면 되는데, 여기서 던지면 사진 검색 자체가 안 된다.
    """
    if not session_id:
        return None
    try:
        context = state_service.get_session_context(session_id, principal=principal)
    except Exception:
        logger.warning("세션 위치를 읽지 못해 좌표로 넘어갑니다.", exc_info=True)
        return None
    if not context.session_exists:
        return None
    conditions = context.user_conditions
    query = conditions.search_center or conditions.current_location
    return (query or "").strip() or None
