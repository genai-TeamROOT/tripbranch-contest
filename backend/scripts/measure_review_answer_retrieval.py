"""장소 하나를 묻는 질문에 후기 근거가 실제로 쓸 만하게 나오는지 실측한다.

배경: INFO 질문 중 "경복궁은 아이와 가기 좋대?"처럼 후기를 묻는 것은 관광 API로 답할
수 없다. `place_embeddings`를 장소 하나로 좁혀 검색해 답하려는데, 그 전에 두 가지를
정해야 한다.

1. **질의문을 어떻게 만들 것인가.** 발화 원문을 그대로 넣으면 장소명과 말끝이 섞여
   "○○ 다녀왔어요" 같은 문장을 당긴다. 그래서 발화 전체와 질문 부분만 비교한다.
2. **근거가 몇 건 이상일 때 답하게 할 것인가.** 추천 경로의 컷 0.43은 여러 장소를 줄
   세우려고 잰 값이라 그대로 쓸 수 없다 — 후보가 한 곳뿐이면 경쟁이 없다.

**2026-09-14 실측 결론.** 장소명·말끝을 손으로 다듬은 정규화 문장("아이와 함께 가기
좋은 고궁")도 함께 쟀는데, 유사도 절대값만 높았을 뿐 답할 수 있는 질문과 없는 질문을
가르는 힘은 질문 원문이 낫거나 같았다(선별까지 돌린 결과 창경궁 야간관람 8건 대 7건,
국립중앙박물관 아이 동반 7건 대 6건). 그래서 구현은 `specific_question`을 그대로 쓰고,
이 스크립트도 정규화 갈래를 뺐다.

**답할 수 없는 질문을 일부러 섞었다.** 그게 있어야 "근거 없음"으로 끊을 기준을 정할 수
있다. 이 스크립트는 판정하지 않고 숫자와 근거 원문을 내놓기만 한다 — 문장이 그 장소
이야기인지는 사람이 읽어야 한다(`docs/근거-장소연결-오염-점검-20260914.md`: 경복궁은
근거의 약 2/3가 근처 맛집 이야기이고, 장소명 포함 여부로는 구분되지 않는다).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import statistics
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import Settings
from app.providers.place_evidence import PlaceEvidenceProvider
from app.providers.place_evidence_encoder import get_shared_encoder
from app.repositories.supabase_places import SupabasePlaceRepository

_RPC_TIMEOUT_SECONDS = 90.0

# 검색 단계에서는 컷을 걸지 않는다. 분포를 통째로 받아 두면 컷을 바꿔가며 다시
# 판단할 때 RPC를 다시 부르지 않아도 된다.
_RETRIEVAL_MIN_SIMILARITY = 0.0
# 장소 하나당 받을 근거 수. 글 단위 중복 제거가 상한보다 먼저 걸리므로 서로 다른
# 글에서 최대 이만큼 온다(실측: 장소당 서로 다른 글 29~31개).
_MATCH_COUNT = 8


@dataclass(frozen=True)
class Case:
    """측정 케이스 하나.

    `answerable`은 사람의 사전 기대이지 정답이 아니다. 결과를 읽고 고쳐 적는다.
    """

    place: str
    utterance: str
    question: str
    answerable: bool


# 장소는 Supabase `places`의 title과 정확히 같아야 한다. 오염이 심한 곳(경복궁·서울숲·
# 남산서울타워)과 깨끗한 곳(창경궁·국립중앙박물관·개별 업소)을 일부러 함께 넣었다.
CASES: tuple[Case, ...] = (
    # --- 개별 업소: 근거가 그 가게 이야기인 편 ---
    Case("청담골", "청담골은 뭐가 맛있대?", "뭐가 맛있대?", True),
    Case("청담골", "청담골 부모님 모시고 가기 좋대?", "부모님 모시고 가기 좋대?", True),
    Case("청담골", "청담골 카공하기 좋대?", "카공하기 좋대?", False),
    Case("부베트 본점", "부베트 본점 뭐가 맛있대?", "뭐가 맛있대?", True),
    Case("부베트 본점", "부베트 본점 주차 편하대?", "주차 편하대?", True),
    Case("부베트 본점", "부베트 본점 아이랑 가기 좋대?", "아이랑 가기 좋대?", True),
    # --- 궁궐·실내 시설: 근거가 비교적 깨끗한 편 ---
    Case("창경궁", "창경궁 아이와 가기 좋대?", "아이와 가기 좋대?", True),
    Case("창경궁", "창경궁 야간관람 어때?", "야간관람 어때?", True),
    Case("창경궁", "창경궁 와이파이 잘 되대?", "와이파이 잘 되대?", False),
    Case("국립중앙박물관", "국립중앙박물관 아이와 가기 좋대?", "아이와 가기 좋대?", True),
    Case("국립중앙박물관", "국립중앙박물관 주차 편하대?", "주차 편하대?", True),
    Case(
        "국립중앙박물관", "국립중앙박물관 반려동물 데려가도 되대?", "반려동물 데려가도 되대?", False
    ),
    # --- 랜드마크·야외: 근처 가게 이야기가 섞인 곳 ---
    Case("경복궁", "경복궁은 아이와 가기 좋대?", "아이와 가기 좋대?", True),
    Case("경복궁", "경복궁 야간개장 어때?", "야간개장 어때?", True),
    Case("경복궁", "경복궁 카공하기 좋대?", "카공하기 좋대?", False),
    Case("서울숲", "서울숲 아이와 가기 좋대?", "아이와 가기 좋대?", True),
    Case("서울숲", "서울숲 카공하기 좋대?", "카공하기 좋대?", False),
    Case("남산서울타워", "남산서울타워 야경 좋대?", "야경 좋대?", True),
    Case("청계천", "청계천 밤에 걷기 좋대?", "밤에 걷기 좋대?", True),
    Case("덕수궁", "덕수궁 데이트하기 좋대?", "데이트하기 좋대?", True),
    Case("광장시장", "광장시장 뭐가 맛있대?", "뭐가 맛있대?", True),
    Case("광장시장", "광장시장 야경 좋대?", "야경 좋대?", False),
    Case("북촌한옥마을", "북촌한옥마을 사진 찍기 좋대?", "사진 찍기 좋대?", True),
    Case("북촌한옥마을", "북촌한옥마을 뭐가 맛있대?", "뭐가 맛있대?", False),
)

_ARMS: tuple[str, ...] = ("utterance", "question")

RESULTS_DIR = Path(__file__).resolve().parents[1] / "test_results"
RESULTS_CSV = RESULTS_DIR / "review_answer_retrieval.csv"
RESULTS_TXT = RESULTS_DIR / "review_answer_retrieval_snippets.txt"


@dataclass(frozen=True)
class ArmResult:
    case: Case
    arm: str
    query: str
    snippet_count: int
    top1: float
    top3_mean: float
    snippets: tuple[tuple[float, str, str | None], ...]  # (유사도, 문장, 출처)


async def _resolve_content_ids(
    client: httpx.AsyncClient, places: set[str]
) -> dict[str, str]:
    """장소 이름을 content_id로 바꾼다. 못 찾은 이름은 빠진 채 돌아온다."""
    resolved: dict[str, str] = {}
    for name in sorted(places):
        response = await client.get(
            "/rest/v1/places",
            params={
                "select": "content_id,title",
                "title": f"eq.{name}",
                "is_active": "eq.true",
                "limit": "1",
            },
        )
        response.raise_for_status()
        rows = response.json()
        if rows:
            resolved[name] = str(rows[0]["content_id"])
        else:
            print(f"[건너뜀] places에 없는 이름: {name}")
    return resolved


async def _measure_arm(
    provider: PlaceEvidenceProvider, case: Case, arm: str, content_id: str
) -> ArmResult:
    query = getattr(case, arm)
    result = await provider.search(query, [content_id])
    match = result.data.get(content_id)
    snippets = (
        tuple(
            (s.similarity, s.source_text, s.source_url)
            for s in sorted(match.snippets, key=lambda s: s.similarity, reverse=True)
        )
        if match
        else ()
    )
    sims = [s[0] for s in snippets]
    return ArmResult(
        case=case,
        arm=arm,
        query=query,
        snippet_count=len(snippets),
        top1=sims[0] if sims else 0.0,
        top3_mean=statistics.mean(sims[:3]) if sims else 0.0,
        snippets=snippets,
    )


async def run(cases: tuple[Case, ...]) -> list[ArmResult]:
    settings = Settings()
    if not settings.supabase_url or not settings.supabase_secret_key:
        raise ValueError("SUPABASE_URL / SUPABASE_SECRET_KEY가 필요합니다.")

    encoder = get_shared_encoder()
    encoder.warmup()

    headers = {
        "apikey": settings.supabase_secret_key,
        "Authorization": f"Bearer {settings.supabase_secret_key}",
    }
    results: list[ArmResult] = []
    async with httpx.AsyncClient(
        base_url=settings.supabase_url.rstrip("/"),
        headers=headers,
        timeout=_RPC_TIMEOUT_SECONDS,
    ) as client:
        content_ids = await _resolve_content_ids(client, {c.place for c in cases})
        repository = SupabasePlaceRepository(
            supabase_url=settings.supabase_url,
            secret_key=settings.supabase_secret_key,
            client=client,
            timeout_seconds=settings.external_api_timeout_seconds,
        )
        provider = PlaceEvidenceProvider(
            encoder,
            repository,
            min_similarity=_RETRIEVAL_MIN_SIMILARITY,
            match_count=_MATCH_COUNT,
        )
        for case in cases:
            content_id = content_ids.get(case.place)
            if content_id is None:
                continue
            for arm in _ARMS:
                results.append(await _measure_arm(provider, case, arm, content_id))
    return results


def _print_summary(results: list[ArmResult]) -> None:
    header = (
        f"{'장소':<14} {'질문':<22} {'기대':<4} {'방식':<11} "
        f"{'근거':>3} {'top1':>6} {'top3':>6}"
    )
    print(header)
    print("-" * len(header))
    for r in results:
        expectation = "가능" if r.case.answerable else "불가"
        print(
            f"{r.case.place:<14} {r.case.question:<22} {expectation:<4} {r.arm:<11} "
            f"{r.snippet_count:>3} {r.top1:>6.3f} {r.top3_mean:>6.3f}"
        )

    print("\n[방식별 평균]")
    for arm in _ARMS:
        rows = [r for r in results if r.arm == arm]
        answerable = [r for r in rows if r.case.answerable]
        unanswerable = [r for r in rows if not r.case.answerable]
        yes_top1 = statistics.mean([r.top1 for r in answerable])
        no_top1 = statistics.mean([r.top1 for r in unanswerable])
        print(
            f"  {arm:<11} 답가능 top1 {yes_top1:.3f} / "
            f"답불가 top1 {no_top1:.3f} / 차이 {yes_top1 - no_top1:+.3f}"
        )


def _write_files(results: list[ArmResult]) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with RESULTS_CSV.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.writer(fp)
        writer.writerow(
            ["place", "question", "answerable", "arm", "query", "snippet_count",
             "top1", "top3_mean", "rank", "similarity", "source_text", "source_url"]
        )
        for r in results:
            for rank, (similarity, text, url) in enumerate(r.snippets, start=1):
                writer.writerow(
                    [r.case.place, r.case.question, r.case.answerable, r.arm, r.query,
                     r.snippet_count, f"{r.top1:.4f}", f"{r.top3_mean:.4f}", rank,
                     f"{similarity:.4f}", text.replace("\n", " "), url or ""]
                )

    with RESULTS_TXT.open("w", encoding="utf-8") as fp:
        for r in results:
            expectation = "답가능" if r.case.answerable else "답불가"
            fp.write(f"\n=== {r.case.place} | {r.case.question} ({expectation}) | {r.arm}\n")
            fp.write(f"    질의: {r.query}\n")
            for rank, (similarity, text, _url) in enumerate(r.snippets[:5], start=1):
                flat = text.replace(chr(10), " ")[:150]
                fp.write(f"    {rank}. [{similarity:.3f}] {flat}\n")
    print(f"\n결과 저장: {RESULTS_CSV}\n근거 원문: {RESULTS_TXT}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--place", action="append", help="이 장소만 잰다(여러 번 지정 가능)"
    )
    args = parser.parse_args()
    cases = CASES
    if args.place:
        wanted = set(args.place)
        cases = tuple(c for c in CASES if c.place in wanted)
    results = asyncio.run(run(cases))
    _print_summary(results)
    _write_files(results)


if __name__ == "__main__":
    main()
