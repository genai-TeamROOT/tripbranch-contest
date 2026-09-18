**문서 버전:** v1.0
**작성일:** 2026-08-12
**관련 인텐트:** SCHEDULE(INT-07), MODIFY(INT-03), COMPARE(INT-04), RECOMMEND(INT-01) — 여러
Intent를 가로지르는 공용 되묻기(clarification) 메커니즘이라 특정 INT 번호를 붙이지 않는다.

**상태:** PR 1~4 구현 완료(2026-08-12) — 인프라 + 케이스 1(SCHEDULE-06 모호 MODIFY,
PR1) · A2(`location_required`, PR2) · 케이스 3(`compare_single_shown`, PR3) · 케이스 4/5
(`schedule_bare_restart`/`bare_restart_active`/`schedule_bare_restart_completed`, PR4).
이어서 2026-08-13 실사용 피드백으로 `no_data_closed`/`no_data_empty`/`no_data_exhausted`/
`schedule_no_candidates` 4개 코드가 추가됐다(§4.2) — 결과 0건 상황을 원인별로 세분화한
되묻기로, 이 문서의 PR 1~4 phasing 밖에서 추가됐다. 백엔드 `pytest`/`ruff`, 프론트
`tsc`/`eslint`/`vitest` 전부 통과.

**PR 1 범위 조정 — A1 제외**: 구현 중 확인해보니 `location_ambiguous`(동명이인 장소 후보)는
`ResolveLocationTool`이 실제로는 후보 이름 목록을 반환하지 않고(`_error_result(code="no_data",
cause="ambiguous_location", details={"reason": "ambiguous_location"})`처럼 이유만 담는다) 단순
재질문 문구만 만든다 — 4.1절에서 다룬 것과 달리 "버튼으로 렌더링만 하면 되는" 케이스가 아니었다.
버튼화하려면 Tool 계층에 후보 이름을 노출하는 별도 작업이 먼저 필요해, PR 1의 인프라 스모크
테스트 대상에서 A1을 빼고 대신 **케이스 1을 PR 1에 포함**시켰다(어차피 실사용 버그를 고치는
최우선 케이스라 스키마가 그대로 들어맞는다). A1은 Tool 계층 후보 노출 작업과 함께 후속 PR로
재검토한다.

## 0. 배경 — 이 문서가 왜 필요한가

"경복궁 근처 일정짜줘"(SCHEDULE, 완료)로 일정을 받은 뒤 "경복궁 근처 카페 추천해줘"라고 하면 새
추천이 아니라 또 다른 일정이 나오는 버그를 조사하는 과정에서, 인텐트 분류가 근본적으로 갈리는
지점들은 규칙을 아무리 정교하게 다듬어도 추측일 뿐이라는 게 드러났다. 이런 지점에서는 추측 대신
되묻기 문구 + 버튼을 보여주고, 사용자가 버튼을 누르면 그 즉시 결정적으로 해당 Intent에 연결하는
공용 메커니즘이 필요하다.

## 1. 근본 원인 (실사용 버그 재확인)

1. `classify_intent()`는 "경복궁 근처 카페 추천해줘"를 **정상적으로** MODIFY로 분류한다 — D-053 규칙
   ("이전 추천 있음 + 지명+근처 표현 → MODIFY", [gemini_prompts.py:82-86](../../backend/app/providers/gemini_prompts.py))
   때문이다. 이 자체는 버그가 아니다.
2. `agent_runtime.py`의 SCHEDULE 재조정 감지("SCHEDULE-06", D-061 — 코드 주석에는 인용되지만
   `docs/decision-log.md`에 대응 항목이 없다. 이 문서와 별개로 알려진 문서화 갭이다)가
   [agent_runtime.py:421-439](../../backend/app/services/runtime/agent_runtime.py)에서
   `last_intent=="SCHEDULE"` + `pending_clarification is None`(직전 SCHEDULE이 되묻기 없이 깔끔히
   끝남) + 이번 턴이 MODIFY/COMPLETE라는 조건만 보고, **왜** MODIFY로 분류됐는지는 안 가리고
   무조건 SCHEDULE로 라벨을 바꿔 편성 모듈을 다시 태운다.

