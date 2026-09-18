"""추천 후보 범위에서 취향 태그가 세 상태로 어떻게 갈리는지 잰다.

**왜 이걸 먼저 재는가.** taste 점수를 임베딩 유사도 대신 취향 태그로 계산하려면
후보를 세 가지로 나눠야 한다 — 요청 취향 태그가 있는 곳, 태그는 있는데 그 취향은
없는 곳, 태그 자체가 없는 곳이다. 세 번째를 0점으로 깎으면 "후기가 적어서 태그가
안 뽑힌 장소"가 "취향에 안 맞는 장소"로 취급된다. 그래서 중립값을 둬야 하는데,
그 값을 정하려면 **세 상태가 실제로 몇 대 몇인지**부터 알아야 한다.

전체 커버리지(활성 8,009곳 중 태그 3,609곳 = 45.1%)는 이미 알지만, 점수에
영향을 주는 건 전체가 아니라 **한 요청의 후보 30곳 안에서의 비율**이다. 중심점과
취향 축에 따라 크게 다를 수 있어서 여기서 따로 잰다.

방법: 중심점에서 가까운 순으로 N곳을 후보로 잡고(하드 필터의 거리 조건을 근사 —
`measure_taste_score_distribution.py`와 같은 방식이다), 그 후보들의
`place_preference_tags`를 읽어 취향 코드별로 세 상태를 센다. 일치한 곳은
positive/negative 문서 수까지 같이 남긴다 — "몇 건이면 만점으로 볼지"를 정하는
재료다.

**임베딩을 쓰지 않는다.** 태그 테이블만 읽으므로 `sentence-transformers` 없이
돈다. 태그 점수와 임베딩 점수가 서로 다른 순위를 내는지는 별도 측정이다.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.config import Settings

_REQUEST_TIMEOUT_SECONDS = 60.0
_PAGE_SIZE = 1000

RESULTS_DIR = Path(__file__).resolve().parents[1] / "test_results"
SUMMARY_CSV = RESULTS_DIR / "preference_tag_coverage.csv"
DETAIL_CSV = RESULTS_DIR / "preference_tag_coverage_matched.csv"

# 구를 흩어 잡는다. 태그 적재가 25개 구 전체로 확장됐어도(2026-09-17 DB 확인)
# 후기 밀도는 지역마다 달라서 한 중심점으로는 대표되지 않는다.
_DEFAULT_CENTERS: tuple[tuple[str, float, float], ...] = (
    ("공덕역(마포)", 37.544300, 126.951500),
    ("안국역(종로)", 37.576500, 126.985700),
    ("강남역(강남)", 37.497900, 127.027600),
    ("성수역(성동)", 37.544500, 127.055700),
    ("홍대입구(마포)", 37.557000, 126.924500),
)

# `_REQUESTED_PREFERENCE_ALIASES`(real_recommendation_provider.py)가 발화에서
# 뽑아내는 코드 중, 동행·분위기를 대표하는 축을 골랐다. 이 코드로 사용자 발화가
# 실제로 매핑된다 — 예: "혼밥"·"혼자"·"나홀로" → alone.
_DEFAULT_CODES: tuple[str, ...] = (
    "alone",
    "date",
    "with_kids",
    "with_parents",
    "quiet",
    "photo_spot",
)

# 실제 추천이 C에서 받는 후보 수(DEFAULT_RECOMMENDATION_CANDIDATE_LIMIT).
_DEFAULT_CANDIDATE_COUNT = 30


@dataclass(frozen=True)
class Place:
    content_id: str
    title: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class TagRow:
    content_id: str
    code: str
    label: str
    mention_count: int
    positive: int
    negative: int
    source_count: int
    confidence: float | None


@dataclass
class StateCount:
    """한 중심점 × 한 취향 코드에서의 세 상태 집계."""

    center: str
    code: str
    candidates: int
    matched: int = 0
    tagged_no_match: int = 0
    untagged: int = 0
    positives: list[int] = field(default_factory=list)
    with_negative: int = 0

    @property
    def matched_pct(self) -> float:
        return self.matched / self.candidates if self.candidates else 0.0

    @property
    def untagged_pct(self) -> float:
        return self.untagged / self.candidates if self.candidates else 0.0


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(d_lng / 2) ** 2
    )
    return radius * 2 * math.asin(math.sqrt(a))


async def _fetch_active_places(client: httpx.AsyncClient) -> list[Place]:
    """활성 장소를 전부 읽는다.

    페이지를 넘겨 가며 다 읽는 이유: 한 번에 1000곳만 받으면 그 1000곳이 서울
    어디에 몰려 있는지 모른 채 "가까운 30곳"을 고르게 된다. 중심점이 그 표본 밖에
    있으면 후보가 통째로 엉뚱해진다.
    """

    places: list[Place] = []
    offset = 0
    while True:
        response = await client.get(
            "/rest/v1/places",
            params={
                "select": "content_id,title,latitude,longitude",
                "is_active": "eq.true",
                "limit": str(_PAGE_SIZE),
                "offset": str(offset),
            },
        )
        response.raise_for_status()
        rows = response.json()
        if not isinstance(rows, list) or not rows:
            break
        for row in rows:
            content_id = row.get("content_id")
            latitude = row.get("latitude")
            longitude = row.get("longitude")
            if content_id is None or latitude is None or longitude is None:
                continue
            places.append(
                Place(
                    content_id=str(content_id),
                    title=str(row.get("title") or ""),
                    latitude=float(latitude),
                    longitude=float(longitude),
                )
            )
        if len(rows) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE
    return places


async def _fetch_tags(
    client: httpx.AsyncClient, content_ids: Sequence[str]
) -> dict[str, list[TagRow]]:
    """후보들의 취향 태그를 한 번에 읽는다. 태그가 없는 장소는 키 자체가 없다."""

    if not content_ids:
        return {}
    joined = ",".join(f'"{content_id}"' for content_id in content_ids)
    response = await client.get(
        "/rest/v1/place_preference_tags",
        params={
            "select": (
                "content_id,preference_code,preference_label,mention_count,"
                "positive_document_count,negative_document_count,source_count,confidence"
            ),
            "content_id": f"in.({joined})",
        },
    )
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list):
        raise ValueError("취향 태그 응답이 목록이 아닙니다.")

    by_place: dict[str, list[TagRow]] = {}
    for row in rows:
        content_id = str(row.get("content_id") or "")
        code = str(row.get("preference_code") or "")
        if not content_id or not code:
            continue
        confidence = row.get("confidence")
        by_place.setdefault(content_id, []).append(
            TagRow(
                content_id=content_id,
                code=code,
                label=str(row.get("preference_label") or ""),
                mention_count=int(row.get("mention_count") or 0),
                positive=int(row.get("positive_document_count") or 0),
                negative=int(row.get("negative_document_count") or 0),
                source_count=int(row.get("source_count") or 0),
                confidence=float(confidence) if confidence is not None else None,
            )
        )
    return by_place


def _nearest(places: Sequence[Place], lat: float, lng: float, count: int) -> list[Place]:
    ranked = sorted(
        places, key=lambda p: _haversine_km(lat, lng, p.latitude, p.longitude)
    )
    return list(ranked[:count])


def _classify(
    center: str,
    code: str,
    candidates: Sequence[Place],
    tags_by_place: dict[str, list[TagRow]],
) -> tuple[StateCount, list[TagRow]]:
    """후보를 세 상태로 가르고, 일치한 태그 행을 함께 돌려준다."""

    state = StateCount(center=center, code=code, candidates=len(candidates))
    matched_rows: list[TagRow] = []
    for place in candidates:
        rows = tags_by_place.get(place.content_id)
        if not rows:
            state.untagged += 1
            continue
        hit = next((row for row in rows if row.code == code), None)
        if hit is None:
            state.tagged_no_match += 1
            continue
        state.matched += 1
        state.positives.append(hit.positive)
        if hit.negative > 0:
            state.with_negative += 1
        matched_rows.append(hit)
    return state, matched_rows


async def run(
    centers: Sequence[tuple[str, float, float]],
    codes: Sequence[str],
    count: int,
) -> tuple[list[StateCount], list[tuple[str, str, Place, TagRow]]]:
    settings = Settings()
    if not settings.supabase_url or not settings.supabase_secret_key:
        raise ValueError("SUPABASE_URL과 SUPABASE_SECRET_KEY가 필요합니다.")

    headers = {
        "apikey": settings.supabase_secret_key,
        "Authorization": f"Bearer {settings.supabase_secret_key}",
    }
    states: list[StateCount] = []
    details: list[tuple[str, str, Place, TagRow]] = []

    async with httpx.AsyncClient(
        base_url=settings.supabase_url.rstrip("/"),
        headers=headers,
        timeout=_REQUEST_TIMEOUT_SECONDS,
    ) as client:
        places = await _fetch_active_places(client)
        print(f"활성 장소 {len(places)}곳을 읽었다. 중심점마다 가까운 {count}곳을 후보로 잡는다.\n")

        for name, lat, lng in centers:
            candidates = _nearest(places, lat, lng, count)
            tags_by_place = await _fetch_tags(
                client, [place.content_id for place in candidates]
            )
            by_id = {place.content_id: place for place in candidates}
            for code in codes:
                state, matched_rows = _classify(name, code, candidates, tags_by_place)
                states.append(state)
                details.extend(
                    (name, code, by_id[row.content_id], row) for row in matched_rows
                )
    return states, details


def _print(states: Sequence[StateCount]) -> None:
    header = (
        f"{'중심점':<16} {'취향코드':<14} {'후보':>5} {'일치':>5} "
        f"{'태그o불일치':>11} {'태그없음':>8} {'일치율':>7} {'긍정중앙':>9} {'부정있음':>8}"
    )
    print(header)
    print("-" * len(header))
    for state in states:
        positive_median = (
            f"{statistics.median(state.positives):.1f}" if state.positives else "-"
        )
        print(
            f"{state.center:<16} {state.code:<14} {state.candidates:>5} {state.matched:>5} "
            f"{state.tagged_no_match:>11} {state.untagged:>8} "
            f"{state.matched_pct:>6.1%} {positive_median:>9} {state.with_negative:>8}"
        )

    if not states:
        return
    total = sum(state.candidates for state in states)
    print(
        f"\n전체 평균 — 일치 {sum(s.matched for s in states) / total:.1%} · "
        f"태그있고 불일치 {sum(s.tagged_no_match for s in states) / total:.1%} · "
        f"태그없음 {sum(s.untagged for s in states) / total:.1%}"
    )
    all_positives = [value for state in states for value in state.positives]
    if all_positives:
        print(
            f"일치 태그의 긍정 문서 수 — 중앙 {statistics.median(all_positives):.1f} / "
            f"최대 {max(all_positives)} / 1건뿐인 비율 "
            f"{sum(1 for v in all_positives if v <= 1) / len(all_positives):.1%}"
        )


def _write_csv(
    states: Sequence[StateCount],
    details: Sequence[tuple[str, str, Place, TagRow]],
) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    with SUMMARY_CSV.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.writer(fp)
        writer.writerow(
            [
                "center", "preference_code", "candidates", "matched",
                "tagged_no_match", "untagged", "matched_pct", "untagged_pct",
                "positive_median", "positive_max", "with_negative",
            ]
        )
        for state in states:
            writer.writerow(
                [
                    state.center, state.code, state.candidates, state.matched,
                    state.tagged_no_match, state.untagged,
                    f"{state.matched_pct:.4f}", f"{state.untagged_pct:.4f}",
                    f"{statistics.median(state.positives):.2f}" if state.positives else "",
                    max(state.positives) if state.positives else "",
                    state.with_negative,
                ]
            )

    with DETAIL_CSV.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.writer(fp)
        writer.writerow(
            [
                "center", "preference_code", "content_id", "title", "label",
                "mention_count", "positive_documents", "negative_documents",
                "source_count", "confidence",
            ]
        )
        for center, code, place, row in details:
            writer.writerow(
                [
                    center, code, place.content_id, place.title, row.label,
                    row.mention_count, row.positive, row.negative,
                    row.source_count,
                    f"{row.confidence:.4f}" if row.confidence is not None else "",
                ]
            )

    print(f"\n요약 저장: {SUMMARY_CSV}")
    print(f"일치 태그 원자료 저장: {DETAIL_CSV}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--count",
        type=int,
        default=_DEFAULT_CANDIDATE_COUNT,
        help=f"중심점당 후보 수(기본 {_DEFAULT_CANDIDATE_COUNT} — 실제 추천의 후보 한도)",
    )
    parser.add_argument(
        "--codes",
        nargs="+",
        default=None,
        help="측정할 취향 코드(기본: alone date with_kids with_parents quiet photo_spot)",
    )
    args = parser.parse_args()

    states, details = asyncio.run(
        run(_DEFAULT_CENTERS, tuple(args.codes or _DEFAULT_CODES), args.count)
    )
    _print(states)
    _write_csv(states, details)


if __name__ == "__main__":
    main()
