# Router Prompt History

## 현재 활성 슬롯

| 슬롯 | 관리 버전 | 템플릿 | 공유 규칙 |
| --- | --- | --- | --- |
| router.classify | v2.6.0 | intent_definitions.md, intent_priority.md, context_rules.md, boundary_cases.md | service_scope, safety |

## Draft

- 2026-09-05(v2.6.0): **기준점을 현재 위치로 바꾸는 후속 발화도 INFO를 유지합니다**(D-121).

  `context_rules.md`에 이미 "직전 INFO를 다른 장소로 이어가면 INFO 유지" 규칙이 있는데
  예시가 전부 지명("인사동은?", "창덕궁은?")이라, 지명 대신 "현재 위치"를 넣은 같은
  모양의 발화에는 적용되지 않았습니다.

  실측(2026-09-05): "근처에 화장실 있어?" → "아니 지금 위치로"가 **RECOMMEND로 분류돼
  화장실을 찾던 사람에게 관광지 추천이 나갔습니다**. 급한 상황에서 답을 통째로 잃는
  실패라 기존 규칙에 현재 위치 표현을 예시로 덧붙였습니다 — 새 규칙을 만들지 않고
  같은 규칙의 적용 범위만 넓혔습니다.
- 2026-09-04(v2.5.0): **행사를 찾는 요청을 RECOMMEND가 아니라 INFO로 보냅니다**(TP-237).
  `intent_definitions.md`의 INFO 정의와 `intent_priority.md`의 5번에 "행사를 찾는
  요청은 장소명이 없어도, '추천해줘' 형태여도 INFO"를 넣고, `boundary_cases.md`에
  경계 예시 넷을 추가했습니다.

  배경은 TP-201입니다. 축제공연행사(contentTypeId=15)를 추천 후보에서 뺐는데
  (D-120 — `places`에 행사 기간 컬럼이 없어 끝난 행사를 거를 수 없습니다),
  모델은 여전히 `place_types=['festival']`을 냈습니다. `prompts/recommend/extract.md`가
  축제를 설명하지 않는데도 그런 이유는 구조화 출력 스키마(`LLMOutput` → `PlaceType`)가
  허용 값으로 제시하기 때문입니다. 그대로 두면 "축제 추천해줘"가 이유 없는 빈 결과로
  끝납니다.

  가른 기준은 **찾는 대상이 장소냐 행사냐**입니다. TourAPI가 이미 그 선을 긋고 있어
  공연장(VE060100)·전시관(VE070300)·미술관(VE070600)·박물관(VE070100)은 문화시설(14)이고,
  그 안에서 열리는 축제·공연·전시가 15입니다. 유형 15 활성 189건의 중분류 분포는
  축제 96·행사 74·공연 19라 "축제만"의 문제가 아닙니다.

  **A 소유 슬롯을 C가 수정했습니다**(@kiminlim 리뷰 요청). TP-201 후속이라 한 PR로
  묶었습니다 — 세 슬롯 중 하나만 바뀌면 여전히 빈 결과가 나갑니다.

  실측(2026-09-04, Gemini 실호출 13발화. Langfuse 프롬프트 관리를 끄고 레포 프롬프트로 확인):

  | 발화 | 전 | 후 |
  | --- | --- | --- |
  | 축제 추천해줘 | RECOMMEND, place_types=['festival'] → 후보 0건 | INFO, realtime_event |
  | 서울에서 축제 갈 만한 곳 추천해줘 | RECOMMEND → 0건 | INFO, realtime_event (place_name='서울') |
  | 강남구 축제 추천해줘 | RECOMMEND → 0건 | INFO, realtime_event (place_name='강남구') |
  | 전시회 추천해줘 | RECOMMEND → 0건(태그 상충) | INFO, realtime_event |
  | 콘서트 갈 만한 곳 알려줘 | RECOMMEND → 0건(태그 상충) | INFO, realtime_event |
  | 공연 볼 만한 데 추천해줘 | RECOMMEND → 공연장만 | INFO, realtime_event |
  | 종로 전시회 뭐 있어? | INFO, realtime_event | INFO, realtime_event |
  | 미술관 추천해줘 | RECOMMEND | RECOMMEND (그대로) |
  | 박물관 추천해줘 | RECOMMEND | RECOMMEND (그대로) |
  | 전시관 추천해줘 | RECOMMEND | RECOMMEND (그대로) |
  | 공연장 어디 있어? | RECOMMEND | RECOMMEND (그대로) |
  | 경복궁 근처 카페 추천해줘 | RECOMMEND | RECOMMEND (그대로) |
  | 오늘 갈 만한 곳 추천해줘 | RECOMMEND | RECOMMEND (그대로) |

  회귀(2026-09-04):

  - `scripts.evaluate_info_question_type --repeat 5` — 30건 전부 정확·전부 안정입니다
    (`accuracy 1.0`, `stable_cases 30`). 이번에 넣은 `realtime_event` 5건도 5회 모두
    같은 답이었습니다. 케이스는 `info/evals/question_type_cases.csv`의 RE-001~RE-005입니다.
  - `scripts.evaluate_agent_quality --split dev` — Intent 94.0% · Macro F1 0.972 ·
    조건 필드 정확도 96.6%. 직전 동일 골드셋 대비 Intent −4.0%p·Macro F1 −0.019인 반면
    조건 필드 정확도 +2.25%p·케이스 통과율 +5.71%p로 방향이 엇갈립니다.
    **이 변경이 겨냥한 칸인 `RECOMMEND × INFO`는 0을 유지했습니다** — 기존 추천 발화
    26건 중 INFO로 샌 것이 없습니다.
  - 실패 2건은 **MODIFY ↔ RECOMMEND 축**이라 이 변경이 가른 경계가 아닙니다. 두 케이스의
    턴을 `classify_intent`로 7회씩 다시 돌려 확인했습니다.
    DEV-025는 재현되지 않았고(7/7 기대대로), DEV-030 첫 턴 "비 와서 실내로 바꿔줘"가
    RECOMMEND 4 · MODIFY 3으로 갈리는 원래 흔들리는 케이스였습니다 — 첫 턴이 뒤집히면
    둘째 턴까지 연쇄로 무너져 조건 4개가 함께 실패합니다. 골드셋의 알려진 약점으로
    보이며, 라벨·구성은 팀 합의 영역이라 이 PR에서 건드리지 않았습니다.
  - `--split final`은 돌리지 않았습니다. `test_results/agent_quality/README.md`가 정한
    대로 프롬프트가 확정된 뒤 1회만 돌립니다.

