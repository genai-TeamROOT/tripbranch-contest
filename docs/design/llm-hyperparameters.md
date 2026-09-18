# LLM(Gemini) 생성 하이퍼파라미터

## 문서 정보

| 항목 | 값 |
|------|-----|
| 버전 | v0.3 |
| 상태 | 초안 (Draft) — 코드 조사 기반 정리 |
| 최종 수정 | 2026-08-14 |
| 관련 코드 | `backend/app/providers/gemini.py` |

---

## 1. 범위

이 문서는 `RealGeminiProvider`가 실제 Gemini API를 호출할 때 `GenerateContentConfig`에
넘기는 **생성 하이퍼파라미터**(temperature, thinking_budget 등)가 무엇이고, 어느
호출부에 어떤 값이 적용되는지, 그 근거가 무엇인지를 정리한다.

다루지 않는 것:

- 모델 선택/폴백 체인(`LLM_MODEL_NAME`/`LLM_FALLBACK_MODEL_NAMES`) 자체의 설계 —
  [`docs/decision-log.md` D-052](../decision-log.md)가 소유한다.
- 타임아웃/재시도 횟수 등 인프라 설정값 — [`docs/development-guide.md`](../development-guide.md)의
  환경변수 표가 소유한다. 이 문서는 §5에서 참고 목적으로만 짧게 언급한다.
- 프롬프트 내용 자체(`gemini_prompts.py`) — 각 `int-0N-*.md` 문서가 소유한다.

## 2. 적용 범위 — 모든 실제 호출이 거치는 두 지점

`RealGeminiProvider`의 실제 Gemini API 호출은 코드 전체에서 딱 두 헬퍼로만
이뤄진다. 각 provider 메서드(`classify_intent`, `extract_*`, `generate_*`,
`stream_*`)는 전부 이 두 지점 중 하나를 거친다 — 호출부별로 별도 config를
직접 만들지 않는다.

```
구조화 출력(JSON) 계열
  provider 메서드 → _call_structured() → _generate() → _try_model()
                                                          └─ GenerateContentConfig(
                                                               temperature=0.0,
                                                               thinking_config=...,
                                                             )

스트리밍 텍스트 계열
  provider 메서드 → _stream_text()
                     └─ GenerateContentConfig(temperature=0.0)
```

