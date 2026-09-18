# Agent 품질 평가 결과

- 실행 ID: `2026-09-15_1605_dev_current_103cases_c210d85f009e`
- 실행 시각: 2026-09-15T16:24:43+09:00
- 프롬프트 기준선: `current`
- 프롬프트 버전: `recommend.extract@2.9.0` · `router.classify@2.7.0` · base `agent-interpret-prompts-1.0.31`
- 평가셋: `dev` · 103건 / 131턴
- 골드셋 해시: `c210d85f009e`

## 핵심 결과

| 지표 | 결과 | 의미 |
| --- | ---: | --- |
| Intent Accuracy | 96.9% | 전체 턴에서 Intent가 일치한 비율 |
| Intent Macro F1 | 0.974 | Intent별 F1을 동등하게 평균낸 균형 점수 |
| 조건 필드 정확도 | 98.3% | 기대 조건 필드 하나하나가 일치한 비율 |
| 최종 조건 완전 일치율 | 95.2% | 조건을 기대한 케이스에서 모든 필드가 맞은 비율 |
| 멀티턴 통과율 | 82.6% | 2턴 이상 케이스가 Intent·조건을 모두 통과한 비율 |
| 전체 케이스 통과율 | 94.2% | 케이스 단위로 모든 검증을 통과한 비율 |
| API 오류 | 0건 | HTTP/Provider 오류로 평가하지 못한 케이스 수 |

## 실행 성능

| 지표 | 결과 |
| --- | ---: |
| 클라이언트 지연시간 p50 | 10.03초 |
| 클라이언트 지연시간 p95 | 25.44초 |

## Intent별 Precision / Recall / F1

| Intent | 표본 수 | Precision | Recall | F1 |
| --- | ---: | ---: | ---: | ---: |
| COMPARE | 5 | 100.0% | 100.0% | 1.000 |
| GENERAL | 14 | 100.0% | 92.9% | 0.963 |
| INFO | 19 | 95.0% | 100.0% | 0.974 |
| MODIFY | 17 | 93.8% | 88.2% | 0.909 |
| OUT_OF_SCOPE | 5 | 100.0% | 100.0% | 1.000 |
| RECOMMEND | 55 | 96.4% | 98.2% | 0.973 |
| SCHEDULE | 16 | 100.0% | 100.0% | 1.000 |

## 혼동행렬

행은 **기대 Intent**, 열은 **실제 Intent**입니다. 대각선 값은 정분류이고, 대각선 밖 값은 어떤 Intent끼리 혼동했는지 보여줍니다.

| 기대 \ 실제 | COMPARE | GENERAL | INFO | MODIFY | OUT_OF_SCOPE | RECOMMEND | SCHEDULE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| COMPARE | 5 | 0 | 0 | 0 | 0 | 0 | 0 |
| GENERAL | 0 | 13 | 1 | 0 | 0 | 0 | 0 |
| INFO | 0 | 0 | 19 | 0 | 0 | 0 | 0 |
| MODIFY | 0 | 0 | 0 | 15 | 0 | 2 | 0 |
| OUT_OF_SCOPE | 0 | 0 | 0 | 0 | 5 | 0 | 0 |
| RECOMMEND | 0 | 0 | 0 | 1 | 0 | 54 | 0 |
| SCHEDULE | 0 | 0 | 0 | 0 | 0 | 0 | 16 |

## 조건 필드별 정확도

| 필드 | 정확도 |
| --- | ---: |
| accessibility_needs | 100.0% |
| budget | 100.0% |
| companion | 100.0% |
| concentration_intent | 100.0% |
| current_location | 100.0% |
| environment | 100.0% |
| exclude_tags | 66.7% |
| max_travel_time | 100.0% |
| place_tags | 97.5% |
| place_types | 100.0% |
| search_center | 100.0% |
| special_requirements | 0.0% |
| taste_query | 100.0% |
| time_available | 100.0% |
| transport | 100.0% |
| travel_origin | 100.0% |
| weather | 100.0% |
| weather_intent | 100.0% |

## 불일치·오류 케이스

### DEV-025 — 카페와 공원 함께 포함

- 기대 Intent: `RECOMMEND, MODIFY`
- 실제 Intent: `RECOMMEND, RECOMMEND`
- 조건 불일치: 없음
- 오류: 없음

### DEV-029 — 제외 조건 후속 추가

- 기대 Intent: `RECOMMEND, MODIFY`
- 실제 Intent: `RECOMMEND, MODIFY`
- 조건 불일치: `exclude_tags` 기대 `['박물관']` / 실제 `[]`
- 오류: 없음

### DEV-030 — 비 회피 후 위치 변경

- 기대 Intent: `RECOMMEND, MODIFY`
- 실제 Intent: `MODIFY, RECOMMEND`
- 조건 불일치: 없음
- 오류: 없음

### DEV-033 — 일정에 카페 추가

- 기대 Intent: `SCHEDULE, SCHEDULE`
- 실제 Intent: `SCHEDULE, SCHEDULE`
- 조건 불일치: `place_tags` 기대 `['카페']` / 실제 `['공원', '전시관', '카페']`
- 오류: 없음

### DEV-077 — 부가 조건과 현재 위치

- 기대 Intent: `RECOMMEND`
- 실제 Intent: `RECOMMEND`
- 조건 불일치: `special_requirements` 기대 `['주차 가능']` / 실제 `['주차 가능한 곳']`
- 오류: 없음

### DEV-101 — 시기 질문

- 기대 Intent: `GENERAL`
- 실제 Intent: `INFO`
- 조건 불일치: 없음
- 오류: 없음

## 직전 동일 골드셋 대비

비교 대상: `2026-09-15_1538_dev_current_103cases_c210d85f009e`

- intent_accuracy: +0.0076
- macro_f1: +0.0048
- condition_field_accuracy: +0.0229
- case_pass_rate: +0.0000

## 원본 파일

- `summary.json`: 기계 처리용 전체 요약
- `case_results.csv`: 케이스별 기대값·실제값·조건 비교
- `intent_metrics.csv`: Intent별 Precision / Recall / F1
- `confusion_matrix.csv`: 혼동행렬 원본
