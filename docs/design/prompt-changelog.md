# 프롬프트 이력 관리 이관 안내

> 상태: 2026-08-19부터 신규 변경을 이 문서에 추가하지 않습니다.

프롬프트의 활성 상태와 과거 이력은 이제 인텐트별 Prompt Library에서 관리합니다.

- 운영 가이드: [`backend/app/prompts/README.md`](../../backend/app/prompts/README.md)
- 현재 슬롯·소유자: 각 인텐트의 `backend/app/prompts/<intent>/meta.yaml`
- 현재 상태·Draft·승인 이력: 각 인텐트의 `HISTORY.md`
- 실행 가능한 과거 기준선: 각 인텐트의 `archive/`와 `archive/variants.json`

공유 규칙은 [`_shared/HISTORY.md`](../../backend/app/prompts/_shared/HISTORY.md)가 원본 이력을
관리합니다. 기존 전역 버전 번호는 인텐트별 Archive의 `legacy-<version>` 참조로
보존합니다.
