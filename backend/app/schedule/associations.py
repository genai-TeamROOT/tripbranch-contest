"""place_associations(TourAPI 관광지별 연관 관광지 정보, D-088)를 SCHEDULE 후보
사이의 "함께 방문된 이력" 힌트로 조회한다.

역할: `SchedulePlanningRequest.candidates`의 place_id 집합을 받아, 그 안에서
서로 연관된(co-visited) 쌍만 골라 돌려준다. planner.py가 이 결과를
`SchedulePlanningRequest.co_visited_hints`에 실어 LLM 프롬프트에 참고 정보로
넘긴다(app.providers.gemini_prompts.format_schedule_planning_context()).

이 모듈은 B가 이미 만든 `backend/scripts/query_place_associations.py`와 같은
Supabase 테이블·자격증명을 쓰지만, 그 스크립트는 content_id 하나를 기준으로
조회하는 수동 CLI용이고(문서화된 대로 "SCHEDULE/RECOMMEND 파이프라인에 실제로
연결하는 배선 작업은 이 스크립트의 범위가 아니다"), 이 모듈은 후보 집합 전체를
한 번에 조회해 그 안에서 완결된 쌍만 남기는 배선 전용 함수라 별도로 둔다.

호출은 전부 opt-in이다 — planner.py의 `plan_schedule()`은 `co_visited_fetcher`가
주어지지 않으면 이 모듈을 아예 import 시점 외에는 건드리지 않는다(기존 호출부
동작 불변). agent_runtime.py(A)가 이 모듈의 `fetch_co_visited_hints`를
`co_visited_fetcher`로 넘겨야 실제로 켜진다.
"""

from __future__ import annotations

from collections.abc import Sequence

import httpx
from pydantic import BaseModel

from app.config import Settings

_ASSOCIATIONS_PATH = "/rest/v1/place_associations"
# 후보 목록은 SCHEDULE 한 요청당 최대 5개(ScheduleLLMPlan.items 상한)라, 쌍의 수는
# 최악의 경우에도 아주 작다 — limit은 방어적 상한일 뿐 실사용에서 걸릴 일이 없다.
_DEFAULT_LIMIT = 200


class CoVisitedHint(BaseModel):
    """SCHEDULE 후보 두 곳이 실제로 함께 방문된 이력(place_associations 한 행)."""

    from_place_id: str
    to_place_id: str
    rank: int


async def fetch_co_visited_hints(
    candidate_place_ids: Sequence[str],
    settings: Settings,
    *,
    client: httpx.AsyncClient | None = None,
    limit: int = _DEFAULT_LIMIT,
) -> list[CoVisitedHint]:
    """candidate_place_ids 안에서 서로 연관된 쌍만 골라 rank 오름차순으로 돌려준다.

    `from_content_id`/`to_content_id` 둘 다 candidate_place_ids 안에 있는 행만
    필요하므로, PostgREST에 두 필터(`in.()`)를 동시에 걸어 AND로 좁힌다 —
    place_associations는 두 컬럼 다 복합 PK라 이 필터만으로 후보 집합 밖의
    행은 아예 응답에 안 실린다.

    후보가 2개 미만이면 쌍 자체가 성립하지 않아 조회를 생략한다. Supabase
    설정이 비어 있으면(예: 로컬 개발/테스트에서 supabase_url 미설정) 조용히
    빈 목록을 돌려준다 — 호출부(planner.py)가 이미 예외를 흡수하지만, 여기서도
    "설정 없음"을 정상 흐름으로 처리해 두면 로그가 매번 예외로 뒤덮이지 않는다.
    """

    unique_ids = sorted(set(candidate_place_ids))
    if len(unique_ids) < 2 or not settings.supabase_url:
        return []

    async def _query(active_client: httpx.AsyncClient) -> list[dict[str, object]]:
        id_filter = "in.(" + ",".join(unique_ids) + ")"
        params = {
            "select": "from_content_id,to_content_id,rank",
            "from_content_id": id_filter,
            "to_content_id": id_filter,
            "order": "rank.asc",
            "limit": str(limit),
        }
        response = await active_client.get(
            settings.supabase_url.rstrip("/") + _ASSOCIATIONS_PATH,
            params=params,
            headers={"apikey": settings.supabase_secret_key},
            timeout=settings.external_api_timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    if client is not None:
        rows = await _query(client)
    else:
        async with httpx.AsyncClient() as owned_client:
            rows = await _query(owned_client)

    return [
        CoVisitedHint(
            from_place_id=str(row["from_content_id"]),
            to_place_id=str(row["to_content_id"]),
            rank=int(row["rank"]),
        )
        for row in rows
    ]


__all__ = ["CoVisitedHint", "fetch_co_visited_hints"]
