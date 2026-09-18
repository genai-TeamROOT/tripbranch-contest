# INFO Prompt History

## 현재 활성 슬롯

| 슬롯 | 관리 버전 | 템플릿 | 공유 규칙 |
| --- | --- | --- | --- |
| info.extract | v3.7.0 | extract.md, question_type_rules.md, place_context_rules.md, visit_time_rules.md, pending_question_block.md | factuality |
| info.answer | v1.1.0 | answer_instruction.md | persona, factuality |

## Draft

- 2026-09-05(info.extract v3.7.0): **기준점을 현재 위치로 지정하면 앞 지명을 이어받지
  않습니다**(D-121).

  `place_context_rules.md`에 현재 위치 절을 새로 넣었습니다. "인사동 근처 화장실" 다음에
  "아니 지금 위치로"라고 고쳐 말했는데도 place_name에 인사동이 그대로 채워지면, 그 지명이
  기준점이 되어 **사용자가 방금 고친 것을 되돌립니다**(실측 2026-09-05: 세 턴 내내 인사동
  화장실이 나왔습니다).

  `question_type_rules.md`의 public_toilet 항목에도 기준점만 바꾸는 후속 발화가 같은
  유형을 유지한다는 한 줄을 덧붙였습니다. 라우팅 쪽 짝은 router.classify v2.6.0입니다.
- 2026-09-05(info.extract v3.6.0): **`public_toilet` 유형 신설** — 주변 공중화장실
  위치를 찾는 질문을 facility에서 떼어냈습니다.

  `facility`는 "그 장소 안에 화장실이 있는지"를 그 관광지의 편의시설 안내문으로
  답합니다. 그런데 "급한데 근처에 화장실 있어?"가 알고 싶은 것은 **어디로 가면
  되는지**이고, 그건 서울시 공중화장실 목록(4,447곳)에서 좌표로 찾아야 나옵니다.
  같은 유형에 두면 답할 데이터가 없는 질문을 답할 수 있는 질문처럼 처리하게 됩니다.

  경계는 `parking` vs `realtime_parking`이 이미 쓰는 방식을 그대로 따랐습니다 —
  대상이 한 곳으로 명확하면 facility("경복궁 화장실 있어?"), 여러 곳 중 갈 데를
  찾으면 public_toilet("경복궁 근처 화장실 어디야?"). `info_field_rules.py`가
  예견한 경계 문제라(무장애 값을 facility에 합칠 때 남긴 주석) 우선순위 절에
  명시적으로 못 박았습니다.

  지명이 없어도 이 유형입니다. 기기 GPS를 기준점으로 쓸 수 있어서 되묻지 않고 바로
  답합니다 — 급한 상황에 "어디 근처요?"를 되묻는 건 답을 안 준 것과 같습니다.

- 2026-09-04(info.answer v1.1.0): **행사 질문은 "행사"로 뭉뚱그려 답한다**(TP-237).
  사용자가 전시회·공연·콘서트·축제 중 하나를 지목해 물어도 그 유형으로 단정하지 않습니다.

  가릴 근거가 없기 때문입니다. 주 경로인 서울시 실시간 도시데이터의 `EVENT_STTS`는
  항목 키가 아홉 개인데(`EVENT_NM`·`EVENT_PERIOD`·`EVENT_PLACE`·좌표·`PAY_YN`·
  `THUMBNAIL`·`URL`·`EVENT_ETC_DETAIL`) **유형 필드가 없습니다.** 제목으로 추론하게
  하면 "[세종문화회관] HYPNOSIS THERAPY on Sync Next 26"이 공연인지 전시인지 갈리지
  않고, 단정하게 시키면 **있는 것을 없다고 말하는 새 오류**를 만듭니다(D-042).

  실측(2026-09-04, 종로구 realtime_event 5건으로 답변 생성): 모델은 이미 "전시회"라고
  부르지 않고 "다양한 행사"로 답했습니다 — `answer_instruction.md`의 "JSON에 없는 사실,
  추측은 절대 추가하지 마세요"가 작동한 결과입니다. 이 규칙은 그 동작을 **명시로
  고정**하는 것이며, 프롬프트를 고치다 유형을 단정하게 되는 회귀를 막습니다.

  같은 이유로 `event_place_required` 되묻기 문구도 "축제·공연·전시를 모두 모아서
  알려드려요"로 고지합니다 — 축제를 물어도 공연·전시가 함께 나오는 것이 현재 동작입니다.

  **유형별로 가려 보여주는 것은 하지 않았습니다.** 필요해지면 그때 추가합니다. 참고로
  폴백 경로인 TourAPI `searchFestival2`에는 `lclsSystm2`(EV01 축제·EV02 공연·EV03 행사)가
  실려 오므로 그쪽은 가릴 수 있습니다 — 다만 지금 `FestivalEvent`가 그 필드를 받지 않고,
  주 경로인 서울시 쪽은 어차피 가릴 수 없습니다.

