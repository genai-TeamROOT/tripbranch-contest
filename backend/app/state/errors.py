"""Package B - State 저장소 관련 공통 오류.

역할: 저장소 구현체(InMemory/Supabase 등) 종류와 무관하게, B의 공개
진입점(service.py)에서 발생하는 예상 못한 예외를 프로젝트 표준 오류
형식(app.errors.AppError)으로 통일한다.

SupabaseRepositoryError처럼 이미 AppError인 예외는 그 자체로 의미가
있으므로 이 파일이 감싸지 않는다. service.py의 _wrap_store_errors가
AppError는 그대로 통과시키고 그 외의 예외만 이 클래스로 감싼다.
"""

from __future__ import annotations

from app.errors import AppError


class StateStoreError(AppError):
    """저장소 호출이 예상 못하게 실패했을 때. (Jira: B 영역 오류 공통 형식)"""

    def __init__(self, detail: str) -> None:
        super().__init__(
            code="state_store_error",
            message="상태 저장 중 문제가 발생했어요. 잠시 후 다시 시도해주세요.",
            status_code=502,
            retryable=True,
            provider="state_store",
            details={"upstream_detail": detail},
        )


class SessionOwnershipError(AppError):
    """인증된 신원과 세션에 저장된 소유자가 다를 때. (D-063 결정 2 후속, D-073)

    session_id만 알면(추측·유출) 남의 세션에 접근할 수 있던 문제를 닫는다.
    principal이 없는 요청(토큰 미전송)에는 이 오류를 내지 않는다 — Phase 4
    전면 필수화 전까지는 정상 경로이기 때문이다(session.verify_ownership 참고).
    401(신원 자체가 무효)과 구분하기 위해 403을 쓴다 — 토큰은 유효하지만
    이 세션에 대한 권한이 없다는 뜻이다.
    """

    def __init__(self) -> None:
        super().__init__(
            code="session_ownership_mismatch",
            message="이 세션에 접근할 권한이 없어요.",
            status_code=403,
        )

class SessionNotFoundError(AppError):
    """없는(또는 만료된) 세션을 지목했을 때. (TP-222 후속 — 대화 이름 바꾸기)

    조회·삭제는 없는 세션을 오류로 보지 않는다 — "없다"가 정상적인 답이고
    멱등이기 때문이다. 반면 이름 바꾸기는 바꿀 대상이 있어야 성립하므로,
    조용히 성공한 척하면 사용자가 바뀐 줄 알고 넘어간다.
    """

    def __init__(self, session_id: str) -> None:
        super().__init__(
            code="session_not_found",
            message="그 대화를 찾을 수 없어요. 이미 지워졌을 수 있어요.",
            status_code=404,
            details={"session_id": session_id},
        )


class SavedPlaceNotRecommendedError(AppError):
    """보관함에 담으려는 장소가 그 세션에서 노출된 적이 없을 때. (SCHEDULE-12)

    보관함은 "화면에서 본 카드를 담는" 기능이므로, 담을 수 있는 것은 추천
    이력에 있는 place_id뿐이다. 임의 id 주입을 막는 것이 1차 목적이고,
    이름을 추천 시점 스냅샷에서 가져오기 위한 전제이기도 하다.

    세션 자체가 없는 경우도 같은 오류로 답한다 — 호출자가 알아야 하는 사실은
    양쪽 모두 "이 세션에서 그 장소를 담을 수 없다"로 같고, 세션 존재 여부를
    별도 코드로 구분해 주면 session_id 유효성을 훑는 통로가 된다.

    400을 쓴다 — 권한 문제(403)나 저장소 장애(502)가 아니라 요청 자체가
    가리키는 대상이 잘못된 경우다.
    """

    def __init__(self, place_id: str) -> None:
        super().__init__(
            code="saved_place_not_recommended",
            message="이 대화에서 추천된 적이 없는 장소는 담을 수 없어요.",
            status_code=400,
            details={"place_id": place_id},
        )


class SavedScheduleNotFoundError(AppError):
    """없는 저장 일정을 지목했을 때. (SCHEDULE 카드 2)

    조회·이름 바꾸기는 대상이 있어야 성립한다. 삭제는 여기에 넣지 않는다 —
    "그 일정이 없다"가 사용자가 원한 결과와 같아 멱등으로 두는 것이 맞다
    (saved_places.remove와 같은 판단).

    **남의 것일 때는 403으로 따로 답한다.** 두 경우를 404로 합치면 존재 여부가
    새지 않지만, id가 uuid라 훑어서 알아낼 수 있는 값이 아니고, 갈라 두는 편이
    화면과 로그에서 원인을 짚기 쉽다 — 대화 쪽(SessionNotFoundError /
    SessionOwnershipError)이 이미 같은 방식이다.
    """

    def __init__(self, schedule_id: str) -> None:
        super().__init__(
            code="saved_schedule_not_found",
            message="그 일정을 찾을 수 없어요. 이미 지워졌을 수 있어요.",
            status_code=404,
            details={"schedule_id": schedule_id},
        )


class SavedScheduleOwnershipError(AppError):
    """저장 일정의 소유자가 인증된 신원과 다를 때. (SCHEDULE 카드 2)

    `SessionOwnershipError`와 성격은 같지만 문구가 다르다 — 그쪽은 "세션"을
    말하는데 사용자가 보는 것은 저장한 일정이다.

    401(신원 자체가 무효)과 구분해 403을 쓴다.
    """

    def __init__(self) -> None:
        super().__init__(
            code="saved_schedule_ownership_mismatch",
            message="이 일정에 접근할 권한이 없어요.",
            status_code=403,
        )
