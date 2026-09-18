"""장소 유형을 붙인 taste 질의가 실제로 컷 통과율을 올리는지 실측한다.

배경: `real_recommendation_provider._taste_matches_for()`가 뽑아 쓰는
`taste_query`는 extract.md 규칙상 "조용한"처럼 단어 하나로 오는 경우가
많다(HISTORY.md 2.3.0). 단어 하나짜리 질의는 문장형 리뷰 텍스트와 임베딩이
잘 안 맞아서, 실제로는 관련 있는 근거인데도 컷(0.43)을 못 넘는 경우가 많다.

지역·카테고리 하드 필터가 이미 "이 후보는 카페다"를 알고 있으므로, 그 값을
질의에 붙이면("조용한" → "조용한 카페") 새 정보 없이 검색 정확도를 올릴 수
있다는 게 가설이다. 이 스크립트는 그 가설을 두 단계로 실측한다.

1. **place_tag(세분류)** — 중심점 4곳(종로, taste_score_distribution.csv와
   같은 지점) 반경 3km 안의 실제 카페(TourAPI 소분류 FD050100) 전체를 후보로
   잡고 "조용한" vs "조용한 카페"를 비교한다.
2. **place_type(넓은 유형)** — "식당"/"레스토랑"처럼 넓게 말한 발화는
   place_tags가 비고 place_types만 채워진다. 그때 쓸 한국어 라벨을 고르려고
   유형별 후보 라벨을 함께 잰다(경복궁 기준, contentTypeId로 후보를 가른다).

**주의: 통과 수가 제일 큰 라벨이 항상 정답은 아니다.** cultural_facility는
"박물관"이 수치상 제일 높지만 인용문을 열어보면 조용함이 아니라 박물관다움을
끌어온다(문화시설의 일부 하위종이라 도서관·갤러리를 잘못 당긴다). 라벨을 바꿀
때는 이 스크립트의 수치만 보지 말고 근거 문장을 직접 읽는다 — 최종 선택과
그 근거는 `real_recommendation_provider._PLACE_TYPE_TASTE_LABELS` 주석에 있다.
"""

from __future__ import annotations

import asyncio
import csv
import math
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import Settings
from app.providers.place_evidence import DEFAULT_MIN_SIMILARITY, PlaceEvidenceProvider
from app.providers.place_evidence_encoder import get_shared_encoder
from app.repositories.supabase_places import SupabasePlaceRepository

_RPC_TIMEOUT_SECONDS = 90.0

_CENTERS: dict[str, tuple[float, float]] = {
    "경복궁": (37.579617, 126.977041),
    "종각": (37.5701, 126.9829),
    "혜화": (37.5823743, 127.0014404),
    "부암동": (37.5924, 126.9634),
}
_RADIUS_KM = 3.0
# RPC 호출당 후보 상한(`supabase_places._MAX_EVIDENCE_CANDIDATES`)과 같은 값.
# 실서비스는 TourAPI 응답 수가 이보다 작아 걸리지 않지만, 측정에서 반경만으로
# 자르면 쇼핑처럼 밀집한 분류가 상한을 넘어 예외가 난다 — 가까운 순으로 자른다.
_MAX_CANDIDATES = 500
_CAFE_SMALL_CODE = "FD050100"
_BASE_QUERY = "조용한"
_CAFE_TAG = "카페"
_GENERIC_SUFFIX = "곳"

# place_type별로 비교할 한국어 라벨 후보. contentTypeId는 TourAPI 대분류다
# (category_rules.PLACE_TYPE_TO_CONTENT_TYPE_ID와 같은 값).
_PLACE_TYPE_LABEL_CANDIDATES: dict[str, tuple[str, tuple[str, ...]]] = {
    "12": ("attraction", ("관광지", "명소", "볼거리")),
    "14": ("cultural_facility", ("문화시설", "전시", "박물관")),
    "15": ("festival", ("축제", "행사")),
    "28": ("leisure", ("레저", "체험", "액티비티")),
    "38": ("shopping", ("쇼핑", "상점", "쇼핑몰")),
    "39": ("restaurant", ("식당", "맛집", "음식점")),
}

RESULTS_DIR = Path(__file__).resolve().parents[1] / "test_results"
RESULTS_CSV = RESULTS_DIR / "taste_query_enrichment.csv"


@dataclass(frozen=True)
class EnrichmentRow:
    scope: str
    center: str
    query: str
    candidate_count: int
    matched: int
    passed_cut: int
    avg_similarity: float


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


_PAGE_SIZE = 1000


async def _fetch_places(client: httpx.AsyncClient) -> list[dict[str, object]]:
    """활성 장소를 **전부** 읽는다.

    페이지네이션이 필수다 — PostgREST가 한 번에 1000행까지만 주는데 활성 장소는
    이미 그 수를 넘었다(2026-08-23 기준 2,220곳, 종로·중구·용산). 처음엔 limit만
    걸고 페이징을 안 해서 앞의 1000곳만 재고 있었다. 같은 표본끼리 비교라
    개선 폭 자체는 맞았지만 "반경 3km 카페 N곳" 같은 절대 수치가 틀렸다.
    """
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


