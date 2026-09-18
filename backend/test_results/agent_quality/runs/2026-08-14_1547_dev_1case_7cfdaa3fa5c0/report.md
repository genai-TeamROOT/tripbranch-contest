# Agent 품질 평가 결과

- 실행 ID: `2026-08-14_1547_dev_1case_7cfdaa3fa5c0`
- 실행 시각: 2026-08-14T15:47:27+09:00
- 평가셋: `dev` · 1건 / 1턴
- 골드셋 해시: `7cfdaa3fa5c0`

## 핵심 결과

| 지표 | 결과 | 의미 |
| --- | ---: | --- |
| Intent Accuracy | 100.0% | 전체 턴에서 Intent가 일치한 비율 |
| Intent Macro F1 | 1.000 | Intent별 F1을 동등하게 평균낸 균형 점수 |
| 조건 필드 정확도 | 100.0% | 기대 조건 필드 하나하나가 일치한 비율 |
| 최종 조건 완전 일치율 | 100.0% | 조건을 기대한 케이스에서 모든 필드가 맞은 비율 |
| 멀티턴 통과율 | 0.0% | 2턴 이상 케이스가 Intent·조건을 모두 통과한 비율 |
| 전체 케이스 통과율 | 100.0% | 케이스 단위로 모든 검증을 통과한 비율 |
| API 오류 | 0건 | HTTP/Provider 오류로 평가하지 못한 케이스 수 |

## 실행 성능

| 지표 | 결과 |
| --- | ---: |
| 클라이언트 지연시간 p50 | 3.37초 |
| 클라이언트 지연시간 p95 | 3.37초 |

## Intent별 Precision / Recall / F1

| Intent | 표본 수 | Precision | Recall | F1 |
| --- | ---: | ---: | ---: | ---: |
| RECOMMEND | 1 | 100.0% | 100.0% | 1.000 |

## 혼동행렬

행은 **기대 Intent**, 열은 **실제 Intent**입니다. 대각선 값은 정분류이고, 대각선 밖 값은 어떤 Intent끼리 혼동했는지 보여줍니다.

| 기대 \ 실제 | RECOMMEND |
| --- | ---: |
| RECOMMEND | 1 |

## 조건 필드별 정확도

| 필드 | 정확도 |
| --- | ---: |
| place_tags | 100.0% |
| place_types | 100.0% |
| search_center | 100.0% |

## 불일치·오류 케이스

모든 케이스가 Intent와 기대 조건을 통과했습니다.

## 원본 파일

- `summary.json`: 기계 처리용 전체 요약
- `case_results.csv`: 케이스별 기대값·실제값·조건 비교
- `intent_metrics.csv`: Intent별 Precision / Recall / F1
- `confusion_matrix.csv`: 혼동행렬 원본
