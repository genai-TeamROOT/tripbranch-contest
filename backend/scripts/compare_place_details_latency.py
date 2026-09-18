"""장소 상세·운영정보 조회 소요시간 비교 — TourAPI 상세 API vs Supabase DB.

역할: 동일한 장소 후보 목록에 대해 상세조회 경로만 바꿔가며 소요시간을 잰다.
- tour_api: 후보마다 상세 API를 호출하는 기존 경로(공통 상세 + 소개 상세 2회씩,
  max_concurrency 제한 병렬).
- supabase: content_id 목록으로 places 테이블을 한 번에 조회하는 경로.
장소 "검색"은 두 경로가 동일하므로 측정에서 제외한다 — TourAPI로 후보를 한 번만
검색해두고, 그 결과를 그대로 돌려주는 스텁 검색 Provider를 양쪽에 주입해서
NearbyPlaceDetailsTool의 상세조회 구간만 비교한다.
**기존 프로덕션 코드(app/ 이하)는 수정하지 않는다** — 실제 Tool과 Provider를 그대로
호출하고, 스텁은 이 스크립트 안에만 둔다.
입력: 없음(하드코딩된 좌표·조건). .env에 PROVIDER_MODE=real과 SUPABASE_* 필요.
출력: 표준 출력 + backend/test_results/place_details_latency.csv
호출 시점: `python -m scripts.compare_place_details_latency`로 수동 실행한다
(1회성 측정 도구, pytest 스위트에는 포함하지 않는다 — 실제 API 호출 비용 때문).
"""

from __future__ import annotations

import asyncio
import csv
import statistics
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import settings
from app.domain.models import PlaceCategoryFilter
from app.providers.contracts import ProviderResult, ProviderSource, provider_result
from app.providers.real_place import RealPlaceProvider
from app.providers.supabase_place_details import SupabasePlaceDetailsProvider
from app.repositories.supabase_places import SupabasePlaceRepository
from app.schemas import PlaceCandidate
from app.tools.nearby_place_details import (
    DetailStatus,
    NearbyPlaceDetailsQuery,
    NearbyPlaceDetailsTool,
)

# 경복궁 주변 — sync_places.py 기본 동기화 구역(area 11 / district 110)과 같은 종로구.
LATITUDE = 37.5796
LONGITUDE = 126.9770
SEARCH_RADIUS_KM = 2.0
CANDIDATE_LIMIT = 10
ROUNDS = 3

RESULTS_DIR = Path(__file__).resolve().parent.parent / "test_results"
RESULTS_CSV = RESULTS_DIR / "place_details_latency.csv"


class FixedSearchProvider:
    """이미 검색해둔 후보를 그대로 돌려주는 스텁 — 검색 비용을 측정에서 뺀다."""

    def __init__(self, candidates: list[PlaceCandidate]) -> None:
        self._candidates = candidates

    async def search_places(
        self,
        latitude: float,
        longitude: float,
        preferred_categories: list[str],
        search_radius_km: float,
        region_code: str | None = None,
        district_code: str | None = None,
        category_filter: PlaceCategoryFilter | None = None,
        limit: int = CANDIDATE_LIMIT,
    ) -> ProviderResult[list[PlaceCandidate]]:
        return provider_result(
            self._candidates[:limit],
            source=ProviderSource.TOUR_API_PLACE,
        )


@dataclass
class RoundResult:
    source: str
    round_number: int
    elapsed_ms: float
    success_count: int
    total_count: int


async def _measure(
    label: str,
    tool: NearbyPlaceDetailsTool,
    query: NearbyPlaceDetailsQuery,
) -> list[RoundResult]:
    results: list[RoundResult] = []
    for round_number in range(1, ROUNDS + 1):
        result = await tool.execute(query)
        success = sum(
            1
            for place in result.places
            if place.detail_status is DetailStatus.SUCCESS
        )
        results.append(
            RoundResult(
                source=label,
                round_number=round_number,
                elapsed_ms=result.elapsed_ms,
                success_count=success,
                total_count=len(result.places),
            )
        )
        print(
            f"  [{label:9}] {round_number}회차 {result.elapsed_ms:8.1f}ms "
            f"(운영정보 확보 {success}/{len(result.places)}, status={result.status})"
        )
    return results


