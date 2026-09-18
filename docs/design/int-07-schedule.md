**문서 버전:** v2.3
**작성일:** 2026-08-06 (v2.1 갱신: 2026-08-14, v2.2 갱신: 2026-08-18, v2.3 갱신: 2026-08-26)
**관련 인텐트:** INT-01 RECOMMEND (파생), 신규 INT-07 SCHEDULE

**v2.3 변경 이력 (place_associations 연동, D-091, v2.2 대비)**
- `SchedulePlanningRequest`에 `co_visited_hints` 필드 신설(6.1절) — D-088로
  만든 `place_associations`에서 후보 집합 안의 "함께 방문된" 쌍을
  `app.schedule.associations.fetch_co_visited_hints()`로 조회한다
- `plan_schedule()`/`plan_partial_schedule()`에 `co_visited_fetcher` opt-in
  키워드 인자 추가 — 기본값 `None`이면 기존 동작과 완전히 동일.
  `agent_runtime.py`가 두 호출부 모두에 `co_visited_fetcher=fetch_co_visited_hints`를
  넘기도록 배선을 마쳐 실제로 켜져 있다(D-091/D-092)
- 프롬프트에 `[함께 방문된 이력]` 섹션과 활용 규칙 추가(`schedule.plan`/
  `schedule.plan_context` 1.0.0 → 1.1.0)
- D/A 스키마·코드 변경 없음 — D의 `RecommendationItem.place_id`만 재사용

**v2.2 변경 이력 (폐점 스탑 구조적 검증 추가, v2.1 대비)**
- 6.2.1절이 남겨뒀던 한계("스탑별 재계산은 계속 범위 밖") 중 일부를 완화 —
  전체 재계산까지는 여전히 하지 않지만, LLM이 계산한 estimated_arrival과
  후보의 운영시간(operating_hours_display)을 대조해 모순되면 그 스탑에
  경고를 붙이는 구조적 후처리를 추가함(`app.schedule.planner._finalize_items`)
- 프롬프트(`build_schedule_planning_instruction`/`build_schedule_fill_instruction`)에도
  후보별 운영시간을 함께 전달해 LLM이 애초에 마감된 곳을 뒷순서에 배치하지
  않도록 유도 — 다만 이것만으로는 부족하다고 판단해(6.2.1절 근거) 구조적
  후처리를 반드시 함께 둠("구조적 보장 우선" 원칙)
- `ScheduleItem`에 `warnings: list[str]` 필드 신설(LLM이 생성하지 않고
  시스템이 결정적으로 채움, basis_note와 동일한 설계)
- `PROMPT_VERSION`을 `agent-interpret-prompts-1.0.13`으로 올림(SCHEDULE 두
  system instruction의 규칙 변경)
