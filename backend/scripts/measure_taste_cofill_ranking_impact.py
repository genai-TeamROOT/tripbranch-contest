"""동행 co-fill이 **실제 카드 순서**를 얼마나 흔드는지 실측한다.

배경: `recommend.extract` 2.4.0이 동행 표현을 `taste_query`에 남기게 바꿨고,
검색 단계는 `measure_taste_condition_dominance.py --scope companion`으로 쟀다
(취향이 묻힌 조합 0/6, 선택성은 수용된 혼합보다 낮음). 하지만 **끝단까지의
영향은 미측정으로 남아 있었다** — taste는 5축 중 가중치 0.15고, 컷 아래로
내려간 곳은 0점으로 묶여 순위 기여가 줄어든다. package_D
"[기록] 취향 근거 RAG 검색과 Scoring 반영.md" 11.7절의 열린 이슈다.

방법: **taste_matches만 바꿔서 같은 후보를 두 번 채점한다.**

`score_prepared_candidates()`가 `taste_matches`를 명시적 인자로 받으므로
(TECH-02: A가 D 진입점에 값을 넘긴다), 프롬프트를 되돌리거나 서버를 띄우지
않고도 두 버전을 비교할 수 있다.

  A(2.3.1 상당) = 취향 단독 질의  "조용한 카페"
  B(2.4.0 상당) = 동행+취향 복합  "아이들이랑 가기 좋은 조용한 카페"

같은 `prepared` 후보를 쓰므로 **날씨·운영시간·거리 축은 구조적으로 동일**하고,
차이는 전부 taste에서 온다. 임베딩·RPC는 결정적이라 실행마다 같은 값이 나온다
(LLM은 부르지 않는다 — 두 질의는 이미 실측된 상수다).

**판정 기준이 없다 — 크기만 잰다.** 어느 카드 순서가 "더 좋은지"는 정답
데이터가 없다. 품질 판단은 검색 단계에서 인용문을 읽어 이미 했다. 이 측정이
답하는 것은 **"이 변경이 사용자에게 보이는 결과를 실제로 바꾸는가"** 하나다.
순서가 거의 안 바뀌면 이 논쟁 전체가 저위험이었다는 뜻이고, 많이 바뀌면
11.7절의 나머지 미측정 항목(중심점 1곳, 조합 6개)도 닫아야 한다.

실행: `cd backend && .venv/bin/python -m scripts.measure_taste_cofill_ranking_impact`
sentence-transformers 필요: `pip install -e ".[embeddings]"`.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from app.agent_context.schemas import ContextValue as AgentContextValue
from app.agent_context.schemas import Coordinates as AgentCoordinates
from app.agent_context.schemas import PlaceCandidate as AgentPlaceCandidate
from app.agent_context.schemas import (
    RecommendationContext,
    ResolvedLocation,
    WeatherForecast,
)
from app.config import Settings
from app.domain.models import PlaceEvidenceMatch
from app.domain.scoring import RankedCandidate, score_prepared_candidates
from app.providers.place_evidence import PlaceEvidenceProvider
from app.providers.place_evidence_encoder import get_shared_encoder
from app.repositories.supabase_places import SupabasePlaceRepository
from app.services.recommendation_pipeline import prepare_recommendation_from_context

_RPC_TIMEOUT_SECONDS = 90.0
_PAGE_SIZE = 1000
_MAX_CANDIDATES = 500
_RADIUS_KM = 3.0
_TOP_N = 5

# 경복궁 — 검색 단계 측정과 같은 기준점이라 두 측정을 나란히 읽을 수 있다.
_CENTER = (37.579617, 126.977041)
_CENTER_NAME = "경복궁"

# 평일 낮. 운영시간 축이 대부분의 후보에서 살아 있도록 고른 시각이다 —
# 심야로 잡으면 후보가 폐점으로 대량 탈락해 순위 비교 자체가 무의미해진다.
_VISIT_AT = datetime(2026, 8, 25, 14, 0, tzinfo=ZoneInfo("Asia/Seoul"))

_CAFE_SMALL_CODE = "FD050100"
_RESTAURANT_CONTENT_TYPE_ID = "39"
_ATTRACTION_CONTENT_TYPE_ID = "12"

# TourAPI 대분류 → PlaceCandidate.category. mappers._CONTENT_TYPE_TO_CATEGORY와
# 같은 값이어야 실내외 판정이 실서비스와 어긋나지 않는다.
_CONTENT_TYPE_TO_CATEGORY: dict[str, str] = {
    "12": "attraction",
    "14": "cultural_facility",
    "15": "festival",
    "28": "leisure",
    "38": "shopping",
    "39": "restaurant",
}


@dataclass(frozen=True)
class Combo:
    """`measure_taste_condition_dominance.py`의 조합과 같은 6쌍이다.

    두 arm은 항상 **"홀로 쓴 질의" 대 "덧붙인 질의"**다.

      A = `"{taste} {label}"`              — 덧붙이기 전
      B = `"{prefix}{joiner}{taste} {label}"` — 덧붙인 뒤

    동행 scope에서 prefix는 동행 표현(조사 없이 이어 붙인다), 취향 scope에서는
    또 다른 취향어("A하고 B")다. **구조가 같아야 널 기준선으로 쓸 수 있다.**
    """

    category: str
    label: str  # _enrich_taste_query()가 붙이는 place_tag/place_type 라벨
    prefix: str
    taste: str
    joiner: str = " "

    def query_before(self) -> str:
        return f"{self.taste} {self.label}"

    def query_after(self) -> str:
        return f"{self.prefix}{self.joiner}{self.taste} {self.label}"


_COMPANION_COMBOS: tuple[Combo, ...] = (
    Combo("카페", "카페", "아이들이랑 가기 좋은", "조용한"),
    Combo("카페", "카페", "친구들이랑 가기 좋은", "아늑한"),
    Combo("음식점", "맛집", "부모님이랑 갈 만한", "분위기 좋은"),
    Combo("음식점", "맛집", "아이들이랑 가기 좋은", "조용한"),
    Combo("관광지", "관광지", "아이들이랑 가기 좋은", "볼거리 많은"),
    Combo("관광지", "관광지", "혼자 가기 좋은", "조용한"),
)

# 널 기준선. TP-128이 "조건 지배는 구조 문제가 아니다"로 결론낸, **이미 수용된**
# 취향+취향 혼합이다. 순위가 원래 얼마나 흔들리는지를 여기서 얻는다 — 동행
# 수치만 보고 "많이 바뀐다"고 말하면 안 된다(선택성 지표에서 같은 함정을 겪었다).
#
# prefix/taste 순서가 dominance 스크립트의 condition_a/condition_b와 반대인 것에
# 주의한다. 여기서는 **덧붙는 쪽이 prefix**여야 arm 구조가 동행 scope와 같아진다.
_TASTE_COMBOS: tuple[Combo, ...] = (
    Combo("카페", "카페", "조용한하고", "감성적인"),
    Combo("카페", "카페", "아늑한하고", "사진 찍기 좋은"),
    Combo("음식점", "맛집", "조용한하고", "분위기 좋은"),
    Combo("음식점", "맛집", "한적한하고", "가족끼리 가기 좋은"),
    Combo("관광지", "관광지", "고즈넉한하고", "볼거리 많은"),
    Combo("관광지", "관광지", "조용한하고", "화려한"),
)

RESULTS_DIR = Path(__file__).resolve().parents[1] / "test_results"
# scope별로 파일을 나눈다 — 한쪽을 다시 돌릴 때 다른 쪽 원자료를 덮으면 안 된다.
RESULTS_CSV = RESULTS_DIR / "taste_cofill_ranking_impact.csv"
TASTE_RESULTS_CSV = RESULTS_DIR / "taste_cofill_ranking_impact_null.csv"


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
        * math.sin(d_lng / 2) ** 2
    )
    return radius * 2 * math.asin(math.sqrt(a))


async def _fetch_places(client: httpx.AsyncClient) -> list[dict[str, object]]:
    """활성 장소를 전부 읽는다(PostgREST가 한 번에 1000행까지만 준다).

    운영시간 원문(`operating_hours_raw`/`rest_date_raw`)까지 함께 읽는다 —
    하드 필터와 운영시간 Feature가 그 값을 쓰므로, 빼면 축이 결측이 되어
    가중치가 재분배되고 실서비스와 다른 순위가 나온다.
    """
    columns = (
        "content_id,title,latitude,longitude,lcls_systm1,lcls_systm2,lcls_systm3,"
        "content_type_id,operating_hours_raw,rest_date_raw"
    )
    places: list[dict[str, object]] = []
    offset = 0
    while True:
        response = await client.get(
            "/rest/v1/places",
            params={
                "select": columns,
                "is_active": "eq.true",
                "limit": str(_PAGE_SIZE),
                "offset": str(offset),
            },
        )
        response.raise_for_status()
        batch = response.json()
        for row in batch:
            if row.get("content_id") and row.get("latitude") is not None:
                places.append(row)
        if len(batch) < _PAGE_SIZE:
            return places
        offset += _PAGE_SIZE


def _keep_for(category: str):  # noqa: ANN202 - 지역 헬퍼
    if category == "카페":
        return lambda p: str(p.get("lcls_systm3") or "") == _CAFE_SMALL_CODE
    if category == "음식점":
        return lambda p: str(p.get("content_type_id") or "") == _RESTAURANT_CONTENT_TYPE_ID
    if category == "관광지":
        return lambda p: str(p.get("content_type_id") or "") == _ATTRACTION_CONTENT_TYPE_ID
    raise ValueError(f"알 수 없는 카테고리: {category}")


def _candidates_for(
    places: list[dict[str, object]], category: str
) -> list[dict[str, object]]:
    keep = _keep_for(category)
    matched = [
        (_haversine_km(_CENTER[0], _CENTER[1], float(p["latitude"]), float(p["longitude"])), p)
        for p in places
        if keep(p)
    ]
    matched = [(d, p) for d, p in matched if d <= _RADIUS_KM]
    matched.sort(key=lambda entry: entry[0])
    return [p for _, p in matched[:_MAX_CANDIDATES]]


def _to_agent_place(row: dict[str, object]) -> AgentPlaceCandidate:
    content_type_id = str(row.get("content_type_id") or "")
    return AgentPlaceCandidate(
        place_id=str(row["content_id"]),
        name=str(row.get("title") or row["content_id"]),
        category=_CONTENT_TYPE_TO_CATEGORY.get(content_type_id, "unknown"),
        lcls_systm1=str(row.get("lcls_systm1") or "") or None,
        lcls_systm2=str(row.get("lcls_systm2") or "") or None,
        lcls_systm3=str(row.get("lcls_systm3") or "") or None,
        location=AgentCoordinates(
            latitude=float(row["latitude"]), longitude=float(row["longitude"])
        ),
        operating_hours_raw=str(row.get("operating_hours_raw") or "") or None,
        rest_date_raw=str(row.get("rest_date_raw") or "") or None,
    )


def _build_context(rows: list[dict[str, object]]) -> RecommendationContext:
    """실 장소로 Context를 만든다.

    날씨는 **맑음으로 고정**한다. 두 arm에 같은 값이 들어가므로 차이에는
    영향이 없고, 축을 결측으로 두면 가중치가 재분배돼(`build_weights`) 실서비스
    가중치 구성과 달라진다 — 그러면 taste 0.15의 상대적 크기가 왜곡된다.
    """
    return RecommendationContext(
        location=AgentContextValue(
            status="success",
            data=ResolvedLocation(
                requested_query=_CENTER_NAME,
                resolved_name=_CENTER_NAME,
                source="query",
                location=AgentCoordinates(latitude=_CENTER[0], longitude=_CENTER[1]),
            ),
        ),
        weather=AgentContextValue(
            status="success",
            data=WeatherForecast(
                forecast_for=_VISIT_AT, precipitation="none", sky="clear"
            ),
        ),
        places=AgentContextValue(
            status="success", data=[_to_agent_place(row) for row in rows]
        ),
    )


@dataclass(frozen=True)
class ImpactRow:
    category: str
    prefix: str  # 덧붙인 말(동행 표현 또는 다른 취향어)
    taste: str
    eligible: int
    # 두 arm의 상위 N곳 비교.
    top_n_kept: int  # 그대로 남은 곳 수
    top_n_changed: int  # 바뀐 곳 수 (= _TOP_N - kept)
    top1_changed: bool
    # 순위 이동 폭 — A의 상위 N곳이 B에서 몇 칸 움직였나(전체 순위 기준).
    max_rank_shift: int
    mean_rank_shift: float
    # taste Feature가 실제로 켜진 후보 수(컷을 넘어 근거가 있는 곳).
    taste_hits_a: int
    taste_hits_b: int


def _ranked_ids(ranked: Sequence[RankedCandidate]) -> list[str]:
    return [entry.place_id for entry in ranked]


async def run(delay: float, combos: tuple[Combo, ...]) -> list[ImpactRow]:
    settings = Settings()
    if not settings.supabase_url or not settings.supabase_secret_key:
        raise ValueError("SUPABASE_URL / SUPABASE_SECRET_KEY가 필요합니다.")

    encoder = get_shared_encoder()
    encoder.warmup()
    headers = {
        "apikey": settings.supabase_secret_key,
        "Authorization": f"Bearer {settings.supabase_secret_key}",
    }

    rows: list[ImpactRow] = []
    async with httpx.AsyncClient(
        base_url=settings.supabase_url.rstrip("/"),
        headers=headers,
        timeout=_RPC_TIMEOUT_SECONDS,
    ) as client:
        places = await _fetch_places(client)
        repository = SupabasePlaceRepository(
            supabase_url=settings.supabase_url,
            secret_key=settings.supabase_secret_key,
            client=client,
            timeout_seconds=settings.external_api_timeout_seconds,
        )
        # 컷은 Provider 기본값(DEFAULT_MIN_SIMILARITY)을 그대로 쓴다 — 실서비스와
        # 같은 taste_matches를 만들어야 순위 비교가 의미를 갖는다. 검색 단계
        # 측정이 0.0을 쓴 것과 다른 이유다(그쪽은 분포를 봐야 했다).
        provider = PlaceEvidenceProvider(encoder, repository)

        for combo in combos:
            candidate_rows = _candidates_for(places, combo.category)
            if len(candidate_rows) < _TOP_N:
                print(f"[건너뜀] {combo.category}: 후보 {len(candidate_rows)}곳뿐")
                continue

            context = _build_context(candidate_rows)
            prepared = await prepare_recommendation_from_context(
                context, visit_at=_VISIT_AT
            )
            eligible = prepared.preparation.eligible_candidates
            if len(eligible) < _TOP_N:
                print(
                    f"[건너뜀] {combo.category}: 하드 필터 통과 {len(eligible)}곳뿐 "
                    f"(후보 {len(candidate_rows)}곳)"
                )
                continue

            eligible_ids = [c.candidate.place_id for c in eligible]
            query_a = combo.query_before()
            query_b = combo.query_after()

            result_a = await provider.search(query_a, eligible_ids)
            await asyncio.sleep(delay)
            result_b = await provider.search(query_b, eligible_ids)
            await asyncio.sleep(delay)

            matches_a: dict[str, PlaceEvidenceMatch] = dict(result_a.data)
            matches_b: dict[str, PlaceEvidenceMatch] = dict(result_b.data)

            scoring_a = score_prepared_candidates(
                eligible,
                weather_condition=prepared.weather_condition,
                weather_reason=prepared.weather_reason,
                max_distance_km=_RADIUS_KM,
                requested_environment=prepared.requested_environment,
                taste_matches=matches_a,
            )
            scoring_b = score_prepared_candidates(
                eligible,
                weather_condition=prepared.weather_condition,
                weather_reason=prepared.weather_reason,
                max_distance_km=_RADIUS_KM,
                requested_environment=prepared.requested_environment,
                taste_matches=matches_b,
            )

            ids_a = _ranked_ids(scoring_a.ranked)
            ids_b = _ranked_ids(scoring_b.ranked)
            top_a, top_b = ids_a[:_TOP_N], ids_b[:_TOP_N]
            kept = sum(1 for pid in top_a if pid in set(top_b))

            rank_b = {pid: i for i, pid in enumerate(ids_b)}
            shifts = [abs(rank_b[pid] - i) for i, pid in enumerate(top_a) if pid in rank_b]

            rows.append(
                ImpactRow(
                    category=combo.category,
                    prefix=combo.prefix,
                    taste=combo.taste,
                    eligible=len(eligible),
                    top_n_kept=kept,
                    top_n_changed=_TOP_N - kept,
                    top1_changed=top_a[0] != top_b[0],
                    max_rank_shift=max(shifts) if shifts else 0,
                    mean_rank_shift=(sum(shifts) / len(shifts)) if shifts else 0.0,
                    taste_hits_a=len(matches_a),
                    taste_hits_b=len(matches_b),
                )
            )
    return rows


def _print(rows: list[ImpactRow]) -> None:
    c_width = max([6, *(len(r.prefix) for r in rows)]) if rows else 12
    header = (
        f"{'카테고리':<8} {'덧붙임':<{c_width}} {'취향':<10} {'통과':>4} "
        f"{'상위5 유지':>8} {'변경':>4} {'1등바뀜':>7} "
        f"{'최대이동':>8} {'평균이동':>8} {'근거A':>5} {'근거B':>5}"
    )
    print()
    print(header)
    print("-" * len(header))
    for r in rows:
        print(
            f"{r.category:<8} {r.prefix:<{c_width}} {r.taste:<10} {r.eligible:>4} "
            f"{r.top_n_kept:>8} {r.top_n_changed:>4} {'예' if r.top1_changed else '아니오':>7} "
            f"{r.max_rank_shift:>8} {r.mean_rank_shift:>8.1f} "
            f"{r.taste_hits_a:>5} {r.taste_hits_b:>5}"
        )
    print()
    if not rows:
        print("측정된 조합이 없다.")
        return
    changed = sum(r.top_n_changed for r in rows)
    top1 = sum(1 for r in rows if r.top1_changed)
    print(
        f"상위 {_TOP_N}곳 변경 합계 {changed}건 / {len(rows) * _TOP_N}칸 "
        f"({changed / (len(rows) * _TOP_N):.0%}) · 1등이 바뀐 조합 {top1}/{len(rows)}"
    )
    print(
        "⚠️ 이 수치는 **크기**다. 어느 순서가 더 좋은지는 정답 데이터가 없어 "
        "판정하지 않는다 — 품질 판단은 검색 단계 인용문으로 했다."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delay", type=float, default=1.0, help="RPC 호출 간 대기(초)")
    parser.add_argument(
        "--scope",
        choices=("companion", "taste"),
        default="companion",
        help=(
            "companion=동행 co-fill 영향(기본), "
            "taste=이미 수용된 취향+취향 혼합으로 잡는 널 기준선"
        ),
    )
    args = parser.parse_args()

    companion = args.scope == "companion"
    combos = _COMPANION_COMBOS if companion else _TASTE_COMBOS
    target_csv = RESULTS_CSV if companion else TASTE_RESULTS_CSV

    rows = asyncio.run(run(args.delay, combos))
    _print(rows)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with target_csv.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.writer(fp)
        writer.writerow(
            [
                "category",
                "prefix",
                "taste",
                "eligible",
                "top_n_kept",
                "top_n_changed",
                "top1_changed",
                "max_rank_shift",
                "mean_rank_shift",
                "taste_hits_a",
                "taste_hits_b",
            ]
        )
        for r in rows:
            writer.writerow(
                [
                    r.category,
                    r.prefix,
                    r.taste,
                    r.eligible,
                    r.top_n_kept,
                    r.top_n_changed,
                    int(r.top1_changed),
                    r.max_rank_shift,
                    f"{r.mean_rank_shift:.2f}",
                    r.taste_hits_a,
                    r.taste_hits_b,
                ]
            )
    print(f"\n결과 저장: {target_csv}")


if __name__ == "__main__":
    main()
