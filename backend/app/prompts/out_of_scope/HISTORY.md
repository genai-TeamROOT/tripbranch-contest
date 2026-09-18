# OUT_OF_SCOPE Prompt History

## 현재 활성 슬롯

| 슬롯 | 관리 버전 | 템플릿 | 공유 규칙 |
| --- | --- | --- | --- |
| out_of_scope.classify | v1 | classify.md | service_scope, safety, router.classify |

## 승인 이력

| 기준선 | 날짜 | 커밋 | 슬롯·의존성 | 변경 내용 | 변경 이유 | 상태 |
| --- | --- | --- | --- | --- | --- | --- |
| legacy-pre-version | 2026-07-24 | `21aad22` | `out_of_scope.classify` | 유해·서비스 범위 밖·프롬프트 인젝션을 Intent 분류에서 차단하는 초기 규칙 | 안전한 서비스 경계 설정 | 승인됨 |

OUT_OF_SCOPE는 Router와 `_shared/rules/safety.md`를 함께 사용합니다. 이후 안전 규칙 변경의
원문 이력은 [`_shared/HISTORY.md`](../_shared/HISTORY.md)에 기록하고 여기에는 영향 항목만
연결합니다.