- 2026-09-04(info.extract v3.5.0): **행사 발화가 "추천해줘" 형태여도 `realtime_event`를
  뽑게 했습니다**(TP-237). `question_type_rules.md`의 `realtime_event` 정의에 추천 형태와
  "지명이 없으면 place_name을 비우고 question_type만 채운다"를 넣고, 건물을 가리키는 말
  (미술관·박물관·전시관·공연장)은 이 유형이 아니라고 못 박았습니다. 판별 우선순위에도
  같은 규칙을 한 줄 넣었습니다.

  라우터가 INFO로 보내줘도 이쪽이 `question_type`을 못 뽑으면 그대로 실패합니다 —
  변경 전 실측에서 "추천해줘"가 붙은 6발화 전부 `question_type=None`이었고
  `종로 전시회 뭐 있어?`만 `realtime_event`로 잡혔습니다.

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

  실측(2026-09-04, Gemini 실호출 13발화. 라우터 v2.5.0·recommend.extract v2.8.0과 함께 잰 값입니다):

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

  덧붙여 `agent_context/service.py`에 **`realtime_event`가 비면 TourAPI(`event`)로 한 번 더
  보는 폴백**을 넣었습니다. 두 출처가 거의 겹치지 않기 때문입니다 — 2026-09-04 실측에서
  그날 진행 중인 행사가 서울시 실시간 95건·TourAPI 21건인데 양쪽에 다 있는 것은 3건뿐이었습니다.

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

- 2026-08-31(info.extract v3.4.0): `_shared/rules/conversation_history.md`를 bundle에 추가했습니다 —
  최근 대화가 이미 API contents로 전달되는데 프롬프트가 그 존재를 몰라 생략된 후속
  발화를 이어받지 못했습니다. 변경 이유·실측·회귀 근거는 `_shared/HISTORY.md`의 같은
  날짜 항목에 한 곳으로 모아 적었습니다.

### v3.3.0 (2026-08-31, 되묻기 자유 텍스트 이어받기)

  장소명이 없어 되묻는 경우(예: "사람 많아?") 지금까지는 버튼도 없고 자유 텍스트로
  답해도 이어받을 코드가 없어, 다음 턴이 장소명만으로 처음부터 재분류되며 "혼잡도
  질문이었다"는 사실이 사라졌습니다(실사용 재현, 2026-08-31 — "사람많아?" 되묻기 뒤
  "여의도 한강공원"이라고 답하면 라우터가 MODIFY로 오분류해 혼잡도와 무관한 식당
  추천이 나왔습니다).

  이 슬롯이 기존에 "반드시 info 필드를 채우고"(26행)라고 지시해 둔 덕분에, 장소명이
  없어도 question_type/specific_question/visit_time은 이미 채워져 있었습니다 — 그
  값을 세션에 저장해뒀다가, 다음 턴에 `pending_question_block.md`로 새로 만든
  블록에 실어 되돌려줍니다. "이번 발화가 그 질문에 대한 장소 답변으로 보이면 이전
  값을 유지하고 place_name만 채우라"고 지시합니다. 값이 없으면(직전이 이 되묻기가
  아니었으면) 블록 자체가 빈 문자열로 생략되므로 기존 케이스의 렌더 결과는 그대로입니다
  (`tests/prompts/snapshots/info_extract__default.txt` 등 기존 스냅샷 불변 확인).

  같은 작업에서 라우터(`router/context_rules.md`)에도 짝이 되는 규칙을 추가했습니다 —
  이 값을 프롬프트에 넣어도 라우터가 애초에 INFO를 유지하지 않으면 소용이 없기
  때문입니다. 단위·e2e 테스트는 `tests/test_agent_runtime.py`의
  `test_info_missing_place_free_text_answer_stays_info`,
  `test_info_place_ambiguous_free_text_answer_resolves_without_button`,
  `tests/test_orchestrator.py`의 `test_info_extraction_receives_pending_question_context`
  참고. 실 서버 재확인 후 승인 이력으로 승격합니다.

