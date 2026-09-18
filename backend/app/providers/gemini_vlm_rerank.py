"""Gemini에게 사진을 보여 주고 사진 검색 후보의 순서를 다시 매기는 구현.

역할: 임베딩이 좁혀 온 후보 몇 곳을, 사진을 실제로 보는 모델에게 다시 줄 세우게 한다.
호출 시점: `POST /api/places/similar-by-photo`의 하드 필터 뒤(D-117, TP-214).

**왜 필요한가.** 임베딩만으로 개선하려는 시도를 아홉 번 했고 평균 빼기(D-115) 하나만
통했다. 나머지는 전부 같은 벡터를 다르게 읽는 방식이었고, 눈가림 채점에서 잡음 바닥
(2.6%p)을 넘지 못했다. 사람이 매긴 결과에서 뚜렷하게 올라간 것은 **다른 판단자를
붙였을 때**뿐이다 — 31.6% → 38.5~41.0%다(TP-213).

**순위를 시키지 않고 점수를 매기게 한다.** 후보마다 0/1/2로 매기게 하고 정렬은 이쪽에서
한다. 순위를 시키면 근거 없이 나열하고, 점수를 시키면 후보를 하나씩 따로 본다. 사람
채점자에게 준 것과 같은 기준을 그대로 준다.

**실패하면 원래 순서를 돌려준다.** 재랭킹은 보강이지 필수가 아니므로, 여기서 예외를
올려 사진 검색 자체를 죽이지 않는다. 대신 실패를 로그와 `record_call`에 남긴다.
D-042(Real 실패 시 Fake로 자동 전환하지 않는다)와는 성격이 다르다 — 가짜 데이터로
바꿔치기하는 것이 아니라 이미 있는 임베딩 결과를 그대로 쓰는 것이다.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from app.observability.api_usage import record_call

logger = logging.getLogger(__name__)

_RERANK_INSTRUCTION = """사진 여러 장을 준다. 첫 번째가 사용자가 올린 사진이고,
나머지는 후보 장소의 사진이다.

각 후보가 **첫 번째 사진과 분위기가 비슷한지**를 매겨라.

  2 = 분위기가 비슷하다
  1 = 애매하다
  0 = 분위기가 다르다

판단 기준:
- 같은 종류인지가 아니라 **분위기**를 본다. 카페 두 곳이라도 하나는 밝고 북적이고
  다른 하나는 어둡고 차분하면 "다르다"이다.
- 낡음과 새것, 정돈됨과 어수선함, 따뜻한 색과 차가운 색, 한산함과 북적임,
  실내와 실외 같은 것이 분위기다.
- 사진의 화질이나 찍은 솜씨는 보지 않는다.

