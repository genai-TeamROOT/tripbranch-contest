"""서울시 공영주차장 주소를 한 번 지오코딩해 좌표 카탈로그로 적재한다.

실시간 주차 대수는 이 스크립트가 저장하지 않는다. ``GetParkingInfo`` 조회 시점에
받아야 최신성이 보장되므로, 여기서는 코드·주소·좌표·기본 속성만 upsert한다.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

import httpx

from app.config import Settings
from app.domain.models import StoredMunicipalParkingLot
from app.providers.geocoding import RealGeocodingProvider
from app.providers.municipal_parking import RealMunicipalParkingProvider
from app.repositories.municipal_parking import SupabaseMunicipalParkingRepository

_SEOUL_DISTRICTS = (
    "종로구", "중구", "용산구", "성동구", "광진구", "동대문구", "중랑구", "성북구",
    "강북구", "도봉구", "노원구", "은평구", "서대문구", "마포구", "양천구", "강서구",
    "구로구", "금천구", "영등포구", "동작구", "관악구", "서초구", "강남구", "송파구", "강동구",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="서울시 공영주차장 좌표 카탈로그 동기화")
    parser.add_argument("--district", action="append", choices=_SEOUL_DISTRICTS)
    parser.add_argument(
        "--dry-run", action="store_true", help="API·지오코딩만 호출하고 DB는 쓰지 않음"
    )
    parser.add_argument("--limit", type=int, help="테스트용 주차장 수 상한")
    return parser


async def run(args: argparse.Namespace, settings: Settings) -> int:
    required = {
        "SEOUL_OPEN_DATA_API_KEY": settings.seoul_open_data_api_key,
        "NAVER_MAP_CLIENT_ID": settings.naver_map_client_id,
        "NAVER_MAP_CLIENT_SECRET": settings.naver_map_client_secret,
    }
    if not args.dry_run:
        required |= {
            "SUPABASE_URL": settings.supabase_url,
            "SUPABASE_SECRET_KEY": settings.supabase_secret_key,
        }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ValueError("필요한 환경변수가 비어 있습니다: " + ", ".join(missing))

    districts = tuple(args.district or _SEOUL_DISTRICTS)
    async with httpx.AsyncClient() as client:
        status_provider = RealMunicipalParkingProvider(
            settings.seoul_open_data_api_key, client, settings.external_api_timeout_seconds
        )
        geocoding = RealGeocodingProvider(
            settings.naver_map_client_id,
            settings.naver_map_client_secret,
            client,
            settings.external_api_timeout_seconds,
        )
        repository = (
            SupabaseMunicipalParkingRepository(
                settings.supabase_url,
                settings.supabase_secret_key,
                client,
                settings.external_api_timeout_seconds,
            )
            if not args.dry_run
            else None
        )
        stored: list[StoredMunicipalParkingLot] = []
        failed_geocodes = 0
        for district in districts:
            lots = (await status_provider.get_district_parking(district)).data
            for lot in lots:
                if args.limit is not None and len(stored) >= args.limit:
                    break
                if not lot.address:
                    failed_geocodes += 1
                    continue
                try:
                    geocoded = await geocoding.geocode(lot.address, use_alias=False)
                except Exception:
                    failed_geocodes += 1
                    continue
                stored.append(
                    StoredMunicipalParkingLot(
                        code=lot.code,
                        name=lot.name,
                        address=lot.address,
                        district=lot.district or district,
                        latitude=geocoded.data.latitude,
                        longitude=geocoded.data.longitude,
                        capacity=lot.capacity,
                        paid=lot.paid,
                    )
                )
            if args.limit is not None and len(stored) >= args.limit:
                break
        if repository is not None:
            await repository.upsert_lots(stored)

    print(
        f"공영주차장 카탈로그 {'검증' if args.dry_run else '적재'} 완료: "
        f"{len(stored)}건, 지오코딩 실패/주소 없음 {failed_geocodes}건"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run(args, Settings()))


if __name__ == "__main__":
    raise SystemExit(main())
