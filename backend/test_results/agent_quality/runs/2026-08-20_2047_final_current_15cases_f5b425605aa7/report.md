# Agent 품질 평가 결과

- 실행 ID: `2026-08-20_2047_final_current_15cases_f5b425605aa7`
- 실행 시각: 2026-08-20T20:48:34+09:00
- 프롬프트 기준선: `current`
- 평가셋: `final` · 15건 / 22턴
- 골드셋 해시: `f5b425605aa7`

## 핵심 결과

| 지표 | 결과 | 의미 |
| --- | ---: | --- |
| Intent Accuracy | 95.5% | 전체 턴에서 Intent가 일치한 비율 |
| Intent Macro F1 | 0.967 | Intent별 F1을 동등하게 평균낸 균형 점수 |
| 조건 필드 정확도 | 87.5% | 기대 조건 필드 하나하나가 일치한 비율 |
| 최종 조건 완전 일치율 | 75.0% | 조건을 기대한 케이스에서 모든 필드가 맞은 비율 |
| 멀티턴 통과율 | 57.1% | 2턴 이상 케이스가 Intent·조건을 모두 통과한 비율 |
| 전체 케이스 통과율 | 73.3% | 케이스 단위로 모든 검증을 통과한 비율 |
| API 오류 | 0건 | HTTP/Provider 오류로 평가하지 못한 케이스 수 |

## 실행 성능

| 지표 | 결과 |
| --- | ---: |
| 클라이언트 지연시간 p50 | 2.99초 |
| 클라이언트 지연시간 p95 | 10.82초 |

## Intent별 Precision / Recall / F1

| Intent | 표본 수 | Precision | Recall | F1 |
| --- | ---: | ---: | ---: | ---: |
| GENERAL | 1 | 100.0% | 100.0% | 1.000 |
| INFO | 2 | 100.0% | 100.0% | 1.000 |
| MODIFY | 4 | 100.0% | 75.0% | 0.857 |
| OUT_OF_SCOPE | 1 | 100.0% | 100.0% | 1.000 |
| RECOMMEND | 9 | 90.0% | 100.0% | 0.947 |
| SCHEDULE | 5 | 100.0% | 100.0% | 1.000 |

## 혼동행렬

행은 **기대 Intent**, 열은 **실제 Intent**입니다. 대각선 값은 정분류이고, 대각선 밖 값은 어떤 Intent끼리 혼동했는지 보여줍니다.

| 기대 \ 실제 | GENERAL | INFO | MODIFY | OUT_OF_SCOPE | RECOMMEND | SCHEDULE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| GENERAL | 1 | 0 | 0 | 0 | 0 | 0 |
| INFO | 0 | 2 | 0 | 0 | 0 | 0 |
| MODIFY | 0 | 0 | 3 | 0 | 1 | 0 |
| OUT_OF_SCOPE | 0 | 0 | 0 | 1 | 0 | 0 |
| RECOMMEND | 0 | 0 | 0 | 0 | 9 | 0 |
| SCHEDULE | 0 | 0 | 0 | 0 | 0 | 5 |

## 조건 필드별 정확도

| 필드 | 정확도 |
| --- | ---: |
| concentration_intent | 100.0% |
| environment | 100.0% |
| exclude_tags | 100.0% |
| place_tags | 87.5% |
| place_types | 75.0% |
| search_center | 83.3% |
| time_available | 66.7% |
| transport | 100.0% |
| weather | 100.0% |
| weather_intent | 100.0% |

## 불일치·오류 케이스

### FINAL-001 — 궁궐 산책 추천

- 기대 Intent: `RECOMMEND`
- 실제 Intent: `RECOMMEND`
- 조건 불일치: `place_types` 기대 `['attraction']` / 실제 `['cultural_facility', 'attraction']`, `place_tags` 기대 `['궁궐']` / 실제 `['궁궐', '산']`
- 오류: 없음

### FINAL-011 — 일정 장소 교체

- 기대 Intent: `SCHEDULE, SCHEDULE`
- 실제 Intent: `SCHEDULE, SCHEDULE`
- 조건 불일치: `search_center` 기대 `'광화문'` / 실제 `None`, `time_available` 기대 `240` / 실제 `None`
- 오류: 없음

### FINAL-014 — 박물관 제외 유지

- 기대 Intent: `RECOMMEND, MODIFY`
- 실제 Intent: `RECOMMEND, RECOMMEND`
- 조건 불일치: 없음
- 오류: 없음

### FINAL-015 — 가용 시간 후속 변경

- 기대 Intent: `SCHEDULE, SCHEDULE`
- 실제 Intent: `SCHEDULE, SCHEDULE`
- 조건 불일치: `search_center` 기대 `'경복궁'` / 실제 `None`
- 오류: 없음


## 원본 파일

- `summary.json`: 기계 처리용 전체 요약
- `case_results.csv`: 케이스별 기대값·실제값·조건 비교
- `intent_metrics.csv`: Intent별 Precision / Recall / F1
- `confusion_matrix.csv`: 혼동행렬 원본
