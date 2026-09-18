"""한국관광공사 "관광지별 연관 관광지 정보"(TarRlteTarService1) 원본 데이터를
서울 25개 구 단위로 수집해 JSONL로 저장한다.

역할: 이후 매칭 단계(장소명 ↔ places.content_id, place_concentration_mappings와
같은 패턴)가 읽어들일 원본 응답을 그대로 보관한다. 이 스크립트는 매칭을 하지
않는다 — tAtsCd/rlteTatsCd가 TourAPI 표준 content_id와 다른 32자리 해시코드라
이름 기반 매칭이 별도로 필요하며, 그건 다음 단계(build_place_association_mappings.py,
아직 없음)의 몫이다.

입력: 없음(외부 API만 호출). 서울 25개 구를 기본으로 순회하며, --districts로
일부 구만 골라 파일럿 실행도 가능하다.
출력: supabase/data/place_associations_raw_<baseYm>_<오늘>.jsonl
      각 줄은 API 응답의 item 하나(구 정보 포함, 원본 필드 그대로).
      + 구별 수집 요약을 표준 출력에 나열한다.
호출 시점: `python -m scripts.collect_place_associations`로 수동 실행한다.
          우선 --districts 11110,11140(종로구·중구)로 파일럿 실행을 권장한다.

오퍼레이션은 "지역기반 관광지별 연관 관광지 정보 조회"(areaBasedList1)만 쓴다 —
키워드검색(searchKeyword1)은 장소 하나씩 조회하는 용도라 전수 수집에는 안 맞는다.

데이터는 매월 8일 갱신되는 월별 스냅샷이다(baseYm=YYYYMM). 이번 달 데이터가 아직
안 올라왔을 수 있어(매뉴얼 v4.1 확인 시점 기준 최신 baseYm이 무엇인지는 실행 시
resultCode/resultMsg로 확인해야 한다) 기본값은 저번 달로 두고, 비어 있으면
--base-ym으로 한두 달 전 값을 다시 시도한다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from app.config import Settings

_KST = ZoneInfo("Asia/Seoul")
_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "supabase" / "data"
_ASSOCIATION_URL = (
    "https://apis.data.go.kr/B551011/TarRlteTarService1/areaBasedList1"
)
_PAGE_SIZE = 100
_SEOUL_AREA_CD = "11"

# 한국관광공사_TourAPI_관광지_시군구_코드정보_v1.0.xlsx 기준 서울 25개 구.
# signguCd는 전부 "11"(서울) + 구 고유 3자리로 구성돼, places.district_code(3자리)와
# "11" + district_code로 그대로 대응된다(2026-08-26 확인, 종로구 11110/중구 11140 등
# 25개 구 전수 대조).
SEOUL_DISTRICTS: tuple[tuple[str, str], ...] = (
    ("11110", "종로구"),
    ("11140", "중구"),
    ("11170", "용산구"),
    ("11200", "성동구"),
    ("11215", "광진구"),
    ("11230", "동대문구"),
    ("11260", "중랑구"),
    ("11290", "성북구"),
    ("11305", "강북구"),
    ("11320", "도봉구"),
    ("11350", "노원구"),
    ("11380", "은평구"),
    ("11410", "서대문구"),
    ("11440", "마포구"),
    ("11470", "양천구"),
    ("11500", "강서구"),
    ("11530", "구로구"),
    ("11545", "금천구"),
    ("11560", "영등포구"),
    ("11590", "동작구"),
    ("11620", "관악구"),
    ("11650", "서초구"),
    ("11680", "강남구"),
    ("11710", "송파구"),
    ("11740", "강동구"),
)


def _default_base_ym() -> str:
    """저번 달을 기본값으로 쓴다.

    데이터가 매월 8일에 갱신되는 월별 스냅샷이라, 이번 달 초에는 이번 달 값이 아직
    없을 수 있다. 확실한 최신 baseYm은 API 자체가 알려주지 않으므로, 안전하게 한 달
    전을 기본값으로 두고 비어 있으면 --base-ym으로 사용자가 재시도한다.
    """
    now = datetime.now(_KST)
    year, month = now.year, now.month - 1
    if month == 0:
        year, month = year - 1, 12
    return f"{year:04d}{month:02d}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="관광지별 연관 관광지 정보(TarRlteTarService1) 서울 원본 수집"
    )
    parser.add_argument("--area-code", default=_SEOUL_AREA_CD, help="TourAPI 광역 코드")
    parser.add_argument(
        "--districts",
        help=(
            "쉼표로 구분한 signguCd 목록(예: 11110,11140). 생략하면 서울 25개 구 전체."
        ),
    )
    parser.add_argument(
        "--base-ym",
        default=_default_base_ym(),
        help="조회 기준연월(YYYYMM). 기본값은 저번 달.",
    )
    parser.add_argument(
        "--page-size", type=int, default=_PAGE_SIZE, help="한 페이지 결과 수"
    )
    parser.add_argument(
        "--min-interval-seconds",
        type=float,
        default=0.3,
        help="요청 간 최소 간격(초). 초당 호출 한도 회피용.",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=_DATA_DIR, help="JSONL 저장 디렉터리"
    )
    return parser


@dataclass(frozen=True)
class DistrictResult:
    signgu_cd: str
    signgu_nm: str
    items: list[dict[str, object]]
    result_code: str
    result_msg: str


async def fetch_district_associations(
    client: httpx.AsyncClient,
    settings: Settings,
    *,
    area_code: str,
    signgu_cd: str,
    signgu_nm: str,
    base_ym: str,
    page_size: int,
    min_interval_seconds: float,
) -> DistrictResult:
    """한 구의 지역기반 연관 관광지 정보를 페이지 끝까지 모은다."""
    items: list[dict[str, object]] = []
    result_code = "0000"
    result_msg = "OK"
    page_no = 1
    while True:
        response = await client.get(
            _ASSOCIATION_URL,
            params={
                "serviceKey": settings.tour_api_service_key,
                "numOfRows": str(page_size),
                "pageNo": str(page_no),
                "MobileOS": "ETC",
                "MobileApp": "TripBranch",
                "baseYm": base_ym,
                "areaCd": area_code,
                "signguCd": signgu_cd,
                "_type": "json",
            },
            timeout=settings.external_api_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        header = payload.get("response", {}).get("header", {})
        result_code = str(header.get("resultCode", result_code))
        result_msg = str(header.get("resultMsg", result_msg))
        if result_code != "0000":
            # NODATA_ERROR(03)를 포함해 정상 실패도 여기서 걸린다 — 그 구에 이번
            # baseYm 데이터가 없다는 뜻이라 예외를 던지지 않고 빈 결과로 멈춘다.
            break
        body = payload.get("response", {}).get("body", {})
        raw_items = body.get("items") or {}
        rows = raw_items.get("item", []) if isinstance(raw_items, dict) else []
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            rows = []
        items.extend(rows)
        total_count = int(body.get("totalCount") or 0)
        if not rows or page_no * page_size >= total_count:
            break
        page_no += 1
        if min_interval_seconds > 0:
            await asyncio.sleep(min_interval_seconds)

    return DistrictResult(
        signgu_cd=signgu_cd,
        signgu_nm=signgu_nm,
        items=items,
        result_code=result_code,
        result_msg=result_msg,
    )


def write_jsonl(results: Sequence[DistrictResult], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("w", encoding="utf-8") as fp:
        for result in results:
            for item in result.items:
                fp.write(json.dumps(item, ensure_ascii=False) + "\n")
                written += 1
    return written


async def run(args: argparse.Namespace, settings: Settings) -> int:
    if not settings.tour_api_service_key:
        raise ValueError("TOUR_API_SERVICE_KEY가 필요합니다.")

    if args.districts:
        wanted = {code.strip() for code in args.districts.split(",") if code.strip()}
        districts = [d for d in SEOUL_DISTRICTS if d[0] in wanted]
        missing = wanted - {d[0] for d in districts}
        if missing:
            raise ValueError(f"알 수 없는 signguCd: {sorted(missing)}")
    else:
        districts = list(SEOUL_DISTRICTS)

    print(f"기준연월: {args.base_ym} / 대상 구: {len(districts)}곳")

    results: list[DistrictResult] = []
    call_count = 0
    async with httpx.AsyncClient() as client:
        for signgu_cd, signgu_nm in districts:
            result = await fetch_district_associations(
                client,
                settings,
                area_code=args.area_code,
                signgu_cd=signgu_cd,
                signgu_nm=signgu_nm,
                base_ym=args.base_ym,
                page_size=args.page_size,
                min_interval_seconds=args.min_interval_seconds,
            )
            results.append(result)
            # 페이지 수만큼 호출했으므로 대략치를 위해 결과 건수로 역산한다.
            call_count += max(1, -(-len(result.items) // args.page_size))
            if result.result_code != "0000":
                print(f"  {signgu_nm}({signgu_cd}): {result.result_msg} (건너뜀)")
                continue
            distinct_spots = len({item.get("tAtsCd") for item in result.items})
            print(
                f"  {signgu_nm}({signgu_cd}): 기준 관광지 {distinct_spots}곳, "
                f"연관관계 {len(result.items)}건"
            )
            if args.min_interval_seconds > 0:
                await asyncio.sleep(args.min_interval_seconds)

    total_items = sum(len(r.items) for r in results)
    if total_items == 0:
        print(
            "\n수집된 데이터가 없습니다 — baseYm이 아직 게시되지 않았을 수 있습니다. "
            "--base-ym으로 한두 달 전 값을 다시 시도해 보세요."
        )
        return 1

    now = datetime.now(_KST)
    output = args.output_dir / f"place_associations_raw_{args.base_ym}_{now:%Y%m%d}.jsonl"
    written = write_jsonl(results, output)

    print(f"\n총 {written}건 저장: {output}")
    print(f"대략 호출 횟수: {call_count}건 (일일 한도 {settings.tour_api_daily_call_limit}건)")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run(args, Settings()))


if __name__ == "__main__":
    raise SystemExit(main())