- 2026-08-31(v2.4.0): `_shared/rules/conversation_history.md`를 bundle에 넣고,
  `context_rules.md`의 "이전 추천 있음 + 지명 단독 → MODIFY" 규칙에 단서를 달았습니다 —
  "직전 턴이 정보 질문(INFO)이었고 이번 발화가 그 질문을 다른 장소로 이어가는 것으로
  보이면 INFO를 유지한다(되묻기 상태가 아니어도 적용)". 8-31 이전 두 번의 수정은
  **되묻기 상태(pending_clarification)가 있을 때**만 다뤘는데, 실사용에서 깨진 것은
  되묻기 없이 완결된 INFO 턴 뒤였습니다("안국역 혼잡도 알려줘" → "인사동은?" → 장소
  추천 5건). 규칙 22개 중 이 하나만 손댔습니다 — 나머지 예외 통폐합은 새 일반 원칙이
  실측으로 안정된 뒤 별도 변경으로 합니다(사용자 결정, 2026-08-31).
  실측·회귀 근거는 `_shared/HISTORY.md`의 같은 날짜 항목에 함께 적었습니다.

- 2026-08-31: INFO/SCHEDULE 되묻기 자유 텍스트 이어받기 버그(실사용 재현: "사람많아?"
  되묻기 뒤 "여의도 한강공원"이라고 답하면 MODIFY로 새어 혼잡도와 무관한 식당 추천이
  나옴)를 고치며 `context_rules.md`에 규칙 2개를 추가했습니다. ① 직전 턴이 INFO
  되묻기(장소 모름/후보 모호)로 끝났고 이번 발화가 그 답변으로 보이면 "이전 추천 있음 +
  지명 단독 → MODIFY" 규칙보다 INFO 유지를 우선합니다. ② `schedule06_ambiguous_recommend`
  되묻기("일정 계속 짤까요, 장소만 추천할까요?")는 두 선택지가 서로 다른 인텐트라 기존
  "SCHEDULE 되묻기 → SCHEDULE 유지" 규칙을 그대로 적용하면 "추천만 해줘"류 답변까지
  SCHEDULE로 잘못 강제되므로 전용 규칙을 별도로 뒀습니다. `interaction_mode.md`와 같은
  방식으로 `clarification_status`에 전용 값을 추가해 분류 프롬프트에 신호를 줍니다.
  `FakeLLMProvider`(테스트 대역)에도 같은 우선순위를 미러링했습니다
  (`tests/test_agent_runtime.py`의 `test_info_missing_place_free_text_answer_stays_info`,
  `test_schedule06_ambiguous_free_text_*` 참고). 실 서버 재확인 후 승인 이력으로
  승격합니다.
