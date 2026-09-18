# GENERAL Prompt History

## 현재 활성 슬롯

| 슬롯 | 관리 버전 | 템플릿 | 공유 규칙 |
| --- | --- | --- | --- |
| general.extract | v1.1.0 | extract.md, topic_rules.md | service_scope |
| general.answer | v1 | answer_instruction.md | persona, factuality |

## Draft

- 2026-08-31(general.extract v1.1.0): `_shared/rules/conversation_history.md`를 bundle에 추가했습니다 —
  최근 대화가 이미 API contents로 전달되는데 프롬프트가 그 존재를 몰라 생략된 후속
  발화를 이어받지 못했습니다. 변경 이유·실측·회귀 근거는 `_shared/HISTORY.md`의 같은
  날짜 항목에 한 곳으로 모아 적었습니다. 실 서버 재확인 후 승인 이력으로 승격합니다.

## 승인 이력

| 기준선 | 날짜 | 커밋 | 슬롯 | 변경 내용 | 변경 이유 | 상태 |
| --- | --- | --- | --- | --- | --- | --- |
| legacy-pre-version | 2026-07-24 | `21aad22` | `general.extract`, `general.answer` | Gemini 구조화 출력 기반 GENERAL 추출·답변 초기 구현 | 인텐트별 기본 응답 경로 구축 | 승인됨 |

전역 `PROMPT_VERSION` 도입 전 변경이므로 버전 번호를 추정하지 않고 커밋 기준으로만 기록합니다.
