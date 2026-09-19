"""기능 스위치 조회 API 라우터.

역할: 서버 설정에 따라 켜지고 꺼지는 기능 중, 화면이 알아야 하는 것만 알려준다.
입력: GET /api/features 요청. 인증이 필요 없다(health와 같다).
출력: FeaturesResponse — 지금은 {"taste_enabled": bool} 하나다.
호출 시점: 프론트가 앱을 띄울 때 한 번 호출한다(frontend/src/state/FeatureFlagsContext.tsx).

**왜 화면이 따로 물어야 하는가.** `TASTE_EVIDENCE_ENABLED`를 끄면 서버는 후기·블로그
데이터를 쓰는 기능(취향 순위·취향 태그 표·상세 카드 후기 절·AI 추천 이유·후기 답변)을
전부 멈추는데, "취향 설정" 메뉴와 화면은 프론트에 있어 서버가 막을 수 없다. 메뉴를
그대로 두면 저장한 취향이 순위에 아무 영향도 주지 않는 화면이 남는다.

**인증을 걸지 않는다.** 로그인 여부와 무관한 서버 전역 설정이고, 관문을 통과하기
전(세션 확인 중)에도 메뉴를 그려야 한다. 대신 설정값을 통째로 내보내지 않고
화면이 쓰는 판정만 싣는다 — 여기 필드를 늘릴 때도 같은 기준을 지킨다.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.config import settings
from app.schemas import FeaturesResponse

router = APIRouter(tags=["features"])


@router.get("/features", response_model=FeaturesResponse)
async def get_features() -> FeaturesResponse:
    return FeaturesResponse(taste_enabled=settings.taste_evidence_enabled)
