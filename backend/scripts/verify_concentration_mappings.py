"""매핑된 집중률 장소명으로 실제 조회가 되는지 확인한다.

역할: 매핑 테이블에 이름이 있어도 집중률 API 조회가 실패할 수 있어(표기 차이·API
데이터 변경) 재매핑 효과를 숫자로 확인한다. 실측(2026-08-03)에서는 100건 중 30건이
실패했고, 그중 6건은 접두·부기를 뗀 이름으로는 조회됐다.
입력: 없음(Supabase 매핑 테이블을 읽는다). .env에 실 자격증명 필요.
출력: 구별 성공률 표 + backend/test_results/concentration_mapping_failures.csv
호출 시점: 매핑 적재(import_concentration_mappings.py) 이후 `python -m
scripts.verify_concentration_mappings`로 수동 실행한다.

조회할 구는 매핑된 장소의 district_code에서 나온다 - 서비스 경로와 같은 규칙이다
(D-095). 집중률 API가 signguCd로 엄격하게 거르므로 다른 구로 물으면 이름이 맞아도
0건이다. 예전에는 --district-code 하나를 전건에 적용해, 그 구가 아닌 매핑이 전부
실패로 잡혔다(2026-08-26: 종로구로 돌리면 391건 중 101건만 성공).

집중률 API를 매핑 건수만큼 호출한다. 동시 요청을 올리면 서비스 요청제한에 걸려
전건이 unavailable로 나오므로(2026-08-03 실측) 기본값을 낮게 두고 재시도한다.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.concentration_policy import (
    CONCENTRATION_AREA_CODE,
    concentration_signgu_code,
)
from app.config import Settings
from app.providers.concentration import RealConcentrationProvider
from app.tools.concentration import ConcentrationQuery, GetConcentrationTool
from scripts.build_concentration_mappings import places_district_code

_RESULTS_DIR = Path(__file__).resolve().parent.parent / "test_results"
# PostgREST가 한 응답에 돌려주는 최대 행 수.
_MAPPING_PAGE_SIZE = 1000
_BRACKET_PATTERN = re.compile(r"\s*\[[^\]]*\]")
_PAREN_PATTERN = re.compile(r"\s*\([^)]*\)")
_SEOUL_PREFIX = "서울 "


@dataclass(frozen=True)
class MappingRecord:
    content_id: str
    place_title: str
    concentration_name: str
    match_method: str
    # places.district_code(시군구 3자리). 조회할 구는 장소마다 다르다.
    district_code: str | None = None


@dataclass
class VerifyResult:
    record: MappingRecord
    status: str
    working_alternative: str = ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="집중률 매핑 조회 검증")
    # 조회할 구는 매핑된 장소에서 나온다(D-095). 이 인자는 대상을 좁히는 용도일
    # 뿐이고, 생략하면 모든 구를 한 번에 검증한다. 예전에는 기본값 11110이 전건에
    # 적용돼, 종로구 아닌 매핑이 전부 실패로 잡혔다.
    parser.add_argument(
        "--district-code",
        default=None,
        help="이 구의 매핑만 검증한다(예: 중구 11140). 생략하면 전 구를 검증한다",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=2,
        help="동시 요청 수. 올리면 요청제한(returnReasonCode 22)에 걸리기 쉽다.",
    )
    parser.add_argument(
        "--retries", type=int, default=3, help="unavailable 응답 재시도 횟수"
    )
    parser.add_argument(
        "--skip-alternatives",
        action="store_true",
        help="실패 건의 정정 후보를 추가 조회하지 않는다(호출량 절약)",
    )
    return parser


def _alternatives(name: str) -> list[str]:
    """접두·부기를 뗀 이름 후보. 실패 원인이 표기 차이인지 가려내는 용도다."""
    candidates: list[str] = []
    stripped = _PAREN_PATTERN.sub("", _BRACKET_PATTERN.sub("", name)).strip()
    if stripped and stripped != name:
        candidates.append(stripped)
    for base in [stripped or name, name]:
        if base.startswith(_SEOUL_PREFIX):
            candidates.append(base[len(_SEOUL_PREFIX) :].strip())
    seen: set[str] = set()
    return [c for c in candidates if c and c != name and not (c in seen or seen.add(c))]


def _parse_total_count(content_range: str) -> int | None:
    """PostgREST의 Content-Range(`0-390/391`)에서 전체 건수를 읽는다."""
    _, _, total = content_range.partition("/")
    return int(total) if total.isdigit() else None


# 출력용 이름표. app.service_area의 지원 구 목록과 같은 값이지만, 스크립트가
# 서비스 지원 범위에 묶이지 않도록(지원에서 빠진 구의 매핑도 검증한다) 여기 둔다.
_DISTRICT_NAMES: dict[str, str] = {
    "110": "종로구", "140": "중구", "170": "용산구", "200": "성동구",
    "215": "광진구", "230": "동대문구", "260": "중랑구", "290": "성북구",
    "305": "강북구", "320": "도봉구", "350": "노원구", "380": "은평구",
    "410": "서대문구", "440": "마포구", "470": "양천구", "500": "강서구",
    "530": "구로구", "545": "금천구", "560": "영등포구", "590": "동작구",
    "620": "관악구", "650": "서초구", "680": "강남구", "710": "송파구",
    "740": "강동구",
}


def _group_by_district(
    results: Sequence[VerifyResult],
) -> list[tuple[str | None, list[VerifyResult]]]:
    """구 코드 순으로 묶는다. 구를 모르는 건은 맨 뒤에 둔다."""
    grouped: dict[str | None, list[VerifyResult]] = {}
    for item in results:
        grouped.setdefault(item.record.district_code, []).append(item)
    known = sorted((k, v) for k, v in grouped.items() if k is not None)
    unknown = [(k, v) for k, v in grouped.items() if k is None]
    return [*known, *unknown]


def _signgu_code_of(record: MappingRecord) -> str:
    """매핑 장소의 구를 집중률 API signguCd로 바꾼다. 없으면 호출자가 걸러야 한다."""
    code = concentration_signgu_code(record.district_code)
    if code is None:
        raise ValueError(f"{record.content_id}: district_code가 없습니다.")
    return code


async def load_mappings(settings: Settings) -> list[MappingRecord]:
    """매핑을 전부 읽는다. 한 건이라도 빠지면 예외로 끊는다.

    PostgREST는 limit을 크게 줘도 한 응답을 1,000행에서 자른다. 매핑이 그보다
    많아지면 뒷부분이 조용히 빠져 "일부만 검증하고 전부 본 줄 아는" 상태가 된다.
    지금은 391건이라 한 페이지로 끝나지만, 지원 구가 늘면 상한에 가까워진다.
    """
    url = settings.supabase_url.rstrip("/") + "/rest/v1/place_concentration_mappings"
    rows: list[object] = []
    total: int | None = None
    offset = 0
    async with httpx.AsyncClient() as client:
        while True:
            response = await client.get(
                url,
                params={
                    "select": (
                        "content_id,primary_concentration_name,match_method,"
                        "places(title,district_code)"
                    ),
                    # 페이지를 넘기려면 정렬이 고정돼야 한다.
                    "order": "content_id",
                },
                headers={
                    "apikey": settings.supabase_secret_key,
                    "Range-Unit": "items",
                    "Range": f"{offset}-{offset + _MAPPING_PAGE_SIZE - 1}",
                    "Prefer": "count=exact",
                },
                timeout=settings.external_api_timeout_seconds,
            )
            response.raise_for_status()
            page = response.json()
            if total is None:
                total = _parse_total_count(response.headers.get("content-range", ""))
            rows.extend(page)
            if not page:
                break
            offset += len(page)
            if total is not None and offset >= total:
                break

    if total is None:
        raise ValueError(
            "Supabase 응답에 Content-Range가 없어 전체 건수를 확인할 수 없습니다."
        )
    if len(rows) != total:
        raise ValueError(
            f"매핑을 {total}건 중 {len(rows)}건만 읽었습니다. 일부만 검증하면 "
            "성공률이 실제와 달라지므로 끊습니다."
        )

    records: list[MappingRecord] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("매핑 응답 행이 객체가 아닙니다.")
        place = row.get("places")
        if isinstance(place, list):
            place = place[0] if place else None
        place = place or {}
        district_code = place.get("district_code")
        records.append(
            MappingRecord(
                content_id=str(row["content_id"]),
                place_title=str(place.get("title", "")),
                concentration_name=str(row["primary_concentration_name"]),
                match_method=str(row.get("match_method", "")),
                district_code=str(district_code) if district_code else None,
            )
        )
    return records


async def verify(args: argparse.Namespace, settings: Settings) -> list[VerifyResult]:
    semaphore = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient() as client:
        tool = GetConcentrationTool(
            RealConcentrationProvider(
                api_key=settings.tour_api_service_key,
                client=client,
                timeout_seconds=settings.external_api_timeout_seconds,
            )
        )

        async def probe(name: str, signgu_code: str) -> str:
            async with semaphore:
                for attempt in range(args.retries):
                    result = await tool.execute(
                        ConcentrationQuery(CONCENTRATION_AREA_CODE, signgu_code, name)
                    )
                    if result.status.value != "unavailable":
                        return result.status.value
                    # 요청제한은 잠깐 쉬면 풀리는 경우가 있어 간격을 늘려 재시도한다.
                    await asyncio.sleep(1.5 * (attempt + 1))
                return "unavailable"

        records = await load_mappings(settings)
        if args.district_code is not None:
            wanted = places_district_code(CONCENTRATION_AREA_CODE, args.district_code)
            records = [item for item in records if item.district_code == wanted]

        # 구를 모르는 매핑은 조회하지 않는다. 종로구로 대신 물으면 다른 구 장소는
        # 언제나 0건이라, 틀린 조회가 이름 문제로 보인다(D-095).
        probable = [item for item in records if item.district_code]
        unknown = [item for item in records if not item.district_code]

        statuses = await asyncio.gather(
            *(
                probe(item.concentration_name, _signgu_code_of(item))
                for item in probable
            )
        )
        results = [
            VerifyResult(record=record, status=status)
            for record, status in zip(probable, statuses, strict=True)
        ]
        results.extend(
            VerifyResult(record=record, status="district_unknown") for record in unknown
        )

        if not args.skip_alternatives:
            for result in results:
                if result.status != "no_data":
                    continue
                signgu_code = _signgu_code_of(result.record)
                for alternative in _alternatives(result.record.concentration_name):
                    if await probe(alternative, signgu_code) == "success":
                        result.working_alternative = alternative
                        break
        return results


def write_failures_csv(results: Sequence[VerifyResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    failures = [item for item in results if item.status != "success"]
    with path.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.writer(fp)
        writer.writerow(
            [
                "content_id",
                "구",
                "district_code",
                "TourAPI_장소명",
                "저장된_집중률명",
                "조회결과",
                "match_method",
                "정상조회되는_후보명",
                "정정가능",
            ]
        )
        for item in failures:
            writer.writerow(
                [
                    item.record.content_id,
                    _DISTRICT_NAMES.get(item.record.district_code or "", "구 미상"),
                    item.record.district_code or "",
                    item.record.place_title,
                    item.record.concentration_name,
                    item.status,
                    item.record.match_method,
                    item.working_alternative,
                    "O" if item.working_alternative else "X",
                ]
            )


async def run(args: argparse.Namespace, settings: Settings) -> int:
    if not settings.tour_api_service_key:
        raise ValueError("TOUR_API_SERVICE_KEY가 필요합니다.")
    if not settings.supabase_url or not settings.supabase_secret_key:
        raise ValueError("SUPABASE_URL / SUPABASE_SECRET_KEY가 필요합니다.")

    results = await verify(args, settings)
    counts: dict[str, int] = {}
    for item in results:
        counts[item.status] = counts.get(item.status, 0) + 1

    total = len(results)
    success = counts.get("success", 0)

    # 구별로 나눠 보여준다. 합계만 보면 어느 구가 문제인지 알 수 없다.
    print(f"{'구':<8}{'매핑':>6}{'성공':>6}{'실패':>6}{'성공률':>8}")
    print("-" * 34)
    for district_code, items in _group_by_district(results):
        district_success = sum(1 for item in items if item.status == "success")
        rate = f"{district_success * 100 // len(items)}%" if items else "-"
        label = _DISTRICT_NAMES.get(district_code, district_code or "구 미상")
        print(
            f"{label:<8}{len(items):>6}{district_success:>6}"
            f"{len(items) - district_success:>6}{rate:>8}"
        )
    print("-" * 34)
    rate = f"{success * 100 // total}%" if total else "-"
    print(f"{'합계':<8}{total:>6}{success:>6}{total - success:>6}{rate:>8}")
    print(f"\n상태 분포: {counts}")
    if counts.get("district_unknown"):
        print(
            f"  주의: district_unknown {counts['district_unknown']}건은 장소의 구를 몰라 "
            "조회하지 않은 것입니다. 이름 문제가 아닙니다."
        )
    # unavailable은 이름 문제가 아니라 요청제한일 수 있어 no_data와 구분해서 읽는다.
    if counts.get("unavailable"):
        print(
            f"  주의: unavailable {counts['unavailable']}건은 요청제한일 수 있습니다. "
            "잠시 후 다시 실행해 확인하세요."
        )
    fixable = [item for item in results if item.working_alternative]
    if fixable:
        print(f"\n표기 정정으로 해결 가능한 {len(fixable)}건:")
        for item in fixable:
            print(f"  '{item.record.concentration_name}' → '{item.working_alternative}'")

    output = _RESULTS_DIR / "concentration_mapping_failures.csv"
    write_failures_csv(results, output)
    print(f"\n실패 목록 저장: {output}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run(args, Settings()))


if __name__ == "__main__":
    raise SystemExit(main())
