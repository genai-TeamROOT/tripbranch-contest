"""세션의 현재 컨텍스트를 확보한다.

역할: Intent 분류/조건 추출 전에 세션의 현재 상태(SessionContextResponse)를 읽어 온다.
입력: session_id(없으면 새 세션), device_location(더 이상 쓰지 않는다 — 아래 참고).
출력: SessionContextResponse.

**GPS를 심던 함수였다.** 서버가 사용자 위치를 저장하지 않게 되면서(state/store.py::
for_persistence) 심을 이유가 사라졌다. 이번 턴의 좌표는 요청에 실려 와 그대로 쓰이고
(agent_runtime의 valid_gps), 서버에 두던 것은 요청에 좌표가 없을 때를 위한 여벌이었다.
device_location 인자는 호출부 두 곳의 서명을 함께 바꾸지 않으려고 남겨 뒀다.

(2026-08-05, D-038) 과거에는 GPS와 함께 날씨(api_context.api_weather)도 여기서
조회·저장했으나, 이 값을 실제로 읽는 소비자가 backend/frontend 어디에도 없어 제거했다
— 실제 RECOMMEND 날씨 Feature는 C의 context.weather 경로(weather_intent 게이팅)를 통해
완전히 별도로 확보된다. 상세 근거는 decision-log.md D-038 참고.
"""

from __future__ import annotations

from app.auth.principal import Principal
from app.state.service import (
    SessionContextResponse,
    get_session_context,
)
from app.state.store import StateStore


async def ensure_current_context(
    session_id: str | None,
    device_location: str | None,
    *,
    store: StateStore | None = None,
    principal: Principal | None = None,
) -> SessionContextResponse:
    """GPS를 최신화한 SessionContextResponse를 반환한다.

    principal은 그대로 get_session_context()에 넘겨 소유권을 대조한다
    (D-063 결정 2 후속, D-073) — 이 함수가 apply()보다 먼저 호출되는
    경로(라우트의 1단계 컨텍스트 확보)라 여기서도 대조가 필요하다.
    """

    # 좌표를 세션에 심던 자리였다. 이제 심지 않는다 — 저장소가 위치를 남기지
    # 않으므로(state/store.py::for_persistence) 저장해도 아무것도 안 남고, 저장된
    # 값이 늘 비어 있어 "만료됐거나 값이 다르면 갱신" 조건이 매 턴 참이 된다.
    # 그대로 두면 턴마다 아무것도 저장하지 않는 쓰기와 재조회가 한 번씩 더 나간다.
    #
    # 이번 턴의 좌표는 요청에 실려 와 그대로 쓰인다(agent_runtime의 valid_gps).
    # 서버에 두던 것은 요청에 좌표가 없을 때를 위한 여벌이었는데, 화면이 매 턴
    # 보내고 있어 실제로 쓰이는 일이 드물었다.
    return get_session_context(session_id, store=store, principal=principal)


__all__ = ["ensure_current_context"]
