"""복합 취향 발화에서 한 조건이 다른 조건을 누르는 문제가 재현되는지 실측한다.

배경: "조용한 저렴한 카페"를 재봤더니 "조용한 카페" 단독 검색과 순위상관이
0.978이었다(경복궁 카페 한 곳, TP-128 착수 근거). 그런데 "저렴한"은 실제로는
taste_query가 아니라 budget 필드로 가는 말이라, 이 결과가 진짜 "취향 축 안에서
조건이 서로 누르는 문제"를 보여주는지 아니면 애초에 taste_query에 안 들어가는
말이라 안 보인 것뿐인지 구분이 안 됐다. 실제로 taste_query에 함께 들어가는
두 조건으로, 다른 카테고리에서도 같은 폭으로 재현되는지 확인한다.

방법: 카페·음식점·관광지 3개 카테고리 x 조건 조합 2개(카테고리당)를 실 LLM으로
뽑은 taste_query 문자열로 잰다. 각 조합에서
  - 복합 질의(A+B)
  - 조건 A 단독
  - 조건 B 단독
세 질의를 모두 `_enrich_taste_query()`와 같은 방식(taste_query + place_tag/
place_type 라벨)으로 보강해 실 RPC로 검색한다.

판정 지표는 **상위 5곳 중첩**이다. 사용자에게 실제로 보이는 건 상위 5곳이고,
복합 질의의 상위 5곳이 어느 한쪽 단독 질의의 상위 5곳을 그대로 베끼면 그
조건이 다른 조건을 덮은 것이다.

실패 기준: 6개 조합 중 과반에서 `overlap_a`와 `overlap_b` 중 큰 쪽이 5/5이면
"조건 지배가 카테고리를 넘어 일반적으로 재현된다"고 본다.

참고 지표 두 개 — 결론을 가르지 않는다:
  - 컷(0.43) 통과 수 — 복합이 단독보다 낮으면 그 자체로 손해다
  - 순위상관 ρ(복합, 조건A만) vs ρ(복합, 조건B만)
    ⚠️ **ρ를 판정에 쓰지 않는 이유**: 후보 수백 곳 전체를 넣고 재는 값이라
    사용자가 보지 않는 꼬리쪽 다수가 결과를 좌우한다. 실제로 이 축에서는
    ρ 0.9대가 정상 범위라, 높은 ρ 하나만 보고 "거의 같다 = 지배한다"고 읽으면
    틀린다. 이 측정의 착수 근거였던 ρ=0.978이 정확히 그 오독이었다.

동행 scope (`--scope companion`, 2026-08-24 추가)
------------------------------------------------
`recommend.extract` 2.4.0이 동행 표현을 `taste_query`에 함께 남기도록 바꿨다
("아이들이랑 가기 좋은 조용한 카페" → `taste_query="아이들이랑 가기 좋은 조용한"`).

**TP-128은 이 안을 "희석 측정 때문에 택하지 않는다"고 적고 닫혔다.** 그런데 그
근거는 발화 전체를 질의로 넣었을 때의 순위상관(ρ 0.88~0.97,
`_enrich_taste_query()` docstring)이고, ρ는 **같은 카드가 위에서 판정에 쓰지
말라고 결론낸 지표**다. 즉 기각 근거가 상위권 기준으로는 한 번도 검증되지
않았다. 그래서 동행 표현을 조건 A로 놓고 같은 상위 5곳 기준으로 다시 잰다.

이 scope에서 A는 동행 표현, B는 취향 표현이다. 복합 질의는 조사를 끼우지 않고
그대로 이어 붙인다(`connector=" "`) — extract.md가 실제로 만드는 문자열이다.
동행 문구는 전부 `verify_taste_query_extraction.py` 2.4.0 검증에서 실 LLM이
실제로 뽑은 `taste_query` 값이다.

실패 기준 — 둘 중 하나면 co-fill이 손해다:
  (a) 복합의 컷 통과 수가 **취향 단독보다 낮다.** 근거를 오히려 잃는 것이므로
      `_TASTE_QUERY_EXCLUDED_TAGS`에서 축제를 뺀 것과 같은 기준이다.
  (b) 복합 상위 5곳이 **동행 단독과 5/5 일치하면서 취향 단독과 0/5.** 취향이
      완전히 묻힌 경우다.

둘 다 아니면 유지한다. 다만 (b)에 가까운 조합(동행 겹침 >= 4, 취향 겹침 <= 1)은
`--quotes`로 인용문을 직접 읽어 동행 근거가 실재하는지 확인한다 — 수치만으로
"묻혔다/아니다"를 가르지 않는다(`_PLACE_TYPE_TASTE_LABELS`가 수치 1등인
"박물관"을 안 고른 것과 같은 이유).

**동행 겹침이 높은 것 자체는 실패가 아니다.** "아이들과 가기 좋아요"는 리뷰가
실제로 쓰는 장소 서술이라 그 근거를 당겨오는 건 요청에 부합한다 — 위치·어투가
매칭되던 발화 전체 희석과 성격이 다르다. 그래서 (b)는 취향이 **완전히** 사라진
경우로만 좁혀 뒀다.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import math
import statistics
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import Settings
from app.domain.models import PlaceEvidenceMatch
from app.providers.place_evidence import DEFAULT_MIN_SIMILARITY, PlaceEvidenceProvider
from app.providers.place_evidence_encoder import get_shared_encoder
from app.repositories.supabase_places import SupabasePlaceRepository

_RPC_TIMEOUT_SECONDS = 90.0
_ANCHOR = (37.579617, 126.977041)  # 경복궁 — 상한 초과 시 자르는 기준점
_PAGE_SIZE = 1000
_MAX_CANDIDATES = 500
_MIN_MATCHED = 5

_CAFE_SMALL_CODE = "FD050100"
_RESTAURANT_CONTENT_TYPE_ID = "39"
_ATTRACTION_CONTENT_TYPE_ID = "12"

# 전부 실 LLM(gemini-3.5-flash-lite)이 "<A>하고 <B> <카테고리> 추천해줘"에서
# 실제로 뽑은 taste_query다 — budget/companion으로 안 새는 것을 확인했다
# (2026-08-24, scripts 실행 로그 참고). "라벨"은 _enrich_taste_query()가
# 실제로 붙이는 place_tag/place_type 라벨과 같다.
@dataclass(frozen=True)
class ConditionCombo:
    category: str
    label: str  # 검색 질의에 붙는 place_tag/place_type 라벨
    condition_a: str
    condition_b: str
    # 복합 질의에서 두 조건을 잇는 말. 취향 두 개는 발화가 "A하고 B"로 오지만,
    # 동행 표현은 extract.md가 조사 없이 그대로 이어 붙인 문자열을 만든다
    # ("아이들이랑 가기 좋은 조용한"). 실제로 나가는 질의와 같아야 한다.
    connector: str = "하고 "


_COMBOS: tuple[ConditionCombo, ...] = (
    ConditionCombo("카페", "카페", "조용한", "감성적인"),
    ConditionCombo("카페", "카페", "아늑한", "사진 찍기 좋은"),
    ConditionCombo("음식점", "맛집", "조용한", "분위기 좋은"),
    ConditionCombo("음식점", "맛집", "한적한", "가족끼리 가기 좋은"),
    ConditionCombo("관광지", "관광지", "고즈넉한", "볼거리 많은"),
    ConditionCombo("관광지", "관광지", "조용한", "화려한"),
)

# A는 동행 표현, B는 취향 표현이다(docstring "동행 scope" 참고). 동행 문구는
# 전부 verify_taste_query_extraction.py 2.4.0 검증에서 실 LLM이 실제로 뽑은
# taste_query 값이다 — 시스템이 만들지 않는 질의로 재면 안 된다.
#
# "혼자 가기 좋은"은 동행값이면서 이미 취향 축으로도 쓰이는 말이라
# (`_TASTE_QUERY_EXCLUDED_TAGS` 주석의 6축 중 하나) 대조군 역할을 한다.
_COMPANION_COMBOS: tuple[ConditionCombo, ...] = (
    ConditionCombo("카페", "카페", "아이들이랑 가기 좋은", "조용한", connector=" "),
    ConditionCombo("카페", "카페", "친구들이랑 가기 좋은", "아늑한", connector=" "),
    ConditionCombo("음식점", "맛집", "부모님이랑 갈 만한", "분위기 좋은", connector=" "),
    ConditionCombo("음식점", "맛집", "아이들이랑 가기 좋은", "조용한", connector=" "),
    ConditionCombo("관광지", "관광지", "아이들이랑 가기 좋은", "볼거리 많은", connector=" "),
    ConditionCombo("관광지", "관광지", "혼자 가기 좋은", "조용한", connector=" "),
)

RESULTS_DIR = Path(__file__).resolve().parents[1] / "test_results"
# scope별로 파일을 나눈다 — 판정 기준이 다르고, 한쪽을 다시 돌릴 때 다른 쪽
# 원자료를 덮어쓰면 안 된다.
RESULTS_CSV = RESULTS_DIR / "taste_condition_dominance.csv"
COMPANION_RESULTS_CSV = RESULTS_DIR / "taste_companion_cofill.csv"


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
    """활성 장소를 전부 읽는다(PostgREST가 한 번에 1000행까지만 준다)."""
    places: list[dict[str, object]] = []
    offset = 0
    while True:
        response = await client.get(
            "/rest/v1/places",
            params={
                "select": "content_id,latitude,longitude,lcls_systm3,content_type_id",
                "is_active": "eq.true",
                "limit": str(_PAGE_SIZE),
                "offset": str(offset),
            },
        )
        response.raise_for_status()
        batch = response.json()
        for row in batch:
            content_id, lat, lng = row.get("content_id"), row.get("latitude"), row.get("longitude")
            if content_id and lat is not None and lng is not None:
                places.append(
                    {
                        "content_id": str(content_id),
                        "latitude": float(lat),
                        "longitude": float(lng),
                        "lcls_systm3": row.get("lcls_systm3"),
                        "content_type_id": str(row.get("content_type_id") or ""),
                    }
                )
        if len(batch) < _PAGE_SIZE:
            return places
        offset += _PAGE_SIZE


def _candidates_for(places: list[dict[str, object]], category: str) -> list[str]:
    if category == "카페":
        keep = lambda p: p["lcls_systm3"] == _CAFE_SMALL_CODE  # noqa: E731
    elif category == "음식점":
        keep = lambda p: p["content_type_id"] == _RESTAURANT_CONTENT_TYPE_ID  # noqa: E731
    elif category == "관광지":
        keep = lambda p: p["content_type_id"] == _ATTRACTION_CONTENT_TYPE_ID  # noqa: E731
    else:
        raise ValueError(f"알 수 없는 카테고리: {category}")

    matched = [
        (_haversine_km(_ANCHOR[0], _ANCHOR[1], p["latitude"], p["longitude"]), p)
        for p in places
        if keep(p)
    ]
    matched.sort(key=lambda entry: entry[0])
    return [str(p["content_id"]) for _, p in matched[:_MAX_CANDIDATES]]


def _spearman(a: dict[str, float], b: dict[str, float]) -> float:
    keys = sorted(set(a) & set(b))
    if len(keys) < 4:
        return float("nan")

    def ranks(scores: dict[str, float]) -> dict[str, int]:
        order = sorted(keys, key=lambda k: -scores[k])
        return {k: i for i, k in enumerate(order)}

    ra, rb = ranks(a), ranks(b)
    xs = [ra[k] for k in keys]
    ys = [rb[k] for k in keys]
    mean_x, mean_y = statistics.mean(xs), statistics.mean(ys)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    denominator = math.sqrt(
        sum((x - mean_x) ** 2 for x in xs) * sum((y - mean_y) ** 2 for y in ys)
    )
    return numerator / denominator if denominator else float("nan")


_TOP_N = 5

# 선택성(하위 4분위 하락 - 상위 4분위 하락)의 널 기준선. **측정해서 얻은 값이다** —
# `--scope taste`의 취향+취향 6조합 선택성 평균(2026-08-24, 경복궁):
# 0.0419 / 0.0337 / 0.0254 / 0.0611 / 0.0202 / 0.0478 → 평균 0.0384, 양수 6/6.
#
# 그 6조합은 TP-128이 "조건 지배는 구조 문제가 아니다"로 결론낸 조합이다. 즉
# **수용된 혼합에서도 이만큼은 나온다.** 처음엔 임계를 0.02로 임의로 잡았는데
# 기준선보다 낮아서, 수용된 조합조차 6/6 "희석"으로 찍히는 값이었다.
_SELECTIVITY_NULL_BASELINE = 0.0384
# 널 기준선의 실측 범위. 평균 초과만 보면 과대 경보가 난다 — 널 분포 안에 드는지도
# 함께 봐야 한다.
_SELECTIVITY_NULL_RANGE = (0.0202, 0.0611)


def _top_ids(scores: dict[str, float], n: int = _TOP_N) -> list[str]:
    """유사도 내림차순 상위 n곳. 동점은 content_id로 갈라 실행마다 같은 순서를 준다."""
    return sorted(scores, key=lambda k: (-scores[k], k))[:n]


def _shift_profile(
    compound: dict[str, float], single: dict[str, float]
) -> tuple[float, float, float]:
    """복합 질의가 단독 대비 유사도를 **어떻게** 깎는지 본다.

    컷 통과 수가 줄었다는 사실만으로는 두 가지를 못 가른다.

    1. **균일 이동** — 질의가 길어져 모든 후보의 유사도가 비슷하게 내려간 것.
       고정 컷(0.43) 아래로 밀리는 곳이 생기지만 순위는 보존되므로, 순위 축
       에서는 손해가 아니다.
    2. **선택적 희석** — 취향 근거가 **강한 곳일수록 더 많이** 깎인 것. 이건
       진짜 희석이고, 이 경우 컷 통과 수 감소는 실제 손실 신호다.

    그래서 단독 유사도 기준 상·하위 4분위의 하락폭을 따로 돌려준다.
    `(전체 평균 하락, 상위 4분위 하락, 하위 4분위 하락)`.

    ⚠️ **부호만 보면 안 된다 — 평균 회귀가 섞인다.** 4분위를 `single`로 나누고
    같은 `single`로 델타를 재므로, 희석이 전혀 없어도 상위 4분위가 더 많이
    깎인 것처럼 나온다(상위는 그 질의에 우연히 잘 맞은 곳들이라 다른 질의에서는
    덜 극단적이다). 실제로 **취향+취향 조합에서도 선택성이 6/6 양수**다.
    그래서 절대값이 아니라 `_SELECTIVITY_NULL_BASELINE`과 비교해서 읽는다.
    """
    keys = sorted(set(compound) & set(single))
    if len(keys) < 4:
        return (float("nan"),) * 3
    deltas = {k: compound[k] - single[k] for k in keys}
    by_single = sorted(keys, key=lambda k: -single[k])
    quartile = max(1, len(by_single) // 4)
    top, bottom = by_single[:quartile], by_single[-quartile:]
    return (
        statistics.mean(deltas.values()),
        statistics.mean(deltas[k] for k in top),
        statistics.mean(deltas[k] for k in bottom),
    )


@dataclass(frozen=True)
class DominanceRow:
    category: str
    condition_a: str
    condition_b: str
    candidate_count: int
    passed_compound: int
    passed_a: int
    passed_b: int
    # 판정 지표 — 복합 상위 5곳 중 각 단독 상위 5곳과 겹치는 수.
    overlap_a: int
    overlap_b: int
    # 복합에만 있는 곳. 5 - (a∪b와의 겹침)이라 overlap 합과 일치하지 않을 수 있다.
    novel_in_compound: int
    # 복합이 취향 단독 대비 유사도를 깎는 모양(`_shift_profile`). 컷 통과 수
    # 감소가 균일 이동인지 선택적 희석인지 가르는 값이다.
    shift_mean: float
    shift_top_quartile: float
    shift_bottom_quartile: float
    # 참고 지표 — 꼬리까지 포함한 값이라 판정에 쓰지 않는다(docstring 참고).
    rho_compound_vs_a: float
    rho_compound_vs_b: float
    dominant: str
    gap: float


_QUOTE_CHARS = 90


def _print_quotes(
    title: str,
    top_ids: list[str],
    matches: dict[str, PlaceEvidenceMatch],
    top_a: set[str],
    top_b: set[str],
) -> None:
    """복합 질의 상위 5곳이 **무슨 문장 때문에** 뽑혔는지 그대로 보여준다.

    수치만으로는 동행 겹침이 높은 것이 "정당한 동행 근거"인지 "취향이 묻힌
    것"인지 가를 수 없다 — 그 판단은 인용문을 읽어야 한다.
    """
    print(f"\n  [인용문] {title}")
    for rank, content_id in enumerate(top_ids, start=1):
        match = matches.get(content_id)
        if match is None:
            continue
        where = "".join(("A" if content_id in top_a else "-", "B" if content_id in top_b else "-"))
        snippet = match.snippets[0].source_text.replace("\n", " ") if match.snippets else ""
        if len(snippet) > _QUOTE_CHARS:
            snippet = snippet[:_QUOTE_CHARS] + "…"
        print(
            f"   {rank}. [{where}] {match.place_title} ({match.avg_similarity:.3f}) {snippet}"
        )


async def run(
    delay: float,
    combos: tuple[ConditionCombo, ...] = _COMBOS,
    *,
    quotes: bool = False,
) -> list[DominanceRow]:
    settings = Settings()
    if not settings.supabase_url or not settings.supabase_secret_key:
        raise ValueError("SUPABASE_URL / SUPABASE_SECRET_KEY가 필요합니다.")

    encoder = get_shared_encoder()
    encoder.warmup()
    headers = {
        "apikey": settings.supabase_secret_key,
        "Authorization": f"Bearer {settings.supabase_secret_key}",
    }
    rows: list[DominanceRow] = []
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
        provider = PlaceEvidenceProvider(encoder, repository, min_similarity=0.0)

        for combo in combos:
            candidates = _candidates_for(places, combo.category)
            if len(candidates) < _MIN_MATCHED:
                print(f"[건너뜀] {combo.category}: 후보 {len(candidates)}곳뿐")
                continue

            compound_query = (
                f"{combo.condition_a}{combo.connector}{combo.condition_b} {combo.label}"
            )
            a_query = f"{combo.condition_a} {combo.label}"
            b_query = f"{combo.condition_b} {combo.label}"

            compound = await provider.search(compound_query, candidates)
            await asyncio.sleep(delay)
            a_only = await provider.search(a_query, candidates)
            await asyncio.sleep(delay)
            b_only = await provider.search(b_query, candidates)
            await asyncio.sleep(delay)

            vc = {k: m.avg_similarity for k, m in compound.data.items()}
            va = {k: m.avg_similarity for k, m in a_only.data.items()}
            vb = {k: m.avg_similarity for k, m in b_only.data.items()}
            if len(vc) < _MIN_MATCHED:
                print(f"[건너뜀] {combo.category} '{compound_query}': 매칭 {len(vc)}곳뿐")
                continue

            rho_a = _spearman(vc, va)
            rho_b = _spearman(vc, vb)
            gap = abs(rho_a - rho_b)
            # 취향 단독(B)이 기준선이다 — 2.4.0 전에 나가던 질의가 그것이다.
            shift_mean, shift_top, shift_bottom = _shift_profile(vc, vb)

            top_c = _top_ids(vc)
            top_a, top_b = set(_top_ids(va)), set(_top_ids(vb))
            overlap_a = sum(1 for k in top_c if k in top_a)
            overlap_b = sum(1 for k in top_c if k in top_b)
            novel = sum(1 for k in top_c if k not in top_a and k not in top_b)
            if quotes:
                _print_quotes(compound_query, top_c, compound.data, top_a, top_b)
            # 지배 판정은 상위 5곳 기준이다. 동수면 지배라고 부르지 않는다.
            if overlap_a == overlap_b:
                dominant = "-"
            else:
                dominant = (
                    combo.condition_a if overlap_a > overlap_b else combo.condition_b
                )

            rows.append(
                DominanceRow(
                    category=combo.category,
                    condition_a=combo.condition_a,
                    condition_b=combo.condition_b,
                    candidate_count=len(candidates),
                    passed_compound=sum(1 for v in vc.values() if v >= DEFAULT_MIN_SIMILARITY),
                    passed_a=sum(1 for v in va.values() if v >= DEFAULT_MIN_SIMILARITY),
                    passed_b=sum(1 for v in vb.values() if v >= DEFAULT_MIN_SIMILARITY),
                    overlap_a=overlap_a,
                    overlap_b=overlap_b,
                    novel_in_compound=novel,
                    shift_mean=shift_mean,
                    shift_top_quartile=shift_top,
                    shift_bottom_quartile=shift_bottom,
                    rho_compound_vs_a=rho_a,
                    rho_compound_vs_b=rho_b,
                    dominant=dominant,
                    gap=gap,
                )
            )
    return rows


def _print(rows: list[DominanceRow], *, companion: bool = False) -> None:
    a_label, b_label = ("동행", "취향") if companion else ("조건A", "조건B")
    a_width = max([len(a_label), *(len(r.condition_a) for r in rows)]) if rows else 10
    b_width = max([len(b_label), *(len(r.condition_b) for r in rows)]) if rows else 14
    header = (
        f"{'카테고리':<8} {a_label:<{a_width}} {b_label:<{b_width}} {'후보':>4} "
        f"{'복합':>4} {'A단독':>5} {'B단독':>5} {'겹A':>4} {'겹B':>4} {'신규':>4} "
        f"{'우세':<{a_width}} {'ρ(A)':>6} {'ρ(B)':>6}"
    )
    print()
    print(header)
    print("-" * len(header))
    dominated = 0
    for r in rows:
        print(
            f"{r.category:<8} {r.condition_a:<{a_width}} {r.condition_b:<{b_width}} "
            f"{r.candidate_count:>4} {r.passed_compound:>4} {r.passed_a:>5} "
            f"{r.passed_b:>5} {r.overlap_a:>4} {r.overlap_b:>4} "
            f"{r.novel_in_compound:>4} {r.dominant:<{a_width}} "
            f"{r.rho_compound_vs_a:>6.3f} {r.rho_compound_vs_b:>6.3f}"
        )
        if max(r.overlap_a, r.overlap_b) >= _TOP_N:
            dominated += 1
    print()

    if not companion:
        print(
            f"판정 — 상위 {_TOP_N}곳이 한쪽 단독과 완전히 같은 조합: "
            f"{dominated}/{len(rows)} (과반이면 조건 지배 재현)"
        )
        print("참고 — ρ는 후보 전체 기준이라 꼬리쪽 다수에 좌우된다. 판정에 쓰지 않는다.")
        return

    # 동행 scope — 실패 기준 두 개를 따로 센다(docstring "동행 scope" 참고).
    lost = [r for r in rows if r.passed_compound < r.passed_b]
    buried = [r for r in rows if r.overlap_a >= _TOP_N and r.overlap_b == 0]
    print(f"(a) 복합 컷 통과 < 취향 단독 — 근거를 잃은 조합: {len(lost)}/{len(rows)}")
    for r in lost:
        print(
            f"    {r.category} '{r.condition_a} {r.condition_b}': "
            f"복합 {r.passed_compound} < 취향 단독 {r.passed_b}"
        )
    print(f"(b) 상위 {_TOP_N}곳이 동행 단독과 {_TOP_N}/{_TOP_N} & 취향 단독과 0 — 취향이 묻힌 "
          f"조합: {len(buried)}/{len(rows)}")
    for r in buried:
        print(f"    {r.category} '{r.condition_a} {r.condition_b}'")

    # 컷 통과 수 감소가 균일 이동인지 선택적 희석인지 — `_shift_profile` 참고.
    print()
    print(
        f"{'카테고리':<8} {'동행':<{a_width}} {'Δ평균':>8} "
        f"{'Δ상위4분위':>10} {'Δ하위4분위':>10} {'선택성':>8} {'판정':<12}"
    )
    selective = 0
    for r in rows:
        # 완전히 균일하면 상·하위 4분위 하락폭이 같다. 그 차이가 선택성이다.
        # 판정은 절대값이 아니라 널 기준선과의 비교다(`_shift_profile` 경고 참고).
        selectivity = r.shift_bottom_quartile - r.shift_top_quartile
        is_selective = selectivity > _SELECTIVITY_NULL_BASELINE
        selective += is_selective
        print(
            f"{r.category:<8} {r.condition_a:<{a_width}} {r.shift_mean:>8.4f} "
            f"{r.shift_top_quartile:>10.4f} {r.shift_bottom_quartile:>10.4f} "
            f"{selectivity:>8.4f} {'기준선 초과' if is_selective else '기준선 이하':<12}"
        )
    print(
        f"→ 널 기준선({_SELECTIVITY_NULL_BASELINE:.4f}) 초과 {selective}/{len(rows)}. "
        f"기준선은 이미 '구조 문제 아님'으로 결론난 취향+취향 6조합의 선택성 평균이다 "
        f"(--scope taste). 초과가 없으면 컷 통과 수 감소를 동행 co-fill 탓으로 "
        f"돌릴 수 없다."
    )
    null_lo, null_hi = _SELECTIVITY_NULL_RANGE
    outside = sum(
        1
        for r in rows
        if (r.shift_bottom_quartile - r.shift_top_quartile) > null_hi
    )
    print(
        f"   널 분포 범위 {null_lo:.4f}~{null_hi:.4f} 기준으로는 초과 {outside}/{len(rows)} — "
        f"평균 초과만 세면 과대 경보다."
    )

    watch = [
        r for r in rows if r.overlap_a >= 4 and r.overlap_b <= 1 and r not in buried
    ]
    if watch:
        print(f"인용문 확인 필요(동행 겹침>=4, 취향 겹침<=1): {len(watch)}건 — --quotes로 본다")
        for r in watch:
            print(f"    {r.category} '{r.condition_a} {r.condition_b}'")
    if not lost and not buried:
        print(f"→ (a)·(b) 모두 0건. 2.4.0 동행 co-fill 유지가 이 {len(rows)}개 조합에서 지지된다.")
    print("참고 — ρ는 후보 전체 기준이라 꼬리쪽 다수에 좌우된다. 판정에 쓰지 않는다.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delay", type=float, default=1.0, help="RPC 호출 간 대기(초)")
    parser.add_argument(
        "--scope",
        choices=("taste", "companion"),
        default="taste",
        help="taste=취향+취향 조건 지배(기본), companion=동행 co-fill 검증(2.4.0)",
    )
    parser.add_argument(
        "--quotes",
        action="store_true",
        help=f"복합 질의 상위 {_TOP_N}곳이 무슨 문장으로 뽑혔는지 함께 출력한다",
    )
    args = parser.parse_args()

    companion = args.scope == "companion"
    combos = _COMPANION_COMBOS if companion else _COMBOS
    target_csv = COMPANION_RESULTS_CSV if companion else RESULTS_CSV

    rows = asyncio.run(run(args.delay, combos, quotes=args.quotes))
    _print(rows, companion=companion)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with target_csv.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.writer(fp)
        writer.writerow(
            [
                "category",
                "condition_a",
                "condition_b",
                "candidate_count",
                "passed_compound",
                "passed_a",
                "passed_b",
                "overlap_a",
                "overlap_b",
                "novel_in_compound",
                "dominant",
                "shift_mean",
                "shift_top_quartile",
                "shift_bottom_quartile",
                "rho_compound_vs_a",
                "rho_compound_vs_b",
                "gap",
            ]
        )
        for r in rows:
            writer.writerow(
                [
                    r.category,
                    r.condition_a,
                    r.condition_b,
                    r.candidate_count,
                    r.passed_compound,
                    r.passed_a,
                    r.passed_b,
                    r.overlap_a,
                    r.overlap_b,
                    r.novel_in_compound,
                    r.dominant,
                    f"{r.shift_mean:.4f}",
                    f"{r.shift_top_quartile:.4f}",
                    f"{r.shift_bottom_quartile:.4f}",
                    f"{r.rho_compound_vs_a:.4f}",
                    f"{r.rho_compound_vs_b:.4f}",
                    f"{r.gap:.4f}",
                ]
            )
    print(f"\n결과 저장: {target_csv}")


if __name__ == "__main__":
    main()
