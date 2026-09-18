"""지역 행사 목록을 조회해 진행 중인 것만 돌려주는 내부 Tool.

INFO(question_type=event) 전용이다. TourAPI는 장소별 행사 조회를 제공하지 않아
지역 단위로 받아온 뒤 기간으로 거른다 — 대상 장소와의 연결(직접/근접)은 좌표를
가진 상위 Service가 판단한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from app.errors import AppError
from app.place_search_policy import PLACE_SEARCH_LDONG_REGION_CODE
from app.providers.contracts import ProviderMetadata, ProviderStatus
from app.providers.festival import FestivalEvent
from app.providers.protocols import FestivalProvider
from app.tools.contracts import ToolError, ToolStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FestivalQuery:
    reference_date: date
    region_code: str = PLACE_SEARCH_LDONG_REGION_CODE
    # 구는 요청에 싣지 않고 응답으로 거른다(D-025). 서울 전체를 한 번에 받아
    # 지원 구만 남기므로, 구가 늘어도 호출 수는 1회 그대로다.
    district_code: str | None = None


@dataclass(frozen=True)
class FestivalToolResult:
    status: ToolStatus
    events: tuple[FestivalEvent, ...]
    error: ToolError | None = None
    provider_metadata: tuple[ProviderMetadata, ...] = ()


class GetFestivalsTool:
    """행사 목록을 조회하고 기준일에 진행 중인 것만 남긴다."""

    def __init__(self, provider: FestivalProvider) -> None:
        self._provider = provider

    async def execute(self, query: FestivalQuery) -> FestivalToolResult:
        try:
            result = await self._provider.search_festivals(
                query.region_code,
                query.district_code,
                query.reference_date,
            )
        except AppError as exc:
            # 여기서 삼킨 오류는 200 응답으로 나가므로 로그가 유일한 흔적이다.
            logger.warning(
                "행사 정보 없이 진행 (code=%s, provider=%s, details=%s)",
                exc.code,
                exc.provider,
                exc.details,
            )
            return FestivalToolResult(
                status=ToolStatus.UNAVAILABLE,
                events=(),
                error=ToolError(
                    code="unavailable",
                    message="행사 정보를 가져오지 못했습니다.",
                    cause=(
                        "timeout" if exc.code == "provider_timeout" else "upstream_error"
                    ),
                    retryable=exc.retryable,
                ),
            )

        ongoing = tuple(
            event for event in result.data if event.is_ongoing(query.reference_date)
        )
        # 등록된 행사는 있는데 오늘 진행 중인 게 없는 경우도 no_data다 — 사용자
        # 입장에서는 "지금 하는 게 없다"로 같다.
        status = (
            ToolStatus.NO_DATA
            if not ongoing or result.metadata.status is ProviderStatus.NO_DATA
            else ToolStatus.SUCCESS
        )
        return FestivalToolResult(
            status=status,
            events=ongoing,
            error=None,
            provider_metadata=(result.metadata,),
        )


__all__ = [
    "FestivalQuery",
    "FestivalToolResult",
    "GetFestivalsTool",
]
