"""INFO 질의를 C에 직접 넣어 question_type별 입력·출력을 눈으로 확인하는 스크립트.

역할: A 배선(D-054 인수인계 항목) 전에는 챗봇으로 INFO 상세·행사 경로에 닿을 수
없다. `ContextService.fetch_info_context()`를 직접 호출해 8종의 응답을 그대로
출력한다.
입력: 장소명(기본 "경복궁")과 question_type(생략하면 8종 전부).
출력: 표준 출력에 status·필드·출처. `--json`을 주면 A가 받을 계약 원본까지 찍는다.
호출 시점: `python -m scripts.try_info_context [장소명] [question_type]`으로 수동
실행한다(1회성 확인 도구, pytest 스위트에는 포함하지 않는다 — 실제 API 호출 비용
때문). 실제 TourAPI를 호출하므로 일일 한도를 소비한다. 반복 확인은
`PLACE_PROVIDER=fake`로 한다.

    python -m scripts.try_info_context                      # 경복궁 8종
    python -m scripts.try_info_context 창덕궁 event
    python -m scripts.try_info_context 경복궁 fee --json
    PLACE_PROVIDER=fake python -m scripts.try_info_context 경복궁 event
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import cast

import httpx

from app.agent_context.factory import get_context_provider
from app.agent_context.info_schemas import (
    EventInfoResult,
    InfoContextRequest,
    InfoContextResponse,
    InfoQuestionType,
    PlaceInfoResult,
)
from app.agent_context.service import ContextService
from app.config import settings

ALL_QUESTION_TYPES: tuple[InfoQuestionType, ...] = (
    "operating_hours",
    "fee",
    "parking",
    "facility",
    "location_info",
    "general_info",
    "event",
    "concentration",
)

_OVERVIEW_PREVIEW_LENGTH = 200
_SEPARATOR = "=" * 72


def _print_event_result(result: EventInfoResult) -> None:
    print(f"       기준일 = {result.reference_date}")
    print(f"       장소   = {result.resolved_place_name}")
    print(f"       직접 매칭 있음 = {result.has_direct_match}")
    if not result.events:
        print("       행사   = (없음)")
        return
    for item in result.events:
        # is_direct_match=False를 "그 장소의 행사"로 읽으면 안 된다(D-055).
        tag = "그 장소" if item.is_direct_match else "근처"
        distance = (
            f"{item.distance_km}km" if item.distance_km is not None else "거리 미상"
        )
        print(f"         · [{tag}] {item.title} ({distance})")
        print(f"           {item.start_date} ~ {item.end_date} / {item.address}")


def _print_place_result(result: PlaceInfoResult) -> None:
    print(f"       장소   = {result.resolved_place_name} (id={result.place_id})")
    if not result.fields:
        print("       fields = {} (답할 값 없음)")
        return
    for key, value in result.fields.items():
        shown = (
            value
            if len(value) <= _OVERVIEW_PREVIEW_LENGTH
            else value[:_OVERVIEW_PREVIEW_LENGTH] + " …(생략)"
        )
        print(f"         · {key}: {shown}")


def _print_response(response: InfoContextResponse) -> None:
    print(f"[출력] status = {response.status}")
    if response.error is not None:
        print(f"       error  = {response.error.code}: {response.error.message}")
    if response.clarification is not None:
        print(f"       되묻기 = {response.clarification.code}")

    result = response.result
    if result is None:
        print("       result = None")
    elif isinstance(result, EventInfoResult):
        _print_event_result(result)
    elif isinstance(result, PlaceInfoResult):
        _print_place_result(result)
    else:
        print(f"       장소   = {result.resolved_place_name} (근처 기준={result.is_proxy})")
        print(f"       예보일 = {result.forecast_date}")
        print(f"       혼잡도 = {result.concentration_label} ({result.concentration_rate})")

    sources = [item.source for item in response.metadata.provider_metadata]
    print(f"       출처   = {sources}")


async def run_one(
    service: ContextService,
    place_name: str,
    question_type: InfoQuestionType,
    *,
    as_json: bool,
) -> None:
    request = InfoContextRequest(
        request_id=f"manual-{question_type}",
        place_name=place_name,
        place_context="explicit",
        question_type=question_type,
    )

    print(f"\n{_SEPARATOR}")
    print(f"[입력] place_name={place_name!r}  question_type={question_type!r}")
    print("-" * 72)

    response = await service.fetch_info_context(request)
    _print_response(response)

    if as_json:
        print("\n[원본 JSON — A가 받는 계약 그대로]")
        print(json.dumps(response.model_dump(mode="json"), ensure_ascii=False, indent=2))


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("place_name", nargs="?", default="경복궁")
    parser.add_argument(
        "question_type",
        nargs="?",
        choices=ALL_QUESTION_TYPES,
        help="생략하면 8종을 모두 실행한다.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="응답 계약 원본을 JSON으로 함께 출력한다.",
    )
    args = parser.parse_args()

    question_types = (
        (cast(InfoQuestionType, args.question_type),)
        if args.question_type
        else ALL_QUESTION_TYPES
    )

    print(
        f"PLACE_PROVIDER={settings.resolved_place_provider}  "
        f"PLACE_DETAILS_SOURCE={settings.resolved_place_details_source}"
    )

    async with httpx.AsyncClient() as client:
        service = get_context_provider(client)
        for question_type in question_types:
            await run_one(service, args.place_name, question_type, as_json=args.as_json)


if __name__ == "__main__":
    asyncio.run(main())
