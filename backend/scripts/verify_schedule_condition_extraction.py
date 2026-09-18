""" "반나절" 같은 시간 표현과 일정 발화의 목적지가 실행마다 다르게 추출되는지
실 LLM으로 재서 후속 프롬프트 작업의 기준선을 만든다 (TP-177 1단계).

배경: 골드셋(test_results/agent_quality/evaluation_dev.csv)의 SCHEDULE 실패가
2026-08-20부터 같은 자리에서 반복된다 — DEV-008/023의 `time_available`,
DEV-008/033의 `search_center`. `prompts/recommend/HISTORY.md`는 이를 "기존
비결정성 케이스"로 기록하고 "1회 실행으로 회귀를 판정하지 않아야 하는 사례로
남긴다"고 적어뒀지만, 그 흔들림이 얼마나 큰지 재는 수단은 없었다. 기준선이
흔들리는 상태에서 프롬프트를 고치면 개선인지 실행 간 분산인지 구분할 수 없다.

그래서 이 스크립트는 세 가지를 따로 본다.
  (1) 흔들림 — 같은 발화·같은 설정에서 실행마다 값이 바뀌는가
  (2) 응답 모델 — 그 흔들림이 폴백 때문인가
  (3) 기대 일치 — 골드셋 라벨과 맞는가
(1)이 있으면 (3)의 개선을 판정할 수 없으므로 (1)을 먼저 닫아야 한다.

(2)를 따로 보는 이유: 조건 추출은 `llm_fast_model_name` 묶음을 쓰고, 현재 .env는
주 모델 gemini-3.5-flash에 폴백 gemini-2.5-flash-lite(구세대)를 걸어두고 있다.
주 모델이 타임아웃·오류로 실패하면 폴백으로 조용히 넘어가므로(gemini.py의 모델
루프), 같은 발화가 실행마다 다른 모델로 처리될 수 있다. `record_llm_call()`이
남기는 `served_model`을 읽어 실제로 어느 모델이 답했는지 함께 기록한다.

`--model`을 주면 그 모델 하나만 쓰고 폴백을 두지 않는다. 기본 실행(폴백 있음)과
`--model gemini-3.5-flash`(폴백 없음)를 비교하면 흔들림이 폴백에서 오는지
격리할 수 있다.

SCHEDULE 조건은 지금 전용 추출 슬롯이 없어 `extract_recommend_conditions()`가
그대로 추출한다(`services/interpret/orchestrator.py`). 이 스크립트도 현재 동작을
재는 것이 목적이므로 같은 경로를 호출한다.

입력: --model(기본: 설정값+폴백), --repeat(기본 3), --delay(기본 1.0), --strict
출력: 케이스별 실행값·흔들림 여부·응답 모델·기대 일치, 그룹별 요약
호출 시점: 로컬 수동 실행. 실 LLM 호출이 필요해 CI 대상이 아니다.

    cd backend
    python -m scripts.verify_schedule_condition_extraction --repeat 3
    python -m scripts.verify_schedule_condition_extraction --repeat 3 --model gemini-3.5-flash
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from app.config import Settings
from app.providers.gemini import RealGeminiProvider
from app.services.runtime.llm_execution import (
    get_llm_execution_metadata,
    reset_llm_execution_metadata,
)

# 기대값을 두지 않는 자리. 규칙이 아직 없어서 "무엇이 맞다"를 정할 수 없는 표현은
# 기대 일치를 판정하지 않고 관측만 한다 — 값 자체보다 흔들리는지가 먼저다.
ANY = "*"

# (그룹, 발화, 기대 search_center, 기대 time_available)
# None은 "null이어야 한다", ANY는 "판정하지 않는다".
CASES: tuple[tuple[str, str, str | None, int | str | None], ...] = (
    # --- ① 골드셋 실패 재현. 라벨은 evaluation_dev.csv 기대값이다 ---
    ("골드셋", "광화문 반나절 일정 짜줘", "광화문", 240),  # DEV-008 (단일 턴)
    ("골드셋", "반나절 일정 짜줘", None, 240),  # DEV-023 1턴
    ("골드셋", "경복궁 근처 반나절 일정 짜줘", "경복궁", 240),  # DEV-033 1턴
    ("골드셋", "경복궁 근처 3시간 코스 짜줘", "경복궁", 180),  # DEV-009
    # --- ② 시간 표현. "반나절" 외에도 규칙이 없는 표현이 어떻게 나오는지 관측 ---
    ("시간표현", "광화문 하루 종일 일정 짜줘", "광화문", ANY),
    ("시간표현", "광화문 오후 내내 일정 짜줘", "광화문", ANY),
    ("시간표현", "광화문 두세 시간 코스 짜줘", "광화문", ANY),
    # 숫자 표현은 규칙이 있다(extract.md "시간(hour)"→×60) — 대조군
    ("시간표현", "광화문 4시간 일정 짜줘", "광화문", 240),
    # --- ③ 위치 표현. location_rules.md가 일정 발화 형태를 커버하는지 ---
    ("위치표현", "경복궁 코스 짜줘", "경복궁", ANY),
    ("위치표현", "경복궁 일정 짜줘", "경복궁", ANY),
    ("위치표현", "경복궁 근처 일정 짜줘", "경복궁", ANY),
    ("위치표현", "북촌 반나절 코스", "북촌", 240),
    # --- ④ 대조군. RECOMMEND 발화는 흔들리지 않아야 한다 ---
    ("대조군", "경복궁 근처 카페 추천해줘", "경복궁", None),
    ("대조군", "종로에서 15분 이내 카페 추천해줘", "종로", None),
    # --- ⑤ 담을 칸이 없는 조건. 그 값이 옆 조건을 흔드는지 본다 ---
    # 시작 시각("오후 2시부터")과 경유 출발지("A에서 B로"의 A)는 조건 스키마에
    # 필드가 없어 어디에도 담기지 않는다. 그래서 **그 값 자체는 판정할 수
    # 없다** — 여기서 재는 것은 옆에 있던 조건이 살아남는가 하나다.
    #
    # 각 발화는 문제되는 조건만 뺀 대조군과 짝을 이룬다. 짝에서만 값이
    # 살아나면 원인이 그 조건으로 좁혀진다.
    #
    # search_center 기대값은 location_rules.md가 정한 대로다 — "~~로"는
    # 목적지이므로 "인사동"이다. 여기서 "홍대입구"가 나오면 모델이 목적지
    # 대신 출발지를 골랐다는 뜻이고, 그것도 별개의 관측 결과다.
    ("칸없음", "토요일 오후 2시부터 5시간 코스 짜줘", None, 300),
    ("칸없음", "5시간 코스 짜줘", None, 300),
    ("칸없음", "홍대입구에서 인사동으로 이동하는 5시간 코스", "인사동", 300),
    ("칸없음", "인사동 근처 5시간 코스", "인사동", 300),
    # --- ⑥ 동음이의 지명. 골드셋 DEV-075가 search_center를 통째로 놓친 자리다 ---
    # 2026-09-15 모델 비교에서 `유모차 끌고 갈 만한 수유 근처 카페 추천해줘`의
    # search_center가 gemini-3.5-flash-lite에서 null로 나왔다(3.1-lite는 뽑았다).
    # "수유"는 지명(수유동/수유역)이면서 동시에 유아 수유를 뜻하고, 발화에 "유모차"가
    # 함께 있다. **원인이 그 단어인지 그 조합인지 이 짝 넷이 가른다.**
    #
    #   유모차 O + 동음이의 O -> 원본
    #   유모차 X + 동음이의 O -> 살아나면 원인은 "유모차"
    #   유모차 O + 동음이의 X -> 살아나면 원인은 "수유"라는 단어
    #   유모차 X + 동음이의 X -> 대조군(골드셋 DEV-001과 같은 발화, 전 모델 통과)
    #
    # 넷 다 RECOMMEND 발화라 time_available은 null이 맞다(④ 대조군과 같다).
    ("동음이의", "유모차 끌고 갈 만한 수유 근처 카페 추천해줘", "수유", None),
    ("동음이의", "수유 근처 카페 추천해줘", "수유", None),
    ("동음이의", "유모차 끌고 갈 만한 성수동 근처 카페 추천해줘", "성수동", None),
    ("동음이의", "성수동 근처 카페 추천해줘", "성수동", None),
)


# 프롬프트 수정의 전후를 재는 12건. 18건 전부를 매번 도는 것은 낭비다 —
# 여섯 그룹 중 흔들림도 불일치도 없던 자리(`반나절 일정 짜줘`, `홍대입구에서
# 인사동으로`, `광화문 오후 내내`, `광화문 두세 시간`, `5시간 코스 짜줘`,
# `경복궁 근처 반나절 일정 짜줘`)는 어느 lite에서도 실패한 적이 없어 전후가
# 같을 것이 뻔하고, 호출 수만 1.5배로 만든다.
#
# 고른 기준은 셋이다.
#   ① 두 lite가 **3/3 고정으로** 실패한 2건 — 확률이 안 섞인 유일한 자리
#   ② 한쪽 lite만 실패한 6건 — 개선이 어느 모델까지 닿는지 가른다
#   ③ 두 lite가 다 맞히던 4건 — 고치다가 깨뜨리는 것을 잡는 대조군
# ③이 없으면 "실패 4건이 0건 됐다"가 회귀를 숨긴다.
EXTRACT_FIX_SUBSET: tuple[str, ...] = (
    # ① 고정 실패 (3.5-lite·3.1-lite 모두 3/3)
    "경복궁 코스 짜줘",
    "경복궁 일정 짜줘",
    # ② 한쪽만 실패
    "경복궁 근처 일정 짜줘",  # 3.1-lite 흔들림
    "토요일 오후 2시부터 5시간 코스 짜줘",  # 3.1-lite 실패
    "광화문 4시간 일정 짜줘",  # 3.5-lite만
    "광화문 하루 종일 일정 짜줘",  # 3.5-lite만
    "경복궁 근처 3시간 코스 짜줘",  # 3.5-lite만
    "광화문 반나절 일정 짜줘",  # 3.5-lite만
    # ③ 회귀 대조군 (두 lite 모두 통과하던 자리)
    "경복궁 근처 카페 추천해줘",
    "종로에서 15분 이내 카페 추천해줘",
    "북촌 반나절 코스",
    "인사동 근처 5시간 코스",
)


def _select_cases(only: str | None, subset: str | None) -> tuple[tuple, ...]:
    """돌릴 케이스를 고른다. 둘 다 없으면 18건 전부."""
    if subset == "extract-fix":
        wanted = set(EXTRACT_FIX_SUBSET)
        chosen = tuple(c for c in CASES if c[1] in wanted)
        missing = wanted - {c[1] for c in chosen}
        if missing:
            # 발화를 CASES에서 고쳐 놓고 이 목록을 안 고치면 조용히 적게 돈다.
            raise SystemExit(f"CASES에 없는 발화가 부분집합에 있습니다: {sorted(missing)}")
        return chosen
    if only:
        needles = [s.strip() for s in only.split(",") if s.strip()]
        chosen = tuple(c for c in CASES if any(n in c[1] for n in needles))
        if not chosen:
            raise SystemExit(f"--only에 걸리는 발화가 없습니다: {needles}")
        return chosen
    return CASES


def _served_model() -> str | None:
    """직전 호출에 실제로 답한 모델. 폴백으로 넘어갔는지 여기서 드러난다."""
    metadata = get_llm_execution_metadata()
    if metadata is None or not metadata.calls:
        return None
    return metadata.calls[-1].served_model


def _tokens() -> dict[str, int]:
    """직전 호출의 토큰. **사고 토큰을 보려고 넣었다.**

    `thinking_budget=0`은 `_thinking_config_for()`에서 `thinking_level=MINIMAL`로
    바뀌어 나가는데, **그것이 실제로 생각을 끄는지는 모델마다 다르다.** 사고
    토큰은 과금 대상이면서 출력 토큰에 안 잡히므로, 안 세면 새 모델의 비용을
    과소 집계한다. 특히 `gemini-3.6-flash`처럼 숫자 0을 거부하는 모델을 잴 때
    이 열이 없으면 "왜 비싼가"에 답할 수 없다.
    """
    metadata = get_llm_execution_metadata()
    if metadata is None or not metadata.calls:
        return {}
    out: dict[str, int] = {}
    for call in metadata.calls:
        for key, value in (
            ("입력토큰", call.input_tokens),
            ("출력토큰", call.output_tokens),
            ("사고토큰", call.thoughts_tokens),
            ("캐시토큰", call.cached_tokens),
        ):
            if isinstance(value, int):
                out[key] = out.get(key, 0) + value
    return out


async def _extract(
    provider: RealGeminiProvider, text: str
) -> tuple[str | None, int | None, str, str | None, str | None, int, dict[str, int]]:
    """(search_center, time_available, 페이로드 모양, 응답 모델, 오류, ms, 토큰).

    **`모양`을 따로 내는 이유가 이 스크립트의 가장 큰 구멍이었다.** 이전 판은
    `recommend`가 None이든, 페이로드는 왔는데 두 칸이 빈 것이든 똑같이
    `null/null`로 찍었다. `3.1-lite_결과.md` §3도 "이 러너는 구분하지 못한다"고
    적어뒀는데, 그 둘은 **고칠 방법이 정반대다** —

      - 페이로드 없음: `extract.md`의 "반드시 recommend.conditions를 채우라"는
        계약 위반이다. 오케스트레이터의 되뽑기(D-126/TP-266)가 이미 잡는 자리고,
        지명 규칙을 아무리 손봐도 안 고쳐진다
      - 페이로드 있음 + 칸이 빔: 진짜 추출 실패다. 프롬프트 규칙으로 닫는다

    `intent`도 함께 남긴다. 추출 프롬프트는 `intent="RECOMMEND"`로 반환하라고
    못 박는데 일정 발화에서 모델이 SCHEDULE을 고르면 `recommend`가 빈 채로 올 수
    있다. 그 경우 원인이 지명이 아니라 **인텐트 자리**라는 뜻이다.

    실 API 호출 수는 그대로다 — 이미 받아 온 응답에서 읽기만 한다.
    """
    reset_llm_execution_metadata()
    started = time.perf_counter()
    try:
        result = await provider.extract_recommend_conditions(text)
        recommend = result.data.recommend
        conditions = recommend.conditions if recommend else None
        search_center = conditions.search_center if conditions else None
        time_available = conditions.time_available if conditions else None
        if recommend is None:
            shape = f"페이로드없음({result.data.intent.value})"
        elif conditions is None:
            shape = f"조건없음({result.data.intent.value})"
        else:
            shape = f"정상({result.data.intent.value})"
        error = None
    except Exception as exc:  # noqa: BLE001 - 실 API 검증 스크립트
        search_center, time_available = None, None
        shape = "예외"
        error = f"{type(exc).__name__}: {exc}"
    ms = round((time.perf_counter() - started) * 1000)
    return search_center, time_available, shape, _served_model(), error, ms, _tokens()


async def run(
    model: str | None,
    repeat: int,
    delay: float,
    *,
    cases: tuple[tuple, ...] = CASES,
    max_consecutive_errors: int = 3,
) -> list[dict[str, object]]:
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
    chain = " → ".join(fast) if len(fast) > 1 else f"{fast[0]} (폴백 없음)"
    print(f"모델 묶음: {chain} | 반복: {repeat}회 | 케이스: {len(cases)}건")

    rows: list[dict[str, object]] = []
    token_totals: dict[str, int] = {}
    # 연속 실패 중단. 설정 오류는 케이스마다 독립 사건이 아니라서, 끝까지 도는
    # 루프는 비용을 케이스 수만큼 곱한다(RULES 함정 46).
    consecutive_errors = 0
    for group, text, expected_center, expected_time in cases:
        centers: list[str | None] = []
        times: list[int | None] = []
        shapes: list[str] = []
        models: list[str | None] = []
        errors: list[str] = []
        latencies: list[int] = []
        for _ in range(repeat):
            center, time_available, shape, served, error, ms, toks = await _extract(
                provider, text
            )
            for key, value in toks.items():
                token_totals[key] = token_totals.get(key, 0) + value
            centers.append(center)
            times.append(time_available)
            shapes.append(shape)
            models.append(served)
            latencies.append(ms)
            if error:
                errors.append(error)
            print("." if error is None else "!", end="", flush=True)
            if error is None:
                consecutive_errors = 0
            else:
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    raise SystemExit(
                        f"\n연속 {consecutive_errors}건 실패로 중단합니다. "
                        f"마지막 오류: {error}"
                    )
            await asyncio.sleep(delay)
        rows.append(
            {
                "그룹": group,
                "발화": text,
                "기대_center": expected_center,
                "기대_time": expected_time,
                "centers": centers,
                "times": times,
                "모양": shapes,
                "models": models,
                "오류": errors,
                "ms_평균": round(sum(latencies) / len(latencies)),
            }
        )

    print(flush=True)
    if token_totals:
        done = sum(1 for r in rows for _ in [0])  # 케이스 수
        calls = max(done * repeat, 1)
        summary = "  ".join(
            f"{k} {v:,} (호출당 {v // calls:,})" for k, v in token_totals.items()
        )
        print(f"토큰 합계 — {summary}")
        # **사고 토큰이 0이 아니면 그 모델은 MINIMAL에서도 생각한다.** 과금 대상이다.
        if token_totals.get("사고토큰"):
            print(
                "⚠️  사고 토큰이 0이 아니다 — 이 모델은 thinking_level=MINIMAL에서도 "
                "생각한다. 비용 계산에 반드시 포함할 것."
            )
        # **자동 캐싱이 걸렸는지.** Gemini는 2.5 이상에서 기본으로 켜져 있고 최소
        # 4,096토큰을 넘어야 적중한다. 추출 프롬프트는 그 문턱 바로 위로 추정돼
        # 있어서, 실제로 넘는지 못 넘는지가 입력 비용을 열 배로 가른다.
        cached = token_totals.get("캐시토큰", 0)
        inp = token_totals.get("입력토큰", 0)
        if cached:
            print(f"자동 캐싱 적중 — 입력의 {cached * 100 // max(inp, 1)}%가 캐시에서 읽혔다.")
        else:
            print(
                "⚠️  캐시 적중 0 — 자동 캐싱이 한 번도 안 걸렸다. 프롬프트가 최소 "
                "토큰(3.5·3.6 Flash 계열 4,096)에 못 미치거나 접두가 매 턴 달라지는 것이다."
            )
    return rows


def _fmt(values: list[object]) -> str:
    """실행값 목록을 표시용 문자열로 만든다. 전부 같으면 값 하나만 보여준다."""
    seen = ["null" if v is None else str(v) for v in values]
    unique = sorted(set(seen))
    return unique[0] if len(unique) == 1 else " / ".join(seen)


def _is_stable(values: list[object]) -> bool:
    return len({"null" if v is None else str(v) for v in values}) == 1


def _matches(values: list[object], expected: object) -> bool | None:
    """기대값과 전부 일치하면 True. ANY면 판정하지 않고 None."""
    if expected == ANY:
        return None
    return all(v == expected for v in values)


def _short_model(name: str | None) -> str:
    """gemini-3.5-flash-lite → 3.5-flash-lite. 표가 넘치지 않게 접두사만 뗀다."""
    if name is None:
        return "?"
    return name.removeprefix("gemini-")


def _report(rows: list[dict[str, object]], repeat: int) -> int:
    unstable: list[dict[str, object]] = []
    mismatched: list[dict[str, object]] = []
    fell_back: list[dict[str, object]] = []

    empty_payload: list[dict[str, object]] = []

    header = (
        f"{'흔들림':<8} {'일치':<6} {'search_center':<20} "
        f"{'time_available':<16} {'페이로드 모양':<28} {'응답모델':<20} {'지연':<8} 발화"
    )
    print(f"\n{header}")
    print("-" * 170)
    current_group = None
    for r in rows:
        if r["그룹"] != current_group:
            current_group = r["그룹"]
            print(f"[{current_group}]")

        centers = r["centers"]  # type: ignore[assignment]
        times = r["times"]  # type: ignore[assignment]
        models = [_short_model(m) for m in r["models"]]  # type: ignore[union-attr]
        stable = _is_stable(centers) and _is_stable(times)
        center_ok = _matches(centers, r["기대_center"])
        time_ok = _matches(times, r["기대_time"])

        judged = [v for v in (center_ok, time_ok) if v is not None]
        if not judged:
            match_mark = "관측"
        elif all(judged):
            match_mark = "✅"
        else:
            match_mark = "❌"
            mismatched.append(r)

        if not stable:
            unstable.append(r)
        if not _is_stable(models):
            fell_back.append(r)
        shapes = r.get("모양") or []
        if any(str(s).startswith("페이로드없음") for s in shapes):  # type: ignore[union-attr]
            empty_payload.append(r)

        print(
            f"{'⚠️  흔들림' if not stable else '  고정':<8} {match_mark:<5} "
            f"{_fmt(centers):<20} {_fmt(times):<16} {_fmt(list(shapes)):<28} "
            f"{_fmt(models):<20} {str(r['ms_평균']) + 'ms':<8} {r['발화']}"
        )
        for error in set(r["오류"]):  # type: ignore[arg-type]
            print(f"{'':>16} ⚠️  {error}")

    print(f"\n{'=' * 70}")
    # 모델을 바꿀지 정하려면 품질만으로는 부족하다 — 느려지는 정도가 반대편
    # 근거이므로 같은 표에서 함께 본다. 값은 이미 모으고 있었고 출력만 없었다.
    all_ms = sorted(int(r["ms_평균"]) for r in rows)  # type: ignore[arg-type]
    median_ms = all_ms[len(all_ms) // 2]
    print(
        f"지연  중앙값 {median_ms}ms · 평균 {round(sum(all_ms) / len(all_ms))}ms · "
        f"최소 {all_ms[0]}ms · 최대 {all_ms[-1]}ms"
    )
    print(f"반복 {repeat}회 기준")
    if repeat < 2:
        # **반복 1회에서는 흔들림이 정의상 0이다.** 그걸 "0/18건"으로 찍으면
        # 다음 사람이 "이 모델은 안 흔들린다"로 읽는다 — 구조적으로 0인 칸을
        # 근거로 세는 것이 RULES 함정 50이다. 숫자 대신 판정 불가를 적는다.
        print("  흔들린 케이스        판정 불가 (반복 1회 — 같은 입력을 두 번 안 넣었다)")
    else:
        print(f"  흔들린 케이스        {len(unstable)}/{len(rows)}건")
    if repeat >= 2:
        for r in unstable:
            print(f"    - {r['발화']}  center={_fmt(r['centers'])} time={_fmt(r['times'])}")
    # **불일치를 두 종류로 갈라 센다.** 이 줄이 없으면 프롬프트를 고쳐야 할
    # 건수와 되뽑기(D-126)가 이미 잡는 건수가 한 숫자에 섞인다.
    print(f"  페이로드가 비어 온 케이스 {len(empty_payload)}/{len(rows)}건")
    for r in empty_payload:
        print(f"    - {r['발화']}  모양={_fmt(list(r.get('모양') or []))}")
    print(f"  응답 모델이 바뀐 케이스 {len(fell_back)}/{len(rows)}건")
    for r in fell_back:
        print(f"    - {r['발화']}  {_fmt([_short_model(m) for m in r['models']])}")
    print(f"  기대 불일치 케이스     {len(mismatched)}/{len(rows)}건")
    for r in mismatched:
        print(f"    - {r['발화']}  기대(center={r['기대_center']}, time={r['기대_time']})")

    if fell_back:
        print(
            "\n응답 모델이 바뀐 케이스가 있다 — 흔들림의 원인이 프롬프트가 아니라 "
            "폴백일 수 있다. --model로 폴백을 없애고 다시 재서 갈라본다."
        )
    print(
        "\n흔들린 케이스가 남아 있으면 프롬프트 개선의 전후 비교가 성립하지 않는다 "
        "— 기대 불일치보다 이쪽을 먼저 닫는다."
    )
    return len(unstable)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model", default=None, help="이 모델 하나만 쓰고 폴백을 두지 않는다(기본: 설정값+폴백)"
    )
    parser.add_argument("--repeat", type=int, default=3, help="발화당 반복 횟수(기본 3)")
    parser.add_argument("--delay", type=float, default=1.0, help="호출 간 대기(초)")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="흔들림이 있으면 종료코드 1. 기본은 0 — 기준선 측정 자체는 실패가 아니다",
    )
    parser.add_argument(
        "--max-calls", type=int, default=60,
        help="예정 호출 수가 이 값을 넘으면 실행을 거부한다",
    )
    parser.add_argument(
        "--max-consecutive-errors", type=int, default=3,
        help="연속 실패가 이 횟수에 닿으면 중단한다",
    )
    parser.add_argument(
        "--out-dir", type=Path, default=None,
        help="주면 <out-dir>/verify_extraction_<모델>.json에 원자료를 남긴다",
    )
    parser.add_argument(
        "--subset", choices=("extract-fix",), default=None,
        help="이름 붙은 부분집합만 돈다. extract-fix=프롬프트 전후 비교용 12건",
    )
    parser.add_argument(
        "--only", default=None,
        help="발화에 이 문자열이 들어간 케이스만 돈다(쉼표로 여러 개). --subset이 우선",
    )
    args = parser.parse_args()

    cases = _select_cases(args.only, args.subset)
    planned = args.repeat * len(cases)
    print(f"예정 호출 {planned}건, 상한 {args.max_calls}건")
    if planned > args.max_calls:
        raise SystemExit(
            f"예정 호출 {planned}건이 --max-calls({args.max_calls})를 넘습니다. "
            "반복을 줄이거나 --max-calls를 명시하세요."
        )

    rows = asyncio.run(
        run(
            args.model,
            args.repeat,
            args.delay,
            cases=cases,
            max_consecutive_errors=args.max_consecutive_errors,
        )
    )
    unstable = _report(rows, args.repeat)

    if args.out_dir:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        # **요청 모델을 파일에 박는다.** 어느 모델의 기준선인지 나중에 config
        # 이력과 .env를 뒤져 확인하는 일이 실제로 있었다(RULES 함정 49).
        tag = (args.model or "설정값").replace("/", "_")
        # **부분집합 실행이 18건 기준선 파일을 덮어쓰면 안 된다.** 같은
        # 이름으로 적히면 나중에 "12건짜리인데 18건으로 읽는" 사고가 난다.
        if args.subset:
            tag = f"{tag}__{args.subset}"
        elif args.only:
            tag = f"{tag}__only{len(cases)}"
        out_path = args.out_dir / f"verify_extraction_{tag}.json"
        out_path.write_text(
            json.dumps(
                {
                    "요청_모델": args.model,
                    "반복": args.repeat,
                    "케이스_수": len(cases),
                    "흔들린_케이스_수": unstable,
                    "행": rows,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"원자료 저장: {out_path}")

    raise SystemExit(1 if (args.strict and unstable) else 0)


if __name__ == "__main__":
    main()