### v3.2.1 (2026-08-27, 공영주차장 실시간 경로)

  `공영/시영주차장`을 명시한 현재 주차 자리 질문을 신규
  `realtime_public_parking`으로 분리했습니다. 이 유형은 서울시
  GetParkingInfo의 구 단위 공영주차장 최신 대수(최근 20분 이내 갱신 여부)를
  사용합니다. 일반 `realtime_parking`은 기존처럼 서울시 실시간 도시데이터의
  공영·민영 근접 목록을 함께 보여줍니다.

  **분리 이유**: 두 데이터는 모두 서울시 제공이지만 범위와 신뢰도가 다릅니다.
  도시데이터는 특정 핫스팟의 근접 목록이라 모든 공영주차장을 포괄하지 않을 수
  있고, GetParkingInfo는 공영주차장 코드·최신 주차 대수를 구 단위로 제공합니다.
  질문의 "공영/시영" 명시 여부를 계약으로 남겨, 사용자에게 민영 목록을 섞어
  공영 잔여 대수처럼 보이게 하지 않습니다.

  기존 v3.2.0 원문은
  `archive/question_type_rules__legacy-3.2.md`에 보관했습니다. 평가 케이스는
  RP-003/RP-004를 추가했습니다. 구조화 출력 enum과 C INFO 계약도 같은 변경에
  포함해야 합니다.

### v3.2.0 (2026-08-26, D-091)

  `question_type_rules.md`의 `realtime_parking`을 "지금/현재/실시간" 필수에서
  "주변/근처"만 있어도 매칭되도록 완화하고(TP-115), 신규 유형 `realtime_traffic`
  (도로소통)을 추가했습니다. `parking`(정적) 정의에 "주변 여러 곳을 찾으면
  realtime_parking으로 본다"는 경계 문구를 덧붙였습니다. 기존 v3.1.0 원문은
  `archive/question_type_rules__legacy-3.1.md`에 보관했습니다.

  **새 유형(`realtime_traffic`)을 만든 이유**: 도로소통은 기존 6종
  (parking/subway/bus/event 중 어디)과도 성격이 다릅니다 — "주변 여러 곳"이
  아니라 그 지역 하나의 단일 스냅샷(단계·속도)이라 응답 조립 방식 자체가
  다르고(카드로 미루지 않고 말풍선에 바로 값을 담음), 기존 유형에 끼워
  넣으면 그 차이가 코드에서 안 드러납니다.

  **단일 턴 평가 결과**: `question_type_cases.csv`에 5건 추가(주차 완화 2건
  RP-001/RP-002, 도로소통 신규 2건 RT-001/RT-002, 정적 `parking` 회귀 확인용
  PK-001 노트 보강)한 뒤 `python -m scripts.evaluate_info_question_type --repeat 5`
  로 실제 Gemini를 호출했습니다.

  1차 실행(스키마에 `realtime_traffic`을 아직 안 넣은 상태) — 기존 21건은
  전부 100%로 회귀 없었지만, 신규 `realtime_traffic` 2건은 **0%**로
  실패했습니다(`realtime_subway`/`realtime_parking`/`realtime_commercial`로
  잘못 분류). `InfoQuestionType`/`RealtimeCityInfoResult.question_type`
  Literal에 `"realtime_traffic"`을 추가한 뒤 2차 실행하니 23건 전체
  **100%, 전부 stable**로 통과했습니다. 프롬프트 규칙만 바꾸고 구조화 출력
  스키마를 안 바꾸면 모델이 애초에 그 값을 고를 수 없다는 걸 실측으로
  확인한 셈입니다 — 순서를 지켜야 하는 이유가 이번에 실패로 드러났습니다.

  실행 기록: `test_results/info_question_type/2026-08-26_1947_v3.2-realtime-parking-traffic/`
  (1차, 스키마 반영 전), `2026-08-26_1955_v3.2-schema-fixed/`(2차, 최종).

  **다중 턴 회귀는 이번에 생략했습니다.** `evaluate_agent_quality --split dev`
  까지 포함하는 것이 팀 통상 프로세스(legacy-3.1 참고)지만, 이번 변경은
  기존 유형 경계(facility/concentration)를 건드리지 않는 좁은 추가 변경이라
  단일 턴 검증만으로 Draft에 남깁니다. 다중 턴 실행은 리뷰 시 필요하면
  추가하겠습니다.

