"""후보 한도를 바꿨을 때 추천이 실제로 어떻게 달라지는지 실측한다.

D-111에서 후보 한도를 10 → 30으로 올릴 때 쓴 측정을 하나로 합친 것이다. 한도를
다시 조정할 일이 생기면(도보 2단 채점이 들어가 카카오 결합이 끊기거나, 결과 개수를
바꾸거나, 밀도가 다른 지역이 문제가 되면) 이 스크립트를 그대로 돌려 근거를 만든다.

**후보는 실물 경로로 뽑는다.** `NearbyPlaceDetailsTool`을 그대로 태워 TourAPI
`locationBasedList2`에서 받고, 하드 필터도 `prepare_candidates()`를 직접 부른다.
D-111 작업 중에 Supabase `places`에서 후보를 뽑는 스크립트로 재다가 두 번 헛짚었다 —
그 둘은 같은 반경에서 279곳 대 1,082곳으로 다르고, 100m 안인데 반경 조회에는 안
나오는 장소가 있다. 측정용 경로를 따로 만들면 실물과 갈린다.

무엇을 재는가
-------------
1. **pool** — 시각 x 한도별 하드 필터 통과 수. 보충 조회를 넣은 값과 뺀 값을 함께
   낸다. **보충을 빼고 보면 밤 시간대를 과소평가한다** — 실제로 D-111 중간에 "9시·21시에
   카드를 못 채운다"고 잘못 판단했다가 보충을 넣고 뒤집혔다.
2. **more** — "더 보기"를 반복했을 때 턴마다 요청한 개수를 채우는지. 채우지 못하면
   `candidate_pool_truncated`가 서는지도 함께 본다.
3. **calls** — 상세 출처(supabase/tour_api) x 한도별 외부 호출 수와 소요 시간.
   한도를 올려도 되는지가 사실상 이 표 하나로 갈린다.

판정 기준
---------
- **결과를 못 채우면 한도가 모자란 것이다.** 통과 수가 RECOMMENDATION_RESULT_LIMIT
  미만인 칸에 `*`가 붙는다. 보충 조회를 넣고도 붙으면 진짜 부족이다.
- **"더 보기"는 3턴까지 꽉 차야 한다.** 그보다 일찍 모자라면 한도가 아니라
  MAX_PLACE_PROVIDER_ROWS가 병목이다(앞에서부터 받아 건너뛰는 구조라 이미 본 곳이
  늘수록 남는 게 준다).
- **tour_api 상세에서 한도를 올리는 것은 언제나 손해다.** 후보 1곳당 호출 2회라
  일일 한도만 태운다. validate_provider_config()가 이 조합을 부팅에서 막는다.

한도 범위
---------
`--limits`는 MIN_RECOMMENDATION_LIMIT ~ MAX_RECOMMENDATION_CANDIDATE_LIMIT 안에서만
받는다. 상한을 넘겨 재보려면 `recommendation_limits.py`의 상수를 먼저 올려야 한다 —
그 상한 자체가 계약 스키마와 부팅 검증에 걸려 있어 우회로 재면 실물과 갈린다.

실행: `python -m scripts.measure_candidate_limit_impact`
      `python -m scripts.measure_candidate_limit_impact --scope calls`
실제 TourAPI/Supabase를 호출하므로 .env에 PROVIDER_MODE=real과 키가 필요하다.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import contextlib
import csv
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

from app.agent_context.mappers import map_location_context, map_places_context
from app.agent_context.schemas import RecommendationContext
from app.config import settings
from app.domain.candidate_mapper import map_context_to_scoring_candidates
from app.domain.scoring import prepare_candidates
from app.providers.factory import get_place_details_provider, get_place_search_provider
from app.recommendation_limits import (
    MAX_RECOMMENDATION_CANDIDATE_LIMIT,
    MIN_RECOMMENDATION_LIMIT,
)
from app.services.runtime.agent_runtime import (
    _MAX_CANDIDATE_REFILL_ATTEMPTS,
    _candidate_pool_exhausted,
)
from app.tools.nearby_place_details import (
    CANDIDATE_POOL_TRUNCATED_WARNING,
    NearbyPlaceDetailsQuery,
    NearbyPlaceDetailsResult,
    NearbyPlaceDetailsTool,
)
from app.tools.resolve_location import (
    ResolutionConfidence,
    ResolutionMethod,
    ResolvedLocation,
    ResolveLocationResult,
    ResolveLocationStatus,
)

KST = ZoneInfo("Asia/Seoul")

RESULTS_DIR = Path(__file__).resolve().parents[1] / "test_results"
RESULTS_CSV = RESULTS_DIR / "candidate_limit_impact.csv"

# 밀도와 미지원 분류 비율이 서로 다른 지점을 고른다. 한 곳만 재면 결론이 그 동네
# 사정에 묶인다 — 홍대·북촌은 게스트하우스가 밀집해 상위 행의 절반이 숙박으로
# 빠지고(생존율 40~58%), 성수동은 98%다.
_CENTERS: dict[str, tuple[float, float]] = {
    "안국역": (37.576, 126.9855),
    "경복궁": (37.579617, 126.977041),
    "홍대입구": (37.5572, 126.9245),
}

# 하루를 대표하는 시각. 하드 필터가 시각에 크게 좌우되므로 낮 한 점만 보면 안 된다.
_HOURS = (9, 14, 19, 21, 23)

_DEFAULT_LIMITS = (10, 20, 30)
_DEFAULT_VISIT_DATE = "2026-08-31"
_DEFAULT_RADIUS_KM = 2.0
_DEFAULT_MORE_TURNS = 4


@dataclass(frozen=True)
class PoolRow:
    center: str
    limit: int
    hour: int
    eligible_first: int
    eligible_with_refill: int
    candidates: int
    context_fetches: int


@contextlib.contextmanager
def _candidate_limit(limit: int) -> Iterator[None]:
    """스윕하는 한도를 설정에도 반영한다.

    `_candidate_pool_exhausted()`가 인자가 아니라 `settings.recommendation_candidate_limit`
    을 읽는다. 운영에서는 Tool에 넘기는 limit과 그 설정이 항상 같은 값이라 문제가
    없지만, 여기서는 한도를 바꿔가며 재므로 둘이 어긋나면 보충 조회가 엉뚱하게
    멈춘다 — 실제로 한도 10을 잴 때 "10 < 30"이 소진으로 읽혀 보충이 한 번도 돌지
    않았다. 스윕 값이 곧 그 설정으로 도는 세상을 재는 것이 목적이므로 함께 맞춘다.
    """
    original = settings.recommendation_candidate_limit
    settings.recommendation_candidate_limit = limit
    try:
        yield
    finally:
        settings.recommendation_candidate_limit = original


def _location(name: str, latitude: float, longitude: float) -> ResolveLocationResult:
    """좌표를 위치 Tool 성공 결과로 감싼다.

    위치 해석 자체는 이 측정의 관심사가 아니라서 Tool을 태우지 않는다 — 중심점을
    고정해야 회차 간 비교가 되고, 지오코딩 결과가 흔들리면 그 비교가 깨진다.
    """
    return ResolveLocationResult(
        status=ResolveLocationStatus.SUCCESS,
        location=ResolvedLocation(
            requested_query=name,
            provider_query=name,
            resolved_name=name,
            latitude=latitude,
            longitude=longitude,
            resolution_method=ResolutionMethod.DIRECT,
            confidence=ResolutionConfidence.EXACT,
        ),
        error=None,
    )


def _to_context(
    name: str,
    center: tuple[float, float],
    result: NearbyPlaceDetailsResult,
) -> RecommendationContext:
    """Tool 결과를 D가 받는 Context 모양으로 만든다.

    `map_places_context()`를 반드시 거친다 — 운영시간이 여기서 컨텍스트 스키마로
    정규화되고(`start`/`end` -> `open_time`/`close_time`), 이 단계를 건너뛰면 모든
    후보가 "운영시간 미확인"이 되어 하드 필터가 아무도 걸러내지 않는다.
    """
    return RecommendationContext(
        location=map_location_context(_location(name, center[0], center[1])),
        weather=None,
        places=map_places_context(result),
    )


def _eligible_count(
    context: RecommendationContext, visit_at: datetime
) -> int:
    candidates = map_context_to_scoring_candidates(context, visit_at=visit_at)
    return prepare_candidates(candidates, now=visit_at).eligible_count


async def _measure_pool(
    tool: NearbyPlaceDetailsTool,
    name: str,
    center: tuple[float, float],
    limit: int,
    hour: int,
    visit_date: str,
    radius_km: float,
) -> PoolRow:
    """보충 조회까지 재현해 하드 필터 통과 수를 센다.

    멈추는 조건은 실물과 같다 — `_candidate_pool_exhausted()`를 직접 부르고
    `_MAX_CANDIDATE_REFILL_ATTEMPTS`를 그대로 쓴다.
    """
    visit_at = datetime.fromisoformat(f"{visit_date}T{hour:02d}:00:00").replace(tzinfo=KST)
    seen: set[str] = set()
    eligible_first = 0
    eligible_total = 0
    fetches = 0
    base: NearbyPlaceDetailsResult | None = None

    for attempt in range(_MAX_CANDIDATE_REFILL_ATTEMPTS + 1):
        if attempt > 0 and eligible_total >= limit:
            break
        result = await tool.execute(
            NearbyPlaceDetailsQuery(
                latitude=center[0],
                longitude=center[1],
                search_radius_km=radius_km,
                limit=limit,
                excluded_place_ids=frozenset(seen),
            )
        )
        fetches += 1
        base = base or result
        fresh = tuple(
            place for place in result.places if place.candidate.place_id not in seen
        )
        if not fresh:
            break
        seen |= {place.candidate.place_id for place in fresh}

        batch_context = _to_context(name, center, replace(base, places=fresh))
        batch_eligible = _eligible_count(batch_context, visit_at)
        eligible_total += batch_eligible
        if attempt == 0:
            eligible_first = batch_eligible

        # 실물과 같은 신호로 멈춘다. 여기서 개수 비교를 새로 만들면 판정이 갈린다.
        if _candidate_pool_exhausted(_to_context(name, center, result)):
            break

    return PoolRow(
        center=name,
        limit=limit,
        hour=hour,
        eligible_first=eligible_first,
        eligible_with_refill=eligible_total,
        candidates=len(seen),
        context_fetches=fetches,
    )


async def run_pool(
    tool: NearbyPlaceDetailsTool,
    limits: Sequence[int],
    hours: Sequence[int],
    visit_date: str,
    radius_km: float,
) -> list[PoolRow]:
    rows: list[PoolRow] = []
    for name, center in _CENTERS.items():
        for limit in limits:
            for hour in hours:
                with _candidate_limit(limit):
                    rows.append(
                        await _measure_pool(
                            tool, name, center, limit, hour, visit_date, radius_km
                        )
                    )
    return rows


def print_pool(rows: Sequence[PoolRow], hours: Sequence[int]) -> None:
    result_limit = settings.recommendation_result_limit
    print(
        f"\n■ 시각별 하드 필터 통과 수 — 괄호는 보충 조회 전, "
        f"* = 결과 {result_limit}곳을 못 채움\n"
    )
    header = f"{'중심점':<9} {'한도':>4} " + " ".join(f"{h:>9}시" for h in hours)
    print(header)
    print("-" * len(header))
    by_key: dict[tuple[str, int], dict[int, PoolRow]] = collections.defaultdict(dict)
    for row in rows:
        by_key[(row.center, row.limit)][row.hour] = row
    for (center, limit), per_hour in by_key.items():
        cells = []
        for hour in hours:
            row = per_hour.get(hour)
            if row is None:
                cells.append(f"{'-':>10}")
                continue
            mark = "*" if row.eligible_with_refill < result_limit else " "
            cells.append(f"{row.eligible_with_refill:>4}({row.eligible_first:>2}){mark}")
        print(f"{center:<9} {limit:>4} " + " ".join(cells))
    print("\n  보충 조회를 뺀 값(괄호)만 보면 밤 시간대를 과소평가한다.")


async def run_more(
    tool: NearbyPlaceDetailsTool,
    limits: Sequence[int],
    turns: int,
    radius_km: float,
) -> list[dict[str, object]]:
    """'더 보기'를 반복하며 턴마다 요청한 개수를 채우는지 본다."""
    rows: list[dict[str, object]] = []
    for name, center in _CENTERS.items():
        for limit in limits:
            seen: set[str] = set()
            for turn in range(1, turns + 1):
                result = await tool.execute(
                    NearbyPlaceDetailsQuery(
                        latitude=center[0],
                        longitude=center[1],
                        search_radius_km=radius_km,
                        limit=limit,
                        excluded_place_ids=frozenset(seen),
                    )
                )
                fresh = [
                    place
                    for place in result.places
                    if place.candidate.place_id not in seen
                ]
                rows.append(
                    {
                        "center": name,
                        "limit": limit,
                        "turn": turn,
                        "seen_before": len(seen),
                        "new": len(fresh),
                        "filled": len(fresh) >= limit,
                        "truncated": CANDIDATE_POOL_TRUNCATED_WARNING in result.warnings,
                    }
                )
                if not fresh:
                    break
                seen |= {place.candidate.place_id for place in fresh}
    return rows


def print_more(rows: Sequence[dict[str, object]]) -> None:
    print("\n■ '더 보기' 턴별 충족 — 3턴까지 꽉 차야 한다\n")
    header = (
        f"{'중심점':<9} {'한도':>4} {'턴':>3} {'이미본곳':>8} "
        f"{'새후보':>7} {'꽉참':>5} {'상한도달경고':>13}"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{row['center']:<9} {row['limit']:>4} {row['turn']:>3} "
            f"{row['seen_before']:>8} {row['new']:>7} "
            f"{'예' if row['filled'] else '아니오':>5} "
            f"{'예' if row['truncated'] else '아니오':>13}"
        )


async def run_calls(limits: Sequence[int], radius_km: float) -> list[dict[str, object]]:
    """상세 출처 x 한도별 외부 호출 수와 소요 시간을 센다.

    한도를 올려도 되는지가 사실상 이 표로 갈린다 — supabase는 후보 수와 무관하게
    배치 1회지만 tour_api는 후보마다 detailCommon2 + detailIntro2가 나간다.
    """
    original = settings.place_details_source
    center = next(iter(_CENTERS.values()))
    rows: list[dict[str, object]] = []
    try:
        for source in ("supabase", "tour_api"):
            for limit in limits:
                settings.place_details_source = source  # type: ignore[assignment]
                calls: collections.Counter[str] = collections.Counter()

                # 반복문 안에서 만드는 훅이라 카운터를 기본 인자로 묶는다
                # (늦은 바인딩이면 마지막 회차 카운터에 전부 쌓인다).
                async def hook(
                    request: httpx.Request,
                    counter: collections.Counter[str] = calls,
                ) -> None:
                    counter[urlsplit(str(request.url)).path.split("/")[-1]] += 1

                async with httpx.AsyncClient(
                    timeout=60.0, event_hooks={"request": [hook]}
                ) as client:
                    tool = NearbyPlaceDetailsTool(
                        get_place_search_provider(client),
                        get_place_details_provider(client),
                    )
                    started = time.perf_counter()
                    result = await tool.execute(
                        NearbyPlaceDetailsQuery(
                            latitude=center[0],
                            longitude=center[1],
                            search_radius_km=radius_km,
                                limit=limit,
                        )
                    )
                    elapsed_ms = (time.perf_counter() - started) * 1000
                rows.append(
                    {
                        "source": source,
                        "limit": limit,
                        "places": len(result.places),
                        "elapsed_ms": round(elapsed_ms),
                        "calls": sum(calls.values()),
                        "detail": dict(calls),
                    }
                )
    finally:
        settings.place_details_source = original
    return rows


def print_calls(rows: Sequence[dict[str, object]]) -> None:
    print("\n■ 상세 출처별 외부 호출 수 (장소 조회 1회 기준)\n")
    header = f"{'출처':<10} {'한도':>4} {'후보':>5} {'소요ms':>8} {'호출':>5}  내역"
    print(header)
    print("-" * (len(header) + 30))
    for row in rows:
        print(
            f"{row['source']:<10} {row['limit']:>4} {row['places']:>5} "
            f"{row['elapsed_ms']:>8} {row['calls']:>5}  {row['detail']}"
        )
    print("\n  tour_api는 후보 1곳당 2회다. 일일 한도가 오퍼레이션별 1000이라")
    print("  후보 30이면 33요청 만에 소진된다 — 부팅 검증이 이 조합을 막는 이유다.")


def write_csv(pool: Sequence[PoolRow], more: Sequence[dict[str, object]]) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with RESULTS_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["scope", "center", "limit", "hour_or_turn", "value_a", "value_b", "note"]
        )
        for row in pool:
            writer.writerow(
                [
                    "pool",
                    row.center,
                    row.limit,
                    row.hour,
                    row.eligible_with_refill,
                    row.eligible_first,
                    f"candidates={row.candidates} fetches={row.context_fetches}",
                ]
            )
        for row in more:
            writer.writerow(
                [
                    "more",
                    row["center"],
                    row["limit"],
                    row["turn"],
                    row["new"],
                    row["seen_before"],
                    f"filled={row['filled']} truncated={row['truncated']}",
                ]
            )
    print(f"\n결과를 {RESULTS_CSV}에 남겼다.")


def _validated_limits(values: Sequence[int]) -> tuple[int, ...]:
    for value in values:
        if not MIN_RECOMMENDATION_LIMIT <= value <= MAX_RECOMMENDATION_CANDIDATE_LIMIT:
            raise SystemExit(
                f"--limits는 {MIN_RECOMMENDATION_LIMIT} 이상 "
                f"{MAX_RECOMMENDATION_CANDIDATE_LIMIT} 이하여야 합니다(받은 값 {value}). "
                "상한을 넘겨 재려면 recommendation_limits.py의 상수를 먼저 올리세요 — "
                "그 상한이 계약 스키마와 부팅 검증에도 걸려 있어 우회로 재면 실물과 갈립니다."
            )
    return tuple(values)


async def main_async(args: argparse.Namespace) -> None:
    limits = _validated_limits(args.limits)
    pool: list[PoolRow] = []
    more: list[dict[str, object]] = []

    if args.scope in ("all", "pool", "more"):
        async with httpx.AsyncClient(timeout=60.0) as client:
            tool = NearbyPlaceDetailsTool(
                get_place_search_provider(client), get_place_details_provider(client)
            )
            if args.scope in ("all", "pool"):
                pool = await run_pool(
                    tool, limits, _HOURS, args.visit_date, args.radius_km
                )
                print_pool(pool, _HOURS)
            if args.scope in ("all", "more"):
                more = await run_more(tool, limits, args.turns, args.radius_km)
                print_more(more)

    if args.scope in ("all", "calls"):
        print_calls(await run_calls(limits, args.radius_km))

    if pool or more:
        write_csv(pool, more)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scope", choices=("all", "pool", "more", "calls"), default="all"
    )
    parser.add_argument("--limits", type=int, nargs="+", default=list(_DEFAULT_LIMITS))
    parser.add_argument("--visit-date", default=_DEFAULT_VISIT_DATE)
    parser.add_argument("--radius-km", type=float, default=_DEFAULT_RADIUS_KM)
    parser.add_argument("--turns", type=int, default=_DEFAULT_MORE_TURNS)
    args = parser.parse_args()

    print(
        f"방문일 {args.visit_date} / 반경 {args.radius_km}km / 한도 {args.limits} / "
        f"결과 개수 {settings.recommendation_result_limit} / "
        f"상세 출처 {settings.resolved_place_details_source}"
    )
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
