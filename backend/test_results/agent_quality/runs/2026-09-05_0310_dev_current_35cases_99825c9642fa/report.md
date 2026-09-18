# Agent 품질 평가 결과

- 실행 ID: `2026-09-05_0310_dev_current_35cases_99825c9642fa`
- 실행 시각: 2026-09-05T03:16:14+09:00
- 프롬프트 기준선: `current`
- 프롬프트 버전: `recommend.extract@2.8.0` · `router.classify@2.6.0` · base `agent-interpret-prompts-1.0.29`
- 평가셋: `dev` · 35건 / 50턴
- 골드셋 해시: `99825c9642fa`

## 핵심 결과

| 지표 | 결과 | 의미 |
| --- | ---: | --- |
| Intent Accuracy | 100.0% | 전체 턴에서 Intent가 일치한 비율 |
| Intent Macro F1 | 1.000 | Intent별 F1을 동등하게 평균낸 균형 점수 |
| 조건 필드 정확도 | 89.9% | 기대 조건 필드 하나하나가 일치한 비율 |
| 최종 조건 완전 일치율 | 80.0% | 조건을 기대한 케이스에서 모든 필드가 맞은 비율 |
| 멀티턴 통과율 | 66.7% | 2턴 이상 케이스가 Intent·조건을 모두 통과한 비율 |
| 전체 케이스 통과율 | 82.9% | 케이스 단위로 모든 검증을 통과한 비율 |
| API 오류 | 0건 | HTTP/Provider 오류로 평가하지 못한 케이스 수 |

## 실행 성능

| 지표 | 결과 |
| --- | ---: |
| 클라이언트 지연시간 p50 | 7.49초 |
| 클라이언트 지연시간 p95 | 22.78초 |

## Intent별 Precision / Recall / F1

| Intent | 표본 수 | Precision | Recall | F1 |
| --- | ---: | ---: | ---: | ---: |
| COMPARE | 1 | 100.0% | 100.0% | 1.000 |
| GENERAL | 1 | 100.0% | 100.0% | 1.000 |
| INFO | 4 | 100.0% | 100.0% | 1.000 |
| MODIFY | 11 | 100.0% | 100.0% | 1.000 |
| OUT_OF_SCOPE | 1 | 100.0% | 100.0% | 1.000 |
| RECOMMEND | 26 | 100.0% | 100.0% | 1.000 |
| SCHEDULE | 6 | 100.0% | 100.0% | 1.000 |

## 혼동행렬

행은 **기대 Intent**, 열은 **실제 Intent**입니다. 대각선 값은 정분류이고, 대각선 밖 값은 어떤 Intent끼리 혼동했는지 보여줍니다.

| 기대 \ 실제 | COMPARE | GENERAL | INFO | MODIFY | OUT_OF_SCOPE | RECOMMEND | SCHEDULE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| COMPARE | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| GENERAL | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| INFO | 0 | 0 | 4 | 0 | 0 | 0 | 0 |
| MODIFY | 0 | 0 | 0 | 11 | 0 | 0 | 0 |
| OUT_OF_SCOPE | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| RECOMMEND | 0 | 0 | 0 | 0 | 0 | 26 | 0 |
| SCHEDULE | 0 | 0 | 0 | 0 | 0 | 0 | 6 |

## 조건 필드별 정확도

| 필드 | 정확도 |
| --- | ---: |
| companion | 100.0% |
| concentration_intent | 100.0% |
| environment | 100.0% |
| exclude_tags | 100.0% |
| max_travel_time | 50.0% |
| place_tags | 85.0% |
| place_types | 77.8% |
| search_center | 92.3% |
| time_available | 75.0% |
| transport | 100.0% |
| weather | 100.0% |
| weather_intent | 100.0% |

## 불일치·오류 케이스

### DEV-008 — 반나절 일정

- 기대 Intent: `SCHEDULE`
- 실제 Intent: `SCHEDULE`
- 조건 불일치: `search_center` 기대 `'광화문'` / 실제 `None`, `time_available` 기대 `240` / 실제 `None`
- 오류: 없음

### DEV-022 — 위치 보충 뒤 카페 유지

- 기대 Intent: `RECOMMEND, MODIFY`
- 실제 Intent: `RECOMMEND, MODIFY`
- 조건 불일치: `search_center` 기대 `'경복궁'` / 실제 `None`
- 오류: 없음

### DEV-024 — 카페를 공원으로 대체

- 기대 Intent: `RECOMMEND, MODIFY`
- 실제 Intent: `RECOMMEND, MODIFY`
- 조건 불일치: `place_types` 기대 `['attraction']` / 실제 `['restaurant']`, `place_tags` 기대 `['공원']` / 실제 `['카페']`
- 오류: 없음

### DEV-025 — 카페와 공원 함께 포함

- 기대 Intent: `RECOMMEND, MODIFY`
- 실제 Intent: `RECOMMEND, MODIFY`
- 조건 불일치: `place_types` 기대 `['restaurant', 'attraction']` / 실제 `['restaurant']`, `place_tags` 기대 `['카페', '공원']` / 실제 `['카페']`
- 오류: 없음

### DEV-028 — 이동 시간 후속 변경

- 기대 Intent: `RECOMMEND, MODIFY`
- 실제 Intent: `RECOMMEND, MODIFY`
- 조건 불일치: `max_travel_time` 기대 `15` / 실제 `None`
- 오류: 없음

### DEV-035 — 태그 교체

- 기대 Intent: `RECOMMEND, MODIFY`
- 실제 Intent: `RECOMMEND, MODIFY`
- 조건 불일치: `place_tags` 기대 `['미술관']` / 실제 `['박물관']`
- 오류: 없음

## 직전 동일 골드셋 대비

비교 대상: `2026-09-04_1455_dev_current_35cases_99825c9642fa`

- intent_accuracy: +0.0600
- macro_f1: +0.0285
- condition_field_accuracy: -0.0674
- case_pass_rate: -0.1143

## 원본 파일

- `summary.json`: 기계 처리용 전체 요약
- `case_results.csv`: 케이스별 기대값·실제값·조건 비교
- `intent_metrics.csv`: Intent별 Precision / Recall / F1
- `confusion_matrix.csv`: 혼동행렬 원본
