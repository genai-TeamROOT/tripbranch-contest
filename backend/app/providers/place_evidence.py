"""사용자 발화로 취향 근거 문장을 벡터 검색하는 Provider.

역할: 발화를 임베딩해 `search_place_evidence` RPC를 부르고, 후보별 근거를
      돌려준다. 추천 점수에 "취향" 축을 더하기 위한 입력이다.

입력: 취향 질의 문장, 하드 필터를 통과한 후보 content_id 목록.
출력: content_id를 키로 하는 PlaceEvidenceMatch dict.
호출 시점: 하드 필터 이후, 채점 전.

**후보를 반드시 좁혀서 부른다.** RPC가 500건 상한을 강제하고, 좁히지 않으면
40,389행을 전부 훑어 6~9초가 걸린다(2026-08-18 실측).

임베딩 인코더는 Protocol로 주입받는다. `sentence-transformers`가 선택
의존성(`pip install -e ".[embeddings]"`)이라 기본 설치에는 없고, 모델을 서버에
상주시킬지가 배포 메모리(RSS 약 1.2GB) 결정에 달려 있기 때문이다. 인코더가
없는 환경에서는 이 Provider를 조립하지 않으면 되고, 테스트는 torch 없이 돈다.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Protocol

from app.domain.models import PlaceEvidenceMatch
from app.observability.langfuse_tracing import observe_step, record_score
from app.providers.contracts import (
    ProviderResult,
    ProviderSource,
    ProviderStatus,
    provider_result,
)
from app.repositories.protocols import PlaceEvidenceRepository

logger = logging.getLogger(__name__)

# 유사도 컷값. 대표 발화 11개의 유사도 분포를 재서 정했다(2026-08-18, RAG 계획
# 문서 §7.13). RPC 기본값은 0.0이라 호출부가 넘기지 않으면 필터가 걸리지 않는다.
DEFAULT_MIN_SIMILARITY = 0.43

# 장소당 남길 근거 문장 수. 근거 문장 한 줄을 만들기에 3개면 충분하고, 늘리면
# jsonb 응답만 커진다.
DEFAULT_MATCH_COUNT = 3


def _search_summary(
    candidate_content_ids: Sequence[str],
    matches: Sequence[PlaceEvidenceMatch],
    *,
    min_similarity: float,
    match_count: int,
) -> dict[str, object]:
    """취향 근거 검색 한 번을 span에 실을 집계로 접는다.

    **유사도 분포가 이 요약의 존재 이유다.** 취향 Feature 점수가 이 값에서 나오는데,
    지금까지는 "몇 곳이 걸렸나"조차 응답으로만 짐작했다. 상한(0.65) 재측정처럼 이
    축을 다시 손대는 일은 여기 숫자가 먼저 있어야 한다.

    **질의 원문과 근거 문장은 싣지 않는다.** 질의는 사용자 발화에서 뽑은 것이고,
    근거 문장은 `scoring` span에서도 같은 이유로 뺐다. 여기서 알고 싶은 건 몇 건이
    걸렸고 유사도가 어디에 몰려 있나다.
    """
    sims = sorted((m.avg_similarity for m in matches), reverse=True)
    return {
        "candidates": len(candidate_content_ids),
        "matched": len(sims),
        # 후보 중 몇 곳이 근거를 얻었나. 낮으면 취향 점수가 대부분 결측이라는 뜻이다.
        "hit_rate": round(len(sims) / len(candidate_content_ids), 3)
        if candidate_content_ids
        else None,
        "similarity_max": round(sims[0], 4) if sims else None,
        "similarity_min": round(sims[-1], 4) if sims else None,
        "similarity_mean": round(sum(sims) / len(sims), 4) if sims else None,
        # 컷을 어디에 두고 잰 결과인지 함께 남긴다 — 설정이 바뀌면 값을 비교할 수 없다.
        "min_similarity": min_similarity,
        "match_count": match_count,
    }


class PlaceEvidenceEncoder(Protocol):
    """취향 질의 문장을 적재 때와 같은 벡터 공간으로 인코딩하는 계약.

    적재는 `jhgan/ko-sroberta-multitask`(768차원, 정규화 임베딩)로 했다. 다른
    모델로 인코딩하면 유사도가 의미를 잃으므로 구현체는 같은 모델을 써야 한다.
    """

    def encode(self, text: str) -> Sequence[float]: ...


class PlaceEvidenceProvider:
    """발화 → 임베딩 → RPC 검색을 한 번에 처리한다."""

    def __init__(
        self,
        encoder: PlaceEvidenceEncoder,
        repository: PlaceEvidenceRepository,
        *,
        min_similarity: float = DEFAULT_MIN_SIMILARITY,
        match_count: int = DEFAULT_MATCH_COUNT,
    ) -> None:
        self._encoder = encoder
        self._repository = repository
        self._min_similarity = min_similarity
        self._match_count = match_count

    async def search(
        self,
        query: str,
        candidate_content_ids: Sequence[str],
    ) -> ProviderResult[dict[str, PlaceEvidenceMatch]]:
        """취향 질의로 후보 범위 안에서만 근거를 찾는다.

        질의가 비었거나 후보가 없으면 인코딩도 조회도 하지 않는다 — 취향을
        말하지 않은 요청이 대부분이라, 여기서 걸러야 모델 호출이 낭비되지 않는다.
        """
        if not query.strip() or not candidate_content_ids:
            return provider_result(
                {},
                source=ProviderSource.SUPABASE_PLACE_EVIDENCE,
                status=ProviderStatus.NO_DATA,
            )

        # 이 단계가 취향 점수의 **입력**을 만든다. 몇 곳이 걸렸고 유사도가 얼마였는지가
        # 안 보이면 "취향을 말했는데 점수가 왜 이러냐"를 코드로만 추론해야 한다.
        # 질의 원문은 싣지 않는다 — 사용자 발화에서 뽑은 문장이다.
        with observe_step("taste_evidence_search", kind="retriever") as step:
            embedding = await self._encode(query)
            matches = await self._repository.search_place_evidence(
                embedding,
                candidate_content_ids,
                match_count=self._match_count,
                min_similarity=self._min_similarity,
            )
            try:
                summary = _search_summary(
                    candidate_content_ids,
                    matches,
                    min_similarity=self._min_similarity,
                    match_count=self._match_count,
                )
                step.record(output=summary)
                # 취향 근거가 실제로 붙는 비율. 후보가 없으면 0/0이라 안 올린다 —
                # "근거를 못 찾았다"와 "찾을 후보가 없었다"를 같은 값으로 적으면
                # 평균이 거짓말을 한다.
                if summary["hit_rate"] is not None:
                    record_score("taste_hit_rate", float(summary["hit_rate"]))
            except Exception:
                logger.warning("취향 검색 관측 요약 실패(응답 흐름에는 영향 없음)", exc_info=True)
        return provider_result(
            {match.content_id: match for match in matches},
            source=ProviderSource.SUPABASE_PLACE_EVIDENCE,
            status=(
                ProviderStatus.SUCCESS if matches else ProviderStatus.NO_DATA
            ),
        )

    async def search_one_place(
        self,
        query: str,
        content_id: str,
        *,
        match_count: int,
        min_similarity: float,
    ) -> ProviderResult[PlaceEvidenceMatch | None]:
        """장소 한 곳 안에서만 근거를 찾는다. 후기로 답하는 INFO 질문이 쓴다.

        추천 채점용 `search()`와 컷·개수를 따로 받는 이유: 저쪽은 여러 장소를 줄
        세우려고 컷을 0.43으로 잡았지만, 여기는 후보가 한 곳뿐이라 경쟁이 없어 같은
        컷이 의미를 갖지 않는다. 실측(2026-09-14, 24개 질문)에서 답할 수 있는 질문과
        없는 질문의 유사도 분포가 크게 겹쳤다 — 답 가능 평균 0.650, 불가 평균 0.481에
        경계가 서지 않는다. 그래서 **유사도로 답 여부를 가르지 않는다.** 낮은 컷으로
        넉넉히 가져오고, 쓸 수 있는 근거인지는 뒤의 선별 단계가 판정한다.
        """
        if not query.strip() or not content_id:
            return provider_result(
                None,
                source=ProviderSource.SUPABASE_PLACE_EVIDENCE,
                status=ProviderStatus.NO_DATA,
            )

        with observe_step("review_evidence_search", kind="retriever") as step:
            embedding = await self._encode(query)
            matches = await self._repository.search_place_evidence(
                embedding,
                [content_id],
                match_count=match_count,
                min_similarity=min_similarity,
            )
            match = matches[0] if matches else None
            try:
                step.record(
                    output={
                        "snippets": len(match.snippets) if match else 0,
                        "avg_similarity": round(match.avg_similarity, 4) if match else None,
                        "min_similarity": min_similarity,
                        "match_count": match_count,
                    }
                )
            except Exception:
                logger.warning("후기 검색 관측 요약 실패(응답 흐름에는 영향 없음)", exc_info=True)
        return provider_result(
            match,
            source=ProviderSource.SUPABASE_PLACE_EVIDENCE,
            status=ProviderStatus.SUCCESS if match else ProviderStatus.NO_DATA,
        )

    async def _encode(self, query: str) -> Sequence[float]:
        """문장 인코딩은 CPU를 수십 ms 잡아먹는 동기 호출이라 스레드로 내보낸다.

        추천 경로는 턴당 한 번이라 넘어갔지만, 후기 답변은 채팅 스트리밍 도중에
        일어난다 — 이벤트 루프에서 그대로 돌리면 그동안 다른 사용자의 토큰 전송까지
        멈춘다.
        """
        return await asyncio.to_thread(self._encoder.encode, query)
