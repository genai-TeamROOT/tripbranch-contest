"""places 테이블에서 읽어 스냅샷 CSV를 만든다(외부 API를 부르지 않는다).

역할: 이미 DB에 적재된 구의 대조 기준(baseline) 스냅샷을 만든다.
입력: --district-code(필수), --area-code, --date, --output-dir.
출력: supabase/data/places_api_snapshot_<지역>-<구>_<날짜>.csv
호출 시점: `python -m scripts.snapshot_places_from_db --district-code 170`

왜 필요한가: 대조는 직전 스냅샷과 비교해 상세조회 대상을 정한다. 스냅샷이 없는
구는 첫 대조에서 전량이 신규로 잡혀, 이미 DB에 다 있는 장소에 detailIntro2를 한
번씩 더 쓴다. 용산구 486건이면 하루 한도 1,000회의 절반이다. DB에는 스냅샷 15개
열이 모두 있으므로 외부 호출 없이 기준을 세울 수 있다.

**이 파일은 TourAPI 응답이 아니라 DB 재구성이다.** 값 자체는 목록 조회로 들어온
것이지만 저장 과정을 한 번 거친 뒤라, 표기가 달라져 실제 변경이 아닌 updated가
섞일 수 있다.

실측으로는 그런 차이가 없었다 — 2026-08-20 중구 892건을 같은 날 TourAPI 목록
스냅샷과 대조하니 비교 대상 14개 열 전부에서 차이 0건이었다(대조가 normalize를
거쳐 비교하므로 좌표 자릿수와 시각 표기 차이는 흡수된다). 다만 한 구 한 번의
측정이므로 "차이가 날 수 없다"가 아니라 "이 경로에서는 안 났다"로 읽는다.

DB는 읽기만 한다.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

import httpx

from app.config import Settings
from app.repositories.supabase_places import SupabasePlaceRepository
from app.services.place_snapshot import (
    DATA_DIR,
    KST,
    SNAPSHOT_COLUMNS,
    normalize,
    snapshot_file_name,
    snapshot_rows_from_db,
    write_snapshot,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DB에서 장소 스냅샷 CSV 생성")
    parser.add_argument("--district-code", required=True, help="TourAPI 시·군·구 코드")
    parser.add_argument("--area-code", help="TourAPI 광역 행정구역 코드")
    parser.add_argument(
        "--date",
        help=(
            "파일명에 쓸 날짜(YYYYMMDD). 생략하면 이 구의 마지막 목록 조회 시각"
            "(list_fetched_at 최댓값)을 쓴다 — 자료가 실제로 언제 것인지 남긴다."
        ),
    )
    parser.add_argument(
        "--include-inactive",
        action="store_true",
        help=(
            "비활성 장소도 넣는다. 기본은 제외 — 비활성은 목록에서 사라져 비활성이 "
            "된 것이라, 넣으면 대조할 때마다 계속 삭제로 잡힌다."
        ),
    )
    parser.add_argument(
        "--output-dir", type=Path, default=DATA_DIR, help="저장할 디렉터리"
    )
    return parser


def resolve_date(rows: Mapping[str, Mapping[str, str]], override: str | None) -> datetime:
    """파일명에 박을 날짜. 자료가 만들어진 시점을 쓰고, 없으면 오늘로 떨어진다."""
    if override:
        return datetime.strptime(override, "%Y%m%d").replace(tzinfo=KST)
    fetched = [
        normalize(row.get("list_fetched_at"))
        for row in rows.values()
        if row.get("list_fetched_at")
    ]
    if not fetched:
        return datetime.now(KST)
    return datetime.fromisoformat(max(fetched)).astimezone(KST)


async def run(args: argparse.Namespace, settings: Settings) -> int:
    area_code = args.area_code or settings.place_sync_area_code
    district_code = args.district_code
    if not settings.supabase_url.strip() or not settings.supabase_secret_key.strip():
        raise ValueError("SUPABASE_URL / SUPABASE_SECRET_KEY가 필요합니다.")

    async with httpx.AsyncClient() as client:
        repository = SupabasePlaceRepository(
            supabase_url=settings.supabase_url,
            secret_key=settings.supabase_secret_key,
            client=client,
            timeout_seconds=max(settings.external_api_timeout_seconds, 30.0),
        )
        rows = await repository.list_region_place_rows(
            area_code,
            district_code,
            SNAPSHOT_COLUMNS,
            active_only=not args.include_inactive,
        )

    if not rows:
        print(f"{area_code}-{district_code}에 해당하는 장소가 DB에 없습니다.")
        return 1

    snapshot = snapshot_rows_from_db(rows)
    when = resolve_date(snapshot, args.date)
    # 이름은 API 스냅샷과 같은 규칙을 쓴다. 이 경로는 기준이 없는 구에 한 번
    # 기준을 세우려고 있는 것이고, 다음 대조부터는 API 응답이 같은 자리에 들어온다.
    path = args.output_dir / snapshot_file_name(area_code, district_code, when)
    write_snapshot(snapshot, path)

    print(f"DB에서 읽은 {len(snapshot)}건을 스냅샷으로 저장했습니다: {path}")
    print(f"자료 시점: {when:%Y-%m-%d} (list_fetched_at 기준)")
    print("외부 API는 호출하지 않았습니다.")
    print(
        "이 파일은 TourAPI 응답이 아니라 DB 재구성입니다 — 2026-08-20 중구 892건 "
        "실측에서는 API 스냅샷과 차이가 0건이었습니다."
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run(args, Settings()))


if __name__ == "__main__":
    raise SystemExit(main())
