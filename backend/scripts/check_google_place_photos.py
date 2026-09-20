"""이미지가 없는 장소에 Google 사진이 실제로 붙는지 확인하는 명령행 도구.

DB에서 대표 이미지·썸네일이 둘 다 비어 있는 장소를 뽑아, Google Places에서
대표 사진을 찾아보고 결과를 표로 낸다. **DB를 바꾸지 않는다** — 붙일 수 있는지
보는 용도다.

켜기 전에 얼마나 건질 수 있는지, 그리고 엉뚱한 장소의 사진이 오지는 않는지
사람이 눈으로 확인하는 자리다. 이름만으로 검색하면 같은 상호의 다른 지점이
잡히는 일이 있어 주소·좌표를 함께 넘기는데, 그게 충분한지는 실제 결과를 봐야
안다.

사용 예:
    python -m scripts.check_google_place_photos --limit 20
    python -m scripts.check_google_place_photos --district-code 110 --limit 10

필요한 환경변수: SUPABASE_URL, SUPABASE_SECRET_KEY, GOOGLE_PLACES_API_KEY.
GOOGLE_PLACE_PHOTO_ENABLED와 무관하게 동작한다 — 켜기 전에 보는 도구다.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import httpx

from app.config import settings
from app.observability.api_usage import create_external_client
from app.providers.google_place_photos import GooglePlacePhotoProvider

_SELECT = "content_id,title,address,latitude,longitude,content_type_id,district_code"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="관광공사 이미지가 없는 장소에 Google 사진을 붙일 수 있는지 확인"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="확인할 장소 수(기본 20). 장소당 Google 호출이 최대 2회 나간다.",
    )
    parser.add_argument(
        "--district-code",
        help="특정 구만 확인한다(예: 110 종로구, 140 중구).",
    )
    return parser


async def _fetch_places_without_image(
    client: httpx.AsyncClient, *, limit: int, district_code: str | None
) -> list[dict[str, object]]:
    params = {
        "select": _SELECT,
        "first_image_url": "is.null",
        "thumbnail_url": "is.null",
        "is_active": "eq.true",
        # 여행코스(25)와 숙박(32)은 추천 후보에서 빠지므로 확인 대상도 아니다.
        "content_type_id": "not.in.(25,32)",
        "limit": str(limit),
    }
    if district_code:
        params["district_code"] = f"eq.{district_code}"
    response = await client.get(
        f"{settings.supabase_url}/rest/v1/places",
        params=params,
        headers={
            "apikey": settings.supabase_secret_key,
            "Authorization": f"Bearer {settings.supabase_secret_key}",
        },
        timeout=settings.external_api_timeout_seconds,
    )
    response.raise_for_status()
    return list(response.json())


async def run(*, limit: int, district_code: str | None) -> int:
    missing = [
        name
        for name, value in (
            ("SUPABASE_URL", settings.supabase_url),
            ("SUPABASE_SECRET_KEY", settings.supabase_secret_key),
            ("GOOGLE_PLACES_API_KEY", settings.google_places_api_key),
        )
        if not value.strip()
    ]
    if missing:
        print(f"환경변수가 비어 있습니다: {', '.join(missing)}", file=sys.stderr)
        return 1

    async with create_external_client() as client:
        places = await _fetch_places_without_image(
            client, limit=limit, district_code=district_code
        )
        if not places:
            print("이미지가 없는 장소를 찾지 못했습니다.")
            return 0

        provider = GooglePlacePhotoProvider(
            api_key=settings.google_places_api_key,
            client=client,
            timeout_seconds=settings.external_api_timeout_seconds,
            max_width_px=settings.google_place_photo_max_width_px,
            # 확인 도구라 캐시가 결과를 가리지 않게 끈다.
            cache_ttl_seconds=0,
        )

        found = 0
        for place in places:
            title = str(place.get("title") or "")
            photo_url = await provider.find_cover_photo(
                name=title,
                address=(str(place["address"]) if place.get("address") else None),
                latitude=(
                    float(place["latitude"]) if place.get("latitude") is not None else None
                ),
                longitude=(
                    float(place["longitude"]) if place.get("longitude") is not None else None
                ),
            )
            if photo_url:
                found += 1
            status = photo_url or "(없음)"
            print(f"[{place.get('content_id')}] {title}\n    {place.get('address')}\n    {status}")

        print(f"\n확인 {len(places)}곳 중 사진을 찾은 곳 {found}곳")
    return 0


def main() -> int:
    args = build_parser().parse_args()
    return asyncio.run(run(limit=args.limit, district_code=args.district_code))


if __name__ == "__main__":
    raise SystemExit(main())
