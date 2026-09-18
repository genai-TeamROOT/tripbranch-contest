# MODIFY Prompt History

## 현재 활성 슬롯

| 슬롯 | 관리 버전 | 템플릿 | 공유 규칙 |
| --- | --- | --- | --- |
| modify.extract | 1.3.0 | extract.md, type_rules.md, target_rules.md, relative_expression_rules.md, field_merge_rules.md | budget, weather, concentration, environment, accessibility_needs, transport, shown_place_list |

## Draft

- 2026-09-03(modify.extract v1.3.0): `_shared/rules/accessibility_needs.md`를 bundle에 추가하고
  `field_merge_rules.md`에 accessibility_needs 병합 규칙을 더했습니다 — TP-207 리뷰(김진형,
  2026-09-03)에서 MODIFY가 이 규칙 파일 없이 어휘를 추측해 "장애인 화장실"을 존재하지 않는
  `accessible_toilet`으로 지어내는 문제가 확인됐습니다(RECOMMEND는 이미 이 파일을 포함해
  17/17 통과, MODIFY만 누락). 같은 리뷰에서 "유모차 끌고 갈 수 있는 곳으로 바꿔줘"처럼 교체
  의도인 발화에서 기존 `wheelchair_access`가 안 빠지고 AND로 남는 문제도 지적되어,
  place_types와 같은 교체/추가 구분 규칙을 accessibility_needs에도 적용했습니다. 실 서버
  재확인 후 승인 이력으로 승격합니다.
- 2026-08-31(modify.extract v1.2.0): `_shared/rules/conversation_history.md`를 bundle에 추가했습니다 —
  최근 대화가 이미 API contents로 전달되는데 프롬프트가 그 존재를 몰라 생략된 후속
  발화를 이어받지 못했습니다. 변경 이유·실측·회귀 근거는 `_shared/HISTORY.md`의 같은
  날짜 항목에 한 곳으로 모아 적었습니다. 실 서버 재확인 후 승인 이력으로 승격합니다.

## 승인 이력

| 기준선 | 날짜 | 커밋 | 슬롯·의존성 | 변경 내용 | 변경 이유 | 상태 |
| --- | --- | --- | --- | --- | --- | --- |
| legacy-1.0.4 | 2026-08-07 | `d1701a4` | `modify.extract` | `concentration_intent` 판별 규칙 추가 | RECOMMEND와 MODIFY의 혼잡도 의도 판별 불일치 해소 | 승인됨 |
| legacy-1.0.9 | 2026-08-10 | `86a9cd1` | `modify.extract` | 위치 되묻기 답변을 `search_center` 변경으로 병합 | 기존 조건을 유지한 재추천 보장 | 승인됨 |
| legacy-1.0.12 | 2026-08-12 | `0c0a548` | `modify.extract` | 바뀐 필드만 채우는 병합 규칙과 되묻기 결정 경로 보강 | 누적 조건 덮어쓰기 방지 | 승인됨 |
| legacy-1.0.13 | 2026-08-18 | `585a045` | 공유 weather/concentration/environment | 허용 표현을 `IGNORE`·`any` 조건 완화로 해석 | 날씨·혼잡도 조건을 반대로 강화하는 문제 방지 | 승인됨 |
| 1.1.0 | 2026-08-20 | (이 커밋) | `modify.extract` (1.0.0 → 1.1.0), 공유 `_shared/rules/transport.md` 신설 | `{{transport_rules}}` 추가 — "차로 바꿔줘" 같은 이동수단 변경 발화를 transport=car/walk/public로 매핑 | TP-105(PR #196)로 D의 자동차 경로 실측이 붙었지만, RECOMMEND뿐 아니라 MODIFY(조건 변경)에서도 이동수단을 바꾸는 발화의 매핑 규칙이 없었다. RECOMMEND와 규칙을 공유해 한쪽만 바뀌는 문제를 방지한다 | 승인됨 — pytest 2137건 통과(신규 `test_extract_modify_conditions_transport_change` 포함). 실 Gemini 골드셋 베이스라인 A/B 비교로 다른 케이스 변동이 이 변경과 무관한 LLM 비결정성임을 확인 |

공유 규칙의 원문 이력은 [`_shared/HISTORY.md`](../_shared/HISTORY.md)에서 관리합니다.
