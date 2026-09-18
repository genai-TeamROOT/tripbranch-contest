# Agent 품질 평가 결과

- 실행 ID: `2026-08-27_1216_dev_current_35cases_99825c9642fa`
- 실행 시각: 2026-08-27T12:24:48+09:00
- 프롬프트 기준선: `current`
- 평가셋: `dev` · 35건 / 50턴
- 골드셋 해시: `99825c9642fa`

## 핵심 결과

| 지표 | 결과 | 의미 |
| --- | ---: | --- |
| Intent Accuracy | 100.0% | 전체 턴에서 Intent가 일치한 비율 |
| Intent Macro F1 | 1.000 | Intent별 F1을 동등하게 평균낸 균형 점수 |
| 조건 필드 정확도 | 98.9% | 기대 조건 필드 하나하나가 일치한 비율 |
| 최종 조건 완전 일치율 | 96.7% | 조건을 기대한 케이스에서 모든 필드가 맞은 비율 |
| 멀티턴 통과율 | 93.3% | 2턴 이상 케이스가 Intent·조건을 모두 통과한 비율 |
| 전체 케이스 통과율 | 97.1% | 케이스 단위로 모든 검증을 통과한 비율 |
| API 오류 | 0건 | HTTP/Provider 오류로 평가하지 못한 케이스 수 |

## 실행 성능

| 지표 | 결과 |
| --- | ---: |
| 클라이언트 지연시간 p50 | 7.24초 |
| 클라이언트 지연시간 p95 | 52.16초 |

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
| exclude_tags | 50.0% |
| max_travel_time | 100.0% |
| place_tags | 100.0% |
| place_types | 100.0% |
| search_center | 100.0% |
| time_available | 100.0% |
| transport | 100.0% |
| weather | 100.0% |
| weather_intent | 100.0% |

## 불일치·오류 케이스

### DEV-029 — 제외 조건 후속 추가

- 기대 Intent: `RECOMMEND, MODIFY`
- 실제 Intent: `RECOMMEND, MODIFY`
- 조건 불일치: `exclude_tags` 기대 `['박물관']` / 실제 `[]`
- 오류: 없음

## 직전 동일 골드셋 대비

비교 대상: `2026-08-27_1208_dev_current_35cases_99825c9642fa`

- intent_accuracy: +0.0000
- macro_f1: +0.0000
- condition_field_accuracy: +0.0000
- case_pass_rate: +0.0000

## 원본 파일

- `summary.json`: 기계 처리용 전체 요약
- `case_results.csv`: 케이스별 기대값·실제값·조건 비교
- `intent_metrics.csv`: Intent별 Precision / Recall / F1
- `confusion_matrix.csv`: 혼동행렬 원본
