"""집중률 API의 장소명을 한 개 구의 `places`와 매칭해 매핑 CSV를 만든다.

역할: import_concentration_mappings.py가 적재할 CSV를 생성한다.
입력: --district-code(필수), --places-snapshot(없으면 Supabase places에서 활성 장소를
      읽는다).
출력: supabase/data/concentration_place_mapping_<구코드>_<오늘>.csv
      + 미매칭 장소 목록을 표준 출력에 나열한다.
호출 시점: `python -m scripts.build_concentration_mappings --district-code 11140`처럼
      구를 지정해 수동 실행한다. 구마다 한 번씩 돌린다.

매칭 로직 자체는 `app/services/concentration_mapping.py`에 있다. 개발자 Ops 패널도
같은 함수를 쓰는데, `app`이 `scripts`를 임포트하면 의존 방향이 뒤집혀서(지금은
scripts → app) 로직을 그쪽에 뒀다. 여기서는 그 모듈을 재수출해 기존 호출부와
테스트가 이 이름으로 계속 임포트할 수 있게 한다.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import Settings
from app.services.concentration_mapping import (
    DATA_DIR as _DATA_DIR,
)
from app.services.concentration_mapping import (
    DEFAULT_OVERRIDES as _DEFAULT_OVERRIDES,
)
from app.services.concentration_mapping import (
    ManualOverride,
    MappingRow,
    PlaceRow,
    apply_search_keys,
    derive_search_key,
    derive_search_keys,
    fetch_concentration_place_names,
    load_manual_overrides,
    load_names_file,
    load_places_from_supabase,
    match_places,
    places_district_code,
    write_mapping_csv,
    write_names_file,
)

# 재수출. 기존 테스트와 호출부가 이 이름으로 임포트한다.
__all__ = [
    "ManualOverride",
    "MappingRow",
    "PlaceRow",
    "apply_search_keys",
    "derive_search_key",
    "derive_search_keys",
    "fetch_concentration_place_names",
    "load_manual_overrides",
    "load_names_file",
    "load_places_from_snapshot",
    "load_places_from_supabase",
    "match_places",
    "places_district_code",
    "write_mapping_csv",
    "write_names_file",
]

_KST = ZoneInfo("Asia/Seoul")

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="집중률 장소 매핑 CSV 생성")
    parser.add_argument("--area-code", default="11", help="집중률 API 광역 코드")
    # 기본값을 두지 않는다. 종로구(11110)가 기본이던 때는 구를 지정하지 않고 돌리면
    # 조용히 종로구가 다시 만들어져, 다른 구를 뽑으려던 실행이 종로구 CSV를 남겼다.
    parser.add_argument(
        "--district-code",
        required=True,
        help="집중률 API 시군구 코드(예: 종로구 11110, 중구 11140)",
    )
    parser.add_argument(
        "--places-snapshot",
        type=Path,
        help="places 목록 CSV. 생략하면 Supabase에서 활성 장소를 읽는다.",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=_DATA_DIR, help="CSV 저장 디렉터리"
    )
    parser.add_argument(
        "--manual-overrides",
        type=Path,
        default=_DEFAULT_OVERRIDES,
        help="규칙으로 못 붙이는 짝을 적어둔 CSV(place_title,concentration_title)",
    )
    parser.add_argument(
        "--names-file",
        type=Path,
        help=(
            "집중률 장소명을 API 대신 이 파일에서 읽는다. 생략하면 API로 수집하고 "
            "같은 이름으로 저장해 다음 실행에서 재사용할 수 있다."
        ),
    )
    return parser


def load_places_from_snapshot(path: Path) -> list[PlaceRow]:
    with path.open(encoding="utf-8-sig") as fp:
        return [
            PlaceRow(content_id=row["content_id"].strip(), title=row["title"].strip())
            for row in csv.DictReader(fp)
            if row.get("content_id") and row.get("title")
        ]

async def run(args: argparse.Namespace, settings: Settings) -> int:
    if not settings.tour_api_service_key:
        raise ValueError("TOUR_API_SERVICE_KEY가 필요합니다.")

    if args.names_file is not None and args.names_file.exists():
        concentration_names = load_names_file(args.names_file)
        print(f"집중률 장소명 {len(concentration_names)}건 재사용: {args.names_file.name}")
    else:
        concentration_names = await fetch_concentration_place_names(
            settings, args.area_code, args.district_code
        )
        print(f"집중률 API 장소명 {len(concentration_names)}건 수집")

    if args.places_snapshot is not None:
        places = load_places_from_snapshot(args.places_snapshot)
        print(f"places 스냅샷 {len(places)}건: {args.places_snapshot.name}")
    else:
        if not settings.supabase_url or not settings.supabase_secret_key:
            raise ValueError("SUPABASE_URL / SUPABASE_SECRET_KEY가 필요합니다.")
        district = places_district_code(args.area_code, args.district_code)
        places = await load_places_from_supabase(settings, district_code=district)
        print(f"Supabase 활성 장소 {len(places)}건(district_code={district})")

    overrides = load_manual_overrides(args.manual_overrides)
    if overrides:
        print(f"수동 매핑 {len(overrides)}건 적용: {args.manual_overrides.name}")
    matched, unmatched, leftover = match_places(places, concentration_names, overrides)
    matched, unresolved = apply_search_keys(matched, concentration_names)
    now = datetime.now(_KST)
    # 파일명에 구 코드를 넣는다. 날짜만 쓰면 같은 날 여러 구를 돌릴 때 앞의 구 결과를
    # 덮어써서 사라진다(TP-136에서 세 구를 하루에 뽑다가 확인).
    write_names_file(
        concentration_names,
        args.output_dir
        / f"concentration_place_names_{args.district_code}_{now:%Y%m%d}.csv",
    )
    output = (
        args.output_dir
        / f"concentration_place_mapping_{args.district_code}_{now:%Y%m%d}.csv"
    )
    write_mapping_csv(matched, output)

    method_counts: dict[str, int] = {}
    for row in matched:
        method_counts[row.match_method] = method_counts.get(row.match_method, 0) + 1
    print(
        f"\n매칭 {len(matched)}건 (정확 {method_counts.get('exact', 0)} / "
        f"별칭포함 {method_counts.get('exact_with_alias', 0)} / "
        f"정규화 {method_counts.get('normalized', 0)} / 수동 {method_counts.get('manual', 0)})"
    )
    for row in matched:
        if row.match_method != "exact":
            alias_text = f" + 별칭 {list(row.aliases)}" if row.aliases else ""
            print(
                f"  {row.match_method}: '{row.place_title}' → "
                f"'{row.concentration_title}'{alias_text}"
            )

    substituted = [row for row in matched if row.search_key]
    if substituted:
        print(f"\n공백 때문에 검색어를 따로 둔 {len(substituted)}건:")
        for row in substituted:
            print(f"  '{row.concentration_title}' → 검색어 '{row.search_key}'")
    if unresolved:
        # 검색어가 다른 집중률 장소까지 끌어온다. 응답에서 정식 명칭으로 골라내야 한다.
        print(f"\n검색어가 유일하지 않은 {len(unresolved)}건(응답 대조 필요):")
        for row in unresolved:
            print(f"  '{row.concentration_title}' → 검색어 '{row.search_key}'")

    # 집중률 API에는 있는데 places와 못 붙은 이름. 사람이 확인해 수동 매핑할 대상이다.
    print(f"\n집중률 API에만 있는 이름 {len(leftover)}건:")
    for name in leftover:
        print(f"  {name}")

    print(f"\nCSV 저장: {output}")
    print(
        "적재: python -m scripts.import_concentration_mappings "
        f"--csv {output} --dry-run"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run(args, Settings()))


if __name__ == "__main__":
    raise SystemExit(main())
