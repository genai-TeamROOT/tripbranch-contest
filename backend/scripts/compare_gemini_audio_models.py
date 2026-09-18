"""Gemini 오디오 트랜스크립션(음성→텍스트) 모델별 속도·정확도 비교.

역할: measure_gemini_audio_transcription_latency.py가 gemini-3.5-flash 하나만 쟀던 것을,
현재 API에서 실제로 쓸 수 있는 여러 후보 모델(플래그십/Lite/최신 버전)로 넓혀서 비교한다 —
음성 입력 기능을 Gemini로 구현하기로 할 경우 어떤 모델을 쓸지 고르기 위한 실측.

대상 모델은 하드코딩 대신 client.models.list()로 확인한 실제 사용 가능 모델 중, 오디오
입력이 가능한 generateContent 지원 모델(네이티브 오디오 대화 모델 bidiGenerateContent
계열은 스트리밍 전용이라 제외) 몇 개를 후보로 고정했다(2026-08-18 기준 카탈로그).

입력: 로컬에 미리 생성해둔 한국어 TTS 샘플 오디오(AIFF) 경로 목록(measure_gemini_audio_
transcription_latency.py와 동일한 파일 재사용). .env에 LLM_PROVIDER=real과 LLM_API_KEY
필요(샌드박스 환경은 외부 네트워크가 막혀 있어 실제 인터넷 접속이 되는 로컬 환경에서
돌려야 한다).
출력: 표준 출력 + backend/test_results/gemini_audio_model_comparison.csv
호출 시점: `python -m scripts.compare_gemini_audio_models`로 수동 실행
(1회성 측정 도구, pytest 스위트에는 포함하지 않는다 — 실제 API 호출 비용 때문).
"""

from __future__ import annotations

import asyncio
import csv
import statistics
import time
from dataclasses import dataclass
from pathlib import Path

from google import genai
from google.genai import types as genai_types

from app.config import settings

ROUNDS = 2

_CANDIDATE_MODELS = [
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.1-flash-lite",
]

RESULTS_DIR = Path(__file__).resolve().parent.parent / "test_results"
RESULTS_CSV = RESULTS_DIR / "gemini_audio_model_comparison.csv"

_AUDIO_DIR = Path(
    "/private/tmp/claude-501/-Users-mac-Documents-Project-Basic-Project-tripbranch/"
    "fd3cf403-d1a4-4aac-bcfa-15245f6691c5/scratchpad/audio_samples"
)

_TRANSCRIBE_INSTRUCTION = (
    "다음 오디오에 담긴 한국어 발화를 있는 그대로 받아써줘. 설명이나 따옴표 없이 "
    "텍스트만 출력해."
)


@dataclass
class Sample:
    label: str
    path: Path
    expected_text: str


_SAMPLES: list[Sample] = [
    Sample("q1_짧은_추천", _AUDIO_DIR / "q1.aiff", "경복궁 근처 카페 추천해줘"),
    Sample("q2_날씨_추천", _AUDIO_DIR / "q2.aiff", "비 오는데 갈 만한 곳 추천해줘"),
    Sample("q3_일정", _AUDIO_DIR / "q3.aiff", "오늘 오후 종로 일정 짜줘"),
    Sample(
        "q4_긴_조건",
        _AUDIO_DIR / "q4.aiff",
        "경복궁 근처에서 비 오는데 10분 이내로 갈만한 카페 추천해줘",
    ),
]


def _normalize(text: str) -> str:
    """띄어쓰기 차이("추천해줘" vs "추천해 줘")는 정확도 판정에서 무시한다."""
    return text.replace(" ", "")


@dataclass
class RoundResult:
    model: str
    label: str
    round_number: int
    elapsed_ms: float
    transcript: str
    correct: bool