- 자세한 배경: 기본프로젝트 최종 발표에서 받은 질문("마지막 장소가 운영시간
  넘겨서 추천되면?")을 계기로 발견, 프롬프트만으로 해결할지 논의 후 두
  레이어를 함께 두는 쪽으로 결정
- (같은 날 후속) dev-chat 실사용 테스트 중 "6시간 코스 짜줘"가 실제로는
  2.5시간 분량만 채워 반환되는 별개 버그 발견 — target_item_range()의 목표
  개수 범위 안에서도 LLM이 일찍 끝내버리는 과소-채움 문제로, 활동 가능
  시간이 길 때 상한 개수에 가깝게 채우라는 지시를 duration_rule에 추가해
  해결(`PROMPT_VERSION`을 `agent-interpret-prompts-1.0.14`로 추가 인상)

**v2.1 변경 이력 (SCHEDULE-08/09 + 안정화 작업 반영, v2.0 대비)**
- 9절 미결 사항이 SCHEDULE-07 시점에 머물러 있던 것을 SCHEDULE-08·09·이후
  안정화 작업(번호 미부여, 08-10~08-13)까지 반영해 최신화 — 기본프로젝트
  최종 발표 준비 중 설계 문서가 실제 구현을 못 따라가고 있다는 걸 뒤늦게
  발견함(발표 피드백 계기)
- 새로 해소된 항목: 부분 재편성(REJECT_SPECIFIC, SCHEDULE-09), 활동 가능
  시간에 따른 동적 개수 편성, LLM 호출 타임아웃 분리, 도착 시각 10분 단위
  반올림, 결과 문구·카드 중복 제거, 세션 복원 버그, SCHEDULE 직후 오분류
  버그, 부분 재편성 stale 값 버그 2건
- 새로 추가된 미결 항목: SCHEDULE 응답 지연시간 개선(`thinking_budget=0`)의
  품질 영향 자동 검증 없음, 단위 환산 버그 수정이 구조적 하드 검증이 아님,
  "대화 중 고른 장소로 일정 구성" 여전히 미착수
- 6.2.1절(basis_note) 관련 한계는 그대로 유지 — 스탑별 재계산은 계속 범위 밖

**v2.0 변경 이력 (A의 1차 구현과 병합, v1.3 대비)**
- A(mintee)가 이 문서와 같은 경로로 독립적으로 작성한 1차 구현 설계(v0.1,
  PR #113/#114, 커밋 `da9f4cc`)가 이미 develop에 병합돼 있던 것을 발견 —
  두 문서를 하나로 합침
- "0. 구현 현황" 절 신설 — 이미 배포된 범위(Intent 분류 + 안전한 안내
  메시지)를 명시
- 3절 판별 경계 표를 A가 만든 구체적 예시 표로 교체(더 명확함)
- 8절 영향 파일 표 정정 — `Intent.SCHEDULE`은 이미 존재(추가 아님),
  `response_composer.py`는 기존 스텁 분기를 교체하는 작업으로 정정,
  A만 알던 `orchestrator.py`(조기 반환 로직)를 신규 항목으로 추가
- 9절에 "A에게 D 협의 결과 공유 필요" 항목 추가 — A의 원문서는 아직
  top_k/혼잡도 재계산을 "D 협의 후 결정"으로 미해결로 남겨둔 상태였음

**v1.1 변경 이력 (v1.0 대비)**
- 일정 편성 LLM 호출 주체를 B(Agent State)에서 신규 독립 모듈로 변경 — B는 코드 변경 없음
- 5절 D 변경안을 실제 코드 구조에 맞게 수정 (`recommendation_transform.py`에 없는 함수 참조 제거)
- `AnswerConditions` → `UserConditions`로 정정 (존재하지 않는 타입명이었음)
- top_k 5→10 확장이 D-040(혼잡도 2차 Scoring) 설계와 충돌하는 지점을 명시, D 협의 선행 항목으로 분리
- 7절 응답 형식에서 신규 `response_type` 필드 대신 기존 `llm_output.intent`를 판별 기준으로 재사용
- 8절 영향 파일 목록을 Protocol/Fake 구현체까지 포함해 재작성
- 9절 미결 사항 정리 (해소된 항목 제거, 신규 항목 추가)

**v1.2 변경 이력 (착수 준비 단계, v1.1 대비)**
- `SchedulePlanningRequest.candidates` 타입을 `RecommendationItem`으로 확정
- `RecommendationItem`/`RankedCandidate` 둘 다 위경도가 없어 `pairwise_distances_km`를
  D 응답만으로 계산할 수 없다는 것을 확인 — A가 C의 `AgentContextResponse.places`를
  place_id로 매칭해 계산하는 방식으로 확정 (4절, 6.1절)
- `docs/design/package_work_breakdown.md`에 일정 편성 모듈 담당 정보 반영 완료

**v1.3 변경 이력 (D 협의 결과 반영, v1.2 대비)**
- top_k 5→10 확장: D 확인 완료(`limit` 파라미터, 기본값 5 유지)
- D-040 혼잡도 2차 Scoring: SCHEDULE에서는 10개 전부 재계산으로 확정
  (API 비용보다 `concentration_intent` 품질 우선)
- (신규) 1차 점수·근거 문장이 단일 `visit_at` 기준이라 뒷 순서 스탑에는
  부정확할 수 있다는 문제를 D가 발견 — `ScheduleResult.basis_note` 고정
  안내 문구로 대응하기로 결정 (6.2.1절 신설)

---

## 이어지는 설계 문서

이 문서(v2.3) 이후의 SCHEDULE 변경은 주제별로 분리된 두 문서에 있다. 이쪽이
최신이므로 아래 주제는 그쪽을 먼저 본다.

| 문서 | 다루는 것 |
|------|-----------|
| [`saved-places.md`](saved-places.md) | 장소 보관함 — 사용자가 고른 장소를 상태로 들고 있다가 일정에 반드시 반영하는 경로 (D-107·D-110·D-114·D-116) |
| [`schedule-engine.md`](schedule-engine.md) | 일정 엔진 — 체류시간·도착시각·이동시간 계산을 LLM에서 회수하는 변경 (TP-215~217) |

---

## 0. 구현 현황

아래는 A가 이미 develop에 배포한 1차 구현이다(PR #113/#114, 커밋
`da9f4cc`). 이 문서의 나머지 절(1~10)은 이 위에 이어 붙일 후속 구현
설계다 — 아래 항목을 다시 만들 필요는 없다.

- `backend/app/schemas.py`: `Intent` enum에 `SCHEDULE` 이미 존재
- `backend/app/services/interpret/orchestrator.py`: `intent is
  Intent.SCHEDULE`이면 조건 추출 없이 즉시
  `LLMOutput(intent=Intent.SCHEDULE, status=OutputStatus.COMPLETE)`로
  조기 반환 — 후속 구현에서 이 조기 반환을 제거·확장해야 조건 추출·D
  호출·일정 편성 모듈로 이어진다
- `backend/app/services/runtime/response_composer.py`: `intent is
  Intent.SCHEDULE`이면 고정 문구 `"일정 추천 기능은 아직 준비
  중이에요."`만 반환 — 후속 구현(6.2절)의 `compose_schedule_message()`가
  이 분기를 **교체**한다(추가 아님)
- 목적: 일정 요청이 RECOMMEND로 오분류돼 단일 장소 추천으로 잘못
  처리되는 걸 막는 안전장치. B/C/D 쪽은 전혀 건드리지 않음

---

## 1. 배경 및 목적

현재 RECOMMEND 인텐트는 조건에 맞는 장소를 **상위 5개** 카드로 반환한다.
사용자가 "오늘 하루 일정 짜줘", "반나절 코스 추천해줘" 같이 **시간 순서가
있는 복수 장소 방문 계획**을 요청할 때는 단순 카드 목록보다 방문 순서·이동
동선·시간 배분이 담긴 일정 형태의 응답이 적합하다.

이를 처리하는 **INT-07 SCHEDULE** 인텐트와 일정 계획 전용 흐름을 신설한다.

부가적으로, 지금까지 LLM이 실제 판단에 쓰이는 지점이 A(인텐트 분류·조건
추출)에 집중돼 있고 추천 채점(D)은 고정 가중치 공식이라 "AI를 잘 안 쓴
프로젝트로 보인다"는 피드백이 있었다. 일정 편성(후보 중 선택·순서·이유
판단)은 LLM이 실제로 판단하는 지점을 늘릴 수 있는 기능이라, 이번 기능은
이 문제에 대한 답이기도 하다.

---

## 2. 기존 RECOMMEND와의 차이

| 항목 | INT-01 RECOMMEND | INT-07 SCHEDULE |
|------|-----------------|-----------------|
| 트리거 | "카페 추천해줘" | "오늘 오후 일정 짜줘", "반나절 코스 만들어줘" |
| D 반환 수 | 상위 5개 | **상위 10개 전부** (D 협의 완료, 5절 참고) |
| 일정 편성 주체 | 없음 | **신규 독립 모듈에서 LLM 호출로 편성** — B는 결과를 기존 방식으로 기록만 함 |
| 응답 형식 | 장소 카드 5개 | 시간순 일정 블록 (1일/반나절 등) |
| shown_ids | 노출된 5개 기록 | **일정에 포함된 장소만** 기록 (B의 기존 `record_recommendation()` 그대로 재사용) |

---

## 3. 인텐트 분류 기준 (INT-07 판별)

A가 1차 구현(0절)에서 이미 확정·배포한 판별 기준(아래 표)을 그대로 따른다.

| 발화 | Intent |
| --- | --- |
| "오늘 오후 종로 반나절 코스 짜줘" | `SCHEDULE` |
| "경복궁, 인사동 가고 싶은데 어디부터 갈까?" | `SCHEDULE` |
| "오늘 갈 만한 곳 추천해줘" | `RECOMMEND` |
| "경복궁 오늘 열어?" | `INFO` |
| "다른 곳 보여줘" | 이전 추천 이력이 있을 때 `MODIFY` |

일반적으로는 "일정/코스/루트 짜줘", "하루/반나절/N시간" + 복수 활동 암시,
"어디부터 갈지 순서" 같은 표현이 SCHEDULE로 분류된다. 단순 "추천해줘"
표현에 일정 맥락이 없으면 RECOMMEND로 유지한다.

### 3.1 SCHEDULE 다음 턴의 조건 변경 발화 (SCHEDULE-06)

위 표의 "다른 곳 보여줘" 판별 기준(이전 추천 이력이 있을 때 `MODIFY`)은 그대로
유지한다 — `classify_intent()`는 여전히 이런 발화를 MODIFY로 분류한다.

다만 SCHEDULE 응답을 받은 바로 다음 턴에 이런 MODIFY 발화가 오면, 사용자는
방금 받은 "일정"을 바꿔달라는 것이므로 일반 RECOMMEND 재추천이 아니라 일정
재편성으로 처리해야 한다. 이건 A의 프롬프트/분류 로직을 바꾸는 대신, Agent
Runtime(`agent_runtime.py`)이 B가 이미 세션마다 저장해온 `last_intent` 값을
읽어 라우팅 단계에서만 처리한다:

- 직전 턴이 SCHEDULE로 완료됐고(`last_intent == "SCHEDULE"`, 되묻기 없이
  끝남 — `pending_clarification is None`) 이번 턴이 MODIFY로 분류되면,
  조건 병합은 원래 MODIFY 페이로드(`llm_output.modify`)로 정상 처리한 뒤
  `llm_output.intent`만 SCHEDULE로 바꿔치기해 기존 SCHEDULE 분기(D 10개 호출
  · 편성 모듈 호출)로 재진입시킨다.
- REJECT_ALL(그냥 "다른 데로")이면 직전 일정의 장소들이 `rejected`로
  기록되어 새 일정에서 자동 제외된다(기존 MODIFY 로직 그대로 재사용, B
  스키마 변경 불필요).
- classify_intent 프롬프트, `extract_modify_conditions()`는 변경하지 않는다.

---

## 4. 처리 흐름

```
사용자 입력 (SCHEDULE 인텐트)
  → A: Intent 분류 → SCHEDULE
  → A→B: 조건 병합 (기존과 동일)
  → A→C: AgentContextRequest (기존과 동일)
  → A→D: 추천 실행 — limit=10으로 요청 (D 협의 완료, 5절 참고)
  → D→A: RecommendationItem 10개 반환
  → A: C의 AgentContextResponse.places(위경도)를 place_id로 매칭해
       pairwise_distances_km 계산 (haversine_km 재사용)
  → A→일정편성모듈: candidates(10개), conditions, pairwise_distances_km
       ↳ 모듈 내부에서 LLM 호출 → 일정 JSON 생성
       ↳ 상태 저장소(StateStore) 비접근 — 순수 입력→출력 함수, D와 같은 위치
  → 일정편성모듈→A: ScheduleResult (방문 순서·시각·이동시간·장소·이유)
  → A→B: record_recommendation (기존 함수 그대로 재사용 — 일정에 포함된
       장소만 place_id+rank로 넘김. B 쪽 코드 변경 없음)
  → A: 일정 블록 형태로 응답 조립
```

concentration_intent가 AVOID/SEEK인 경우 D-040 분기(1차 10개 → C 혼잡도
후조회 → 2차 재순위)도 SCHEDULE에서는 **10개 전부** 재계산한다 (D 협의
완료). `rerank_with_concentration()`은 개수를 하드코딩하지 않아 D 쪽 구현
부담은 없다. 5개만 재계산하면 `concentration_intent`를 명시한 사용자
조건이 6~10번째 후보에서 조용히 무시되는 품질 문제가 생기므로, API 호출이
2배로 늘더라도 일관성 있게 10개 전부 적용하기로 확정했다.

---

## 4.1 되묻기(clarification) 흐름 (D-059)

SCHEDULE도 RECOMMEND와 같은 조건 병합 경로를 타므로(4절), 위치가 여러 곳으로
해석되는 등 Tool(C) 레벨에서 `needs_clarification`이 나면 RECOMMEND와 동일하게
`pending_clarification` 플래그가 B에 저장되고, `state_transform.transform()`도
SCHEDULE을 RECOMMEND와 동일하게 취급해 되묻기 답변 시 soft reset을 건너뛴다(§6
조건 병합은 손대지 않음).

**분류(Intent 판별) 단계는 SCHEDULE 전용 처리가 필요했다.** RECOMMEND는
`_INTENT_PRIORITY`의 fallback 기본값이라 되묻기 답변("광화문으로" 같은 짧은
지명 응답)이 별다른 신호 없이도 대개 자연스럽게 RECOMMEND로 분류되지만,
SCHEDULE은 "일정/코스/순서" 키워드가 있어야만 선택되는 명시적 분류라 fallback이
없다 — 되묻기 답변은 오히려 MODIFY의 "지명+조사" 예시 패턴과 겹쳐 잘못
분류되기 쉽다.

그래서 `classify_intent()` 호출에 `pending_clarification`/`last_intent`
(B의 `SessionContextResponse`에서 그대로 옴)를 추가로 넘기고, 프롬프트에 "직전
턴이 SCHEDULE 되묻기로 끝났고 이번 발화가 그 답변으로 보이면 SCHEDULE 유지"
규칙을 추가했다. `Fake`도 같은 컨텍스트를 받아 동일 규칙을 미러링한다. 상태
레벨에서 Intent를 강제로 덮어쓰는 방식은 채택하지 않았다 — 되묻기 답변에
욕설이나 완전히 무관한 질문이 온 경우까지 SCHEDULE로 덮어써 버릴 위험이 있기
때문이다(자세한 원인·대안 비교는 decision-log.md D-059 참고).

SCHEDULE 외 다른 Intent(INFO, COMPARE 등)가 되묻기로 끝나는 경우의 이어가기는
아직 이 프로젝트 범위 밖이다 — 필요성이 확인되면 같은 패턴을 확장한다.

---

## 5. D(추천 엔진) 변경

현재 D는 `RealRecommendationProvider.recommend()` 안에 `_RECOMMENDATION_LIMIT
= 5`가 모듈 상수로 하드코딩돼 있고, 이 값을 `run_recommendation_pipeline_
from_context()`의 `recommendation_limit` 파라미터로 그대로 전달한다. 슬라이싱
(상위 N개로 자르기) 자체는 이미 `recommendation_pipeline.py`가
`scoring.ranked[:recommendation_limit]`로 처리하고 있으므로, **`domain/
scoring.py`의 `score_candidates()`는 건드릴 필요가 없다.**

필요한 변경은 이 하드코딩된 상수를 호출자가 넘기는 값으로 바꾸는 것뿐이다.

```python
# app/services/runtime/protocols.py — RecommendationProvider Protocol
class RecommendationProvider(Protocol):
    async def recommend(
        self,
        conditions: UserConditions,
        context: RecommendationContext,
        excluded_place_ids: list[str],
        limit: int = 5,            # 신규 파라미터. 미지정 시 기존과 동일
    ) -> RecommendationResponse:
        ...
```

```python
# app/services/runtime/real_recommendation_provider.py
class RealRecommendationProvider:
    async def recommend(
        self,
        conditions: UserConditions,
        context: RecommendationContext,
        excluded_place_ids: list[str],
        limit: int = _RECOMMENDATION_LIMIT,   # 기존 하드코딩 상수를 기본값으로
    ) -> RecommendationResponse:
        search_radius_km = to_search_radius_km(conditions)
        return await run_recommendation_pipeline_from_context(
            context,
            conditions=conditions,
            visit_at=datetime.now(_KST),
            search_radius_km=search_radius_km,
            shown_place_ids=frozenset(excluded_place_ids),
            recommendation_limit=limit,   # 이미 있는 파라미터를 그대로 재사용
        )
```

**주의**: `RecommendationProvider` Protocol을 구현하는 곳이 여기 하나가
아니다 — `app/services/runtime/stubs.py`의 Fake 구현체와 `tests/
test_agent_runtime.py` 등에 흩어진 테스트 더블(최소 4곳)도 같은 시그니처로
맞춰야 기존 테스트가 깨지지 않는다.

기존 RECOMMEND 흐름은 `limit` 미지정 시 기본값 5로 동작해 영향 없음.

---

## 6. 일정 편성 모듈 — 신규, B/D와 독립

### 6.0 모듈 배치 결정

상태 저장소에 의존하지 않는 **신규 모듈**(`app/schedule/`)로 분리한다.
입력(후보+조건)을 받아 계산된 결과(편성된 일정)를 반환하는 구조로, A가
이 모듈을 호출하는 방식은 지금 A가 D를 호출하는 방식과 동일하다 — 다만
점수 공식 대신 LLM 판단을 쓴다는 차이만 있다.

### 6.1 LLM 입력 구성

```python
class SchedulePlanningRequest(BaseModel):
    candidates: list[RecommendationItem]  # D의 공개 응답 스키마(app.schemas.RecommendationItem)
                                         # 사용 확정. D 내부 도메인 타입(RankedCandidate)은
                                         # 레이어 경계를 넘어가므로 쓰지 않는다.
    conditions: UserConditions          # 기존 15개 조건 그대로 사용
                                         # (time_available, transport 등 이미 있는 필드 재사용)
    visit_datetime: datetime | None     # 방문 예정 시각
    pairwise_distances_km: dict[tuple[str, str], float]
                                         # app.geo.haversine_km()로 계산해 LLM에 근거로 제공.
                                         # RecommendationItem에는 위경도가 없어(distance_km만
                                         # 검색 중심 기준 거리) D 응답만으로는 후보 간 거리를
                                         # 못 구한다 — A가 C의 AgentContextResponse.places(위경도
                                         # 보유)를 place_id로 매칭해 계산한다. D/C 스키마 변경 불필요.
    co_visited_hints: list[CoVisitedHint] = []
                                         # 신규(D-091) — place_associations(D-088) 기반 "이 후보들은
                                         # 실제로 함께 방문됐다" 힌트. opt-in — plan_schedule()이
                                         # co_visited_fetcher를 받았을 때만 채운다(app.schedule
                                         # .associations 참고). D 스키마 변경 없음(place_id만 사용).
```

### 6.2 LLM 출력 스키마

```python
class ScheduleItem(BaseModel):
    order: int                  # 방문 순서 (1부터)
    place_id: str
    place_name: str
    estimated_arrival: str      # "14:30" 형식
    estimated_duration_min: int # 해당 장소 체류 예상 시간
    travel_to_next_min: int | None  # 다음 장소까지 이동 시간 (마지막은 null)
    reason: str                 # 이 장소를 이 순서에 배치한 이유 1~2문장

class ScheduleResult(BaseModel):
    items: list[ScheduleItem]   # 최종 일정 (3~5개)
    total_duration_min: int
    route_summary: str          # 동선 요약 1~2문장
    basis_note: str             # 신규(D 피드백 반영) — 근거 데이터 기준 시각 안내.
                                 # LLM이 생성하지 않고 A가 visit_at 값을 넣어 고정
                                 # 템플릿으로 채운다(6.2.1 참고)
```

#### 6.2.1 basis_note — 근거 시각 안내 (D 피드백 반영)

D가 발견한 문제: 후보 10개의 1차 점수·근거 문장(운영시간·날씨)은 단일
`visit_at`(현재 시각) 기준으로 계산된다. 일정 뒷 순서 장소는 실제 방문
시점이 몇 시간 뒤인데도 근거 문장은 "지금 기준"으로 나와 부정확할 수
있다(예: 마감 임박 안내가 실제 방문 시점과 다를 수 있음).

스탑마다 D를 다시 호출해 방문 예정 시각 기준으로 재계산하는 방식은 이번
범위에서 비용이 크므로 채택하지 않는다. 대신 `basis_note`에 고정 문구를
넣는다 — LLM에게 문구 작성을 맡기지 않고 A/일정편성모듈이 결정적으로
채운다(예: `"이 정보는 {visit_at} 기준으로 계산됐어요. 실제 방문
시간에는 운영시간·날씨 상황이 달라질 수 있어요."`). 근본적인 재계산
정확도 개선은 이번 범위 밖으로 남겨둔다.

**(2026-08-18 추가, v2.2)** 위 한계 중 "뒷 순서 스탑이 실제로는 마감 이후일
수 있다"는 부분은 재계산 없이도 상당 부분 완화할 수 있다는 걸 확인해 별도로
처리했다 — D가 이미 후보마다 내려주는 `operating_hours_display`("09:00~18:00")와
LLM이 계산한 `estimated_arrival`을 대조하기만 해도, 재방문 시각을 몰라도
"이미 알고 있던 운영시간과 지금 계산된 도착 시각이 서로 모순되는지"는 판단할
수 있기 때문이다. `app.schedule.planner._finalize_items()`가 이 대조를
결정적으로 수행해 어긋나는 스탑에만 `ScheduleItem.warnings`를 채운다.
프롬프트에도 운영시간을 함께 전달해 LLM이 애초에 그런 배치를 피하도록
유도하지만, 그 지시만 믿지 않고 항상 이 구조적 재검증을 거친다("구조적
보장 우선" 원칙 — SCHEDULE-07의 개수 하드 검증, stale 값 무효화와 같은
접근). basis_note가 안내하는 "근거 데이터가 단일 시각 기준"이라는 한계
자체는 여전히 남아있다 — 이 재검증도 D가 준 운영시간 값이 정확하다는
전제 위에서만 유효하다.

LLM은 10개 후보 중 시간·동선 효율을 고려해 **3~5개**를 선택하고 방문
순서를 결정한다. 나머지는 자동 제외된다.

`estimated_duration_min`/`travel_to_next_min`/`reason`은 SCHEDULE-06부터
B 히스토리에 함께 저장된다(6.3절 갱신 참고. SCHEDULE-04~05 시점에는 저장되지
않았다 — 해당 시점 알려진 한계는 9절 "해소된 항목" 참고).

#### 6.2.2 후보 부족 처리 및 선택 개수 하드 검증 (SCHEDULE-07)

`app.schedule.planner.plan_schedule()`은 `SchedulePlanningRequest.candidates`가
3개 미만이면 LLM을 아예 호출하지 않고, `ScheduleResult(items=[], ...)`를
고정 안내 문구(SCHEDULE-06 후속에서 만든 `_NO_CANDIDATES_ROUTE_SUMMARY`)와
함께 즉시 반환한다. `ScheduleLLMPlan.items`에는 `min_length=3`/`max_length=5`
제약을 걸어뒀는데, 이 순서(후보 수 체크 → 그 다음에만 제약이 걸린 LLM
호출) 덕분에 실제로 LLM이 불릴 때는 후보가 항상 3개 이상이라 제약이 항상
만족 가능하다.

LLM이 그래도 개수를 못 지키면(예: 후보 5개 중 2개만 선택) Pydantic 검증이
실패하는데, `app.providers.gemini.RealGeminiProvider._call_structured()`가
이미 갖고 있던 공용 재시도 경로(검증 오류 안내를 프롬프트에 덧붙여 1회
재호출)를 다른 구조화 출력(`IntentClassificationResult` 등)과 동일하게
그대로 탄다. 재시도까지 실패하면 `llm_output_invalid`(502, retryable)로
명시적으로 실패한다 — 개수를 어긴 일정을 조용히 그대로 반환하지 않는다.

### 6.3 B 기록 — SCHEDULE-06부터 일정 세부 필드도 함께 저장

일정에 **포함된 장소만** B의 `record_recommendation()`을 그대로 호출해
기록한다. `ScheduleItem.order`는 `rank`로 매핑한다.

(SCHEDULE-06) `RecommendedPlace`/`RecommendedItem`에 `estimated_arrival`/
`estimated_duration_min`/`travel_to_next_min`/`reason` 선택 필드가 추가되어,
SCHEDULE 항목은 이 값들도 함께 저장한다 — SCHEDULE 재조정 시 직전 일정
내용을 참고하기 위함이다. RECOMMEND/MODIFY 흐름은 이 필드들을 생략하면
되고(항상 None), 기존 동작에 영향 없다.

LLM이 제외한 후보 5~7개는 기록되지 않아 이후 일반 RECOMMEND 요청에서
재노출 가능하다.

### 6.4 LLM Provider 연동

- provider 획득은 A가 이미 쓰고 있는 `app.providers.factory.get_llm_provider()`를
  그대로 재사용한다 — 이 모듈만을 위한 별도 획득 경로를 새로 만들지 않는다.
- `LLMProvider` Protocol에 일정 편성용 메서드(예: `generate_schedule_plan()`)를
  추가할 때는 `app/providers/protocols.py`(Protocol 정의), `app/providers/
  gemini.py`(실제 구현), `app/providers/stub.py`의 `FakeLLMProvider`(가짜 구현)
  **세 곳을 동시에** 구현한다. GENERAL 인텐트 크래시 버그(실제 provider엔
  있고 Fake엔 없어서 `PROVIDER_MODE=fake`에서 500 크래시 났던 사고)와
  같은 패턴이 재발하지 않도록 하기 위함이다.
- 테스트는 `tests/conftest.py`의 기존 autouse fixture가 `provider_mode`를
  이미 강제로 `fake`로 고정해주므로, 이 모듈을 위한 별도 테스트 격리
  장치를 새로 만들 필요가 없다.

---

## 7. 응답 형식

새 `response_type` 필드를 추가하지 않는다 — `AgentResponse`는 지금
`llm_output`/`state`/`recommendations`/`message` 4개 필드뿐이고, 프론트는
이미 `llm_output`의 intent 값으로 화면을 분기하는 구조다. SCHEDULE도 같은
방식(`llm_output.intent == "SCHEDULE"`)으로 판별하게 해서 분기 기준이
두 군데로 갈라지지 않게 한다. `AgentResponse`에는 `schedule` 필드 하나만
추가한다.

```json
{
  "llm_output": { "intent": "SCHEDULE", "...": "..." },
  "state": { "...": "..." },
  "message": "오늘 오후 3시간 코스를 짜봤어요.",
  "schedule": {
    "total_duration_min": 180,
    "route_summary": "홍대 → 연남동 → 망원한강공원 순으로 이동 거리를 최소화했어요.",
    "basis_note": "이 정보는 15:00 기준으로 계산됐어요. 실제 방문 시간에는 운영시간·날씨 상황이 달라질 수 있어요.",
    "items": [
      {
        "order": 1,
        "place_id": "...",
        "place_name": "연남동 카페 A",
        "estimated_arrival": "15:00",
        "estimated_duration_min": 60,
        "travel_to_next_min": 15,
        "reason": "도보 이동 시작점에 가깝고 실내 공간이라 날씨 영향이 없어요."
      }
    ]
  }
}
```

---

## 8. 영향받는 파일 (예상)

| 파일 | 변경 종류 |
|------|---------|
| `backend/app/schemas.py` | `Intent.SCHEDULE`은 **이미 존재**(0절, PR #114) — 추가 작업은 `AgentResponse`에 `schedule` 필드 추가뿐 |
| `backend/app/services/interpret/orchestrator.py` | (신규 항목, A만 알던 파일) `intent is Intent.SCHEDULE`일 때의 조기 반환(0절)을 제거·확장해 조건 추출로 이어지게 함 |
| `backend/app/schedule/schemas.py` (신규) | `SchedulePlanningRequest`, `ScheduleResult`, `ScheduleItem` |
| `backend/app/schedule/planner.py` (신규) | 일정 편성 로직 — LLM 호출, candidates/conditions → ScheduleResult. 상태 비접근 |
| `backend/app/services/runtime/protocols.py` | `RecommendationProvider.recommend()`에 `limit` 파라미터 추가 |
| `backend/app/services/runtime/real_recommendation_provider.py` | 하드코딩된 `_RECOMMENDATION_LIMIT` 대신 `limit` 인자를 `run_recommendation_pipeline_from_context()`에 전달 |
| `backend/app/services/runtime/stubs.py` | Fake `RecommendationProvider`도 동일 시그니처로 반영 |
| `backend/tests/test_agent_runtime.py` 등 | `RecommendationProvider` Protocol을 구현하는 테스트 더블 시그니처 갱신 |
| `backend/app/providers/protocols.py`, `gemini.py`, `stub.py` | `LLMProvider`에 일정 편성용 메서드 추가 (Real+Fake 동시 구현) |
| `backend/app/services/runtime/agent_runtime.py` | SCHEDULE 분기 추가; D 호출 후 일정 편성 모듈 호출; B의 기존 `record_recommendation` 재사용 |
| `backend/app/services/runtime/response_composer.py` | `intent is Intent.SCHEDULE`일 때 고정 문구를 반환하던 기존 분기(0절)를 `compose_schedule_message()` 호출로 **교체**(추가 아님) — `tests/test_response_composer.py`의 관련 테스트도 갱신 필요 |
| `docs/design/package_work_breakdown.md` | 일정 편성 기능 담당자 정보 한 줄 추가 — 새 패키지 letter 아님 |
| `docs/design/int-07-schedule.md` | 본 문서 — A의 1차 구현 설계(v0.1)와 병합됨(v2.0) |

`backend/app/state/service.py`, `backend/app/domain/scoring.py`,
`backend/app/services/runtime/recommendation_transform.py`는 **변경 없음**
(v1.0에서 잘못 언급됐던 파일들).

---

## 9. 미결 사항

* **A에게 D 협의 결과 공유 필요.** A의 원 문서(0절, v0.1)에는 top_k
  5→10 확장과 혼잡도 2차 Scoring 처리를 아직 "D 협의 후 결정"으로
  남겨뒀는데, 이미 D와 합의 완료됨(top_k 10, 혼잡도 10개 전부 재계산,
  4·5절 참고) — A가 조건 추출·후속 구현에 들어가기 전에 알려줘야 함.
  (2026-08-14 재확인: 아직 A에게 공유했다는 기록 없음 — 코드 작업 아니라
  확인만 필요한 항목이라 우선순위가 계속 밀려온 것으로 보임)
* `travel_to_next_min`은 현재 TBD인 `estimate_travel_time` Tool과 연동
  가능. Tool 미구현 상태이므로 1차에서는 LLM 추정값을 쓰되, 근거 없는
  추측이 되지 않도록 `pairwise_distances_km`(haversine 기반)을 프롬프트에
  반드시 함께 제공한다. Tool 구현 완료 시 실측값으로 교체. (SCHEDULE-09
  시점까지도 Tool 미착수 확인 — C 영역이라 B가 임의로 만들 수 없음)
* **(2026-08-14 신규)** `ScheduleResult`의 `thinking_budget=0` 적용(응답
  지연시간 약 6.5배 단축)이 답변 품질에 주는 영향은 자동화된 방식으로
  검증되지 않았다. 이 프로젝트의 실행 환경이 외부 네트워크를 막고 있어
  실제 Gemini 응답을 자동으로 비교할 수 없었고, 검증은 사용자의 수동
  QA(`/dev-chat`)에만 의존했다. 이상 징후가 보고되면 최우선 확인 필요.
* **(2026-08-14 신규)** `time_available`/`max_travel_time` 조건 추출의
  단위 환산(분/시간) 수정은 프롬프트·스키마 설명으로만 개선됐고 구조적
  하드 검증은 아니다 — "5"가 5분인지 잘못 추출된 5시간인지 값만으로는
  구조적으로 판별할 방법이 없다. 여전히 확률적으로 틀릴 여지가 남아있다.
* **(2026-08-14 신규)** "대화 중 고른 장소로 일정 구성" — 지금 SCHEDULE은
  항상 "AI가 후보 10개 중 자율 선택"만 지원한다. 사용자가 대화 중 마음에
  든 장소를 모아 그걸로 일정을 짜는 기능은 State에 "원하는 장소" 개념
  자체가 없어 SCHEDULE 입력 모델을 바꿔야 한다(SCHEDULE-08에서 발견, 아직
  티켓 없음, A와 설계 논의 필요).
* **(2026-08-14 신규)** 인사동이 실제 지오코딩 별칭 테이블
  (`_JONGNO_LANDMARK_ADDRESS_ALIASES`)에 없어 불필요한 disambiguation이
  발생(SCHEDULE-09에서 발견). C(지오코딩) 영역이라 B가 임의로 고치지
  않음.
* **(2026-08-14 신규)** 이른 아침 시간대에 후보 조회는 성공(7개)했는데도
  최종 일정이 빈 배열로 나오는 사례 확인(SCHEDULE-09에서 발견). 영업시간
  기준 필터링이 D의 스코어링에서 걸렸을 가능성이 높다는 가설만 세움 —
  D 코드는 B 범위 밖이라 직접 확인하지 못함.
* **(2026-08-14 신규)** `FakeLLMProvider`의 `_SCHEDULE_MARKERS`가 "일정
  짜"류 고정 문구만 매칭해 "일정 다시 짜줘"처럼 어순이 바뀐 입력을 못
  잡는다. 테스트 스텁 전용 한계이고 실제 Gemini는 영향 없음(동적 개수
  편성 작업 중 발견, 아직 미조치).
* ~~FE 타임라인 UI 컴포넌트는 별도 이슈로 관리.~~ → SCHEDULE-08에서
  구현 완료(세로 타임라인, 이동 구간 분리). 아래 "해소된 항목" 참고.
* **(2026-08-26, D-091/D-092, 해소됨)** `co_visited_hints`(6.1절) 연동 —
  B 쪽 코드(associations.py/schemas.py/planner.py/프롬프트)와 A 쪽 배선
  (`agent_runtime.py`의 `plan_schedule`/`plan_partial_schedule` 호출부에
  `co_visited_fetcher=fetch_co_visited_hints`) 모두 반영 완료. RECOMMEND
  목록 자체의 2차 스코어링에도 같은 신호를 연결했다(D-092,
  `rerank_with_co_visited()`, D-040 패턴 재사용, `docs/decision-log.md` 참고)
  — SCHEDULE 설계 문서인 이 문서의 범위 밖이라 상세는 decision-log에만 남긴다.

**해소된 항목(번호 미부여, 08-18)**
* 뒷 순서 스탑이 estimated_arrival 기준으로 이미 마감했을 수 있는데도
  일정에 그대로 들어가는 문제(6.2.1절이 원래 남겨둔 한계): 완전한 재계산
  대신, D가 후보마다 내려주는 `operating_hours_display`와 LLM이 계산한
  `estimated_arrival`을 `app.schedule.planner._finalize_items()`가 대조해
  어긋나면 `ScheduleItem.warnings`에 경고를 채우는 구조적 후처리를 추가.
  프롬프트에도 운영시간을 함께 전달해 LLM이 애초에 피하도록 유도하되(1단계
  힌트), 그 지시만으로는 부족하다고 보고 후처리 재검증(2단계, 구조적 보장)을
  반드시 함께 둠. `PROMPT_VERSION` `1.0.12` → `1.0.13`. (6.2.1절 참고,
  자세한 배경은 위 v2.2 변경 이력)
* "6시간 코스 짜줘"처럼 활동 가능 시간이 긴 요청에서 실제로는 2.5시간
  분량만 채워 반환되는 문제: target_item_range()가 계산하는 목표 개수
  범위(예: 3~5개) 자체는 정상인데, duration_rule 문구가 "시간이 짧으면
  줄이라"는 하한 방향 지시만 있고 시간이 넉넉할 때 상한 방향으로 채우라는
  지시가 없어 LLM이 목표 개수 범위 안에서도 일찍 끝내버림. 시간이 넉넉하면
  상한 개수에 가깝게 채우고 체류시간도 넉넉히 잡으라는 지시를
  duration_rule에 추가. `target_item_range()`의 상한 계산이나
  `ScheduleLLMPlan.max_length=5` 하드 캡은 그대로 둠 — 순수 프롬프트 문구만
  수정. `PROMPT_VERSION` `1.0.13` → `1.0.14`.

**해소된 항목(안정화 작업, 08-10~08-13, 번호 미부여)**
* 활동 가능 시간이 짧으면(예: "2시간 코스 짜줘") `min_length=3` 고정
  하한 때문에 개수 제약을 못 맞춰 502가 반복되던 문제: `target_item_range()`
  신설로 시간 구간별(2시간 미만/2~3.5시간/3.5시간 이상) 목표 개수를
  동적으로 계산, 스키마 하한은 1로 완화하고 목표 개수는 프롬프트가
  매번 지시하는 방식으로 역할 분리.
* Gemini 응답 지연 대응으로 늘린 `EXTERNAL_API_TIMEOUT_SECONDS`가 원래
  짧게 끝나야 할 Tool/DB 조회까지 물려받던 문제: `LLM_API_TIMEOUT_SECONDS`
  신설로 분리(값 없으면 기존 설정 폴백, 하위호환 유지).
* 도착시각이 "11:59"처럼 어중간하게 나오던 문제: `estimated_arrival`만
  10분 단위로 반올림하는 후처리 추가(체류·이동 시간은 LLM 추정값 자체가
  정보라 그대로 둠).
* "N시간 코스를 짜봤어요" 문구가 말풍선과 카드 양쪽에서 중복 계산되던
  문제, RECOMMEND 카드가 서버 지연시간(ms)을 프로덕션 화면에도 노출하던
  버그(SCHEDULE도 동일하게 만들려다 발견): 말풍선을 단일 진실 공급원으로
  정리, 지연시간은 `isDeveloperView` 플래그로 개발자 화면 전용 통일.
* SCHEDULE/INFO 메시지가 있는 세션은 새로고침 시 대화 전체가 복원
  실패하던 버그(storage.ts의 `isChatMessage()` 타입 가드 누락): 두
  메시지 타입 분기 추가, 회귀 테스트 작성.
* SCHEDULE 직후 순수 추천 요청("일정 짜줘" 다음 "카페 추천해줘")이 재일정
  편성으로 오분류되던 버그: 조건 병합 없이 재라벨링 조건을 좁히는 방식은
  REJECT_ALL 회귀 위험이 있어 기각, 대신 intent 분류 프롬프트에 직전
  Intent를 노출해 예외 규칙 추가.
* 개발자 감사 패널이 SCHEDULE 턴을 항상 "추천 0건"으로 표시하던 버그
  (`recommendations` 기준으로만 집계, SCHEDULE은 `schedule` 필드 사용):
  SCHEDULE 턴 여부에 따라 라벨·상세 카드·Scoring 탭 분기.
* REJECT_SPECIFIC 부분 재편성에서 교체 슬롯 앞뒤 pinned 항목의
  `travel_to_next_min`·도착시각이 옛 값 그대로 남던 stale 값 버그 2건
  (코드 리뷰로 선제 발견, 실사용 신고 아님): `_resync_downstream_arrivals()`
  신설, 회귀 테스트 2건 추가.
* SCHEDULE 응답 지연시간 16.6초 → 2.55초(약 6.5배) 단축:
  `generate_schedule_plan`/`generate_schedule_fill` 두 호출에
  `thinking_budget=0` 적용(나머지 9개 호출은 파라미터 기본값 유지로
  영향 없음). 이후 같은 실측 검증 방식으로 `classify_intent`/
  `extract_recommend_conditions`에도 확장 적용(각 2.3배/1.8배 단축,
  정확도 동일 유지 확인) — 나머지 문장 생성·요약류 8개 호출은 품질 저하
  위험이 커 확장 대상에서 제외.

**해소된 항목(SCHEDULE-09)**
* "두 번째는 별로야"류 순번 지목, "두가헌은 빼줘"류 이름 지목, "N번째
  말고는 다 별로야"류 여집합 패턴까지 지원하는 일정 부분 수정
  (`ModifyType.REJECT_SPECIFIC`) 구현. 지목 안 된 자리는 Python이
  그대로 유지하고 지목된 자리만 LLM이 새 후보로 채우는 구조(Approach B)
  — LLM이 pinned 항목까지 통째로 되돌려주는 방식(Approach A)은 SCHEDULE-07의
  "구조적 보장 우선" 기조에 따라 채택하지 않음.
* 실사용 재현 버그 2건 수정: 지명 검색(Naver local search) 폴백이
  호출마다 다른 좌표를 반환해 pinned 항목까지 바뀌던 문제 →
  `RecommendedItem.name` 저장 예외 추가로 재검색 의존 제거. REJECT_SPECIFIC
  연속 2회째부터 감지 실패하던 문제 → `set_last_intent()` 신설로
  relabel 직후 `last_intent` 동기화.

**해소된 항목(SCHEDULE-08)**
* 세로 타임라인 UI(배지+선 구조, 이동 구간을 카드 사이 별도 컴포넌트로
  분리) 구현. SCHEDULE-06이 이미 완성해두고 프론트에 노출만 안 됐던
  재조정("다른 코스 보기")·범위 확대("검색 범위 넓혀서 다시 찾기") 버튼을
  RECOMMEND의 기존 문구·버튼 패턴 그대로 재사용해 노출.
* items 빈 배열일 때 프론트가 자체적으로 "0분 코스를 짜봤어요" 헤더를
  계산해 중복 표시하던 버그 발견·수정(백엔드 SCHEDULE-06 후속과 별개로
  프론트에도 독립적으로 있던 문제).

**해소된 항목(SCHEDULE-07)**
* SCHEDULE 다음 턴(및 최초 요청)에 D 후보가 3개 미만이면 편성 동작이
  정의돼 있지 않던 문제: `plan_schedule()`이 후보가 3개 미만이면 LLM을
  아예 호출하지 않고 정규화된 안내(`_NO_CANDIDATES_ROUTE_SUMMARY`)로
  바로 반환하도록 해소. `ScheduleLLMPlan.items`에는 이제 `min_length=3`
  제약을 걸었는데, 이 가드 덕분에 LLM이 실제로 불릴 때는 항상 후보가
  3개 이상이라 제약이 항상 만족 가능하다 — 이전에 우려했던 "제약을 걸면
  하드 실패만 늘어난다"는 문제는 이 순서(가드 먼저, 제약은 그 다음)로
  해소됨.
* LLM이 프롬프트의 "3~5개 선택" 지시를 가끔 안 지키던 문제(후보 10개가
  있어도 2개만 선택한 사례 확인, SCHEDULE-04 수동 테스트 중 발견): 위
  `min_length=3`/`max_length=5` 스키마 제약으로 하드 검증하도록 변경.
  `app.providers.gemini.RealGeminiProvider._call_structured()`가 이미
  갖고 있던 "검증 실패 시 오류 안내를 붙여 1회 자동 재시도" 경로를
  다른 구조화 출력과 동일하게 그대로 탄다 — 재시도까지 실패하면
  `llm_output_invalid`(502)로 명시적으로 실패하고, 조용히 개수를 어긴
  일정을 반환하지 않는다. 프롬프트 문구도 "반드시 3~5개" 식으로 더
  단호하게 보강해 재시도 빈도 자체를 줄이는 보조 조치를 함께 함.

**해소된 항목(SCHEDULE-06)**
* `ScheduleItem`의 세부 근거(도착 시각·체류 시간·이유 문장)가 B 히스토리에
  저장되지 않던 문제: `RecommendedItem`에 선택 필드로 추가해 해소(6.3절
  참고). RECOMMEND/MODIFY 흐름은 영향 없음.
* SCHEDULE 다음 턴의 조건 변경 발화("다른 데로 바꿔줘")가 MODIFY로
  오분류되어 잘못 응답하던 문제: 라우팅 단계(agent_runtime.py)에서 B의
  `last_intent`를 읽어 해소(3.1절 참고). classify_intent는 변경 없음.
* 실제 Gemini 수동 테스트(2026-08-10)에서 `items`는 빈 배열로 오면서
  `route_summary`/`total_duration_min`은 그럴듯한 문장으로 채워 보내는
  비일관 응답이 관측됨 — planner.py의 `plan_schedule()`이 `items`가 비면
  나머지 필드도 결정적으로 정규화(고정 안내 문구, `total_duration_min=0`)
  하도록 수정. `compose_schedule_message()`도 이 경우 "0분 코스를
  짜봤어요" 같은 어색한 접두사 없이 안내 문구만 반환하도록 함께 수정.

**해소된 항목(SCHEDULE-04, 문서 정리 누락 반영)**
* `estimated_arrival`은 `visit_datetime`이 없을 때 현재 시각 기준으로 계산할지,
  LLM이 상대적 표현만 반환하도록 할지 결정 필요했던 항목: `planner.py`의
  `plan_schedule()`이 `visit_datetime`이 없으면 현재 시각(KST)을 결정적으로
  채우고 LLM 호출·`basis_note` 둘 다 같은 값을 쓰도록 이미 SCHEDULE-04에서
  구현 완료. 9절 목록에서 지우는 걸 SCHEDULE-07 때까지 누락했던 것을 정리.

**해소된 항목(v2.0 → A의 1차 구현과 병합하며 확인)**
* INT-07 트리거 표현·분류 기준 문서화: 별도로 새로 쓸 필요 없음 — A가
  이미 판별 표를 만들어 구현·배포까지 끝냄(0절, 3절 참고).
* `Intent.SCHEDULE` 추가: 이미 완료(0절, PR #114) — 추가 작업 불필요.

**해소된 항목(v1.3 → D 협의 완료)**
* top_k 5→10 확장: D 확인 완료, `recommend()`에 `limit` 파라미터 추가(기본값
  5 유지)로 진행. 5절 참고.
* D-040 혼잡도 2차 Scoring: SCHEDULE에서는 **10개 전부** 재계산하는 것으로
  확정(`rerank_with_concentration()`은 개수 하드코딩 없어 D 쪽 구현 부담
  없음). API 호출 2배 비용을 감수하고 `concentration_intent` 품질을
  우선한 결정. 4절 참고.
* (D 피드백으로 신규 발견) 1차 점수·근거 문장이 단일 `visit_at` 기준으로
  계산돼 뒷 순서 스탑에는 부정확할 수 있다는 문제 → `ScheduleResult.basis_note`
  고정 안내 문구로 처리, 스탑별 재계산은 이번 범위 밖. 6.2.1절 참고.

**해소된 항목(v1.1 → 준비 단계에서 결정)**
* `SchedulePlanningRequest.candidates`는 `RecommendationItem`(D의 공개
  응답 스키마)으로 확정. 6.1절 참고.
* `pairwise_distances_km` 계산 주체: `RecommendationItem`에는 위경도가 없어
  D 응답만으로는 후보 간 거리를 못 구한다는 게 확인됨 → A가 C의
  `AgentContextResponse.places`(위경도 보유)를 place_id로 매칭해 계산하는
  것으로 확정. D/C 스키마 변경 불필요.
* 일정 편성 모듈의 담당자 표기는 `package_work_breakdown.md`에 반영 완료
  (새 패키지 letter 없이 "참고" 섹션으로 기록).

---

## 10. 기대 효과

"오늘 하루 어디 갈지 모르겠어" 유형의 고의도 사용자가 단일 메시지로 완성된
일정을 받을 수 있어, 여러 번 RECOMMEND → MODIFY를 반복하는 불편을 줄인다.
기존 Scoring·하드 필터 파이프라인을 재사용하므로 추천 품질이 유지되며,
LLM 일정 편성 로직은 B가 아니라 신규 독립 모듈에 격리되어 있어 B의 상태
관리 원칙과 RECOMMEND 흐름 어느 쪽에도 영향을 주지 않는다.
