"""개발자 Ops 패널이 띄우는 장소 동기화 job 레지스트리.

역할: 오래 걸리는 동기화를 백그라운드 태스크로 돌리고 진행 상황을 메모리에 담는다.
입력: 실행 파라미터와 코루틴 팩토리.
출력: job 상태 스냅샷(진행률, 결과, 오류).
호출 시점: /api/dev/place-sync 계열 엔드포인트.

프로세스 메모리다 — 서버를 재시작하면 진행 중이던 job의 추적을 잃는다. 다만 DB
쪽 잠금(`place_sync_locks`)과 `place_sync_runs` 행은 남으므로 중복 실행은 그쪽이
막는다. 여기 레지스트리는 같은 프로세스 안에서의 중복만 막는다.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.services.place_sync import PlaceSyncResult, SyncProgress

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")

# 끝난 job을 몇 개까지 남길지. 화면이 직전 결과를 다시 읽을 수 있으면 충분하다.
_RETAINED_JOBS = 10


class SyncJobConflictError(RuntimeError):
    """이미 실행 중인 job이 있다."""


def _failure_detail(exc: BaseException) -> str:
    """실패 사유. AppError는 사용자 문구 뒤에 상류 원문을 붙인다.

    `SupabaseRepositoryError`의 메시지는 "장소 데이터 저장 중 문제가 발생했어요"라는
    안내문이고, 실제 원인(`HTTP 400: 23514 - ...` 같은 제약 위반)은 details에만
    있다. 그것을 안 꺼내면 로그가 무엇이 잘못됐는지 말하지 못해, 원인을 찾으려면 DB를
    직접 뒤져야 한다(2026-08-30 중구 반영 실패).

    키 값이 섞일 수 있는 자료는 넣지 않는다 — `upstream_detail`은 상류 응답의 code와
    message만 담는다.
    """
    detail = f"{type(exc).__name__}: {exc}"
    upstream = getattr(exc, "details", None)
    if isinstance(upstream, Mapping):
        text = str(upstream.get("upstream_detail") or "").strip()
        if text:
            return f"{detail} ({text})"
    return detail


@dataclass(frozen=True)
class SyncJobOutcome:
    """job 하나가 끝나고 남긴 것.

    동기화 결과 자체와, 결과를 보고 별도로 확인한 후속 조치 거리를 함께 담는다.
    """

    result: PlaceSyncResult
    unmapped_new_place_ids: list[str]


@dataclass
class SyncJob:
    id: str
    params: dict[str, Any]
    status: str = "running"
    started_at: datetime = field(default_factory=lambda: datetime.now(_KST))
    finished_at: datetime | None = None
    phase: str = "list"
    processed: int = 0
    total: int = 0
    result: PlaceSyncResult | None = None
    error: str | None = None
    # 동기화로 새로 들어왔는데 집중률 매핑이 없는 장소. 매핑이 없으면 혼잡도
    # 조회 자체를 건너뛰므로(enrichment_service) 알리지 않으면 조용히 빠진다.
    unmapped_new_place_ids: list[str] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "params": self.params,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "phase": self.phase,
            "processed": self.processed,
            "total": self.total,
            "result": asdict(self.result) if self.result is not None else None,
            "error": self.error,
            "unmapped_new_place_ids": self.unmapped_new_place_ids,
        }


class SyncJobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, SyncJob] = {}
        self._task: asyncio.Task[None] | None = None
        self._running_id: str | None = None

    def running(self) -> SyncJob | None:
        if self._running_id is None:
            return None
        return self._jobs.get(self._running_id)

    def get(self, job_id: str) -> SyncJob | None:
        return self._jobs.get(job_id)

    def start(
        self,
        params: dict[str, Any],
        run: Callable[[Callable[[SyncProgress], None]], Awaitable[SyncJobOutcome]],
    ) -> SyncJob:
        current = self.running()
        if current is not None and current.status == "running":
            raise SyncJobConflictError(
                f"이미 실행 중인 동기화가 있습니다 (job_id={current.id})."
            )

        job = SyncJob(id=str(uuid4()), params=params)
        self._jobs[job.id] = job
        self._running_id = job.id
        self._prune()

        def on_progress(progress: SyncProgress) -> None:
            job.phase = progress.phase
            job.processed = progress.processed
            job.total = progress.total

        async def runner() -> None:
            try:
                outcome = await run(on_progress)
                job.result = outcome.result
                job.unmapped_new_place_ids = outcome.unmapped_new_place_ids
                job.status = outcome.result.status
            except Exception as exc:
                # 여기서 삼키면 태스크가 조용히 죽어 화면이 영원히 running으로 남는다.
                job.status = "failed"
                job.error = _failure_detail(exc)
                logger.exception(
                    "장소 동기화 job 실패 (job_id=%s): %s", job.id, job.error
                )
            finally:
                job.finished_at = datetime.now(_KST)
                if self._running_id == job.id:
                    self._running_id = None

        self._task = asyncio.create_task(runner())
        return job

    def _prune(self) -> None:
        if len(self._jobs) <= _RETAINED_JOBS:
            return
        finished = sorted(
            (job for job in self._jobs.values() if job.finished_at is not None),
            key=lambda job: job.started_at,
        )
        for job in finished[: len(self._jobs) - _RETAINED_JOBS]:
            del self._jobs[job.id]

    def reset(self) -> None:
        """테스트 격리용. 실행 중 태스크는 취소한다."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._jobs.clear()
        self._task = None
        self._running_id = None


_registry = SyncJobRegistry()


def get_job_registry() -> SyncJobRegistry:
    return _registry


__all__ = [
    "SyncJob",
    "SyncJobConflictError",
    "SyncJobOutcome",
    "SyncJobRegistry",
    "get_job_registry",
]
