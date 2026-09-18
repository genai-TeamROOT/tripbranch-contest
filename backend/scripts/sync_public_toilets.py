"""서울시 공중화장실 위치정보를 Supabase에 적재한다.

주차장 동기화(sync_municipal_parking_lots.py)와 두 가지가 다르다.

1. **지오코딩이 없다.** 원본이 WGS84 좌표를 이미 주므로(실측 4,447건 전부) 주소를
   좌표로 바꿀 필요가 없다. 네이버 지도 키도 필요 없다.
2. **구 단위로 나눠 받을 수 없다.** API에 지역 필터 파라미터가 없어(경로에 구
   이름을 덧붙여도 무시한다) 항상 전량을 받는다. 그래서 `--district`가 없고,
   받은 뒤 필요하면 `--district`로 걸러 적재만 제한한다.

적재주기가 "비정기(자료 변경 시)"라 하루 한 번이면 충분하다.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

import httpx

from app.config import Settings
from app.domain.models import PublicToilet
from app.providers.public_toilet import RealPublicToiletProvider
from app.repositories.public_toilet import SupabasePublicToiletRepository

# PostgREST 한 요청에 담는 행 수. 4,447건을 한 번에 보내면 요청 본문이 3MB를 넘어
# 게이트웨이에서 잘릴 수 있어 나눠 보낸다(주차장은 구 단위라 최대 122건이었다).
_UPSERT_CHUNK_SIZE = 500


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="서울시 공중화장실 위치 적재")
    parser.add_argument(
        "--district",
        action="append",
        help="적재할 자치구(반복 지정 가능). 미지정 시 전체. API 필터가 아니라 수신 후 필터다.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="API만 호출하고 DB는 쓰지 않음"
    )
    parser.add_argument("--limit", type=int, help="테스트용 적재 건수 상한")
    return parser


async def run(args: argparse.Namespace, settings: Settings) -> int:
    required = {"SEOUL_OPEN_DATA_API_KEY": settings.seoul_open_data_api_key}
    if not args.dry_run:
        required |= {
            "SUPABASE_URL": settings.supabase_url,
            "SUPABASE_SECRET_KEY": settings.supabase_secret_key,
        }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ValueError("필요한 환경변수가 비어 있습니다: " + ", ".join(missing))

    districts = set(args.district or ())
    async with httpx.AsyncClient() as client:
        provider = RealPublicToiletProvider(
            settings.seoul_open_data_api_key, client, settings.external_api_timeout_seconds
        )
        repository = (
            SupabasePublicToiletRepository(
                settings.supabase_url,
                settings.supabase_secret_key,
                client,
                settings.external_api_timeout_seconds,
            )
            if not args.dry_run
            else None
        )

        fetched = (await provider.list_all_toilets()).data
        selected = _select(fetched, districts=districts, limit=args.limit)
        if repository is not None:
            for start in range(0, len(selected), _UPSERT_CHUNK_SIZE):
                await repository.upsert_toilets(selected[start : start + _UPSERT_CHUNK_SIZE])

    skipped = len(fetched) - len(selected)
    print(
        f"공중화장실 {'검증' if args.dry_run else '적재'} 완료: "
        f"{len(selected)}건, 수신 {len(fetched)}건 중 제외 {skipped}건"
    )
    return 0


def _select(
    toilets: tuple[PublicToilet, ...], *, districts: set[str], limit: int | None
) -> list[PublicToilet]:
    chosen = [
        toilet for toilet in toilets if not districts or (toilet.district or "") in districts
    ]
    return chosen[:limit] if limit is not None else chosen


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run(args, Settings()))


if __name__ == "__main__":
    raise SystemExit(main())