후보 번호 순서대로 점수만 JSON 배열로 답하라. 설명하지 마라."""

_IMAGE_MIME_TYPE = "image/jpeg"

# 후보 사진을 받아오는 시간. 전체 타임아웃과 별개로 짧게 잡는다 — 한 장이 느리다고
# 재랭킹 전체를 붙잡고 있을 이유가 없고, 못 받은 후보는 그냥 빼면 된다.
_PHOTO_FETCH_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class RerankCandidate:
    """재랭킹에 넣을 후보 한 곳."""

    content_id: str
    photo_url: str


class GeminiPhotoReranker:
    """Gemini 멀티모달 입력으로 사진 검색 후보의 순서를 다시 매긴다."""

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        timeout_seconds: float,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        # Gemini SDK는 공유 httpx 클라이언트를 받지 않고 자체 비동기 클라이언트를
        # 관리한다(gemini.py 주석 참고). 사진을 받아오는 데만 공유 클라이언트를 쓴다.
        self._client = genai.Client(
            api_key=api_key,
            http_options=genai_types.HttpOptions(timeout=int(timeout_seconds * 1000)),
        )
        self._http = http_client
        self.model_name = model_name

    async def rerank(
        self,
        *,
        query_image: bytes,
        candidates: Sequence[RerankCandidate],
    ) -> tuple[str, ...] | None:
        """후보를 다시 줄 세운 `content_id` 순서를 돌려준다.

        재랭킹하지 못하면 `None`을 돌려준다 — 호출 측은 원래 순서를 그대로 쓴다.
        예외를 올리지 않는 것이 이 메서드의 계약이다.
        """
        if len(candidates) < 2:
            # 한 곳이면 바꿀 것이 없다. 돈만 쓴다.
            return None

        usable, parts = await self._collect_parts(query_image, candidates)
        if len(usable) < 2:
            logger.warning(
                "VLM 재랭킹: 사진을 받은 후보가 %d곳뿐이라 건너뜁니다.", len(usable)
            )
            return None

        marks = await self._score(parts, len(usable))
        if marks is None:
            return None

        # 같은 점수면 임베딩 순서를 유지한다. 사람이 매긴 값도 3단계뿐이라 동점이
        # 흔한데, 그때 임의로 섞으면 이미 확인된 순서를 근거 없이 버리는 셈이 된다.
        order = sorted(range(len(usable)), key=lambda i: (-marks[i], i))
        return tuple(usable[i].content_id for i in order)

    async def _collect_parts(
        self, query_image: bytes, candidates: Sequence[RerankCandidate]
    ) -> tuple[list[RerankCandidate], list[genai_types.Part]]:
        """질의 사진과 후보 사진을 Gemini에 넣을 형태로 모은다.

        사진을 직접 받아 바이트로 넣는다. URL을 넘겨 Gemini가 받아오게 하면 그쪽에서
        실패했을 때 어느 후보가 빠졌는지 알 수 없다.
        """
        parts = [
            genai_types.Part.from_bytes(data=query_image, mime_type=_IMAGE_MIME_TYPE)
        ]
        usable: list[RerankCandidate] = []
        client = self._http or httpx.AsyncClient(timeout=_PHOTO_FETCH_TIMEOUT_SECONDS)
        owned = self._http is None
        try:
            for candidate in candidates:
                try:
                    response = await client.get(
                        candidate.photo_url, timeout=_PHOTO_FETCH_TIMEOUT_SECONDS
                    )
                    response.raise_for_status()
                except Exception as error:  # 한 장 실패가 전체를 막지 않는다
                    logger.warning(
                        "VLM 재랭킹: 후보 %s의 사진을 받지 못했습니다 (%s).",
                        candidate.content_id,
                        type(error).__name__,
                    )
                    continue
                parts.append(
                    genai_types.Part.from_bytes(
                        data=response.content, mime_type=_IMAGE_MIME_TYPE
                    )
                )
                usable.append(candidate)
        finally:
            if owned:
                await client.aclose()
        return usable, parts

    async def _score(
        self, parts: list[genai_types.Part], count: int
    ) -> list[float] | None:
        """후보 수만큼의 점수를 받아온다. 못 받으면 `None`."""
        started = time.perf_counter()
        try:
            response = await self._client.aio.models.generate_content(
                model=self.model_name,
                contents=[
                    genai_types.Content(
                        role="user",
                        parts=[*parts, genai_types.Part(text=_RERANK_INSTRUCTION)],
                    )
                ],
                config=genai_types.GenerateContentConfig(
                    temperature=0.0,
                    response_mime_type="application/json",
                    # **칸 수를 형식으로 못 박는다.** 프롬프트로 부탁하면 모델이
                    # 한 칸 모자란 답을 낸다 — flash-lite는 후보 12곳일 때 14번 중
                    # 12번을 그렇게 답했다. 형식을 고정하면 실패가 사라지고, 덤으로
                    # 응답이 2.5배 빨라진다(32.6초 → 13.1초, TP-213).
                    response_schema=genai_types.Schema(
                        type=genai_types.Type.ARRAY,
                        items=genai_types.Schema(type=genai_types.Type.INTEGER),
                        min_items=count,
                        max_items=count,
                    ),
                ),
            )
        except (httpx.TimeoutException, TimeoutError):
            # 전송 계층이 aiohttp일 수도 httpx일 수도 있어 둘 다 잡는다
            # (gemini.py의 `_TIMEOUT_ERRORS` 주석 참고).
            self._record(started, ok=False, status="timeout")
            logger.warning("VLM 재랭킹: 시간이 초과되어 임베딩 순서를 그대로 씁니다.")
            return None
        except genai_errors.APIError as error:
            self._record(started, ok=False, status=str(error.code))
            logger.warning(
                "VLM 재랭킹: Gemini 오류로 임베딩 순서를 그대로 씁니다 (%s).",
                error.code,
            )
            return None

        try:
            marks = [float(value) for value in json.loads(response.text or "")]
        except (TypeError, ValueError) as error:
            self._record(started, ok=False, status="unparsable")
            logger.warning("VLM 재랭킹: 응답을 읽지 못했습니다 (%s).", error)
            return None

        if len(marks) != count:
            # 스키마를 걸었으므로 여기 오면 안 되지만, 모델이 지키지 못했을 때
            # 조용히 잘못된 순서를 내는 것보다 원래 순서를 쓰는 편이 낫다.
            self._record(started, ok=False, status="count_mismatch")
            logger.warning(
                "VLM 재랭킹: 점수 개수가 %d개여야 하는데 %d개입니다.",
                count,
                len(marks),
            )
            return None

        self._record(started, ok=True, status="ok")
        return marks

    def _record(self, started: float, *, ok: bool, status: str) -> None:
        record_call(
            "gemini",
            self.model_name,
            ok=ok,
            latency_ms=(time.perf_counter() - started) * 1000,
            status=f"photo_rerank:{status}",
        )


__all__ = ["GeminiPhotoReranker", "RerankCandidate"]
