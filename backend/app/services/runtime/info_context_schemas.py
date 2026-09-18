"""A Runtime의 INFO 혼잡도 계약 재노출 모듈.

concentration-conditions.md §2.4/§3.3, a-c-context-contract-draft.md §3이
스케치해둔 discriminated union(RecommendContextRequest | InfoContextRequest |
CompareContextRequest)의 첫 실제 구현이다.

실제 모델의 단일 원본은 ``app.agent_context.info_schemas``다. A가 기존 import
경로를 계속 사용할 수 있게 재노출만 한다. INFO의 Tool 호출과 근접치 fallback
오케스트레이션은 C 내부 책임이며, A는 구조화된 결과만 받는다.
"""

from app.agent_context.info_schemas import (
    ConcentrationInfoResult,
    DistrictPopulationInfoResult,
    EventInfoResult,
    EventItem,
    InfoContextRequest,
    InfoContextResponse,
    PlaceCard,
    PlaceInfoResult,
    RealtimeCityInfoResult,
    RealtimeCommercialInfoResult,
    RealtimePopulationInfoResult,
)

__all__ = [
    "ConcentrationInfoResult",
    "EventInfoResult",
    "EventItem",
    "InfoContextRequest",
    "InfoContextResponse",
    "PlaceCard",
    "PlaceInfoResult",
    "RealtimeCommercialInfoResult",
    "RealtimeCityInfoResult",
    "DistrictPopulationInfoResult",
    "RealtimePopulationInfoResult",
]