## 승인 이력

| 기준선 | 날짜 | 커밋 | 슬롯 | 변경 내용 | 변경 이유 | 상태 |
| --- | --- | --- | --- | --- | --- | --- |
| legacy-3.1 | 2026-08-25 | `c7cd67a` | `info.extract` | `facility`를 편의시설·접근성에서 동반자 적합성까지 확대, `concentration`을 혼잡·사람 수·붐빔을 직접 묻는 경우로 한정, 기본 낙하 지점을 `general_info`로 명시 | 동반자 질문이 실행마다 다른 유형으로 갈렸고, TP-144가 붙여 둔 답변 재료가 쓰이지 못했다 | 승인됨 (단일 턴 19케이스×10회 0.705→1.000, 동반자 0.44→1.00, 인접 유형 1.00 유지 / 다중 턴 dev Intent 0.98→1.00. 상세는 아래) |
| legacy-3 | 2026-08-20 | `fc4a967` | `info.extract` | `realtime_parking`·`realtime_subway`·`realtime_bus`·`realtime_event` 추가, `realtime_commercial`을 카페 한정에서 전 업종으로 확장 | 서울시 실시간 도시데이터 연동 | 승인됨 (변경: mintee/A, 다중 턴 회귀 dev 0.98 · final 0.955, 2026-08-20 20:44·20:47) |
| legacy-1.0.12 | 2026-08-12 | `0c0a548` | `info.answer` | 검증된 INFO fields를 자연어 답변으로 변환하는 슬롯 신설 | 관광 데이터 결과를 사용자 답변으로 조립 | 승인됨 |