def _nearest_within(
    places: Sequence[dict[str, object]],
    center: tuple[float, float],
    keep: Callable[[dict[str, object]], bool],
) -> list[str]:
    """반경 안에서 `keep`을 만족하는 후보를 가까운 순으로 상한까지 돌려준다."""
    matched = [
        (_haversine_km(center[0], center[1], p["latitude"], p["longitude"]), p)
        for p in places
        if keep(p)
    ]
    matched = [(d, p) for d, p in matched if d <= _RADIUS_KM]
    matched.sort(key=lambda entry: entry[0])
    return [str(p["content_id"]) for _, p in matched[:_MAX_CANDIDATES]]


async def _measure(
    provider: PlaceEvidenceProvider,
    *,
    scope: str,
    center_name: str,
    query: str,
    candidates: Sequence[str],
) -> EnrichmentRow:
    result = await provider.search(query, candidates)
    matches = list(result.data.values())
    passed = sum(1 for m in matches if m.avg_similarity >= DEFAULT_MIN_SIMILARITY)
    avg_sim = statistics.mean(m.avg_similarity for m in matches) if matches else 0.0
    return EnrichmentRow(
        scope=scope,
        center=center_name,
        query=query,
        candidate_count=len(candidates),
        matched=len(matches),
        passed_cut=passed,
        avg_similarity=avg_sim,
    )


async def run(centers: dict[str, tuple[float, float]]) -> list[EnrichmentRow]:
    settings = Settings()
    if not settings.supabase_url or not settings.supabase_secret_key:
        raise ValueError("SUPABASE_URL / SUPABASE_SECRET_KEY가 필요합니다.")

    encoder = get_shared_encoder()
    encoder.warmup()

    headers = {
        "apikey": settings.supabase_secret_key,
        "Authorization": f"Bearer {settings.supabase_secret_key}",
    }
    rows: list[EnrichmentRow] = []
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
        # p_min_similarity=0.0으로 전체 분포를 받아서, 컷(DEFAULT_MIN_SIMILARITY)
        # 적용 여부를 이쪽에서 판단한다 — 실제 서비스 컷과 다른 값을 실험할 때도
        # RPC를 다시 부르지 않아도 된다.
        provider = PlaceEvidenceProvider(encoder, repository, min_similarity=0.0)

        # 1단계: place_tag(카페) — 중심점 4곳에서 재현되는지 본다.
        for center_name, center in centers.items():
            candidates = _nearest_within(
                places, center, lambda p: p["lcls_systm3"] == _CAFE_SMALL_CODE
            )
            # 3단 폴백(태그 > 유형 라벨 > 일반 접미어)을 한 후보군에서 비교할 수
            # 있게 "조용한 곳"도 같이 잰다.
            for query in (
                _BASE_QUERY,
                f"{_BASE_QUERY} {_GENERIC_SUFFIX}",
                f"{_BASE_QUERY} {_CAFE_TAG}",
            ):
                rows.append(
                    await _measure(
                        provider,
                        scope="place_tag/카페",
                        center_name=center_name,
                        query=query,
                        candidates=candidates,
                    )
                )

        # 2단계: place_type별 후보 라벨 — 경복궁 한 지점에서 라벨을 고른다.
        label_center_name = next(iter(centers))
        label_center = centers[label_center_name]
        for content_type_id, (place_type, labels) in _PLACE_TYPE_LABEL_CANDIDATES.items():
            candidates = _nearest_within(
                places,
                label_center,
                lambda p, ctid=content_type_id: p["content_type_id"] == ctid,
            )
            if not candidates:
                continue
            for query in (
                f"{_BASE_QUERY} {_GENERIC_SUFFIX}",
                *(f"{_BASE_QUERY} {label}" for label in labels),
            ):
                rows.append(
                    await _measure(
                        provider,
                        scope=f"place_type/{place_type}",
                        center_name=label_center_name,
                        query=query,
                        candidates=candidates,
                    )
                )
    return rows


def _print(rows: Sequence[EnrichmentRow]) -> None:
    header = (
        f"{'구분':<24} {'중심':<6} {'질의':<16} {'후보':>4} {'매칭':>4} "
        f"{'컷통과':>5} {'평균유사':>8}"
    )
    print(header)
    print("-" * len(header))
    for r in rows:
        print(
            f"{r.scope:<24} {r.center:<6} {r.query:<16} {r.candidate_count:>4} "
            f"{r.matched:>4} {r.passed_cut:>5} {r.avg_similarity:>8.4f}"
        )


def main() -> None:
    rows = asyncio.run(run(_CENTERS))
    _print(rows)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with RESULTS_CSV.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.writer(fp)
        writer.writerow(
            [
                "scope",
                "center",
                "query",
                "candidate_count",
                "matched",
                "passed_cut",
                "avg_similarity",
            ]
        )
        for r in rows:
            writer.writerow(
                [
                    r.scope,
                    r.center,
                    r.query,
                    r.candidate_count,
                    r.matched,
                    r.passed_cut,
                    f"{r.avg_similarity:.4f}",
                ]
            )
    print(f"\n결과 저장: {RESULTS_CSV}")


if __name__ == "__main__":
    main()
