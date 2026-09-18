"""LangGraph를 씌워서 늘어난 응답 시간 실측 (D-072, §10.3 6번).

역할: 그래프 이관의 병합 판정 기준 중 "응답 지연이 유의미하게 나빠지지 않을 것"을
확인한다. 재는 것은 **그래프라는 껍데기가 더한 비용**이지 전체 응답 시간이 아니다.

2026-08-24 측정 결과는 **호출당 약 1ms 고정 오버헤드**였다(문서 §9.12에 표로 기록).
외부 호출이 붙는 RECOMMEND는 428ms→439ms, SCHEDULE은 오히려 426ms→422ms로 잡음
범위였다. 실 LLM이 붙으면 응답이 초 단위라 1ms는 체감되지 않는다.

측정 설계에서 신경 쓴 것 세 가지:

1. **LLM은 fake로 고정한다.** 실 LLM을 쓰면 초 단위 편차에 밀리초 오버헤드가 묻힌다.
   따라서 이 수치에는 LLM 호출 시간이 빠져 있다 — 그래프 오버헤드만 분리한 값이다.
2. **ON/OFF를 번갈아 실행한다.** 한쪽을 몰아서 돌리면 캐시 워밍과 머신 부하 변화가
   한쪽에만 유리하게 작용한다.
3. **평균이 아니라 중앙값을 본다.** 첫 실행은 항상 느려서 평균을 왜곡한다(워밍업
   2회는 아예 버린다).

사용법:
    python scripts/measure_langgraph_overhead.py            # fake Provider — 순수 오버헤드
    python scripts/measure_langgraph_overhead.py --real     # 실제 Provider — 실사용 맥락

**`--real` 주의** — 카카오 도보경로 API가 중간에
`HTTP 400, message=API limit has been exceeded.`로 실패하기 시작한다. HTTP 400이지만
파라미터 오류가 아니라 **초당 요청 속도 제한**이다(카카오가 429가 아닌 400으로 준다).
이 스크립트는 대기 없이 연속으로 요청을 쏘고 요청 하나당 도보경로 7건을 동시에
부르므로 그 밀도를 만든다. 실사용에서는 나오지 않는 패턴이고, 실패해도 코드가
직선거리 추정으로 대체하므로 측정 자체는 유효하다. 자세한 확인 과정은 §9.12 참고.
"""

from __future__ import annotations

import asyncio
import os
import statistics
import sys
import time

USE_REAL = "--real" in sys.argv
ITERATIONS = 12
WARMUP = 2

os.environ["STATE_STORE_BACKEND"] = "memory"
os.environ["LLM_PROVIDER"] = "fake"
if not USE_REAL:
    # .env가 개별 Provider를 real로 지정해 두어 PROVIDER_MODE만으로는 안 덮인다.
    os.environ["PROVIDER_MODE"] = "fake"
    for _key in (
        "WEATHER_PROVIDER",
        "PLACE_PROVIDER",
        "PLACE_DETAILS_PROVIDER",
        "GEOCODING_PROVIDER",
        "CONCENTRATION_PROVIDER",
        "HOLIDAY_PROVIDER",
        "LOCAL_SEARCH_PROVIDER",
        "TRAVEL_ROUTE_PROVIDER",
        "SEOUL_CITYDATA_PROVIDER",
        "DRIVING_ROUTE_PROVIDER",
        "TRANSIT_ROUTE_PROVIDER",
        "FESTIVAL_PROVIDER",
    ):
        os.environ[_key] = "fake"

from app.config import settings  # noqa: E402
from app.schemas import AgentRequest  # noqa: E402
from app.services.runtime.agent_runtime import run_agent  # noqa: E402
from app.state.store import get_store  # noqa: E402

GPS = "37.5796,126.9770"

# (라벨, 발화, 어느 그래프를 타는가)
CASES = [
    ("GENERAL", "넌 누구야?", "조기 반환(스트리밍)"),
    ("OUT_OF_SCOPE", "주식 추천해줘", "조기 반환(단발)"),
    ("RECOMMEND", "경복궁 근처 카페 추천해줘", "추천 파이프라인"),
    ("SCHEDULE", "경복궁 근처로 일정 짜줘", "추천 파이프라인 + 일정"),
]


async def measure(utterance: str, *, langgraph: bool) -> tuple[float, float | None]:
    """한 번 돌리고 (전체 소요, 첫 delta까지)를 ms로 돌려준다."""

    settings.use_langgraph_early_return = langgraph
    settings.use_langgraph_pipeline = langgraph
    get_store().clear()

    first_delta: float | None = None
    started = time.perf_counter()

    async def sink(event: str, payload: dict) -> None:
        nonlocal first_delta
        if event == "message_delta" and first_delta is None:
            first_delta = (time.perf_counter() - started) * 1000

    await run_agent(
        AgentRequest(
            user_input=utterance,
            device_location=GPS,
            debug_ignore_operating_hours=True,
        ),
        stream_event_sink=sink,
        stream_recommendation_summary=True,
    )
    return (time.perf_counter() - started) * 1000, first_delta


def summarize(samples: list[float]) -> tuple[float, float]:
    ordered = sorted(samples)
    p90 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))]
    return statistics.median(ordered), p90


async def main() -> None:
    print("=" * 78)
    print("LangGraph ON/OFF 응답 지연 비교")
    print(
        f"Provider: {'real(.env 그대로, LLM만 fake)' if USE_REAL else 'fake 전체'}"
        f" | 반복 {ITERATIONS}회(워밍업 {WARMUP}회 제외)"
    )
    print("=" * 78)

    for label, utterance, path in CASES:
        off_total: list[float] = []
        on_total: list[float] = []
        off_ttfd: list[float] = []
        on_ttfd: list[float] = []

        for index in range(ITERATIONS + WARMUP):
            # 번갈아 — 한쪽에만 유리한 조건이 쏠리지 않게 한다
            total_off, delta_off = await measure(utterance, langgraph=False)
            total_on, delta_on = await measure(utterance, langgraph=True)
            if index < WARMUP:
                continue
            off_total.append(total_off)
            on_total.append(total_on)
            if delta_off is not None:
                off_ttfd.append(delta_off)
            if delta_on is not None:
                on_ttfd.append(delta_on)

        off_median, off_p90 = summarize(off_total)
        on_median, on_p90 = summarize(on_total)
        gap = on_median - off_median
        pct = (gap / off_median * 100) if off_median else 0.0

        print(f"\n[{label}] {path}")
        print(
            f"  전체 소요  기존 {off_median:7.1f}ms (p90 {off_p90:7.1f})"
            f"  →  그래프 {on_median:7.1f}ms (p90 {on_p90:7.1f})"
            f"   차이 {gap:+.1f}ms ({pct:+.1f}%)"
        )
        if off_ttfd and on_ttfd:
            first_off, _ = summarize(off_ttfd)
            first_on, _ = summarize(on_ttfd)
            print(
                f"  첫 글자까지 기존 {first_off:7.1f}ms                →  "
                f"그래프 {first_on:7.1f}ms   차이 {first_on - first_off:+.1f}ms"
            )

    print("\n" + "=" * 78)
    print("판정 기준: 그래프 쪽이 유의미하게 느려지지 않을 것")
    print("퍼센트가 커 보여도 기준이 3~4ms면 의미 없다 — 절대값(ms)으로 판단할 것")
    print("=" * 78)


if __name__ == "__main__":
    asyncio.run(main())
