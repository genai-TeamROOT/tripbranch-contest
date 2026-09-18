"""Provider가 공유하는 정상 결과 메타데이터와 wrapper 계약."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Generic, TypeVar


class ProviderStatus(StrEnum):
    """외부 호출과 정규화가 완료된 Provider 정상 결과 상태."""

    SUCCESS = "success"
    NO_DATA = "no_data"
    PARTIAL = "partial"


class ProviderSource(StrEnum):
    """Provider 구현 클래스가 아닌 데이터 출처·기능 식별자."""

    NAVER_GEOCODING = "naver_geocoding"
    NAVER_LOCAL_SEARCH = "naver_local_search"
    DEVICE_GPS = "device_gps"
    KMA_ULTRA_SHORT_FORECAST = "kma_ultra_short_forecast"
    TOUR_API_PLACE = "tour_api_place"
    TOUR_API_FESTIVAL = "tour_api_festival"
    SUPABASE_PLACES = "supabase_places"
    SUPABASE_PLACE_EVIDENCE = "supabase_place_evidence"
    SUPABASE_PLACE_MOOD = "supabase_place_mood"
    SUPABASE_BARRIER_FREE_PLACES = "supabase_barrier_free_places"
    TOUR_API_CONCENTRATION = "tour_api_concentration"
    SEOUL_CITYDATA_COMMERCIAL = "seoul_citydata_commercial"
    SEOUL_CITYDATA_POPULATION = "seoul_citydata_population"
    SEOUL_MUNICIPAL_PARKING = "seoul_municipal_parking"
    SEOUL_PUBLIC_TOILET = "seoul_public_toilet"
    KASI_HOLIDAY = "kasi_holiday"
    KAKAO_WALKING_ROUTE = "kakao_walking_route"
    NAVER_DRIVING_ROUTE = "naver_driving_route"
    KAKAO_TRANSIT_ROUTE = "kakao_transit_route"
    GEMINI = "gemini"
    FAKE_GEOCODING = "fake_geocoding"
    FAKE_LOCAL_SEARCH = "fake_local_search"
    FAKE_WEATHER = "fake_weather"
    FAKE_PLACE = "fake_place"
    FAKE_FESTIVAL = "fake_festival"
    FAKE_PLACES = "fake_places"
    FAKE_CONCENTRATION = "fake_concentration"
    FAKE_SEOUL_CITYDATA = "fake_seoul_citydata"
    FAKE_MUNICIPAL_PARKING = "fake_municipal_parking"
    FAKE_PUBLIC_TOILET = "fake_public_toilet"
    FAKE_HOLIDAY = "fake_holiday"
    FAKE_WALKING_ROUTE = "fake_walking_route"
    FAKE_DRIVING_ROUTE = "fake_driving_route"
    FAKE_TRANSIT_ROUTE = "fake_transit_route"
    FAKE_BARRIER_FREE_PLACES = "fake_barrier_free_places"
    FAKE_LLM = "fake_llm"


@dataclass(frozen=True)
class ProviderMetadata:
    source: ProviderSource
    status: ProviderStatus
    retrieved_at: datetime
    # 미리 구축된 저장소에서 읽어온 경우, 그 데이터가 원본 API로부터 마지막으로
    # 동기화된 시각. retrieved_at(이번 요청의 조회 시각)과 구분하기 위한 값이라
    # 외부 API를 직접 호출하는 provider에서는 항상 None이다.
    detail_fetched_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.retrieved_at.tzinfo is None:
            raise ValueError("retrieved_at은 timezone-aware datetime이어야 합니다.")
        if self.detail_fetched_at is not None and self.detail_fetched_at.tzinfo is None:
            raise ValueError("detail_fetched_at은 timezone-aware datetime이어야 합니다.")


T = TypeVar("T")


@dataclass(frozen=True)
class ProviderResult(Generic[T]):
    data: T
    metadata: ProviderMetadata


Clock = Callable[[], datetime]


def provider_result(
    data: T,
    *,
    source: ProviderSource,
    status: ProviderStatus = ProviderStatus.SUCCESS,
    clock: Clock | None = None,
    detail_fetched_at: datetime | None = None,
) -> ProviderResult[T]:
    """UTC 조회 시각을 포함한 정상 Provider 결과를 생성한다."""

    retrieved_at = (clock or (lambda: datetime.now(UTC)))()
    if retrieved_at.tzinfo is None:
        raise ValueError("Provider clock은 timezone-aware datetime을 반환해야 합니다.")
    return ProviderResult(
        data=data,
        metadata=ProviderMetadata(
            source=source,
            status=status,
            retrieved_at=retrieved_at.astimezone(UTC),
            detail_fetched_at=(
                detail_fetched_at.astimezone(UTC)
                if detail_fetched_at is not None and detail_fetched_at.tzinfo is not None
                else detail_fetched_at
            ),
        ),
    )
