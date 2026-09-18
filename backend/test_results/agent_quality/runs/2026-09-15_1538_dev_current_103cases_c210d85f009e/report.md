# Agent 품질 평가 결과

- 실행 ID: `2026-09-15_1538_dev_current_103cases_c210d85f009e`
- 실행 시각: 2026-09-15T16:02:36+09:00
- 프롬프트 기준선: `current`
- 프롬프트 버전: `recommend.extract@2.9.0` · `router.classify@2.7.0` · base `agent-interpret-prompts-1.0.31`
- 평가셋: `dev` · 103건 / 131턴
- 골드셋 해시: `c210d85f009e`

## 핵심 결과

| 지표 | 결과 | 의미 |
| --- | ---: | --- |
| Intent Accuracy | 96.2% | 전체 턴에서 Intent가 일치한 비율 |
| Intent Macro F1 | 0.969 | Intent별 F1을 동등하게 평균낸 균형 점수 |
| 조건 필드 정확도 | 96.0% | 기대 조건 필드 하나하나가 일치한 비율 |
| 최종 조건 완전 일치율 | 95.2% | 조건을 기대한 케이스에서 모든 필드가 맞은 비율 |
| 멀티턴 통과율 | 87.0% | 2턴 이상 케이스가 Intent·조건을 모두 통과한 비율 |
| 전체 케이스 통과율 | 94.2% | 케이스 단위로 모든 검증을 통과한 비율 |
| API 오류 | 1건 | HTTP/Provider 오류로 평가하지 못한 케이스 수 |

## 실행 성능

| 지표 | 결과 |
| --- | ---: |
| 클라이언트 지연시간 p50 | 9.50초 |
| 클라이언트 지연시간 p95 | 23.94초 |

## Intent별 Precision / Recall / F1

| Intent | 표본 수 | Precision | Recall | F1 |
| --- | ---: | ---: | ---: | ---: |
| COMPARE | 5 | 100.0% | 100.0% | 1.000 |
| GENERAL | 14 | 100.0% | 92.9% | 0.963 |
| INFO | 19 | 95.0% | 100.0% | 0.974 |
| MODIFY | 17 | 100.0% | 76.5% | 0.867 |
| OUT_OF_SCOPE | 5 | 100.0% | 100.0% | 1.000 |
| RECOMMEND | 55 | 96.5% | 100.0% | 0.982 |
| SCHEDULE | 16 | 100.0% | 100.0% | 1.000 |
| __ERROR__ | 0 | 0.0% | 0.0% | 0.000 |

## 혼동행렬

행은 **기대 Intent**, 열은 **실제 Intent**입니다. 대각선 값은 정분류이고, 대각선 밖 값은 어떤 Intent끼리 혼동했는지 보여줍니다.

| 기대 \ 실제 | COMPARE | GENERAL | INFO | MODIFY | OUT_OF_SCOPE | RECOMMEND | SCHEDULE | __ERROR__ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| COMPARE | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| GENERAL | 0 | 13 | 1 | 0 | 0 | 0 | 0 | 0 |
| INFO | 0 | 0 | 19 | 0 | 0 | 0 | 0 | 0 |
| MODIFY | 0 | 0 | 0 | 13 | 0 | 2 | 0 | 2 |
| OUT_OF_SCOPE | 0 | 0 | 0 | 0 | 5 | 0 | 0 | 0 |
| RECOMMEND | 0 | 0 | 0 | 0 | 0 | 55 | 0 | 0 |
| SCHEDULE | 0 | 0 | 0 | 0 | 0 | 0 | 16 | 0 |
| __ERROR__ | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## 조건 필드별 정확도

| 필드 | 정확도 |
| --- | ---: |
| accessibility_needs | 100.0% |
| budget | 100.0% |
| companion | 100.0% |
| concentration_intent | 100.0% |
| current_location | 100.0% |
| environment | 92.3% |
| exclude_tags | 100.0% |
| max_travel_time | 100.0% |
| place_tags | 97.5% |
| place_types | 100.0% |
| search_center | 96.3% |
| special_requirements | 0.0% |
| taste_query | 100.0% |
| time_available | 100.0% |
| transport | 100.0% |
| travel_origin | 100.0% |
| weather | 90.0% |
| weather_intent | 90.0% |

## 불일치·오류 케이스

### DEV-025 — 카페와 공원 함께 포함

- 기대 Intent: `RECOMMEND, MODIFY`
- 실제 Intent: `RECOMMEND, RECOMMEND`
- 조건 불일치: 없음
- 오류: 없음

### DEV-027 — 다른 곳 보기 조건 유지

- 기대 Intent: `RECOMMEND, MODIFY`
- 실제 Intent: `RECOMMEND, RECOMMEND`
- 조건 불일치: 없음
- 오류: 없음

### DEV-053 — 지역 정보

- 기대 Intent: `GENERAL`
- 실제 Intent: `INFO`
- 조건 불일치: 없음
- 오류: 없음

### DEV-075 — 유모차 접근과 유아 시설

- 기대 Intent: `RECOMMEND`
- 실제 Intent: `RECOMMEND`
- 조건 불일치: `search_center` 기대 `'수유'` / 실제 `None`
- 오류: 없음

### DEV-077 — 부가 조건과 현재 위치

- 기대 Intent: `RECOMMEND`
- 실제 Intent: `RECOMMEND`
- 조건 불일치: `special_requirements` 기대 `['주차 가능']` / 실제 `['주차 가능한']`
- 오류: 없음

### DEV-097 — 위치만 바꾸고 나머지 유지

- 기대 Intent: `RECOMMEND, MODIFY, MODIFY`
- 실제 Intent: `RECOMMEND`
- 조건 불일치: `search_center` 기대 `'인사동'` / 실제 `'광화문'`, `place_tags` 기대 `['박물관']` / 실제 `['카페']`, `weather` 기대 `'rain'` / 실제 `'rain'`, `weather_intent` 기대 `'AVOID'` / 실제 `'AVOID'`, `environment` 기대 `'indoor'` / 실제 `'indoor'`
- 오류: HTTP 502: {'error': {'code': 'state_store_error', 'message': '상태 저장 중 문제가 발생했어요. 잠시 후 다시 시도해주세요.', 'retryable': True, 'details': {'provider': 'state_store', 'upstream': {'upstream_detail': 'request timeout'}, 'llm_execution': {'calls': [{'operation': 'classify_intent', 'attempted_models': ['gemini-3.5-flash-lite'], 'served_model': 'gemini-3.5-flash-lite', 'latency_ms': 1260, 'input_tokens': 4728, 'output_tokens': 48, 'thoughts_tokens': None, 'cached_tokens': None, 'total_tokens': 4776, 'retry_count': 0}, {'operation': 'extract_modify_conditions', 'attempted_models': ['gemini-3.5-flash-lite'], 'served_model': 'gemini-3.5-flash-lite', 'latency_ms': 1926, 'input_tokens': 4976, 'output_tokens': 306, 'thoughts_tokens': None, 'cached_tokens': None, 'total_tokens': 5282, 'retry_count': 0}]}}}}


## 원본 파일

- `summary.json`: 기계 처리용 전체 요약
- `case_results.csv`: 케이스별 기대값·실제값·조건 비교
- `intent_metrics.csv`: Intent별 Precision / Recall / F1
- `confusion_matrix.csv`: 혼동행렬 원본