REJECT_ALL/REJECT_SPECIFIC("다른 곳", "2번 빼줘")처럼 일정이 있어야만 말이 되는 발화는 이래도
맞지만, "카페 추천해줘"류는 "일정 재조정"인지 "그냥 카페 추천"인지 글자로 구분이 안 되는 진짜
모호 케이스다.

## 2. 현재 인프라 — 왜 새로 만들어야 하는가

- `ClarificationPayload`([schemas.py:469-472](../../backend/app/schemas.py))는 `message` 텍스트 +
  `missing_fields`/`ambiguous_fields`뿐, options/버튼 필드가 없다.
- `AgentResponse`에도 quick-reply류 필드가 없다(grep 0건).
- 프론트도 되묻기는 `ChatPage.tsx`의 placeholder 텍스트 힌트로만 처리하고 버튼 컴포넌트가 없다.
- 기존 버튼(`RecommendationResultMessage.tsx`의 `onRequestMore`/`onRelaxRadius`)은 고정 텍스트를
  재전송하는 방식이라 재사용 불가 — 이번 케이스들의 모호성 자체가 "고정 텍스트로도 분류가
  안 갈린다"는 것이기 때문이다.

## 3. 백엔드 스키마 설계

### 3.1 `ClarificationOption` 신설 + `ClarificationPayload.options` 추가
[schemas.py:469-472](../../backend/app/schemas.py):
```python
class ClarificationOption(BaseModel):
    id: str                    # 세션 내 안정적 키, 예: "schedule_continue"
    label: str                 # 버튼 표시 문구
    resolved_intent: Intent    # 선택 시 강제할 Intent (감사 표시용)

class ClarificationPayload(BaseModel):
    missing_fields: list[MissingField] = Field(default_factory=list)
    ambiguous_fields: list[AmbiguousField] = Field(default_factory=list)
    message: str
    options: list[ClarificationOption] = Field(default_factory=list)  # 신규, 기본 빈 리스트
```
기본값이 빈 리스트라 기존 되묻기(옵션 없음)는 전부 그대로 호환된다.

### 3.2 `AgentRequest.clarification_choice` 추가
[schemas.py:561-567](../../backend/app/schemas.py):
```python
class AgentRequest(BaseModel):
    user_input: str = Field(..., min_length=1)
    session_id: str | None = None
    device_location: str | None = None
    clarification_choice: str | None = None  # 신규 — ClarificationOption.id를 그대로 echo
```
`user_input`에는 프론트가 버튼 `label`을 그대로 채워 보낸다(채팅 이력 표시/감사 로그용일 뿐,
라우팅에는 안 쓴다). 라우팅은 오직 `clarification_choice`가 결정한다.

### 3.3 해소 방식 — 텍스트 재전송이 아니라 명시적 override
기존 `onRequestMore`("다른 곳 보여줘")류 버튼은 문구 자체가 모든 규칙에서 명확하게 한 Intent로
분류되기 때문에 텍스트 재전송이 안전하다. 이번 케이스들은 정확히 "고정 문구로도 분류가 갈린다"가
모호성의 원인이라 같은 방식을 못 쓴다.

`run_agent_flow`([agent_runtime.py](../../backend/app/services/runtime/agent_runtime.py)) 최상단,
`ensure_current_context()` 직후에 분기 추가:
```python
if request.clarification_choice is not None:
    resolution = _resolve_clarification_choice(
        code=session_context.pending_clarification,
        choice_id=request.clarification_choice,
    )
    if resolution is not None:
        llm_output = resolution.llm_output  # classify_intent()/extract_*_conditions() 생략
        # 이후 apply()/transform() 이하 정상 흐름 그대로 진행
```
`pending_clarification` 코드와 `choice_id`가 안 맞으면(새로고침 후 오래된 버튼 클릭 등) `None`을
반환하고 평소처럼 `build_interpretation()`으로 폴백한다 — 절대 죽지 않는다.