- `_try_model()`([gemini.py:661-712](../../backend/app/providers/gemini.py#L661-L712)) —
  `response_mime_type="application/json"` + `response_schema=<Pydantic 모델>`로
  구조화 출력을 강제하는 11개 호출부가 공유: `classify_intent`,
  `extract_recommend_conditions`, `extract_modify_conditions`, `extract_info_query`,
  `extract_compare_request`, `extract_general_request`, `generate_general_answer`,
  `generate_recommendation_summary`, `generate_compare_summary`,
  `generate_schedule_plan`, `generate_schedule_fill`.
- `_stream_text()`([gemini.py:376-400](../../backend/app/providers/gemini.py#L376-L400)) —
  일반 텍스트 스트림 3개 호출부가 공유: `stream_recommendation_summary`,
  `stream_general_answer`, `stream_info_answer`.

## 3. `temperature = 0.0` — 예외 없이 전체 적용

두 헬퍼 모두 `temperature=0.0`으로 고정돼 있고, 호출부별 예외나 조절 로직이
없다. 즉 인텐트 분류부터 조건 추출, 추천/비교/일정 요약, INFO 답변 스트리밍까지
**모든 Gemini 호출이 같은 값을 쓴다**.

**근거 — 문서화된 설계 결정 없음.** `git log -S"temperature=0.0"`으로 도입
이력을 추적한 결과, 최초 도입 커밋(`21aad22`, "LLM provider 1차 구현",
2026-07-24)부터 이미 0.0이었고 커밋 메시지·`docs/decision-log.md`·설계 문서
어디에도 근거가 기록되어 있지 않다. 즉 **초기 구현 때부터의 관례**이지, 팀이
논의해 정한 값이 아니다.

정황상 추정 가능한 이유(코드에 명시된 근거는 아님):

- 인텐트 분류/조건 추출은 구조화 스키마로 받아 이후 로직이 그대로 분기하는
  입력값이다 — 같은 발화에 매번 다른 결과가 나오면 되묻기·상태머신이
  불안정해진다.
- `_resolve_clarification_choice()`([clarification-options.md](./clarification-options.md))처럼
  되묻기 버튼 해소를 LLM 재호출 없이 결정적 코드로만 처리하는 경로를 따로 둔
  것도 같은 맥락("같은 입력엔 같은 결과")으로 읽힌다.
- 답변 문구 생성(GENERAL/INFO/COMPARE)까지 0.0인 건 표현의 다양성보다 재현
  가능한 디버깅(감사 로그로 같은 입력을 재현해 확인)에 무게를 둔 선택으로 보인다.

이 값이 실제로 팀에서 논의된 결정이었다면 `docs/decision-log.md`에 근거가
빠진 문서화 갭일 가능성이 있다.

## 4. `thinking_budget` — 호출부별로 다름 (실측 근거 있음)

§3과 달리 이 값은 **실측 데이터가 코드 주석에 남아있는, 명시적으로 검토된
결정**이다.

| 값 | 적용 대상 | 근거 |
|---|---|---|
| `0`(끔) | `classify_intent`([gemini.py:189-195](../../backend/app/providers/gemini.py#L189-L195)), `extract_recommend_conditions`([gemini.py:207-208](../../backend/app/providers/gemini.py#L207-L208)), `generate_schedule_plan`([gemini.py:493](../../backend/app/providers/gemini.py#L493)), `generate_schedule_fill`([gemini.py:515](../../backend/app/providers/gemini.py#L515)) | 실측(2026-08-13, 10개 대표 질문×2회, `scripts/compare_classify_extract_thinking_budget.py`): `classify_intent` 평균 3609ms→1561ms(2.3배 단축), 정확도 90%(18/20)로 thinking_on과 동일 유지 — 유일한 오답은 thinking on/off 양쪽에서 동일하게 틀려 이 변경과 무관한 기존 프롬프트 이슈로 확인됨. `extract_recommend_conditions`는 평균 3122ms→1745ms(1.8배), search_center 추출 정확도 4/4로 동일 유지. SCHEDULE 두 함수는 "구조화 출력이 무겁고(3~5개 항목×6개 필드) thinking이 응답 시간의 상당 부분을 차지하는 것으로 추정"([gemini.py:493-499](../../backend/app/providers/gemini.py#L493-L499))되어 같은 조치 적용. 결과 원본: `test_results/classify_extract_thinking_budget.csv` |
| `None`(모델 기본 — `gemini-2.5-flash`는 동적 thinking) | 나머지 구조화 출력 7개(`extract_modify_conditions`, `extract_info_query`, `extract_compare_request`, `extract_general_request`, `generate_general_answer`, `generate_recommendation_summary`, `generate_compare_summary`) + 스트리밍 3개(`stream_recommendation_summary`, `stream_general_answer`, `stream_info_answer`) | 위 실측이 이 호출부들까지 검증한 것은 아니라, 끌 근거가 없는 채로 기존 동작(모델 기본값)을 유지 |

`thinking_budget=None`이면 `GenerateContentConfig`에 `thinking_config` 자체를
넣지 않아 모델 기본 동작을 그대로 둔다 — `_try_model()`의 `thinking_budget`
인자가 새로 추가됐을 때도 기존 9개 호출부(당시 기준)는 동작 변화가 없도록
설계됐다.

### 4.1 호출부 값은 그대로 나가지 않는다 — 모델별 보정

위 표는 **호출부가 요청하는 값**이다. 실제로 실리는 값은 `_try_model()`이
[`_resolve_thinking_budget()`](../../backend/app/providers/gemini.py#L107)로 한 번 보정한 뒤
정해진다. **예산의 최적값이 모델마다 다르기 때문이다.**

| 보정 | 대상 | 근거 |
|---|---|---|
| `0` → **`512`** | `gemini-2.5-flash-lite` × `classify_intent` — **현재 미사용 모델(아래 참고)** | `flash-lite`는 **thinking이 기본 꺼져 있어** `0`을 걸어도 동작이 같다(미설정과 `budget=0`의 68건 예측이 한 건도 다르지 않다). `512`를 줘야 대화 이력에 의존하는 판정(MODIFY/COMPARE/되묻기)이 산다 — 채점 대상 64건에서 56→59, 대조쌍 12건에서 9→12 |
| `0` → **`thinking_level=MINIMAL`** | 모든 모델 (**모든 호출**) | 숫자 `0`은 `gemini-3.5-flash-lite`·`gemini-3.6-flash`에서 `400 INVALID_ARGUMENT`를 낸다. `400`은 비재시도라 폴백도 못 타고 즉시 실패한다. 그래서 `0`은 숫자로 내보내지 않고 항상 `thinking_level=MINIMAL`로 바꿔 보낸다 — 거부하는 모델도 이 값은 받는다(2026-08-24 실측). 양수 예산은 그 모델들에서도 정상이다(`512` 성공) |

근거 데이터: `backend/test_results/intent_experiments_2026-08.md` (케이스 68건 전수,
모델 3종 × 예산 3점, 판정이 갈린 케이스는 5회 반복)

> **`512` 보정은 지금 발동하지 않는다(2026-08-24 확인).** Gemini 키를 바꾸면서
> `gemini-2.5-*`를 쓰지 않기로 했고, `.env`·`.env.example`·`config.py`가 모두
> `gemini-3.5-*`를 가리킨다. 지금 호출될 수 있는 모델은 `gemini-3.5-flash`와
> `gemini-3.5-flash-lite` 둘뿐이라, `gemini-2.5-flash-lite`를 키로 하는 이 보정에는
> 도달할 경로가 없다.
>
> **그래도 표에서 지우지 않는다.** 근거가 실측(위 68건 전수)이고, 그 모델을 폴백
> 후보로 다시 올릴 수 있기 때문이다 — 그때 같은 실험을 다시 할 필요가 없어야 한다.
> 코드(`_MODEL_BUDGET_OVERRIDES`)도 같은 이유로 남겨둔다. 읽는 사람이 "지금 이 보정이
> 걸리고 있다"고 오해하지 않도록 이 단서만 붙인다.

> **두 번째 행은 2026-08-24에 방식이 바뀌었다(D-076).** 예전에는 거부 모델에
> `thinking_config`를 **아예 싣지 않는** 방식이었다. 400은 피하지만 "thinking 끄기"도
> 같이 사라져서, fast 모델이 `gemini-3.5-flash-lite`로 바뀐 뒤(2026-08-18) 분류·조건
> 추출의 thinking이 조용히 다시 켜져 있었다 — 코드가 아니라 모델만 바뀐 것이라
> 아무도 알아채지 못했다. 지금은 `0`을 `thinking_level=MINIMAL`로 바꿔 보내
> 400도 피하고 thinking도 실제로 끈다. 거부 모델 목록
> (`_REJECTS_ZERO_THINKING_BUDGET`)은 실측 사실이므로 지우지 않고, "숫자 `0`은
> 절대 실리지 않는다"를 검증하는 테스트가 그 목록을 직접 읽는다.

**보정 조건이 "폴백일 때"가 아니라 "모델명이 무엇일 때"인 점이 중요하다.** `512`가
맞는 이유는 폴백이라서가 아니라 그 모델의 thinking 기본값이 꺼짐이기 때문이라,
`.env`에서 1순위·폴백 순서가 바뀌어도 따라가야 한다.

**폴백은 호출 단위다.** `_generate()`가 매 호출마다 1순위부터 다시 시도하므로, 한
요청 안에서도 호출마다 다른 모델이 쓰일 수 있고 그때마다 예산이 다시 정해진다.

보정 범위를 `classify_intent`로 한정한 것은 **그 호출만 폴백 모델로 실측했기
때문**이다. 조건 추출·일정 편성은 폴백 모델로 재본 적이 없어 §4 표의 값을 그대로 둔다.

> **알려진 한계** — `0`을 거부하는 모델 목록은 실측한 6개 모델 기준이다. 목록에 없는
> 모델이 `0`을 거부하면 여전히 `400`이 난다. `400` 응답이
> `"Request contains an invalid argument."`뿐이라 원인을 구분할 수 없어, 오류를 잡아
> 재시도하는 방식 대신 목록을 택했다. **모델을 교체할 때는 `0` 수용 여부 확인이
> 선행돼야 한다.**

### 4.2 모델 선택과의 관계

§1은 모델 선택·폴백 체인을 이 문서 범위 밖으로 뒀지만, §4.1의 보정은 **예산이 모델에
의존한다**는 뜻이므로 둘이 완전히 분리되지는 않는다. `LLM_MODEL_NAME`/
`LLM_FALLBACK_MODEL_NAMES`를 바꿀 때는 §4.1 표를 함께 확인해야 한다.

## 5. 설정하지 않는 파라미터

`GenerateContentConfig`에 아예 넘기지 않아 전부 Gemini API 기본값을 쓴다.
**주의**: Google 공식 문서(`ai.google.dev`)는 모델별 정확한 기본값을 일관되게
공개하지 않는다 — 아래 기본값은 API 레퍼런스 페이지와 제3자 모델 파라미터
레퍼런스([modelparams.dev](https://modelparams.dev/models/google/gemini-2.5-flash))를
교차 확인한 값이며, `gemini-2.5-flash` 모델판이 바뀌면 달라질 수 있다. 실제로
민감한 튜닝을 하기 전에는 SDK 응답의 `usage_metadata`나 공식 콘솔로 재확인을
권장한다.

| 파라미터 | 의미 | 기본값(미설정 시) |
|---|---|---|
| `top_p` | 누적 확률이 P에 도달할 때까지 확률 높은 토큰만 후보로 남기는 nucleus sampling. 낮을수록 후보가 좁아져 출력이 보수적이 된다 | `0.95` (범위 0~1) |
| `top_k` | 확률 상위 K개 토큰 중에서만 다음 토큰을 샘플링. `top_k=1`이면 사실상 그리디 디코딩과 같다 | `64` |
| `max_output_tokens` | 응답으로 생성할 수 있는 최대 토큰 수. 이 한도에 도달하면 응답이 중간에 잘린다(에러 없이 조용히 truncate) | 모델 최대 한도(`gemini-2.5-flash`는 65,536)까지 허용 — 다만 커뮤니티에 보고된 사례([google-gemini/gemini-cli#23081](https://github.com/google-gemini/gemini-cli/issues/23081))로는 명시적으로 안 올려주면 8,192 부근에서 잘리는 경우가 있어, "미설정 = 항상 65,536까지 다 쓴다"고 단정하기는 위험하다 |
| `candidate_count` | 한 번의 호출로 서로 다른 응답 후보를 몇 개 생성할지. 여러 개를 받아 그중 하나를 고르는 용도(예: 스키마 통과 여부로 선택) | `1` |
| `stop_sequences` | 이 문자열이 출력에 나타나면 그 즉시 생성을 중단시키는 트리거 목록 | 없음(빈 목록 — 끝까지 생성) |

`response_mime_type`/`response_schema`(구조화 출력 강제)는 생성 확률 분포를
조절하는 하이퍼파라미터가 아니라 출력 형식 제약이라 이 문서의 범위(§1)에서는
제외한다.

## 6. 향후 테스트해볼 만한 것 (우선순위)

지금은 전부 "설정 안 함 = 모델 기본값에 의존" 또는 "초기 구현부터의 관례"
상태라, 실측 없이 최적인지 알 수 없다. 실사용 리스크·기대 효과 기준으로
우선순위를 매긴다.

1. **`max_output_tokens`를 명시해 SCHEDULE 응답이 잘리는지 확인.** `generate_schedule_plan`/`generate_schedule_fill`은 구조화 출력이 가장 무겁고(3~5개 항목×6개 필드, §4) `thinking_budget=0`으로 이미 한 번 손댄 이력이 있는 호출부다. §5의 truncate 리스크가 사실이라면, 일정이 긴 경우 JSON이 중간에 잘려 `ValidationError` → 1회 재시도(`_call_structured`)로 이어지는 지연의 숨은 원인일 수 있다. `max_output_tokens`를 넉넉히 명시하고 재시도율이 줄어드는지 실측 — 리스크와 실측 난이도 모두 가장 낮아 최우선.
2. **답변 생성 계열만 `temperature`를 0보다 높여 A/B.** 분류·추출 계열(`classify_intent` 등)은 결정성이 중요하니 0.0을 유지하되, `generate_general_answer`/`stream_general_answer`(트리비 페르소나 답변)처럼 자연어 표현력이 중요한 호출부만 0.3~0.7 정도로 올렸을 때 답변이 매번 똑같아 단조롭다는 느낌이 줄어드는지 확인. 사용자 체감 품질과 직결되는 항목이라 2순위.
3. **`top_p`/`top_k`가 `classify_intent`/`extract_*`의 정확도·지연시간에 영향을 주는지.** `thinking_budget` 실측 때 쓴 것과 같은 프레임워크(`scripts/compare_classify_extract_thinking_budget.py`, 10개 대표 질문×N회)를 그대로 재사용할 수 있어 실측 비용이 낮다. 다만 temperature=0.0 상태에서는 top_p/top_k 효과가 이미 거의 죽어 있어(최상위 토큰만 사실상 선택됨) 개선 여지가 작을 수 있다는 점에서 1·2번보다 후순위.
4. **`candidate_count>1`로 구조화 출력 검증 실패 시 재시도 대체.** 지금은 `ValidationError` 시 안내 문구를 덧붙여 **순차** 재호출 1회(`_call_structured`)로 처리한다. `candidate_count`로 여러 후보를 한 번에 받아 스키마 통과하는 것을 고르면 순차 재시도보다 지연이 줄어들 수 있는지 확인할 가치가 있음 — 다만 비용(후보 수만큼 과금)과 API 지원 여부(구조화 출력에서 `candidate_count`가 실제로 동작하는지)부터 확인 필요.
5. **`stop_sequences`로 스트리밍 답변 후행 문구 제어.** 현재 보고된 문제는 없어 우선순위 최하. `stream_info_answer` 등에서 불필요한 반복/면책 문구가 관찰되면 그때 검토.

## 7. 참고 — 하이퍼파라미터는 아니지만 관련 있는 인프라 설정

생성 파라미터는 아니지만 Gemini 호출 동작에 함께 영향을 주는 설정. 상세는
각 소유 문서를 참고한다.

| 설정 | 기본값 | 비고 |
|---|---|---|
| `LLM_FAST_MODEL_NAME` | `gemini-3.5-flash-lite` | 분류·조건 추출 1순위 모델. [development-guide.md](../development-guide.md) |
| `LLM_FAST_FALLBACK_MODEL_NAMES` | `gemini-3.5-flash` | 위 모델의 폴백(콤마 구분). 설계 근거는 [decision-log.md D-052](../decision-log.md) |
| `LLM_GENERATION_MODEL_NAME` | `gemini-3.5-flash` | 문장·일정 생성 1순위 모델 |
| `LLM_GENERATION_FALLBACK_MODEL_NAMES` | `gemini-3.5-flash-lite` | 위 모델의 폴백(콤마 구분) |
| ~~`LLM_MODEL_NAME`~~ / ~~`LLM_FALLBACK_MODEL_NAMES`~~ | — | **폐지됐다.** 남아 있으면 부팅에서 막는다(D-042). 역할별 모델 라우팅 도입(2026-08-18)으로 위 네 개가 대체했다 |
| `MODE_JUDGE_MODEL_NAME` | `gemini-3.1-flash-lite` | 구간 이동수단 판정 전용 모델. 비우면 생성 묶음을 따른다. 추론 단계는 다른 호출과 같은 MINIMAL이고, 이 판정에서는 LOW·HIGH가 이득이 없다(`backend/test_results/mode_judge_thinking_2026-09-14/`) |
| `LLM_API_TIMEOUT_SECONDS` | 빈 값(`EXTERNAL_API_TIMEOUT_SECONDS`로 폴백) | Gemini 전용 타임아웃, Tool/DB 호출과 분리(2026-08-11) |
| `EXTERNAL_API_RETRY_COUNT` | `2` | Gemini 호출에만 적용되는 모델 하나당 재시도 횟수(지수 백오프) |

## 8. 관련 문서

- [`docs/decision-log.md`](../decision-log.md) D-052 — Gemini 동일 벤더 내 모델 fallback 결정
- [`docs/development-guide.md`](../development-guide.md) — 환경변수 전체 표
- [`docs/design/agent-response-streaming.md`](./agent-response-streaming.md) — `stream_*` 계열이 쓰이는 SSE 스트리밍 설계

---

## 9. 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|-----------|
| v0.1 | 2026-08-14 | 초안 작성 — 코드 조사(`gemini.py`) 기반으로 temperature/thinking_budget 적용 범위와 근거 정리 |
| v0.2 | 2026-08-14 | 파일명을 `llm-hyperparameters.md`로 변경. §5에 미설정 파라미터별 의미·기본값 표 추가(제3자 레퍼런스 교차 확인), §6 향후 테스트 우선순위 5개 신규 작성 |
| v0.3 | 2026-08-14 | §4.1 모델별 예산 보정 추가 — 호출부 값이 `_resolve_thinking_budget()`을 거쳐 정해진다. 폴백 `gemini-2.5-flash-lite`의 `classify_intent`는 `512`로, `thinking_budget=0`을 거부하는 모델(`gemini-3.5-flash-lite`·`gemini-3.6-flash`)은 미설정으로 보정. §4.2로 모델 선택과의 경계도 정리. 근거: `test_results/intent_experiments_2026-08.md` |
