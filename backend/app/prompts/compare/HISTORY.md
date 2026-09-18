# COMPARE Prompt History

## 현재 활성 슬롯

| 슬롯 | 관리 버전 | 템플릿 | 공유 규칙 |
| --- | --- | --- | --- |
| compare.extract | 2.1.0 | extract.md, target_rules.md, criteria_rules.md | shown_place_list |
| compare.summary | 2.0.1 | summary_instruction.md | persona, factuality |

## Draft

- 2026-08-31(compare.extract v2.1.0): `_shared/rules/conversation_history.md`를 bundle에 추가했습니다 —
  최근 대화가 이미 API contents로 전달되는데 프롬프트가 그 존재를 몰라 생략된 후속
  발화를 이어받지 못했습니다. 변경 이유·실측·회귀 근거는 `_shared/HISTORY.md`의 같은
  날짜 항목에 한 곳으로 모아 적었습니다. 실 서버 재확인 후 승인 이력으로 승격합니다.

## 승인 이력

| 기준선 | 날짜 | 커밋 | 슬롯 | 변경 내용 | 변경 이유 | 상태 |
| --- | --- | --- | --- | --- | --- | --- |
| legacy-1.0.10 | 2026-08-11 | `6904af7` | `compare.summary` | 검증된 비교 사실을 3~6줄 답변으로 만드는 슬롯 신설 | COMPARE가 준비 중 안내로 끝나던 상태 해소 | 승인됨 |
| legacy-git-bea1e86 | 2026-08-11 | `bea1e86` | `compare.summary` | 거리·운영시간 표기를 도보 시간·시간 단위로 바꾸고 여행 안내 문체 보정 | 기계적인 수치 나열 대신 선택 가능한 비교 제공 | 승인됨 |
| legacy-git-52ec573 | 2026-08-13 | `52ec573` | `compare.extract` | 노출된 장소 이름으로도 비교 대상을 식별 | 이름 지목 시 엉뚱한 장소가 비교에 섞이는 문제 방지 | 승인됨 |
| 2.0.0 | 2026-08-21 | `d724e89` | `compare.extract`(criteria_rules.md), `compare.summary`(summary_instruction.md) | `distance` 기준을 폐지하고 `travel_time`으로 합친다 — "가까워?", "거리 차이?", "빨리 갈까?", "얼마나 걸려?"를 모두 travel_time으로 판별해, 직선거리 하나 대신 실측 거리 + 도보·자동차·대중교통 세 수단 소요시간을 함께 답한다(criteria enum 값이 빠지는 하위 호환 깨짐이라 MAJOR) | TP-105(네이버 자동차 실측)·TP-106(카카오 대중교통 실측) 연결 후, "이동이 얼마나 용이한지"를 직선거리 하나로 답하는 것보다 실제 경로·수단별 소요시간을 함께 보여주는 쪽이 실제 질문 의도에 맞다고 판단. "덜 막힐까?"(실시간 도로 정체)는 ROAD_TRAFFIC_STTS 연동 전이라 이번 범위에서 제외 — 우선 정체 미반영 실측으로 답하고 프롬프트에도 그렇게 명시 | 승인됨 |
| 2.0.1 | 2026-08-21 | (이 커밋) | `compare.summary`(summary_instruction.md) | travel_time 답변에서 장소별 거리·수단별 소요시간을 문장으로 나열하던 부분을 제거하고 1~2줄의 짧은 추천 문장만 남긴다(PATCH, 판별 로직 변화 없음) | 실 서버 확인 결과 travel_time 답변이 문장으로만 오니 장소별 수치를 비교하기 어렵다는 피드백. 상세 수치(거리·도보/자동차/대중교통 소요시간)는 프론트엔드 비교 카드(CompareResultCards)로 옮기고, 텍스트는 짧은 추천 결론만 남긴다 | Draft — 단위 테스트·프론트 빌드 확인 진행 중 |

## 실행 가능한 과거 기준선

- `compare-summary@legacy-1.0.10`: `6904af7` 당시의 비교 요약과 페르소나 조합입니다.
  [compare_summary__legacy-1.0.10.md](archive/compare_summary__legacy-1.0.10.md)는 설명·코드
  블록 없이 그대로 모델에 전달할 수 있는 원문이며, 필요한 공유 페르소나는
  [`_shared` 기준선](../_shared/archive/persona__legacy-1.0.5.md)과 함께
  [variants.json](archive/variants.json)에서 묶습니다.