`_resolve_clarification_choice`는 LLM 호출이 없는 순수 dict 매핑이라 단위 테스트하기 쉽고, 해소
턴에서 분류 LLM 호출 2개(`classify_intent`, `extract_*_conditions`)를 아예 건너뛰는 부수 이득도
있다.

기존 `pending_clarification` 문자열 코드 메커니즘([state/service.py:528-548](../../backend/app/state/service.py))을
그대로 재사용한다 — 케이스별로 새 코드값만 추가. 새 구조체를 따로 만들지 않는다.

## 4. 적용 대상

| # | 케이스 | 감지 위치 | 신규 판정 로직 | 비고 |
|---|---|---|---|---|
| A1 | 동명이인 장소 후보 (`location_ambiguous`) | 기존 그대로 | 불필요 — `ambiguous_fields.candidates`를 버튼으로 렌더링만 | 인프라 스모크 테스트용, 가장 먼저 |
| 1 | SCHEDULE 완료 후 "OO 근처 카페 추천해줘"류 모호 MODIFY | `agent_runtime.py`, SCHEDULE-06 내부 | 키워드 체크 1개 | 실사용 버그, 최우선 임팩트 |
| A2 | `location_required` (검색 중심점 없음) | 기존 그대로 | 불필요 — 종로구 대표 스팟 고정 버튼 4개 | 서비스 지역이 종로구 한정이라 고정 목록이 자연스러움 |
| 3 | 노출 1개 상태 "어디가 좋아?" (COMPARE/RECOMMEND 흔들림) | `agent_runtime.py`/`orchestrator.py`, 분류 직후 | `shown_place_count==1` 카운트 체크 | 저위험, PR1 인프라 재사용 |
| 4 | SCHEDULE 위치 되묻기 중 "처음부터 다시"(목적어 없음) | `orchestrator.py`, 분류 이전 선제 차단 | 상태 체크 + 재시작 문구 매칭 | `orchestrator.py`가 새 위치라 살짝 더 큼 |
| 5 | RECOMMEND/MODIFY 진행 중(되묻기 아님) "처음부터 다시" | `orchestrator.py`, 분류 이전 선제 차단 | 케이스4와 동일 패턴 + 조건 요약 문구 조합 | 6절 참고 |
| 2 | 이력 없이 "OO랑 XX 중 어디가 좋아?" | `classify_intent()` 프롬프트 | 신규 stage-1 필드 필요 | **범위 밖** — 프롬프트/스키마 변경 필요, 후속 작업 |

### 4.1 범위에서 제외한 케이스 — "이전 추천 있음 + 완전히 동떨어진 지명"
서비스 지역은 종로구 한정이다([service_area.py](../../backend/app/service_area.py),
[place_search_policy.py:15](../../backend/app/place_search_policy.py)). "제주도"처럼 서울 카페 추천
도중 완전히 동떨어진 지명이 나와도, `ResolveLocationTool._outside_service_area_result()`
([resolve_location.py:534-554](../../backend/app/tools/resolve_location.py))가 좌표가 종로구
밖이면 MODIFY든 RECOMMEND든 상관없이 이미 `unsupported_region`으로 걸러지고, 안내 문구도 이미
있다(D-044, [response_composer.py:70-75](../../backend/app/services/runtime/response_composer.py)):

> "현재는 베타 서비스로 종로구의 장소 추천만 가능해요. 종로에서 가고 싶은 위치를 말씀해주세요."

어느 Intent로 분류되든 결과가 같으므로 되묻기가 필요 없다. **신규 작업 불필요.**

### 4.2 2026-08-13 추가 — 결과 0건 원인별 되묻기

