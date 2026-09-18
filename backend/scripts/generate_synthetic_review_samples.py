"""같은 표본 10곳을 Gemini로 돌려 기준선을 만든다.

콜랩 노트북과 같은 입력(시스템 지시문 + promptPayload + 응답 스키마)을 쓰고, 결과를
같은 jsonl 형식으로 남긴다. 그래야 score_results.py로 똑같이 채점해 비교할 수 있다.

저장소의 GeminiSyntheticReviewGenerator.generate()를 그대로 쓰지 않는 이유는, 그
메서드가 검증 실패 시 예외를 던져 원본 출력을 잃기 때문이다. 통과율을 재려면 실패한
출력도 그대로 남겨야 한다.

실행:
    cd /Users/jinhyoungkim/Dev/TripBranch-synthetic-reviews/backend
    python <이 파일 경로> --sample <경로>/places_sample.json --out <경로>/reviews__gemini.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections.abc import Sequence
from pathlib import Path

from google import genai
from google.genai import types as genai_types

from app.config import Settings
from app.synthetic_reviews.review_generator import (
    _SYSTEM_INSTRUCTION,
    wire_schema_for,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="표본 장소를 Gemini로 생성해 오픈 모델과 비교할 기준선을 만든다"
    )
    parser.add_argument("--sample", required=True, help="places_sample.json 경로")
    parser.add_argument("--out", required=True, help="출력 jsonl 경로")
    parser.add_argument("--model", help="생략 시 LLM_FAST_MODEL_NAME")
    return parser


async def main_async(args: argparse.Namespace) -> int:
    settings = Settings()
    if not settings.llm_api_key.strip():
        raise ValueError("LLM_API_KEY가 필요합니다. backend/.env를 확인하세요.")
    model_name = args.model or settings.llm_fast_model_name

    document = json.loads(Path(args.sample).read_text(encoding="utf-8"))
    places = document["places"]

    out_path = Path(args.out)
    done: set[str] = set()
    if out_path.exists():
        done = {
            json.loads(line)["contentId"]
            for line in out_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        print(f"이미 완료된 {len(done)}곳은 건너뜁니다.")

    pending = [p for p in places if p["contentId"] not in done]
    print(f"모델 {model_name}로 {len(pending)}곳 생성\n")

    client = genai.Client(api_key=settings.llm_api_key)
    total_in = total_out = 0

    for index, place in enumerate(pending, 1):
        started = time.perf_counter()
        try:
            response = await client.aio.models.generate_content(
                model=model_name,
                contents=place["promptPayload"],
                config=genai_types.GenerateContentConfig(
                    system_instruction=_SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    response_schema=wire_schema_for(place["reviewCount"]),
                    temperature=0.7,
                ),
            )
        except Exception as exc:  # 키가 노출되지 않도록 형식과 메시지만 남긴다
            print(f"[{index}/{len(pending)}] {place['title']}  호출 실패: {type(exc).__name__}")
            continue
        elapsed = time.perf_counter() - started

        usage = getattr(response, "usage_metadata", None)
        prompt_tokens = getattr(usage, "prompt_token_count", None)
        output_tokens = getattr(usage, "candidates_token_count", None)
        total_in += prompt_tokens or 0
        total_out += output_tokens or 0

        record = {
            "contentId": place["contentId"],
            "title": place["title"],
            "model": model_name,
            "raw": response.text or "",
            "elapsedSeconds": round(elapsed, 2),
            "promptTokens": prompt_tokens,
            "outputTokens": output_tokens,
            "finishReason": "stop",
        }
        with out_path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")

        print(
            f"[{index}/{len(pending)}] {place['title']}  "
            f"{elapsed:.1f}초  입력 {prompt_tokens}  출력 {output_tokens}토큰"
        )

    # Flash-Lite 공개 단가 기준 참고값이다.
    cost = total_in / 1_000_000 * 0.30 + total_out / 1_000_000 * 2.50
    print(f"\n입력 {total_in:,}토큰 / 출력 {total_out:,}토큰")
    print(f"이번 실행 비용: 약 ${cost:.4f} (원화 약 {cost * 1383:,.0f}원)")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(main_async(build_parser().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