- 2026-08-20: 실시간 주차·지하철·버스정류장·행사 INFO 추가에 따라 API 조회 가능 사실
  정보의 범위와 지하철/주차/행사 경계 사례를 보강했습니다. 변경 전 INFO 정의 원문은
  `archive/intent_definitions__legacy-1.md`에 보관했습니다. INFO 추출 v3과 함께 단위
  테스트·실서버 질문 확인을 거쳐 승인 이력으로 승격합니다.
- 2026-08-21: COMPARE 실측 이동시간 연결(D-050) 검증 중 "빨리 갈까?", "얼마나 걸려?"
  같은 이동 소요시간 비교 표현이 COMPARE 트리거 예시에 없어 GENERAL로 새는 문제를
  발견했습니다. `intent_priority.md`(4번 COMPARE 항목)와 `context_rules.md`(이전 추천
  2개 이상 조건부 규칙)에 예시를 추가했습니다. 변경 전 원문은
  `archive/intent_priority__legacy-2.0.0.md`, `archive/context_rules__legacy-2.0.0.md`에
  보관했습니다. "덜 막힐까?"류 교통 정체 표현은 실시간 교통 API 연동 전까지 이번
  범위에서 제외합니다. 실 서버 재확인 후 승인 이력으로 승격합니다.

## 승인 이력

| 기준선 | 날짜 | 커밋 | 슬롯 | 변경 내용 | 변경 이유 | 상태 |
| --- | --- | --- | --- | --- | --- | --- |
| legacy-1.0.1 | 2026-08-06 | `d476280` | `router.classify` | 이전 추천 뒤 위치만 제시한 발화를 MODIFY로 분류 | TP-67 위치 변경 시 조건 초기화 방지 | 승인됨 |
| legacy-1.0.3 | 2026-08-06 | `0bfdcfc` | `router.classify` | 단독 지명과 정보 질의 경계 사례를 재정렬 | D-053, 위치 표현의 오분류 방지 | 승인됨 |
| legacy-1.0.5 | 2026-08-07 | `bfad75f` | `router.classify` | 트리비 정체성·서비스 소개 문맥을 GENERAL로 구분 | 정체성 질문의 응답 경로 명확화 | 승인됨 |
| legacy-1.0.7 | 2026-08-08 | `c30bb68` | `router.classify` | SCHEDULE 되묻기 진행 상태를 분류 컨텍스트에 추가 | D-059, 일정 되묻기 답변이 MODIFY로 가는 문제 방지 | 승인됨 |
| legacy-1.0.9 | 2026-08-10 | `86a9cd1` | `router.classify` | 위치 되묻기 직후 단순 지명을 MODIFY의 검색 중심 변경으로 연결 | TP-67 후속, soft reset 뒤 위치·조건 보존 | 승인됨 |

## 실행 가능한 과거 기준선

- `router-context@legacy-1.0.0`: TP-67 전 `context_rules.md`만 바꾸는 슬롯 기준선입니다.
  [context_rules__legacy-1.0.0.md](archive/context_rules__legacy-1.0.0.md)와
  [variants.json](archive/variants.json)에서 파일·조합을 확인할 수 있습니다.
- 과거 `router.classify` 전체 원문은 프롬프트가 하나의 Python 파일에 있던 시기의 소스입니다.
  앞으로 승인된 행동 변경부터는 바뀐 슬롯마다 실행 가능한 Markdown 스냅샷을 보관합니다.