### legacy-3.1 상세 (2026-08-25, TP-148)

  `question_type_rules.md`의 `facility` 정의를 편의시설·접근성에서
  **동반자 적합성까지** 넓히고, 판별 우선순위에 세 줄을 더했습니다. 동반자 질문은
  혼잡도로도 읽히면 `facility`로 보내고, `concentration`은 혼잡·사람 수·붐빔을 직접
  묻는 경우로 한정하며, 어느 유형에도 맞지 않으면 `general_info`로 보냅니다. 기존 v3
  원문은 `archive/question_type_rules__legacy-3.md`에 보관했습니다.

  **변경 이유**: "보성사터에 아이들과 가기 좋아?" 같은 동반자 질문이 실행마다 다른
  유형으로 갈렸습니다. 13개 유형 중 동반자 적합성을 받는 곳이 없었고, 판별 우선순위
  마지막 줄("그 외 … 또는 미래 시점 질문 → concentration")이 애매한 질문을 혼잡도로
  빨아들였습니다. 답할 재료(유모차 대여·수유실·기저귀교환대·휠체어 접근·장애인
  화장실)는 TP-144에서 이미 `facility`에 붙여 두었는데, 그 유형으로 가지 않아 쓰이지
  못하는 상태였습니다.

  **새 유형을 만들지 않은 이유**: `visit_suitability`를 신설하면 혼잡도를 뺀 필드가
  `facility`와 같아져 유형만 늘고 경계가 하나 더 생깁니다. 출력 스키마
  (`InfoQuestionType`)를 건드리지 않으므로 A와의 계약도 그대로입니다.

  **어휘를 나열한 이유**: 실측에서 같은 뜻인데 표현에 따라 결과가 갈렸습니다 —
  "유아 데리고 가도 돼?"는 10회 모두 `facility`인데 "애기 데리고 갈만해?"는 10회 모두
  `general_info`였습니다. 한자어·문어체는 시설로, 구어체는 개요·혼잡도로 가는 경향이
  있어 예시를 구어체 위주로 골랐습니다.

  **단일 턴 평가 결과**: `app/prompts/info/evals/question_type_cases.csv`(19케이스)를
  `python -m scripts.evaluate_info_question_type --repeat 10`으로 돌렸습니다.

  | | 기준선 v3 | 변경 후 v3.1 |
  | --- | --- | --- |
  | 전체 | 0.7053 (안정 14/19) | **1.0000 (안정 19/19)** |
  | 동반자 적합성 10건 | 0.4400 (안정 5/10) | **1.0000 (안정 10/10)** |
  | 인접 유형 9건 | 1.0000 (안정 9/9) | 1.0000 (안정 9/9) |

  통과 조건으로 둔 "인접 유형 1.00 유지"를 지켰습니다. 특히 `concentration`을 좁혔는데도
  "주말에 사람 많아?"·"붐빌까?"가 10/10이고, `facility`를 넓혔는데도 "화장실 있어?"·
  "휠체어로 들어갈 수 있어?"·"유모차 대여돼?"가 10/10입니다.

  기준선에서 0/10이던 문장이 모두 10/10이 됐습니다 — "애기 데리고 갈만해?"(개요 10),
  "애기랑 가도 괜찮아?"(혼잡도 10), "아이랑 가기 어때?"(개요 7·혼잡도 3),
  "초등학생이랑 가기 괜찮아?"(개요 5·혼잡도 5). 원래 잘 되던 "유아·임산부·노약자"도
  10/10을 유지해, 구어체 예시가 문어체를 밀어내지 않았습니다.

  실행 기록: `test_results/info_question_type/2026-08-25_1224_baseline-v3-final/`(기준선),
  `2026-08-25_1238_v3.1/`(변경 후). 기대값을 다듬는 과정의 중간 측정 3건은 어느 것이
  기준선인지 흐려져 남기지 않았습니다.

  **다중 턴 회귀 결과**(dev 35건, `evaluate_agent_quality --split dev`):

  | 실행 | 코드 | 프롬프트 | Intent 정확도 | 실패 케이스 |
  | --- | --- | --- | --- | --- |
  | 2026-08-24_1845 | 8-24 시점 | v3 | 0.98 | 008 · 009 · 023 |
  | 2026-08-25_1241 | 현재 | v3.1 | **1.00** | 008 · 009 · 023 · 029 · 033 |
  | 2026-08-25_1248 | 현재 | v3.1 | **1.00** | 위와 동일 |
  | 2026-08-25_1253 | 현재 | **v3로 되돌림** | 0.96 | 위 5건 + **010** |

  8-24 기준선보다 실패가 2건 늘어 원인을 가렸습니다. **프롬프트만 v3로 되돌려도
  DEV-029·DEV-033은 그대로 실패하므로 이번 변경과 무관합니다** — 8-24 이후 develop에
  들어온 다른 머지(PR #234·#235·#236)에서 생긴 것으로 보입니다. 두 건 모두 인텐트는
  맞히고 조건 추출에서 틀리며(MODIFY의 `exclude_tags`, SCHEDULE의 `search_center`),
  `info/` 프롬프트를 읽지 않는 슬롯입니다.

  되돌린 실행에서는 **DEV-010("경복궁 주차 가능해?")이 추가로 실패**하고 Intent
  정확도가 0.96으로 떨어집니다. v3.1이 이 INFO 케이스를 지키고 있습니다.

  조건 필드 정확도는 세 실행 모두 0.92로 같습니다.

`Draft`는 승인 기준선이 아니므로 별도 Markdown 스냅샷으로 보관하지 않습니다.
