# Shared Prompt Archive

행동 변경이 승인된 공유 규칙의 **실제 모델 입력 원문**만 보관합니다. 파일명은
`<slot>__legacy-<version>.md`를 사용하며, 설명·제목·Markdown 코드 블록은 넣지 않습니다.
여러 인텐트 슬롯과 함께 재현해야 하는 공유 기준선은 해당 인텐트의 `archive/variants.json`에서
조합합니다. 이력과 원본 커밋은 상위 [`HISTORY.md`](../HISTORY.md)에 기록합니다.
