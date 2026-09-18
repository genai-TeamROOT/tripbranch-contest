"""RECOMMEND 조건 추출이 취향 발화만 taste_query에 담는지 실 LLM으로 검증한다.

프롬프트 스냅샷 테스트는 지시문 문자열만 고정할 뿐 모델이 그 지시를 따르는지는
보지 않는다. 이 스크립트는 실제로 호출해서 두 가지를 확인한다.

1. 취향 발화 -> taste_query가 채워지고, 시간·거리·예산·인원수 조건이 섞이지 않는가
2. 비취향 발화(일정·거리·장소유형) -> taste_query가 null인가

2번은 실측(2026-08-19)에서 "3시간 안에 다녀올 수 있는 곳"이 취향 근거 검색에서
유사도 0.523으로 진짜 취향 발화(0.498)보다 높게 나온 것과 직결된다 — 이 문장이
taste_query에 담기면 엉뚱한 장소에 취향 점수가 붙는다.

"조용한/한적한" 류 혼잡도 표현은 2.3.0부터 taste_query에도 채워지는 게 정상이다
(concentration_intent와 co-fill 허용, HISTORY.md 2.3.0 결정 근거 참고). 이 스크립트는
그 co-fill 비율을 참고로 측정하되 실패로 보지 않는다 — 실패 기준은 여전히 "일정·거리·
예산·인원수 조건이 taste_query에 섞이는가"뿐이다.

3. 동행(companion) + 취향이 함께 오는 발화 -> companion과 taste_query가 동시에
   채워지는가(COMPANION_OVERLAP_CASES). 혼잡도 co-fill과 달리 **이건 실패 기준이다**
   — TP-128 조사에서 "아이들과 조용한 카페" 류가 실 LLM 3회 중 1회만 두 조건을
   함께 남기는 걸 애드혹으로 확인했다(HISTORY.md 결정 근거 참고). taste_query 규칙에
   동행 co-fill을 명문화하는 프롬프트 변경 전후를 이 그룹으로 비교한다.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import time

from app.config import Settings
from app.providers.gemini import RealGeminiProvider

# 2차 혼잡도 가중치(0.15)가 실제로 붙는 intent. IGNORE/null은 재순위를 안
# 켜므로(D-040) co-fill 집계에서 제외한다 — 이 둘은 "한 단어가 0.30을 먹는"
# 문제를 일으키지 않는다.
_SCORING_INTENTS = ("AVOID", "SEEK")

# (발화, taste_query가 채워져야 하는가)
CASES: tuple[tuple[str, bool], ...] = (
    # --- 취향 발화: 채워져야 한다 ---
    ("혼자 조용히 쉴 만한 곳 추천해줘", True),
    ("부모님이랑 갈 만한 분위기 좋은 곳", True),
    ("감성적인 사진 찍기 좋은 데 알려줘", True),
    ("빈티지하고 레트로한 분위기 카페", True),
    # --- 비취향: null이어야 한다 (일정·거리·장소유형 — 취향 축이 아니다) ---
    ("3시간 안에 다녀올 수 있는 곳", False),
    ("지하철역에서 가까운 곳", False),
    ("반나절 코스로 짜줘", False),
    ("종로 맛집 추천", False),
    ("걸어서 20분 이내로", False),
    ("경복궁 근처 추천해줘", False),
    # --- 혼잡도 표현도 취향 서술로 채워져야 한다 (2.3.0: co-fill 허용) ---
    ("사람 많은 곳 말고 한적한 데로", True),
    ("3시간 안에 갈 수 있는 조용한 카페", True),  # "3시간" 조건은 빠지고 "조용한"만 남아야 한다
    ("친구들이랑 시끌벅적하게 놀 만한 곳", True),
    # --- 혼합: 취향만 뽑고 시간·거리는 뺀다(혼잡도는 남긴다) ---
    ("지하철역 가까우면서 분위기 좋은 곳", True),
)

# 혼잡도·취향 축이 겹치는 발화 — co-fill 비율을 참고로 측정한다(실패 기준 아님).
OVERLAP_CASES: tuple[str, ...] = (
    "조용한 데 가고 싶어",
    "한적한 곳 추천해줘",
    "사람 없는 조용한 카페",
    "붐비지 않는 데서 쉬고 싶어",
    "북적이지 않는 분위기 좋은 곳",
    "한적하고 감성적인 곳",
)

# 동행·취향 축이 겹치는 발화 — TP-128 조사에서 "아이들이랑 가기 좋은 조용한
# 카페"가 3회 중 1회만 두 조건을 taste_query에 함께 남긴다고 애드혹으로
# 확인했었다(산출물 미보존). 이 스크립트로 재확인하니 정확한 비율은 달랐다
# (2026-08-24 실측: 동일 발화 5회 중 3회 성공 — "1/3"이라는 원래 수치 자체가
# 표본 3회짜리 근사였다). 다만 **불안정하다는 방향은 재현됐다.**
#
# 처음 만든 케이스(아이들과 조용한/아이들이랑 감성적인/혼자 아늑한/친구들 활기찬/
# 강아지 조용한)는 전부 12/12로 안정적이었다 — 우연히 이 스크립트가 못 잡는
# 쉬운 표현만 골랐던 것이다. 실제로 불안정한 건 "가기 좋은" 연결형이었다.
# 아래는 그 연결형으로 다시 뽑은 케이스다.
#
# 세 번째 값(marker)이 중요하다 — 처음엔 "taste_query가 비어있지 않고 companion
# 필드가 맞으면 통과"로만 쟀는데, 그러면 "친구들이랑 가기 좋은 아늑한 카페"가
# taste_query="아늑한"(동행 문구가 통째로 빠짐)으로 나와도 통과로 잘못 잡혔다.
# taste_query 안에 동행을 가리키는 원문 조각(marker)이 실제로 남아 있는지까지
# 봐야 한다 — 정규식 프록시 오판을 문장으로 확인해야 했던 9.6절과 같은 함정이다.
# "일치"는 marker 포함 + companion 필드 정확도가 **동시에** 맞는가다 — 혼잡도
# OVERLAP_CASES와 달리 여기는 참고용이 아니라 이번 수정의 목표 그 자체라
# 실패 기준이다.
COMPANION_OVERLAP_CASES: tuple[tuple[str, str, str], ...] = (
    ("아이들이랑 가기 좋은 조용한 카페", "child", "아이들"),  # 08-24: 5회 중 3회만 co-fill
    ("친구들이랑 가기 좋은 아늑한 카페", "friend", "친구"),  # 08-24: 3회 중 0회 co-fill(항상 드롭)
    ("부모님이랑 갈 만한 분위기 좋은 곳", "parent", "부모님"),  # extract.md 예시 — 안정된 양성 대조
    ("혼자 가기 좋은 조용한 카페", "solo", "혼자"),  # 08-24: 3회 중 3회 안정
    ("강아지랑 갈 만한 조용한 카페", "pet", "강아지"),
)

# 대조군 — 취향 단어가 전혀 없는 순수 혼잡도 발화도 이제 taste_query가 함께
# 채워지는 게 정상이다(2.3.0). "taste" 축 대조군은 혼잡도 단어 없이 취향만
# 말한 경우로, concentration_intent가 안 켜지는지를 본다.
CONTROL_CASES: tuple[tuple[str, str], ...] = (
    ("붐비는 델 피하고 싶어", "concentration"),        # AVOID + taste 동시 채움이 정상
    ("사람 많고 활기찬 데 가고 싶어", "concentration"),  # SEEK + taste 동시 채움이 정상
    ("빈티지하고 레트로한 카페", "taste"),               # 취향이지만 혼잡도 단어는 없다
)

# taste_query에 섞이면 안 되는 표현. 다른 필드가 이미 받는 조건들이다.
# 부분 문자열로 찾으면 "분위기"의 "분"을 시간 조건으로 오탐하므로 패턴으로 잡는다.
_LEAK_PATTERNS = (
    r"\d+\s*(시간|분|km|킬로|미터|m)\b",   # 3시간, 20분, 2km
    r"반나절|코스|일정",                      # 일정 조건
    r"지하철|역에서|도보로|걸어서",             # 교통·거리 조건
    r"\d+\s*(명|인)",                      # 인원
)

# 참고용: taste_query에 혼잡도 표현이 같이 들어왔는지 표시만 한다(2.3.0부터는
# 정상 동작이라 실패 신호가 아니다). concentration_intent와 co-fill되는 비율을
# 보려는 목적이다.
_CROWD_PATTERNS = (
    r"조용",
    r"한적",
    r"붐비",
    r"북적",
    r"시끌",
    r"사람\s*(많|없|적)",
)


async def _extract(
    provider: RealGeminiProvider, text: str
) -> tuple[str | None, str | None, str | None, str | None, int]:
    """(taste_query, concentration_intent, companion, 오류, ms)를 반환한다."""
    started = time.perf_counter()
    try:
        result = await provider.extract_recommend_conditions(text)
        recommend = result.data.recommend
        conditions = recommend.conditions if recommend else None
        taste = conditions.taste_query if conditions else None
        conc = conditions.concentration_intent if conditions else None
        conc_str = conc.value if conc is not None else None
        companion = conditions.companion if conditions else None
        companion_str = companion.value if companion is not None else None
        error = None
    except Exception as exc:  # noqa: BLE001 - 실 API 검증 스크립트
        taste, conc_str, companion_str, error = None, None, None, f"{type(exc).__name__}: {exc}"
    ms = round((time.perf_counter() - started) * 1000)
    return taste, conc_str, companion_str, error, ms


async def run(
    model: str | None, delay: float
) -> dict[str, list[dict[str, object]]]:
    settings = Settings()
    if not settings.llm_api_key:
        raise ValueError("LLM_API_KEY가 필요합니다.")

    fast = [model] if model else settings.resolved_llm_fast_models
    provider = RealGeminiProvider(
        api_key=settings.llm_api_key,
        fast_model_names=fast,
        generation_model_names=settings.resolved_llm_generation_models,
        timeout_seconds=60.0,
    )
    print(f"판단 모델: {fast[0]}")

    taste_rows: list[dict[str, object]] = []
    for text, should_fill in CASES:
        taste, conc, _companion, error, ms = await _extract(provider, text)
        filled = bool(taste)
        leaked = [p for p in _LEAK_PATTERNS if re.search(p, taste)] if taste else []
        # 혼잡도 단어는 2.3.0부터 taste_query에 남아도 정상이라 실패 신호가
        # 아니다 — 참고용으로만 같이 표시한다.
        crowd = [p for p in _CROWD_PATTERNS if re.search(p, taste)] if taste else []
        taste_rows.append(
            {
                "발화": text,
                "기대": "채움" if should_fill else "null",
                "taste": taste if taste else "null",
                "혼잡도": conc if conc else "null",
                "일치": error is None and filled == should_fill and not leaked,
                "누출": leaked,
                "혼잡참고": crowd,
                "오류": error,
                "ms": ms,
            }
        )
        print("." if error is None else "!", end="", flush=True)
        await asyncio.sleep(delay)

    overlap_rows: list[dict[str, object]] = []
    for text in OVERLAP_CASES:
        taste, conc, _companion, error, ms = await _extract(provider, text)
        overlap_rows.append(
            {
                "발화": text,
                "taste": taste if taste else "null",
                "혼잡도": conc if conc else "null",
                "동시": bool(taste) and conc in _SCORING_INTENTS,
                "오류": error,
                "ms": ms,
            }
        )
        print("." if error is None else "!", end="", flush=True)
        await asyncio.sleep(delay)

    companion_rows: list[dict[str, object]] = []
    for text, expected_companion, marker in COMPANION_OVERLAP_CASES:
        taste, _conc, companion, error, ms = await _extract(provider, text)
        companion_ok = companion == expected_companion
        marker_kept = marker in taste if taste else False
        companion_rows.append(
            {
                "발화": text,
                "기대동행": expected_companion,
                "marker": marker,
                "taste": taste if taste else "null",
                "companion": companion if companion else "null",
                "일치": error is None and companion_ok and marker_kept,
                "오류": error,
                "ms": ms,
            }
        )
        print("." if error is None else "!", end="", flush=True)
        await asyncio.sleep(delay)

    control_rows: list[dict[str, object]] = []
    for text, axis in CONTROL_CASES:
        taste, conc, _companion, error, ms = await _extract(provider, text)
        if axis == "concentration":
            # 순수 혼잡도 발화: 혼잡도 축만 켜지면 통과다. taste_query가 같이
            # 채워지는 건 2.3.0부터 정상이라 실패 조건이 아니다.
            ok = conc in _SCORING_INTENTS
        else:
            ok = bool(taste) and conc not in _SCORING_INTENTS
        control_rows.append(
            {
                "발화": text,
                "축": axis,
                "taste": taste if taste else "null",
                "혼잡도": conc if conc else "null",
                "일치": error is None and ok,
                "오류": error,
                "ms": ms,
            }
        )
        print("." if error is None else "!", end="", flush=True)
        await asyncio.sleep(delay)

    print(flush=True)
    return {
        "taste": taste_rows,
        "overlap": overlap_rows,
        "companion": companion_rows,
        "control": control_rows,
    }


def _report(groups: dict[str, list[dict[str, object]]]) -> int:
    failures = 0

    print("\n■ 취향 추출 (실패 기준: 기대 불일치 또는 일정·거리·예산·인원수 누출)")
    print(f"{'기대':<5} {'일치':<5} {'혼잡도':<8} {'발화':<30} {'taste_query'}")
    print("-" * 92)
    for r in groups["taste"]:
        ok = "✅" if r["일치"] else "❌"
        if ok == "❌":
            failures += 1
        print(f"{r['기대']:<5} {ok:<4} {str(r['혼잡도']):<8} {r['발화']:<30} {r['taste']}")
        if r["누출"]:
            print(f"{'':>11} ⚠️  조건 누출: {r['누출']}")
        if r["혼잡참고"]:
            print(f"{'':>11} ℹ️  혼잡도 단어 포함(정상): {r['혼잡참고']}")
        if r["오류"]:
            print(f"{'':>11} ⚠️  {r['오류']}")

    print("\n■ 겹침 측정 (참고: 혼잡도·취향 두 축이 동시에 채워지는 비율)")
    print(f"{'동시':<5} {'혼잡도':<8} {'발화':<30} {'taste_query'}")
    print("-" * 92)
    co_fill = 0
    for r in groups["overlap"]:
        mark = "✅" if r["동시"] else "·"
        if r["동시"]:
            co_fill += 1
        print(f"{mark:<4} {str(r['혼잡도']):<8} {r['발화']:<30} {r['taste']}")
        if r["오류"]:
            print(f"{'':>11} ⚠️  {r['오류']}")
    total_overlap = len(groups["overlap"])
    print(
        f"\n  → co-fill(두 축 동시) {co_fill}/{total_overlap}건 "
        "— 2.3.0부터는 이게 정상 동작이라 목표치를 두지 않는다(참고용)"
    )

    print(
        "\n■ 동행 co-fill (실패 기준: companion 필드가 안 맞거나 taste_query에서 "
        "동행 원문 조각(marker)이 빠짐)"
    )
    print(
        f"{'일치':<5} {'기대동행':<8} {'companion':<10} {'marker':<6} "
        f"{'발화':<28} {'taste_query'}"
    )
    print("-" * 100)
    companion_co_fill = 0
    for r in groups["companion"]:
        ok = "✅" if r["일치"] else "❌"
        if r["일치"]:
            companion_co_fill += 1
        else:
            failures += 1
        print(
            f"{ok:<4} {r['기대동행']:<8} {str(r['companion']):<10} {r['marker']:<6} "
            f"{r['발화']:<28} {r['taste']}"
        )
        if r["오류"]:
            print(f"{'':>15} ⚠️  {r['오류']}")
    total_companion = len(groups["companion"])
    print(f"\n  → 동행 co-fill {companion_co_fill}/{total_companion}건")

    print("\n■ 대조군")
    print(f"{'축':<14} {'일치':<5} {'혼잡도':<8} {'발화':<24} {'taste_query'}")
    print("-" * 92)
    for r in groups["control"]:
        ok = "✅" if r["일치"] else "❌"
        if ok == "❌":
            failures += 1
        print(f"{r['축']:<14} {ok:<4} {str(r['혼잡도']):<8} {r['발화']:<24} {r['taste']}")
        if r["오류"]:
            print(f"{'':>15} ⚠️  {r['오류']}")

    print(
        f"\n실패(취향+동행+대조군) {failures}건 · 참고 혼잡도 co-fill "
        f"{co_fill}/{total_overlap}건 · 동행 co-fill {companion_co_fill}/{total_companion}건"
    )
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=None, help="모델 이름(기본: 설정값)")
    parser.add_argument("--delay", type=float, default=1.0, help="호출 간 대기(초)")
    args = parser.parse_args()

    groups = asyncio.run(run(args.model, args.delay))
    raise SystemExit(1 if _report(groups) else 0)


if __name__ == "__main__":
    main()
