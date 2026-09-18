# Prompt Library Ownership

| 영역 | 주 소유자 | Git | 현재 역할 |
| --- | --- | --- | --- |
| `router/`, `modify/`, `compare/`, `general/`, `out_of_scope/`, `_shared/` | A | @kiminlim | 인텐트 경계, 대화 흐름, 공통 응답 규칙 |
| `recommend/` | D | @rayquaza410 | 추천 조건·RAG/Scoring 연계 규칙 |
| `info/` | C | @jjinsword | 관광 데이터·외부 API 기반 정보 질의 규칙 |
| `follow_up/` | C | @jjinsword | 턴이 끝난 뒤 제안할 다음 발화와 서비스 기능 목록 |
| `schedule/` | B | @lth2295 | 일정 편성·부분 재편성 규칙 |

공유 규칙을 바꿀 때는 영향받는 모든 슬롯의 단일 턴 평가와
`backend/test_results/agent_quality/`의 다중 턴 회귀를 함께 확인합니다.

해당 영역의 프롬프트(위 경로) 또는 그 영역이 소유한 코드(예: `schedule/` → B의
일정 편성 로직)를 변경하는 PR을 올릴 때는, PR 설명의 리뷰참고사항에 위 Git
계정을 `@handle` 형태로 남겨 리뷰를 요청합니다.
