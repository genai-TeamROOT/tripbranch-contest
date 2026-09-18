from __future__ import annotations

import pytest

from app.errors import AppError
from app.state.errors import StateStoreError
from app.state.service import (
    RecordTraceRequest,
    StateApplyRequest,
    apply,
    record_trace,
)


class _BrokenStore:
    """모든 메서드가 임의 예외를 던지는 가짜 저장소. 감싸기 동작만 검증한다."""

    def get_state(self, session_id):
        raise RuntimeError("boom")

    def save_state(self, state):
        raise RuntimeError("boom")

    def delete_state(self, session_id):
        raise RuntimeError("boom")

    def get_history(self, session_id):
        raise RuntimeError("boom")

    def save_history(self, history):
        raise RuntimeError("boom")

    def delete_history(self, session_id):
        raise RuntimeError("boom")

    def append_change_logs(self, logs):
        raise RuntimeError("boom")

    def get_change_logs(self, session_id):
        raise RuntimeError("boom")

    def append_traces(self, records):
        raise RuntimeError("boom")

    def get_traces(self, session_id):
        raise RuntimeError("boom")


class _AppErrorStore(_BrokenStore):
    """AppError를 직접 던지는 가짜 저장소. 이미 의미 있는 오류는 안 감싸는지 검증."""

    def append_traces(self, records):
        raise AppError(code="custom_upstream_error", message="already meaningful")


def test_unexpected_exception_becomes_state_store_error() -> None:
    request = StateApplyRequest(intent="RECOMMEND", confirmed=True)
    with pytest.raises(StateStoreError) as exc_info:
        apply(request, store=_BrokenStore())
    assert exc_info.value.code == "state_store_error"
    assert exc_info.value.retryable is True


def test_existing_app_error_passes_through_unwrapped() -> None:
    request = RecordTraceRequest(session_id="sess-1", run_id="run-1", step="llm_interpret")
    with pytest.raises(AppError) as exc_info:
        record_trace(request, store=_AppErrorStore())
    assert exc_info.value.code == "custom_upstream_error"


def test_session_not_found_is_not_an_error() -> None:
    """계약 5.2/6.7절: 세션 없음은 오류가 아니라 정상 흐름이어야 한다."""
    from app.state.service import get_session_context
    from app.state.store import InMemoryStateStore

    response = get_session_context("nonexistent-session", store=InMemoryStateStore())
    assert response.session_exists is False