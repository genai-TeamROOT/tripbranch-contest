"""Agent Runtime 단계별 소요시간 측정 스크립트 — RECOMMEND 1개 시나리오.

역할: `agent_runtime.py::run_agent_flow()`와 동일한 순서(B 세션 컨텍스트 → A LLM 해석 →
B 조건 병합 → C Tool 실행 → D 추천 Scoring → B 결과 기록 → A 메시지 조립)를 그대로
재현하면서 perf_counter()로 단계별 소요시간을 잰다.
- 2번(LLM 해석)은 Gemini 1차 분류(classify_intent)/2차 조건추출
  (extract_recommend_conditions)을 각각 나눠서 잰다(RECOMMEND 확정 시나리오라
  build_interpretation()의 RECOMMEND 분기와 동일한 두 호출만 실행됨).
- 4번(Tool 실행)은 ContextService에 주입하는 각 Provider를 얇은 타이밍 프록시로 감싸서,
  실제 `fetch_context()` 한 번 호출 안에서 실행되는 위치(Naver Geocoding)/날씨(기상청)/
  공휴일/장소검색(TourAPI search_places+get_details) 각 호출을 개별로 기록한다
  (날씨·공휴일·장소검색은 서로 asyncio로 병렬 실행되므로 개별 합계가 4번 총 소요시간보다
  클 수 있다 — 정상이다).
**기존 프로덕션 코드(app/ 이하)는 수정하지 않는다** — run_agent_flow()가 실제로 호출하는
동일 함수·Provider를 이 스크립트에서 같은 순서로 다시 호출하고, 얇은 타이밍 프록시만
스크립트 안에 둔다.
입력: 없음(하드코딩된 RECOMMEND 시나리오, 최초 턴 — session_id 없음).
출력: 표준 출력에 단계별 소요시간.
호출 시점: `python -m scripts.time_agent_stages`로 수동 실행(.env에 PROVIDER_MODE=real 필요).
"""

from __future__ import annotations

import asyncio
import inspect
from time import perf_counter
from typing import Any

import httpx

from app.agent_context.service import ContextService, ContextTools
from app.config import settings
from app.providers.factory import (
    get_geocoding_provider,
    get_holiday_provider,
    get_llm_provider,
    get_place_provider,
    get_weather_provider,
)
from app.schemas import AgentRequest
from app.services.interpret.session_orchestrator import ensure_current_context
from app.services.interpret.state_transform import to_user_conditions, transform
from app.services.runtime.context_transform import to_agent_context_request
from app.services.runtime.real_recommendation_provider import RealRecommendationProvider
from app.services.runtime.response_composer import compose_chat_message
from app.state.schema import now_kst
from app.state.service import (
    RecommendedPlace,
    RecordRecommendationRequest,
    UpdateApiContextRequest,
    apply,
    record_recommendation,
    update_api_context,
)
from app.state.session import new_trace_id
from app.tools.holiday import GetHolidaysTool
from app.tools.nearby_place_details import NearbyPlaceDetailsTool
from app.tools.resolve_location import ResolveLocationTool
from app.tools.weather_forecast import GetWeatherForecastTool

USER_INPUT = "경복궁 근처 카페 추천해줘"
DEVICE_LOCATION = "37.5788,126.9770"


class _TimedProvider:
    """Provider를 감싸서 실제로 호출되는 async 메서드마다 소요시간을 기록에 남긴다.
    Provider 구체 클래스나 메서드 이름을 스크립트가 미리 알 필요 없이(duck typing),
    Tool이 실제로 부르는 메서드를 그대로 잡아 잰다. 동기 속성·메서드는 그대로 통과시킨다.
    """

    def __init__(self, inner: Any, log: list[str]) -> None:
        self._inner = inner
        self._log = log

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if not inspect.iscoroutinefunction(attr):
            return attr

        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            started = perf_counter()
            try:
                return await attr(*args, **kwargs)
            finally:
                self._log.append(f"{name} {perf_counter() - started:.2f}s")

        return wrapper


