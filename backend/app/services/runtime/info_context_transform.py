"""A → C 변환: INFO 질의 전체.

context_transform.py(RECOMMEND)와 같은 원칙으로, 이 모듈은 INFO 질의 변환을
담당한다. question_type 8종(operating_hours/fee/parking/facility/event/
location_info/general_info/concentration) 모두 이 함수를 거친다(D-054/D-055,
backend/docs/package-a/info-question-types-handoff.md). info_context_schemas.py의
계약 문서 참고.
"""

from __future__ import annotations

from app.agent_context.schemas import Coordinates
from app.schemas import InfoPayload
from app.services.runtime.info_context_schemas import InfoContextRequest


def to_info_context_request(
    request_id: str, info: InfoPayload, *, device_location: str | None = None
) -> InfoContextRequest:
    """A의 InfoPayload를 C에 보낼 InfoContextRequest로 변환한다.

    ``device_location``("위도,경도")은 좌표로 바꿔 실어 보낸다. INFO의 다른 유형은
    사용자가 말한 지명을 지오코딩해 쓰지만, "근처에 화장실 있어?"는 지명이 없을 수
    있어 현재 위치가 유일한 기준점이 된다. 형식이 깨진 값은 조용히 버린다 — 잘못된
    GPS 하나가 INFO 질의 전체를 막지 않아야 한다(A의 _valid_location과 같은 태도).
    """

    return InfoContextRequest(
        request_id=request_id,
        place_name=info.place_name,
        place_context=info.place_context.value,
        question_type=info.question_type.value,
        specific_question=info.specific_question,
        visit_time=info.visit_time,
        origin_coordinates=_to_coordinates(device_location),
    )


def _to_coordinates(device_location: str | None) -> Coordinates | None:
    if not device_location:
        return None
    parts = device_location.split(",")
    if len(parts) != 2:
        return None
    try:
        return Coordinates(latitude=float(parts[0]), longitude=float(parts[1]))
    except ValueError:
        return None


__all__ = ["to_info_context_request"]