def _summarize(results: list[RoundResult]) -> dict[str, float]:
    values = [item.elapsed_ms for item in results]
    return {
        "평균": statistics.mean(values),
        "중앙값": statistics.median(values),
        "최소": min(values),
        "최대": max(values),
    }


async def main() -> None:
    if settings.resolved_place_provider != "real":
        print("PLACE_PROVIDER(또는 PROVIDER_MODE)가 real이어야 합니다.")
        return
    if not settings.supabase_url or not settings.supabase_secret_key:
        print("SUPABASE_URL / SUPABASE_SECRET_KEY가 필요합니다.")
        return

    async with httpx.AsyncClient() as client:
        tour_provider = RealPlaceProvider(
            api_key=settings.tour_api_service_key,
            client=client,
            timeout_seconds=settings.external_api_timeout_seconds,
        )
        supabase_provider = SupabasePlaceDetailsProvider(
            SupabasePlaceRepository(
                supabase_url=settings.supabase_url,
                secret_key=settings.supabase_secret_key,
                client=client,
                timeout_seconds=settings.external_api_timeout_seconds,
            )
        )

        # 1) 후보 검색은 한 번만 — 두 경로가 완전히 같은 후보를 대상으로 재도록 고정한다.
        search_result = await tour_provider.search_places(
            latitude=LATITUDE,
            longitude=LONGITUDE,
            preferred_categories=[],
            search_radius_km=SEARCH_RADIUS_KM,
            limit=CANDIDATE_LIMIT,
        )
        candidates = list(search_result.data)
        print(f"후보 {len(candidates)}건으로 비교합니다 (각 {ROUNDS}회 반복)\n")
        if not candidates:
            print("후보가 없어 측정을 중단합니다.")
            return

        query = NearbyPlaceDetailsQuery(
            latitude=LATITUDE,
            longitude=LONGITUDE,
            search_radius_km=SEARCH_RADIUS_KM,
            limit=CANDIDATE_LIMIT,
        )
        search_stub = FixedSearchProvider(candidates)

        results: list[RoundResult] = []
        results.extend(
            await _measure(
                "tour_api",
                NearbyPlaceDetailsTool(search_stub, tour_provider),
                query,
            )
        )
        results.extend(
            await _measure(
                "supabase",
                NearbyPlaceDetailsTool(search_stub, supabase_provider),
                query,
            )
        )

    print("\n=== 요약 ===")
    summaries: dict[str, dict[str, float]] = {}
    for label in ("tour_api", "supabase"):
        rows = [item for item in results if item.source == label]
        summaries[label] = _summarize(rows)
        stats = summaries[label]
        print(
            f"  {label:9} 평균 {stats['평균']:8.1f}ms "
            f"중앙값 {stats['중앙값']:8.1f}ms "
            f"(최소 {stats['최소']:.1f} / 최대 {stats['최대']:.1f})"
        )
    tour_mean = summaries["tour_api"]["평균"]
    supabase_mean = summaries["supabase"]["평균"]
    if supabase_mean > 0:
        print(f"\n  Supabase가 평균 {tour_mean / supabase_mean:.1f}배 빠름")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with RESULTS_CSV.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.writer(fp)
        writer.writerow(["출처", "회차", "소요시간_ms", "운영정보_확보", "후보수"])
        for item in results:
            writer.writerow(
                [
                    item.source,
                    item.round_number,
                    f"{item.elapsed_ms:.1f}",
                    item.success_count,
                    item.total_count,
                ]
            )
        writer.writerow([])
        writer.writerow(["출처", "평균_ms", "중앙값_ms", "최소_ms", "최대_ms"])
        for label, stats in summaries.items():
            writer.writerow(
                [
                    label,
                    f"{stats['평균']:.1f}",
                    f"{stats['중앙값']:.1f}",
                    f"{stats['최소']:.1f}",
                    f"{stats['최대']:.1f}",
                ]
            )
    print(f"\n결과 저장: {RESULTS_CSV}")


if __name__ == "__main__":
    asyncio.run(main())