async def _measure(
    client: genai.Client, model: str, sample: Sample
) -> list[RoundResult]:
    audio_bytes = sample.path.read_bytes()
    results: list[RoundResult] = []
    for round_number in range(1, ROUNDS + 1):
        started = time.perf_counter()
        try:
            response = await client.aio.models.generate_content(
                model=model,
                contents=[
                    _TRANSCRIBE_INSTRUCTION,
                    genai_types.Part.from_bytes(data=audio_bytes, mime_type="audio/aiff"),
                ],
                config=genai_types.GenerateContentConfig(temperature=0.0),
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            transcript = (response.text or "").strip()
        except Exception as exc:  # noqa: BLE001 — 벤치마크 스크립트, 실패도 결과로 기록
            elapsed_ms = (time.perf_counter() - started) * 1000
            transcript = f"[오류] {exc}"
        correct = _normalize(transcript) == _normalize(sample.expected_text)
        results.append(
            RoundResult(
                model=model,
                label=sample.label,
                round_number=round_number,
                elapsed_ms=elapsed_ms,
                transcript=transcript,
                correct=correct,
            )
        )
        mark = "OK" if correct else "MISS"
        print(
            f"  [{model:24}][{sample.label:14}] {round_number}회차 {elapsed_ms:8.1f}ms "
            f"{mark} -- {transcript!r}"
        )
    return results


def _summarize_ms(values: list[float]) -> dict[str, float]:
    return {
        "평균": statistics.mean(values),
        "중앙값": statistics.median(values),
        "최소": min(values),
        "최대": max(values),
    }


async def main() -> None:
    if settings.resolved_llm_provider != "real":
        print("LLM_PROVIDER(또는 PROVIDER_MODE)가 real이어야 합니다.")
        return
    if not settings.llm_api_key:
        print("LLM_API_KEY가 필요합니다.")
        return

    missing = [s for s in _SAMPLES if not s.path.exists()]
    if missing:
        print(f"오디오 샘플이 없습니다: {[str(s.path) for s in missing]}")
        return

    client = genai.Client(api_key=settings.llm_api_key)

    print(
        f"모델 {len(_CANDIDATE_MODELS)}개 × 샘플 {len(_SAMPLES)}개 × {ROUNDS}회 = "
        f"{len(_CANDIDATE_MODELS) * len(_SAMPLES) * ROUNDS}건\n"
    )

    all_results: list[RoundResult] = []
    for model in _CANDIDATE_MODELS:
        print(f"\n== {model} ==")
        for sample in _SAMPLES:
            all_results.extend(await _measure(client, model, sample))

    print("\n=== 모델별 요약 ===")
    summaries: dict[str, dict[str, float]] = {}
    for model in _CANDIDATE_MODELS:
        rows = [r for r in all_results if r.model == model]
        stats = _summarize_ms([r.elapsed_ms for r in rows])
        summaries[model] = stats
        accuracy = sum(1 for r in rows if r.correct) / len(rows)
        print(
            f"  {model:24} 평균 {stats['평균']:8.1f}ms 중앙값 {stats['중앙값']:8.1f}ms "
            f"(최소 {stats['최소']:.1f} / 최대 {stats['최대']:.1f}) "
            f"정확도 {accuracy * 100:5.1f}% ({sum(1 for r in rows if r.correct)}/{len(rows)})"
        )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with RESULTS_CSV.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.writer(fp)
        writer.writerow(["모델", "문장", "회차", "소요시간_ms", "정확", "변환결과"])
        for item in all_results:
            writer.writerow(
                [
                    item.model,
                    item.label,
                    item.round_number,
                    f"{item.elapsed_ms:.1f}",
                    item.correct,
                    item.transcript,
                ]
            )
        writer.writerow([])
        writer.writerow(["모델", "평균_ms", "중앙값_ms", "최소_ms", "최대_ms", "정확도"])
        for model in _CANDIDATE_MODELS:
            rows = [r for r in all_results if r.model == model]
            stats = summaries[model]
            accuracy = sum(1 for r in rows if r.correct) / len(rows)
            writer.writerow(
                [
                    model,
                    f"{stats['평균']:.1f}",
                    f"{stats['중앙값']:.1f}",
                    f"{stats['최소']:.1f}",
                    f"{stats['최대']:.1f}",
                    f"{accuracy * 100:.1f}%",
                ]
            )
    print(f"\n결과 저장: {RESULTS_CSV}")


if __name__ == "__main__":
    asyncio.run(main())
