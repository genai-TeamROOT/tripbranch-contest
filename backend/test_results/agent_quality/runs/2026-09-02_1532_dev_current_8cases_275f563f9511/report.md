# Agent 품질 평가 결과

- 실행 ID: `2026-09-02_1532_dev_current_8cases_275f563f9511`
- 실행 시각: 2026-09-02T15:33:20+09:00
- 프롬프트 기준선: `current`
- 프롬프트 버전: `recommend.extract@2.7.0` · `router.classify@2.4.0` · base `agent-interpret-prompts-1.0.27`
- 평가셋: `dev` · 8건 / 8턴
- 골드셋 해시: `275f563f9511`

## 핵심 결과

| 지표 | 결과 | 의미 |
| --- | ---: | --- |
| Intent Accuracy | 100.0% | 전체 턴에서 Intent가 일치한 비율 |
| Intent Macro F1 | 1.000 | Intent별 F1을 동등하게 평균낸 균형 점수 |
| 조건 필드 정확도 | 92.6% | 기대 조건 필드 하나하나가 일치한 비율 |
| 최종 조건 완전 일치율 | 87.5% | 조건을 기대한 케이스에서 모든 필드가 맞은 비율 |
| 멀티턴 통과율 | 0.0% | 2턴 이상 케이스가 Intent·조건을 모두 통과한 비율 |
| 전체 케이스 통과율 | 87.5% | 케이스 단위로 모든 검증을 통과한 비율 |
| API 오류 | 0건 | HTTP/Provider 오류로 평가하지 못한 케이스 수 |

## 실행 성능

| 지표 | 결과 |
| --- | ---: |
| 클라이언트 지연시간 p50 | 7.29초 |
| 클라이언트 지연시간 p95 | 8.05초 |

## Intent별 Precision / Recall / F1

| Intent | 표본 수 | Precision | Recall | F1 |
| --- | ---: | ---: | ---: | ---: |
| RECOMMEND | 7 | 100.0% | 100.0% | 1.000 |
| SCHEDULE | 1 | 100.0% | 100.0% | 1.000 |

## 혼동행렬

행은 **기대 Intent**, 열은 **실제 Intent**입니다. 대각선 값은 정분류이고, 대각선 밖 값은 어떤 Intent끼리 혼동했는지 보여줍니다.

| 기대 \ 실제 | RECOMMEND | SCHEDULE |
| --- | ---: | ---: |
| RECOMMEND | 7 | 0 |
| SCHEDULE | 0 | 1 |

## 조건 필드별 정확도

| 필드 | 정확도 |
| --- | ---: |
| concentration_intent | 100.0% |
| environment | 100.0% |
| max_travel_time | 100.0% |
| place_tags | 100.0% |
| place_types | 100.0% |
| search_center | 85.7% |
| time_available | 0.0% |
| transport | 100.0% |
| weather | 100.0% |
| weather_intent | 100.0% |

## 불일치·오류 케이스

### DEV-008 — 반나절 일정

- 기대 Intent: `SCHEDULE`
- 실제 Intent: `SCHEDULE`
- 조건 불일치: `search_center` 기대 `'광화문'` / 실제 `None`, `time_available` 기대 `240` / 실제 `None`
- 오류: 없음


## 원본 파일

- `summary.json`: 기계 처리용 전체 요약
- `case_results.csv`: 케이스별 기대값·실제값·조건 비교
- `intent_metrics.csv`: Intent별 Precision / Recall / F1
- `confusion_matrix.csv`: 혼동행렬 원본
