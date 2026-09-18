"""Package B - 응답 피드백(좋아요/싫어요) 기록.

roadmap.md 14번. trace.py와 같은 패턴(append-only, 발급 후 저장)을 따르되,
trace_id가 아니라 run_id 단위로 기록한다는 점만 다르다 — FeedbackRecord
docstring 참고.
"""

from datetime import datetime

from app.state.schema import FeedbackRecord, now_kst
from app.state.store import StateStore


def record(
    store: StateStore,
    session_id: str,
    run_id: str,
    rating: str,
    user_input: str | None = None,
    assistant_message: str | None = None,
    intent: str | None = None,
    reason_code: str | None = None,
    comment: str | None = None,
) -> FeedbackRecord:
    """응답 1건에 대한 사용자 반응을 기록한다.

    rating은 FeedbackRecord가 "like"/"dislike"로 검증한다 — 잘못된 값이면
    여기서 즉시 pydantic ValidationError가 난다(TraceRecord의 step 등과
    달리 B가 값을 검증하는 예외적인 필드, 스키마 docstring 참고).

    user_input/assistant_message/intent/comment는 모두 선택 필드다 — 프론트가
    안 보내면 None으로 저장되고 rating 기록 자체는 그대로 유효하다(스키마
    docstring 참고).
    """
    feedback = FeedbackRecord(
        session_id=session_id,
        run_id=run_id,
        rating=rating,
        user_input=user_input,
        assistant_message=assistant_message,
        intent=intent,
        reason_code=reason_code,
        comment=comment,
        recorded_at=now_kst(),
    )
    store.append_feedback([feedback])
    return feedback


def get_feedback(store: StateStore, session_id: str) -> list[FeedbackRecord]:
    """세션의 피드백 기록 전체를 조회한다. append-only이므로 순서를 보존한다."""
    return store.get_feedback(session_id)


_DEFAULT_DISLIKE_LIMIT = 50


def list_dislikes(store: StateStore, limit: int = _DEFAULT_DISLIKE_LIMIT) -> list[FeedbackRecord]:
    """최근 순으로 "싫어요" 기록을 모은다. 세션 범위가 아니라 전체 대상이다.

    roadmap.md 14번이 말하는 "싫어요가 많이 눌린 답변만 따로 뽑아 분석"의
    입력 목록이다. 실제 분석(어떤 prompt_version에서 몰렸는지 등)은
    호출부가 이 목록의 run_id로 get_traces(session_id)를 다시 불러 조인한다
    — get_traces는 이미 세션 단위로 존재하므로 여기서 다시 만들지 않는다.
    """
    return store.list_dislike_feedback(limit)


def list_for_stats(
    store: StateStore, since: datetime | None = None, until: datetime | None = None
) -> list[FeedbackRecord]:
    """집계(TP-146)를 위해 rating을 가리지 않고 전체 피드백을 모은다.

    list_dislikes와 달리 like/dislike를 모두 포함하고 limit도 없다 — 통계는
    상위 N건이 아니라 전체 합이어야 의미가 있다. since/until은 recorded_at
    기준 반열린구간이며 둘 다 선택이다.
    """
    return store.list_feedback_for_stats(since=since, until=until)


__all__ = ["record", "get_feedback", "list_dislikes", "list_for_stats"]