실사용 피드백으로 "조건에 맞는 곳이 없어요"류 되묻기가 원인과 무관하게 뭉뚱그려져
있어 사용자가 뭘 바꿔야 할지 알기 어렵다는 문제가 드러났다. 결과 0건의 원인을
아래 4가지로 나눠 각각 다른 되묻기를 띄운다. 코드는 [agent_runtime.py](../../backend/app/services/runtime/agent_runtime.py)에
있다.

| code | 원인 | 감지 위치 | 선택지 | 비고 |
|---|---|---|---|---|
| `no_data_closed` | 검색된 후보가 있지만 전부 **운영종료**라 하드 필터에서 제외됨(`ScoringResult.excluded_closed_place_ids`, [recommendation-scoring.md §7](./recommendation-scoring.md#7-출력-구조)) | `_respond_no_data_closed()` | "운영 중이 아닌 곳도 볼게요"(`show_closed`) 1개 — 선택 시 조건은 그대로 두고 `ignore_operating_hours=True`로 재조회([recommendation-scoring.md §3](./recommendation-scoring.md#3-제외-규칙-하드-필터)) | RECOMMEND/MODIFY/SCHEDULE 공통 사용 — SCHEDULE도 원인이 운영종료뿐이면 이 되묻기를 먼저 띄워 무한 되묻기를 막는다 |
| `no_data_empty` | TourAPI 자체가 0건(카테고리 불일치 원인1 / 반경이 좁음 원인3 — 신호가 같아 구분 불가) | `no_data_empty` 분기 | "검색 범위 넓히기"(`widen_radius`, `max_travel_time`을 상한까지 올림) / "다른 종류도 보기"(`widen_category`, `place_types`/`place_tags` 비움) | |
| `no_data_exhausted` | 후보는 있었지만 이전 노출/거절로 전부 소진됨(원인2 — `provider_metadata.status=="success"`로 원인1/3과 구분) | `no_data_exhausted` 분기 | "다른 종류도 보기" / "검색 범위 넓혀서 보기" / "다른 지역에서 찾기" / "날씨 상관없이 보기" / "새로운 조건 직접 말할게요"(자유 입력 유도) | 선택지 5개로 가장 많음 — 제외 이력 자체를 다시 보여주는 선택지는 B(세션 상태) 리셋이 필요해 제외 |
| `schedule_no_candidates` | SCHEDULE 편성용 후보가 부족해 일정을 만들지 못함 | SCHEDULE 편성 실패 분기 | "다른 지역에서 찾기" / "다른 종류의 장소도 포함해서 찾기" | 선택 시 조건 병합 후 intent를 SCHEDULE로 되돌려(`force_schedule=True`) 편성을 재시도 |

`no_data_empty`/`no_data_exhausted`의 원인1·2·3 구분은 [agent_runtime.py](../../backend/app/services/runtime/agent_runtime.py)의
`_NO_DATA_RESOLVABLE_INTENTS` 주석에 상세 근거가 있다. 이 4개 코드는 §10의
PR 1~4 phasing이 끝난 뒤 별도로 추가됐다.

## 5. 케이스 1 상세 (실사용 버그 수정, 최우선)

REJECT_ALL/REJECT_SPECIFIC은 무조건 재라우팅을 유지하고, CHANGE_CONDITION일 때만 모호성 체크를
추가한다:
```python
_SCHEDULE_CONTINUATION_MARKERS = ("일정", "코스", "루트", "편성", "순서", "스케줄")
_RECOMMEND_STYLE_MARKERS = ("추천", "보여줘", "알려줘", "찾아줘", "찾아봐")

def _is_ambiguous_schedule_or_recommend(user_input: str) -> bool:
    """SCHEDULE 완료 직후 CHANGE_CONDITION MODIFY가 '일정 재조정'인지 '그냥 추천'인지
    글자로는 구분 안 되는 경우를 감지한다."""
    return (
        not any(m in user_input for m in _SCHEDULE_CONTINUATION_MARKERS)
        and any(m in user_input for m in _RECOMMEND_STYLE_MARKERS)
    )
```
`ModifyPayload.changed_fields`로 구분하려는 접근은 채택하지 않는다 — 실제 Gemini 프롬프트는
카테고리 단어("카페")도 같은 턴에 `changed_fields`로 함께 묶어버려서
([gemini_prompts.py:335-336,386-389](../../backend/app/providers/gemini_prompts.py)) 신뢰할 수
없다.

[agent_runtime.py:421](../../backend/app/services/runtime/agent_runtime.py)의 기존 조건 안,
relabel 직전에 `modify.modify_type is ModifyType.CHANGE_CONDITION and
_is_ambiguous_schedule_or_recommend(request.user_input)`이면 SCHEDULE로 바꾸는 대신
`NEEDS_CLARIFICATION` + options 2개로 조기 반환한다:

| id | label | resolved_intent |
|---|---|---|
| `schedule_continue` | 일정 다시 짜기 | SCHEDULE |
| `recommend_only` | {카테고리}만 추천받기 (카테고리 없으면 "장소만 추천받기") | RECOMMEND |

**카테고리 보간(2026-08-12 PR 1 구현 중 반영)**: 애초 범용 문구로 시작할 계획이었으나, "카페
추천해줘"에 "장소만 추천해드릴까요?"로 되묻는 건 너무 밋밋하다는 피드백을 받아 바로 반영했다.
`_extracted_category_label(modify)`가 `modify.condition_changes.place_tags[0]`(PlaceTag는 값
자체가 한국어라 그대로 씀) → 없으면 `place_types[0]`(`_PLACE_TYPE_LABELS`로 한국어 변환) → 둘 다
없으면 `None` 순으로 확인한다. `None`이면 기존 범용 문구("장소만 추천받기"/"...장소만
추천해드릴까요?")로 폴백한다. Fake(`stub.py`)도 `extract_modify_conditions()`에 "카페" 단독 언급
(기존엔 "말고"+"카페" 조합만 처리) 시 `place_tags`/`place_types`에 추가하는 분기를 새로 넣어 Real과
동작을 맞췄다.

## 6. 케이스 5 상세 — 조건을 문장에 실제로 넣는 방식

"조건을 초기화할까요?" 같은 추상적 표현 대신, 실제 검색 맥락(장소/날씨/카테고리)을 문장에 넣는다.
`UserConditions`를 짧은 한국어 구절로 바꾸는 조합 함수를 새로 만든다(D-054 INFO 템플릿 작업 때
만든 `_compose_place_info_sentence` 패턴과 동일하게 고정 템플릿 조합, LLM 호출 없음):

```python
def _compose_condition_phrase(conditions: UserConditions) -> str:
    """채워진 필드만 우선순위(장소 → 날씨 → 카테고리) 순으로 최대 2개까지만 이어붙인다.
    다 붙이면 문장이 길고 부자연스러워지므로 신호를 2개로 제한한다."""
```

예시:
- `search_center="경복궁"` + `weather_intent=AVOID` → "경복궁 근처 비를 피할 장소로 다시
  알아볼까요?"
- `search_center="경복궁"`만 있음 → "경복궁 근처로 다시 알아볼까요?"
- 아무 조건도 안 남아있으면 → "다시 알아볼까요?"

버튼 2개:

| id | label | resolved_intent |
|---|---|---|
| `keep_context` | {조합된 구절}로 다시 알아볼까요? | (기존 조건 유지, REJECT_ALL 재실행) |
| `full_reset` | 새로운 목적지로 다시 찾아봐드릴까요? | (전체 리셋) |

정확한 조사/어미 처리는 구현 시 실제 문장으로 테스트하며 다듬는다 — 기존 INFO 템플릿 작업
(`_compose_place_info_sentence`)에서도 은/는·이에요/예요 같은 조사 처리에 테스트가 필요했다.

케이스 4(SCHEDULE 위치 되묻기 중 "처음부터 다시")도 같은 선제 차단 위치(`orchestrator.py`,
분류 이전)와 재시작 문구 매칭 로직을 공유하지만, 옵션은 SCHEDULE 내부 상태 초기화 여부만 묻는
1쌍("일정을 처음부터 다시 잡을게요" / "아니요, 계속할게요")으로 케이스 5보다 단순하다.

## 7. 케이스 A2 상세 — `location_required` 버튼 구체화

현재 프로덕션에서 실제로 도는 `missing_fields`류 되묻기는 사실상 `location_required`/
`location_ambiguous` 둘뿐(`_CLARIFICATION_TEMPLATES`,
[response_composer.py:56-63](../../backend/app/services/runtime/response_composer.py)).
`place_required`/`place_ambiguous`(INFO/COMPARE용)는 템플릿만 정의돼 있고 실제로 채워 넣는
호출부를 찾지 못했다 — 버튼화 우선순위를 낮게 둔다. MODIFY의 "`current_conditions` 없음" 케이스
(아직 아무 추천도 없는 상태에서 "다른 곳"류 발화)는 제안할 선택지 자체가 없어 버튼 후보에서
제외하고, 기존 자유 입력 유도 문구("아직 추천한 결과가 없어요. 어떤 장소를 찾고 계신가요?")를
그대로 유지한다.

`location_required`("어디 근처에서 찾아드릴까요?") 버튼: 서비스 지역이 종로구 한정이므로 대표
스팟 고정 버튼 4개 — "경복궁 근처" / "인사동 근처" / "광화문 근처" / "북촌 근처".

## 8. 프론트엔드

- `types.ts`: `ClarificationPayload`에 `options: {id, label, resolved_intent}[]` 추가,
  `ChatRequest`에 `clarification_choice?: string | null` 추가, `ChatMessage` 유니온에
  `{type: "clarification", text, options}` 추가.
- 신규 `components/chat/ClarificationMessage.tsx` — `RecommendationResultMessage.tsx`의
  `onRequestMore`/`onRelaxRadius` 버튼 스타일 재사용. Props: `{text, options, isLoading,
  onSelectOption}`.
- `ChatMessageList.tsx`에 `clarification` 타입 분기 추가.
- `TripContext.tsx`의 `APPEND_CHAT_TURN` 리듀서: `clarification.options`가 있으면
  `assistant_text` 대신 `clarification` 메시지를 push(중복 렌더링 방지). `awaiting_clarification`
  불리언은 자유 텍스트 입력 fallback용으로 그대로 유지.
- `ChatPage.tsx`: `send(text, clarificationChoice?)`로 일반화, 버튼 클릭 시
  `send(option.label, option.id)` — `label`은 화면 표시용, `id`는 `clarification_choice`로 전송.

## 9. 테스트 계획

- `backend/tests/test_agent_runtime.py`(기존 SCHEDULE-06 블록 527-746행 옆에 추가): 케이스별로
  "모호 감지 → `NEEDS_CLARIFICATION` + options 2개" 1개, "옵션별 해소 → 올바른 Intent" 옵션당 1개,
  "비모호 회귀"(기존 REJECT_ALL/CHANGE_CONDITION 케이스가 그대로 SCHEDULE 재라우팅되는지) 1개,
  "잘못된/오래된 `clarification_choice` → 정상 폴백" 1개.
- 프론트: `ClarificationMessage.test.tsx` 신규(버튼 렌더링, 클릭 시 `onSelectOption` 호출,
  `isLoading` 시 비활성화) — 기존 `PlaceInfoCard.test.tsx` 패턴 참고.

## 10. Phasing

한 PR로 묶지 않는다:

- **PR 1**(완료): 인프라(옵션 스키마 + `clarification_choice` 해소 경로) + **케이스 1**
  (SCHEDULE-06 모호 MODIFY) — 실사용 버그 수정, 최우선. 원래 계획은 인프라 스모크 테스트로
  A1을 같이 넣는 것이었으나, 0절에 적은 이유로 A1을 빼고 실사용 버그 수정을 바로 합쳤다.
- **PR 2**(완료): **A2**(`location_required` 종로구 대표 스팟 버튼).
- **PR 3**(완료): **케이스 3**(노출 1개 COMPARE/RECOMMEND 흔들림, `compare_single_shown`).
- **PR 4**(완료): **케이스 4 + 5**("처음부터 다시" 양쪽 상태, `schedule_bare_restart`/
  `bare_restart_active`/`schedule_bare_restart_completed`) — `orchestrator.py` 선제 차단이라는
  새 위치와 조건-요약 함수가 필요해 가장 나중이었다.
- **PR 5**(완료, 2026-08-13): §4.2의 `no_data_closed`/`no_data_empty`/`no_data_exhausted`/
  `schedule_no_candidates` — PR 1~4 마무리 후 실사용 피드백으로 추가된, 결과 0건 원인별
  되묻기. 이 phasing 계획을 세울 당시엔 없던 범위라 PR 1~4와 별도로 번호를 매긴다.
- **A1**(동명이인 후보 버튼)은 Tool 계층이 후보 이름을 노출하도록 먼저 손봐야 해서 phasing에서
  뺐다 — 필요성이 재확인되면 별도 PR로 설계한다. 아직 미착수.

## 11. 범위 밖

- **케이스 2**(이력 없이 "OO랑 XX 중 어디가 좋아?") — LLM 분류 자체가 버전마다 흔들리는 케이스라
  결정적 코드 체크로 못 잡고, 새 프롬프트 규칙 + `IntentClassificationResult` 신규 필드가
  필요하며, "각각 소개해드릴까요" 옵션이 기존 Intent 하나에 안 맞물린다(INFO 두 번 호출 등). PR
  1~5로 메커니즘을 검증한 뒤 후속 작업으로 분리한다.
- 되묻기 문구에 LLM 호출 추가 — 기존처럼 고정 한국어 템플릿만 사용
  ([response_composer.py:500-502](../../backend/app/services/runtime/response_composer.py)의
  `needs_clarification` → `message` 그대로 통과하는 패턴과 동일).
- `pending_clarification`을 문자열 코드 이상의 구조체로 바꾸는 것 — 이미 병합된 세션 상태를
  재사용하는 쪽을 택했으므로 스키마 변경이 불필요하다.
- 기존 `onRequestMore`/`onRelaxRadius` 텍스트 재전송 버튼을 이 옵션 메커니즘으로 교체 — 이미
  명확한 케이스라 손댈 필요 없다.
- D-053 규칙 텍스트나 `ModifyPayload.changed_fields` 시맨틱 변경 — 케이스 1의 수정은 기존
  override에 좁히는 조건 하나를 더하는 것뿐, `classify_intent()`/`extract_modify_conditions()`
  반환값 자체는 건드리지 않는다.
- "이전 추천 있음 + 완전히 동떨어진 지명" — 4.1절 참고, 이미 D-044로 해결됨.

## 12. 관련 문서

- [`docs/design/int-07-schedule.md`](int-07-schedule.md) — SCHEDULE 편성 흐름, SCHEDULE-06 재조정
  감지의 원 설계 배경(3절).
- `docs/decision-log.md` D-039/D-053/D-059 — `pending_clarification`/`last_intent` 컨텍스트
  전달 메커니즘의 앞선 결정들.
- **알려진 문서화 갭**: 코드 주석([agent_runtime.py:432](../../backend/app/services/runtime/agent_runtime.py))이
  인용하는 D-061이 `docs/decision-log.md`에 없다. 이 문서 작업 범위는 아니지만, SCHEDULE-06을
  수정하는 PR 2에서 함께 채워 넣는 것을 권장한다.
