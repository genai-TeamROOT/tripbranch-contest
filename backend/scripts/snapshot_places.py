"""TourAPI 장소 목록을 구 단위로 스냅샷에 저장하고 같은 구의 이전 스냅샷과 대조한다.

역할: DB 동기화 전에 "이번에 무엇이 바뀌는가"를 파일로 남긴다. 목록 조회는 여기서
한 번만 수행하고, 저장한 스냅샷을 sync_places.py --from-snapshot이 재사용해 같은
날 목록 API를 두 번 호출하지 않는다.
입력: --baseline(이전 스냅샷 CSV). 생략하면 저장된 최신 스냅샷을 자동으로 쓴다.
출력:
  - supabase/data/places_api_snapshot_<지역>-<구>_<오늘>.csv   (이번 조회 원본)
  - supabase/data/places_reconciliation_<지역>-<구>_<오늘>.csv (added/removed/updated)
호출 시점: `python -m scripts.snapshot_places`로 수동 실행.

DB는 건드리지 않는다 — 반영은 sync_places.py가 담당한다. 대조·스냅샷 로직 자체는
app/services/place_snapshot.py에 있다(개발자 Ops 패널도 같은 코드를 쓴다).
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import httpx

from app.config import Settings
from app.services.place_snapshot import (
    COMPARED_COLUMNS,
    DATA_DIR,
    KST,
    build_reconciliation_rows,
    comparable_columns,
    fetch_place_rows,
    find_baseline,
    load_snapshot,
    reconciliation_file_name,
    snapshot_file_name,
    snapshot_regions,
    write_reconciliation,
    write_snapshot,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TourAPI 장소 목록 스냅샷·대조")
    parser.add_argument("--area-code", help="TourAPI 광역 행정구역 코드")
    parser.add_argument("--district-code", help="TourAPI 시·군·구 코드")
    parser.add_argument(
        "--baseline",
        type=Path,
        help="비교 기준 스냅샷 CSV. 생략하면 저장된 최신 스냅샷을 쓴다.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DATA_DIR,
        help="스냅샷과 대조 결과를 저장할 디렉터리",
    )
    return parser


async def fetch_places(
    settings: Settings, area_code: str, district_code: str, fetched_at: datetime
) -> dict[str, dict[str, str]]:
    async with httpx.AsyncClient() as client:
        return await fetch_place_rows(
            client,
            settings.tour_api_service_key,
            area_code,
            district_code,
            fetched_at,
        )


async def run(args: argparse.Namespace, settings: Settings) -> int:
    area_code = args.area_code or settings.place_sync_area_code
    district_code = args.district_code or settings.place_sync_district_code
    if not settings.tour_api_service_key:
        raise ValueError("TOUR_API_SERVICE_KEY가 필요합니다.")

    now = datetime.now(KST)
    current = await fetch_places(settings, area_code, district_code, now)
    snapshot_path = args.output_dir / snapshot_file_name(area_code, district_code, now)
    baseline_path = args.baseline or find_baseline(
        args.output_dir,
        area_code=area_code,
        district_code=district_code,
        exclude=snapshot_path,
    )
    write_snapshot(current, snapshot_path)
    print(f"스냅샷 {len(current)}건 저장: {snapshot_path}")

    if baseline_path is None:
        print("기준 스냅샷이 없어 대조는 건너뜁니다.")
        return 0

    baseline = load_snapshot(baseline_path)
    # 기준 스냅샷이 정말 이 구의 것인지 내용으로 확인한다. 다른 구를 기준으로
    # 잡으면 "전량 삭제 + 전량 신규"가 나오는데, 그 모양은 실제 대량 변경과
    # 구분되지 않는다(2026-08-20 중구 사례).
    regions = snapshot_regions(baseline)
    if regions and regions != {(area_code, district_code)}:
        found = ", ".join(sorted(f"{area}-{district}" for area, district in regions))
        raise ValueError(
            f"기준 스냅샷 {baseline_path.name}은 {found} 자료라 "
            f"{area_code}-{district_code} 대조에 쓸 수 없습니다."
        )
    baseline_columns = next(iter(baseline.values()), {}).keys()
    compared = comparable_columns(list(baseline_columns))
    skipped = [column for column in COMPARED_COLUMNS if column not in compared]
    if skipped:
        # 조용히 빼지 않는다. 이 줄이 없으면 "안 바뀌었다"와 "안 봤다"가 결과에서
        # 구분되지 않는다.
        print(
            f"기준 스냅샷에 없는 열은 비교하지 않습니다: {', '.join(skipped)}"
        )
    rows = build_reconciliation_rows(baseline, current, compared)
    reconciliation_path = args.output_dir / reconciliation_file_name(
        area_code, district_code, now
    )
    write_reconciliation(
        rows,
        reconciliation_path,
        baseline_name=baseline_path.name,
        compared_at=now,
    )

    counts = {"added": 0, "removed": 0, "updated": 0}
    for row in rows:
        counts[str(row["change_type"])] += 1
    print(f"기준 {baseline_path.name}: {len(baseline)}건")
    print(
        f"변경: 신규 {counts['added']} / 삭제 {counts['removed']} / 수정 {counts['updated']}"
    )
    for change_type in ("added", "removed"):
        titles = [str(row["title"]) for row in rows if row["change_type"] == change_type]
        if titles:
            preview = ", ".join(titles[:8])
            suffix = " …" if len(titles) > 8 else ""
            print(f"  {change_type}: {preview}{suffix}")
    print(f"대조 결과 저장: {reconciliation_path}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run(args, Settings()))


if __name__ == "__main__":
    raise SystemExit(main())
