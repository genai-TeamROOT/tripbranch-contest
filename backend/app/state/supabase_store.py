"""Package B - Supabase 기반 State 저장소.

설계 문서: docs/package-b/db-store-design-v1.md

StateStore Protocol을 동기(httpx.Client)로 구현한다. supabase_places.py와
같은 Supabase 프로젝트를 쓰지만 완전히 별개의 클라이언트/테이블이라 서로
간섭하지 않는다 (async/sync 클라이언트도 독립적).

get_state/save_state, get_history/save_history는 기존 InMemoryStateStore와
동일하게 "전체를 읽고 통째로 다시 쓰는" 방식을 유지한다 (개별 필드 upsert
아님) — 상위 계층(history.py 등)의 호출 패턴을 그대로 지원하기 위함이다.
ConditionChangeLog/TraceRecord는 append-only라 행 단위 insert로 저장한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

import httpx

from app.state.errors import StateStoreError
from app.state.schema import (
    AgentState,
    ConditionChangeLog,
    FeedbackRecord,
    RecommendationHistory,
    SavedPlaceList,
    SavedSchedule,
    SessionMessage,
    TraceRecord,
    UserFavoriteList,
    UserPreferenceList,
    now_kst,
)

# store.py의 Supabase 임포트는 함수 안에 있어 순환하지 않는다.
from app.state.store import for_persistence


class SupabaseStateStore:
    """Supabase PostgREST를 사용하는 StateStore 구현체.

    테이블: agent_states, recommendation_histories,
            condition_change_logs, trace_records
    (설계 문서 1절 — DDL은 별도 마이그레이션 파일 참고)
    """

    def __init__(
        self,
        supabase_url: str,
        secret_key: str,
        client: httpx.Client,
        timeout_seconds: float = 10.0,
    ) -> None:
        normalized_url = supabase_url.strip().rstrip("/")
        if not normalized_url:
            raise ValueError("supabase_url이 필요합니다.")
        if not secret_key.strip():
            raise ValueError("secret_key가 필요합니다.")
        self._rest_url = f"{normalized_url}/rest/v1"
        self._secret_key = secret_key
        self._client = client
        self._timeout_seconds = timeout_seconds

    # ------------------------------------------------------------ 내부 HTTP

    def _headers(self, prefer: str | None = None) -> dict[str, str]:
        headers = {
            "apikey": self._secret_key,
            "Content-Type": "application/json",
        }
        if prefer is not None:
            headers["Prefer"] = prefer
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json: object | None = None,
        prefer: str | None = None,
    ) -> httpx.Response:
        try:
            response = self._client.request(
                method,
                self._rest_url + path,
                params=params,
                json=json,
                headers=self._headers(prefer),
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            return response
        except httpx.TimeoutException:
            raise StateStoreError("request timeout") from None
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            detail = f"HTTP {status_code}"
            try:
                error_payload = exc.response.json()
                if isinstance(error_payload, Mapping):
                    code = str(error_payload.get("code", "")).strip()
                    message = str(error_payload.get("message", "")).strip()
                    safe_parts = [part for part in (code, message) if part]
                    if safe_parts:
                        detail = f"{detail}: {' - '.join(safe_parts)}"
            except ValueError:
                pass
            raise StateStoreError(detail) from None
        except httpx.HTTPError:
            raise StateStoreError("request failed") from None

    @staticmethod
    def _json(response: httpx.Response) -> object:
        try:
            return response.json()
        except ValueError:
            raise StateStoreError("non-JSON response") from None

    @staticmethod
    def _one_or_none(payload: object) -> Mapping[str, object] | None:
        if not isinstance(payload, list):
            raise StateStoreError("invalid list response")
        if not payload:
            return None
        row = payload[0]
        if not isinstance(row, Mapping):
            raise StateStoreError("invalid row shape")
        return row

    # PostgREST(Supabase REST)는 명시적으로 요청하지 않아도 응답을 기본
    # 1000행으로 자른다(Supabase 프로젝트 API 설정의 max rows). "전체"를
    # 반환해야 하는 통계 조회(list_traces_for_stats/list_feedback_for_stats)가
    # 이 한도에 걸리면 총 건수·집계가 조용히 잘못된 값을 낸다 — limit/offset을
    # 페이지 단위로 넘겨가며 끝까지 모은다. 세션 범위 조회(get_traces 등)는
    # 애초에 이 정도로 커지지 않아 대상이 아니다.
    _STATS_PAGE_SIZE = 1000

    def _fetch_all_rows(self, path: str, params: Mapping[str, str]) -> list[Mapping[str, object]]:
        rows: list[Mapping[str, object]] = []
        offset = 0
        while True:
            page_params = dict(params)
            page_params["limit"] = str(self._STATS_PAGE_SIZE)
            page_params["offset"] = str(offset)
            response = self._request("GET", path, params=page_params)
            payload = self._json(response)
            if not isinstance(payload, list):
                raise StateStoreError(f"invalid {path} response")
            rows.extend(payload)
            if len(payload) < self._STATS_PAGE_SIZE:
                break
            offset += self._STATS_PAGE_SIZE
        return rows

    # ------------------------------------------------------------ AgentState

    def get_state(self, session_id: str) -> AgentState | None:
        response = self._request(
            "GET",
            "/agent_states",
            params={"session_id": f"eq.{session_id}", "select": "*", "limit": "1"},
        )
        row = self._one_or_none(self._json(response))
        if row is None:
            return None
        try:
            return AgentState.model_validate(row)
        except Exception:
            raise StateStoreError("invalid agent_states row") from None

    def save_state(self, state: AgentState) -> None:
        # 사용자 위치는 DB에 남기지 않는다 — 이유는 store.for_persistence 참고.
        self._request(
            "POST",
            "/agent_states",
            params={"on_conflict": "session_id"},
            json=for_persistence(state).model_dump(mode="json"),
            prefer="resolution=merge-duplicates,return=minimal",
        )

    def delete_state(self, session_id: str) -> None:
        self._request(
            "DELETE",
            "/agent_states",
            params={"session_id": f"eq.{session_id}"},
            prefer="return=minimal",
        )

    # ------------------------------------------------------------ History

    def get_history(self, session_id: str) -> RecommendationHistory | None:
        response = self._request(
            "GET",
            "/recommendation_histories",
            params={"session_id": f"eq.{session_id}", "select": "*", "limit": "1"},
        )
        row = self._one_or_none(self._json(response))
        if row is None:
            return None
        try:
            return RecommendationHistory.model_validate(row)
        except Exception:
            raise StateStoreError(
                "invalid recommendation_histories row"
            ) from None

    def save_history(self, history: RecommendationHistory) -> None:
        self._request(
            "POST",
            "/recommendation_histories",
            params={"on_conflict": "session_id"},
            json=history.model_dump(mode="json"),
            prefer="resolution=merge-duplicates,return=minimal",
        )

    def delete_history(self, session_id: str) -> None:
        self._request(
            "DELETE",
            "/recommendation_histories",
            params={"session_id": f"eq.{session_id}"},
            prefer="return=minimal",
        )

    # ------------------------------------------------------------ SavedPlaces

    def get_saved_places(self, session_id: str) -> SavedPlaceList | None:
        response = self._request(
            "GET",
            "/saved_place_lists",
            params={"session_id": f"eq.{session_id}", "select": "*", "limit": "1"},
        )
        row = self._one_or_none(self._json(response))
        if row is None:
            return None
        try:
            return SavedPlaceList.model_validate(row)
        except Exception:
            raise StateStoreError("invalid saved_place_lists row") from None

    def save_saved_places(self, saved: SavedPlaceList) -> None:
        self._request(
            "POST",
            "/saved_place_lists",
            params={"on_conflict": "session_id"},
            json=saved.model_dump(mode="json"),
            prefer="resolution=merge-duplicates,return=minimal",
        )

    def delete_saved_places(self, session_id: str) -> None:
        self._request(
            "DELETE",
            "/saved_place_lists",
            params={"session_id": f"eq.{session_id}"},
            prefer="return=minimal",
        )

    # ------------------------------------------------------------ Preferences

    def list_sessions_for_user(self, user_id: str, limit: int) -> list[AgentState]:
        """사용자의 대화를 최근 순으로. 제목 없는 세션은 대화가 아니라 제외한다.

        **만료 여부로는 거르지 않는다.** 만료는 "낡은 조건을 버렸다"는 뜻이지
        "그 대화가 없었다"는 뜻이 아니다 — 거르면 오래된 대화일수록 목록에서
        사라지는데, 그런 대화를 열어 이어가는 것이 이 목록의 목적이다.

        agent_states_user_recent_idx(user_id, last_active_at desc)를 그대로 탄다.
        """
        response = self._request(
            "GET",
            "/agent_states",
            params={
                "user_id": f"eq.{user_id}",
                "title": "not.is.null",
                "select": "*",
                "order": "last_active_at.desc",
                "limit": str(limit),
            },
        )
        rows = self._json(response)
        if not isinstance(rows, list):
            raise StateStoreError("invalid agent_states rows")
        try:
            return [AgentState.model_validate(row) for row in rows]
        except Exception:
            raise StateStoreError("invalid agent_states row") from None

    def get_preferences(self, user_id: str) -> UserPreferenceList | None:
        """계정 단위 취향. 다른 조회와 달리 키가 user_id다."""
        response = self._request(
            "GET",
            "/user_preferences",
            params={"user_id": f"eq.{user_id}", "select": "*", "limit": "1"},
        )
        row = self._one_or_none(self._json(response))
        if row is None:
            return None
        try:
            return UserPreferenceList.model_validate(row)
        except Exception:
            raise StateStoreError("invalid user_preferences row") from None

    def save_preferences(self, preferences: UserPreferenceList) -> None:
        self._request(
            "POST",
            "/user_preferences",
            params={"on_conflict": "user_id"},
            json=preferences.model_dump(mode="json"),
            prefer="resolution=merge-duplicates,return=minimal",
        )

    def list_session_ids_for_user(self, user_id: str) -> list[str]:
        """탈퇴용. **제목 없는 세션도 담는다** — 남는 행이 없어야 한다(store.py 주석).

        list_sessions_for_user와 달리 title 필터가 없고 id만 가져온다.
        agent_states_user_recent_idx(user_id, ...)의 앞 컬럼을 그대로 탄다.
        """
        response = self._request(
            "GET",
            "/agent_states",
            params={"user_id": f"eq.{user_id}", "select": "session_id"},
        )
        payload = self._json(response)
        if not isinstance(payload, list):
            raise StateStoreError("invalid agent_states response")
        try:
            return [str(row["session_id"]) for row in payload]
        except (KeyError, TypeError):
            raise StateStoreError("invalid agent_states row") from None

    def delete_preferences(self, user_id: str) -> None:
        """탈퇴 전용. 화면의 "취향 비우기"는 빈 목록 저장이다(store.py 주석)."""
        self._request(
            "DELETE",
            "/user_preferences",
            params={"user_id": f"eq.{user_id}"},
            prefer="return=minimal",
        )

    # ------------------------------------------------------------ Favorites

    def get_favorites(self, user_id: str) -> UserFavoriteList | None:
        """계정 단위 즐겨찾기. 취향과 같이 키가 user_id다."""
        response = self._request(
            "GET",
            "/user_favorites",
            params={"user_id": f"eq.{user_id}", "select": "*", "limit": "1"},
        )
        row = self._one_or_none(self._json(response))
        if row is None:
            return None
        try:
            return UserFavoriteList.model_validate(row)
        except Exception:
            raise StateStoreError("invalid user_favorites row") from None

    def save_favorites(self, favorites: UserFavoriteList) -> None:
        self._request(
            "POST",
            "/user_favorites",
            params={"on_conflict": "user_id"},
            json=favorites.model_dump(mode="json"),
            prefer="resolution=merge-duplicates,return=minimal",
        )

    def delete_favorites(self, user_id: str) -> None:
        """탈퇴 전용. 취향과 같은 이유로 비우기와 삭제를 가른다(store.py 주석)."""
        self._request(
            "DELETE",
            "/user_favorites",
            params={"user_id": f"eq.{user_id}"},
            prefer="return=minimal",
        )

    # ------------------------------------------------------------ ChangeLog

    def append_change_logs(self, logs: list[ConditionChangeLog]) -> None:
        """append-only. 기존 기록을 수정하거나 삭제하지 않는다."""
        if not logs:
            return
        self._request(
            "POST",
            "/condition_change_logs",
            json=[log.model_dump(mode="json") for log in logs],
            prefer="return=minimal",
        )

    def get_change_logs(self, session_id: str) -> list[ConditionChangeLog]:
        response = self._request(
            "GET",
            "/condition_change_logs",
            params={
                "session_id": f"eq.{session_id}",
                "select": "*",
                "order": "id.asc",
            },
        )
        payload = self._json(response)
        if not isinstance(payload, list):
            raise StateStoreError("invalid condition_change_logs response")
        try:
            return [ConditionChangeLog.model_validate(row) for row in payload]
        except Exception:
            raise StateStoreError(
                "invalid condition_change_logs row"
            ) from None

    # ------------------------------------------------------------ 화면 기록

    def append_session_messages(self, messages: list[SessionMessage]) -> None:
        """append-only. (TP-222 후속)

        recorded_at을 보내지 않고 서버 기본값에 맡기지 않는다 — 다른 append-only
        테이블과 같이 모델이 만든 시각을 그대로 싣는다. 복원 순서는 id로 정하므로
        이 값이 화면 순서를 좌우하지는 않는다.
        """
        if not messages:
            return
        self._request(
            "POST",
            "/session_messages",
            json=[message.model_dump(mode="json") for message in messages],
            prefer="return=minimal",
        )

    def get_session_messages(self, session_id: str) -> list[SessionMessage]:
        response = self._request(
            "GET",
            "/session_messages",
            params={
                "session_id": f"eq.{session_id}",
                "select": "*",
                "order": "id.asc",
            },
        )
        payload = self._json(response)
        if not isinstance(payload, list):
            raise StateStoreError("invalid session_messages response")
        try:
            return [SessionMessage.model_validate(row) for row in payload]
        except Exception:
            raise StateStoreError("invalid session_messages row") from None

    def delete_session_messages(self, session_id: str) -> None:
        self._request(
            "DELETE",
            "/session_messages",
            params={"session_id": f"eq.{session_id}"},
            prefer="return=minimal",
        )

    # ------------------------------------------------------------ 저장한 일정

    def save_schedule(self, schedule: SavedSchedule) -> SavedSchedule:
        """저장하고 id가 채워진 것을 돌려준다. (SCHEDULE 카드 2)

        **on_conflict로 멱등을 만들지 않고 먼저 조회한다.** 유니크 인덱스가
        `where run_id is not null`인 부분 인덱스라 PostgREST의 on_conflict가
        그 조건까지 실어 보낼 수 없다 — 중재 인덱스를 못 찾아 실패한다. 왕복이
        한 번 늘지만 저장은 사용자가 버튼을 누를 때만 일어나므로 값이 싸다.

        id는 보내지 않는다. DB 기본값(gen_random_uuid)이 만든다.
        """
        if schedule.run_id is not None:
            found = self._request(
                "GET",
                "/saved_schedules",
                params={
                    "user_id": f"eq.{schedule.user_id}",
                    "run_id": f"eq.{schedule.run_id}",
                    "select": "*",
                    "limit": "1",
                },
            )
            row = self._one_or_none(self._json(found))
            if row is not None:
                return self._schedule_row(row)

        response = self._request(
            "POST",
            "/saved_schedules",
            json=schedule.model_dump(mode="json", exclude={"id"}),
            prefer="return=representation",
        )
        row = self._one_or_none(self._json(response))
        if row is None:
            raise StateStoreError("invalid saved_schedules insert response")
        return self._schedule_row(row)

    def list_schedules_for_user(self, user_id: str, limit: int) -> list[SavedSchedule]:
        response = self._request(
            "GET",
            "/saved_schedules",
            params={
                "user_id": f"eq.{user_id}",
                "select": "*",
                "order": "created_at.desc",
                "limit": str(limit),
            },
        )
        payload = self._json(response)
        if not isinstance(payload, list):
            raise StateStoreError("invalid saved_schedules response")
        return [self._schedule_row(row) for row in payload]

    def get_schedule(self, schedule_id: str) -> SavedSchedule | None:
        """**소유권을 여기서 보지 않는다.** 저장소는 행을 돌려주고, 남의 것인지는
        service가 principal과 대조한다 — 저장소가 신원을 알면 계층이 섞인다."""
        response = self._request(
            "GET",
            "/saved_schedules",
            params={"id": f"eq.{schedule_id}", "select": "*", "limit": "1"},
        )
        row = self._one_or_none(self._json(response))
        return self._schedule_row(row) if row is not None else None

    def rename_schedule(self, schedule_id: str, title: str) -> SavedSchedule | None:
        response = self._request(
            "PATCH",
            "/saved_schedules",
            params={"id": f"eq.{schedule_id}"},
            json={"title": title, "updated_at": now_kst().isoformat()},
            prefer="return=representation",
        )
        row = self._one_or_none(self._json(response))
        return self._schedule_row(row) if row is not None else None

    def delete_schedule(self, schedule_id: str) -> bool:
        """지웠으면 True. 없으면 오류가 아니라 False다(다른 삭제와 같이 멱등)."""
        response = self._request(
            "DELETE",
            "/saved_schedules",
            params={"id": f"eq.{schedule_id}"},
            prefer="return=representation",
        )
        return self._one_or_none(self._json(response)) is not None

    @staticmethod
    def _schedule_row(row: Mapping[str, object]) -> SavedSchedule:
        try:
            return SavedSchedule.model_validate(row)
        except Exception:
            raise StateStoreError("invalid saved_schedules row") from None

    # ------------------------------------------------------------ Trace

    def append_traces(self, records: list[TraceRecord]) -> None:
        """append-only. 기존 기록을 수정하거나 삭제하지 않는다."""
        if not records:
            return
        self._request(
            "POST",
            "/trace_records",
            json=[record.model_dump(mode="json") for record in records],
            prefer="return=minimal",
        )

    def get_traces(self, session_id: str) -> list[TraceRecord]:
        response = self._request(
            "GET",
            "/trace_records",
            params={
                "session_id": f"eq.{session_id}",
                "select": "*",
                "order": "id.asc",
            },
        )
        payload = self._json(response)
        if not isinstance(payload, list):
            raise StateStoreError("invalid trace_records response")
        try:
            return [TraceRecord.model_validate(row) for row in payload]
        except Exception:
            raise StateStoreError("invalid trace_records row") from None

    def list_traces_for_stats(
        self, since: datetime | None = None, until: datetime | None = None
    ) -> list[TraceRecord]:
        """세션을 가리지 않고 전량을 읽는다(TP-157). response_feedback의
        list_feedback_for_stats와 동일한 이유로 집계는 여기가 아니라
        호출부(service.py)가 Python에서 한다."""
        params: dict[str, str] = {"select": "*", "order": "recorded_at.asc"}
        if since is not None:
            params["recorded_at"] = f"gte.{since.isoformat()}"
        if until is not None:
            until_filter = f"lt.{until.isoformat()}"
            if "recorded_at" in params:
                since_filter = params.pop("recorded_at")
                params["and"] = f"(recorded_at.{since_filter},recorded_at.{until_filter})"
            else:
                params["recorded_at"] = until_filter
        rows = self._fetch_all_rows("/trace_records", params)
        try:
            return [TraceRecord.model_validate(row) for row in rows]
        except Exception:
            raise StateStoreError("invalid trace_records row") from None

    # ------------------------------------------------------------ Feedback

    def append_feedback(self, records: list[FeedbackRecord]) -> None:
        """append-only. 기존 기록을 수정하거나 삭제하지 않는다."""
        if not records:
            return
        self._request(
            "POST",
            "/response_feedback",
            json=[record.model_dump(mode="json") for record in records],
            prefer="return=minimal",
        )

    def get_feedback(self, session_id: str) -> list[FeedbackRecord]:
        response = self._request(
            "GET",
            "/response_feedback",
            params={
                "session_id": f"eq.{session_id}",
                "select": "*",
                "order": "id.asc",
            },
        )
        payload = self._json(response)
        if not isinstance(payload, list):
            raise StateStoreError("invalid response_feedback response")
        try:
            return [FeedbackRecord.model_validate(row) for row in payload]
        except Exception:
            raise StateStoreError("invalid response_feedback row") from None

    def list_dislike_feedback(self, limit: int) -> list[FeedbackRecord]:
        response = self._request(
            "GET",
            "/response_feedback",
            params={
                "rating": "eq.dislike",
                "select": "*",
                "order": "recorded_at.desc",
                "limit": str(limit),
            },
        )
        payload = self._json(response)
        if not isinstance(payload, list):
            raise StateStoreError("invalid response_feedback response")
        try:
            return [FeedbackRecord.model_validate(row) for row in payload]
        except Exception:
            raise StateStoreError("invalid response_feedback row") from None

    def list_feedback_for_stats(
        self, since: datetime | None = None, until: datetime | None = None
    ) -> list[FeedbackRecord]:
        """rating을 가리지 않고 전량을 읽는다(TP-146). 집계는 호출부(service.py)가
        Python에서 한다 — PostgREST group-by/count()는 이 프로젝트 설정에서
        기본 활성화가 보장되지 않고, 다른 조회 메서드도 전부 원본 행을 그대로
        FeedbackRecord로 검증해 반환하는 방식을 따르고 있어 그 패턴을 유지한다.
        """
        params: dict[str, str] = {"select": "*", "order": "recorded_at.asc"}
        if since is not None:
            params["recorded_at"] = f"gte.{since.isoformat()}"
        if until is not None:
            until_filter = f"lt.{until.isoformat()}"
            if "recorded_at" in params:
                # PostgREST는 같은 컬럼에 여러 조건을 걸 때 and=(...) 문법이
                # 필요하다 — 쿼리 파라미터 하나에 값 하나만 담을 수 있어서다.
                since_filter = params.pop("recorded_at")
                params["and"] = f"(recorded_at.{since_filter},recorded_at.{until_filter})"
            else:
                params["recorded_at"] = until_filter
        rows = self._fetch_all_rows("/response_feedback", params)
        try:
            return [FeedbackRecord.model_validate(row) for row in rows]
        except Exception:
            raise StateStoreError("invalid response_feedback row") from None

    # ------------------------------------------------------------ 정리(TP-134)

    def list_stale_session_ids(self, cutoff: datetime) -> list[str]:
        response = self._request(
            "GET",
            "/agent_states",
            params={
                "last_active_at": f"lt.{cutoff.isoformat()}",
                "select": "session_id",
            },
        )
        payload = self._json(response)
        if not isinstance(payload, list):
            raise StateStoreError("invalid agent_states response")
        try:
            return [str(row["session_id"]) for row in payload]
        except (KeyError, TypeError):
            raise StateStoreError("invalid agent_states row") from None

    def delete_change_logs(self, session_id: str) -> None:
        self._request(
            "DELETE",
            "/condition_change_logs",
            params={"session_id": f"eq.{session_id}"},
            prefer="return=minimal",
        )

    def delete_traces(self, session_id: str) -> None:
        self._request(
            "DELETE",
            "/trace_records",
            params={"session_id": f"eq.{session_id}"},
            prefer="return=minimal",
        )