"""후기 근거를 답변에 쓸 수 있는 것만 남긴다.

역할: 벡터 검색이 돌려준 문장에서 **읽어도 뜻이 서지 않는 것**과 **관광 안내문**을
      걷어낸다. 남 이야기인지(다른 가게 후기인지)는 여기서 판정하지 않는다 — 그건
      문장의 주어를 읽어야 알 수 있어 선별 단계의 LLM이 맡는다.
입력: `PlaceEvidenceSnippet` 목록.
출력: 같은 순서의 부분집합.

**왜 관광 안내문을 빼나.** `tour_overview`는 TourAPI 소개글이라 기존 INFO 경로가 이미
쓰는 텍스트이고, 출처로 걸 블로그 링크도 없다. 후기로 답하는 자리에 그걸 섞으면
"사람들이 뭐라더라"를 물었는데 안내문을 읽어 주는 꼴이 된다.

**왜 조각을 빼나.** 저장된 문장 중 일부가 `많이 넓다`, `넓음 / 여유`, `공간 & 분위기`
처럼 본문에서 잘린 조각이다(`docs/근거문장-추출-고도화-방안-20260908.md`, 서초 150건 중
6건). 근거로 실으면 요약이 어색해지고, LLM이 그 조각을 부풀려 말할 여지를 준다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.domain.models import PlaceEvidenceSnippet

# 관광 안내문. 후기가 아니다.
_EXCLUDED_SOURCE_TYPE = "tour_overview"

# 이보다 짧으면 "무엇이 어떻다"가 문장 안에서 끝나지 않는다. 위 문서의 부적합 사례가
# 전부 20자 미만이었다(`많이 넓다` 6자, `넓음 / 여유` 7자).
_MIN_TEXT_LENGTH = 20

# 소제목·목록 조각. 구분자로 단어를 늘어놓기만 하고 서술이 없는 문장이다
# (`넓음 / 여유`, `추천 상황 혼자 방문 / 대화 중심 데이트`). 종결어미가 없는 것이
# 공통점이라 그것으로 가른다.
_LIST_SEPARATORS = re.compile(r"[/·|]|&")
_SENTENCE_ENDING = re.compile(r"(다|요|음|임|죠|네|까|나|군)[.!?~]?\s*$")


def _looks_like_heading(text: str) -> bool:
    """구분자로 토막 난 목록인데 문장으로 끝나지 않으면 소제목 조각으로 본다."""
    if not _LIST_SEPARATORS.search(text):
        return False
    return not _SENTENCE_ENDING.search(text)


def usable_snippets(
    snippets: Sequence[PlaceEvidenceSnippet],
    *,
    limit: int,
) -> tuple[PlaceEvidenceSnippet, ...]:
    """답변 근거로 넘길 만한 문장만 유사도 순서 그대로 남긴다.

    `limit`은 선별 LLM에 넘길 상한이다. 여기서 자르는 이유는 토큰이 아니라 판정
    품질이다 — 문장이 많아질수록 고르라는 지시가 흐려진다.
    """
    kept: list[PlaceEvidenceSnippet] = []
    for snippet in snippets:
        text = snippet.source_text.strip()
        if snippet.source_type == _EXCLUDED_SOURCE_TYPE:
            continue
        if len(text) < _MIN_TEXT_LENGTH:
            continue
        if _looks_like_heading(text):
            continue
        kept.append(snippet)
        if len(kept) >= limit:
            break
    return tuple(kept)


__all__ = ["usable_snippets"]