async def main() -> None:
    print(f'[RECOMMEND] "{USER_INPUT}"')
    total_started = perf_counter()

    async with httpx.AsyncClient() as client:
        llm = get_llm_provider()

        location_log: list[str] = []
        weather_log: list[str] = []
        holiday_log: list[str] = []
        places_log: list[str] = []

        weather_provider = _TimedProvider(get_weather_provider(client), weather_log)
        geocoding_provider = _TimedProvider(get_geocoding_provider(client), location_log)
        place_provider = _TimedProvider(get_place_provider(client), places_log)
        holiday_provider = _TimedProvider(get_holiday_provider(client), holiday_log)

        context_service = ContextService(
            ContextTools(
                location=ResolveLocationTool(geocoding_provider),
                places=NearbyPlaceDetailsTool(place_provider, place_provider),
                weather=GetWeatherForecastTool(weather_provider),
                holidays=GetHolidaysTool(holiday_provider),
            ),
            candidate_limit=settings.recommendation_candidate_limit,
        )
        recommendation_provider = RealRecommendationProvider()
        request = AgentRequest(
            user_input=USER_INPUT, session_id=None, device_location=DEVICE_LOCATION
        )

        # 1) B: 세션 컨텍스트 (최초 턴 — 세션이 없어 GPS/날씨 API 호출 없이 즉시 반환됨)
        t0 = perf_counter()
        session_context = await ensure_current_context(
            request.session_id, request.device_location, weather_provider
        )
        t1_session = perf_counter() - t0
        print(f"  1. B: 세션 컨텍스트     {t1_session:.2f}s")

        # 2) A: LLM 해석 — build_interpretation()의 RECOMMEND 분기와 동일하게
        #    1차 classify_intent → 2차 extract_recommend_conditions 순서로 직접 호출한다.
        t0 = perf_counter()
        classification = (
            await llm.classify_intent(
                request.user_input,
                has_previous_recommendation=session_context.has_recommendation,
                shown_place_count=len(session_context.shown_place_ids),
            )
        ).data
        t_classify = perf_counter() - t0

        t0 = perf_counter()
        llm_output = (await llm.extract_recommend_conditions(request.user_input)).data
        t_extract = perf_counter() - t0

        t2_llm = t_classify + t_extract
        print(
            f"  2. A: LLM 해석          {t2_llm:.2f}s"
            f"   ← 내부: 1차 분류 {t_classify:.2f}s / 2차 추출 {t_extract:.2f}s"
        )

        if classification.intent.value != "RECOMMEND":
            print(
                f"\n  예상과 다른 분류 결과(intent={classification.intent}) — "
                "이 스크립트는 RECOMMEND 1개 시나리오만 다룬다. 종료합니다."
            )
            return

        # 3) B: 조건 병합 (로컬 연산, 외부 API 없음)
        t0 = perf_counter()
        apply_request = transform(llm_output, session_context, request.user_input)
        state_response = apply(apply_request)
        t3_merge = perf_counter() - t0
        print(f"  3. B: 조건 병합         {t3_merge:.2f}s")

        if state_response.session_created and request.device_location:
            update_api_context(
                UpdateApiContextRequest(
                    session_id=state_response.session_id,
                    gps_location=request.device_location,
                    gps_location_updated_at=now_kst(),
                )
            )

        # 4) C: Tool 실행 — fetch_context() 한 번 호출. 내부에서 위치 조회 후,
        #    날씨/공휴일/장소검색이 asyncio로 병렬 실행된다(각 _TimedProvider가 개별 기록).
        agent_conditions = to_user_conditions(state_response.user_conditions)
        t0 = perf_counter()
        context_request = to_agent_context_request(
            request_id=new_trace_id(), conditions=agent_conditions
        )
        tool_response = await context_service.fetch_context(context_request)
        t4_tool_total = perf_counter() - t0

        print(f"  4. C: Tool 실행        {t4_tool_total:.2f}s")
        print(f"     ← 위치(Naver Geocoding):  {', '.join(location_log) or '호출 없음'}")
        print(f"     ← 날씨(기상청):           {', '.join(weather_log) or '호출 없음'}")
        print(f"     ← 공휴일:                 {', '.join(holiday_log) or '호출 없음'}")
        print(f"     ← 장소검색(TourAPI):      {', '.join(places_log) or '호출 없음'}")
        print(f"     (C 상태: {tool_response.status})")

        if tool_response.status in {"needs_clarification", "unsupported", "unavailable"}:
            print("  C 단계 종료 상태라 5~7단계(Recommendation)를 실행하지 않습니다.")
            print("  ─────────────────────────")
            print(f"  Total                  {perf_counter() - total_started:.2f}s")
            return

        tool_context = tool_response.context
        if tool_context is None:
            print("  C 응답에 Context 없음 — 5~7단계를 실행하지 않습니다.")
            print("  ─────────────────────────")
            print(f"  Total                  {perf_counter() - total_started:.2f}s")
            return

        # 5) D: 추천 Scoring (로컬 연산)
        t0 = perf_counter()
        recommendations = await recommendation_provider.recommend(
            agent_conditions, tool_context, state_response.excluded_place_ids
        )
        t5_scoring = perf_counter() - t0
        print(f"  5. D: 추천 Scoring      {t5_scoring:.2f}s")

        # 6) B: 결과 기록 (인메모리)
        t0 = perf_counter()
        shown = [*recommendations.recommendations, *recommendations.unverified_recommendations]
        if shown:
            record_recommendation(
                RecordRecommendationRequest(
                    session_id=state_response.session_id,
                    run_id=state_response.run_id,
                    recommended=[
                        RecommendedPlace(place_id=item.place_id, rank=index + 1)
                        for index, item in enumerate(shown)
                    ],
                )
            )
        t6_record = perf_counter() - t0
        print(f"  6. B: 결과 기록         {t6_record:.2f}s")

        # 7) A: 메시지 조립
        t0 = perf_counter()
        message = await compose_chat_message(llm_output, recommendations=recommendations, llm=llm)
        t7_message = perf_counter() - t0
        print(f"  7. A: 메시지 조립       {t7_message:.2f}s")
        print(f"     message={message!r}")
        print(
            f"     recommendations={len(recommendations.recommendations)}건"
            f", unverified={len(recommendations.unverified_recommendations)}건"
        )

    total_elapsed = perf_counter() - total_started
    print("  ─────────────────────────")
    print(f"  Total                  {total_elapsed:.2f}s")


if __name__ == "__main__":
    asyncio.run(main())
