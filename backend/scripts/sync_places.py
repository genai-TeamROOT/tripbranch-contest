"""TourAPI 지역 장소를 Supabase에 동기화하는 명령행 진입점."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

import httpx

from app.config import Settings
from app.domain.models import TourPlacePage
from app.providers.factory import get_closure_extractor
from app.providers.real_place import RealPlaceProvider
from app.repositories.supabase_places import SupabasePlaceRepository
from app.services.place_snapshot import records_from_snapshot
from app.services.place_sync import PlaceSyncService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="지역별 TourAPI 장소 동기화")
    parser.add_argument("--area-code", help="TourAPI 광역 행정구역 코드")
    parser.add_argument("--district-code", help="TourAPI 시·군·구 코드")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="외부 API와 DB 조회만 수행하고 DB를 수정하지 않음",
    )
    parser.add_argument(
        "--details-limit",
        type=int,
        help="상세조회 대상을 지정한 건수로 제한하며 비활성화도 수행하지 않음",
    )
    parser.add_argument(
        "--force-details",
        action="store_true",
        help="수정 시각과 TTL에 관계없이 상세정보를 다시 조회",
    )
    parser.add_argument(
        "--from-snapshot",
        type=Path,
        help=(
            "장소 목록을 TourAPI 대신 스냅샷 CSV에서 읽는다. snapshot_places.py가 저장한 "
            "파일을 넘기면 같은 날 목록 API를 두 번 호출하지 않는다(상세조회는 그대로 API 사용)."
        ),
    )
    return parser


class SnapshotAreaPlaceProvider:
    """목록만 스냅샷에서 읽고 상세조회는 실제 Provider에 위임한다.

    대조에 쓴 목록과 DB에 반영하는 목록이 같은 데이터임을 보장한다 — 두 번 조회하면
    그 사이 원본이 바뀌어 대조 결과와 실제 반영분이 어긋날 수 있다.
    """

    def __init__(self, snapshot_path: Path, inner: RealPlaceProvider) -> None:
        self._records = records_from_snapshot(snapshot_path)
        self._inner = inner

    async def list_places_by_area(
        self,
        area_code: str,
        district_code: str,
        page_no: int,
        num_of_rows: int = 100,
    ) -> TourPlacePage:
        start = (page_no - 1) * num_of_rows
        return TourPlacePage(
            page_no=page_no,
            num_of_rows=num_of_rows,
            total_count=len(self._records),
            places=tuple(self._records[start : start + num_of_rows]),
        )

    async def get_operating_details(self, content_id: str, content_type_id: str):
        return await self._inner.get_operating_details(content_id, content_type_id)


async def run(args: argparse.Namespace, settings: Settings) -> int:
    area_code = args.area_code or settings.place_sync_area_code
    district_code = args.district_code or settings.place_sync_district_code
    if not settings.tour_api_service_key:
        raise ValueError("TOUR_API_SERVICE_KEY가 필요합니다.")
    if not settings.supabase_url:
        raise ValueError("SUPABASE_URL이 필요합니다.")
    if not settings.supabase_secret_key:
        raise ValueError("SUPABASE_SECRET_KEY가 필요합니다.")

    async with httpx.AsyncClient() as client:
        provider = RealPlaceProvider(
            api_key=settings.tour_api_service_key,
            client=client,
            timeout_seconds=settings.external_api_timeout_seconds,
        )
        repository = SupabasePlaceRepository(
            supabase_url=settings.supabase_url,
            secret_key=settings.supabase_secret_key,
            client=client,
            timeout_seconds=settings.external_api_timeout_seconds,
        )
        list_provider = (
            SnapshotAreaPlaceProvider(args.from_snapshot, provider)
            if args.from_snapshot is not None
            else provider
        )
        service = PlaceSyncService(
            list_provider,
            repository,
            page_size=settings.place_sync_page_size,
            detail_concurrency=settings.place_sync_detail_concurrency,
            detail_min_interval_seconds=(
                settings.place_sync_detail_min_interval_seconds
            ),
            detail_ttl_days=settings.place_sync_detail_ttl_days,
            retry_count=settings.external_api_retry_count,
            # 정규식이 못 읽은 휴무 문구를 LLM으로 읽는다(TP-231). 꺼져 있거나
            # fake LLM이면 None이고, 그때는 정규식 결과만 저장한다.
            closure_extractor=get_closure_extractor(),
        )
        result = await service.sync(
            area_code,
            district_code,
            dry_run=args.dry_run,
            details_limit=args.details_limit,
            force_details=args.force_details,
        )

    print(json.dumps(asdict(result), ensure_ascii=False, default=str, indent=2))
    return 0 if result.status == "success" else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run(args, Settings()))


if __name__ == "__main__":
    raise SystemExit(main())
