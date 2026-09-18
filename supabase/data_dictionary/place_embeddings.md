# place_embeddings 데이터 딕셔리

## 개요

`public.place_embeddings`는 네이버 블로그, Google 리뷰, TourAPI 개요를
청킹·임베딩한 RAG 근거 저장소입니다. `content_id` 후보를 먼저 좁힌 뒤 같은
원문에서는 가장 관련도 높은 청크 하나만 사용해 장소별 근거 점수를 계산합니다.

| 필드 | 타입 | NULL 허용 | 정의 | 값 예시 | 활용 예시 |
| --- | --- | --- | --- | --- | --- |
| `id` | bigint | 아니오 | DB 내부 행 식별자 | `102341` | 운영·감사 시 특정 행 조회 |
| `content_id` | text | 아니오 | `places`와 연결되는 장소 ID | `2808041` | 후보 장소와 RAG 근거 조인 |
| `place_title` | text | 아니오 | 장소 표시명 | `후암편백` | 검색 결과 표시 |
| `source_type` | text | 아니오 | 원문 출처 | `naver_post`, `google_review`, `tour_overview` | 출처별 근거 제한 |
| `source_text` | text | 아니오 | 임베딩한 청크 본문 | `부모님과 식사하기 좋았어요.` | LLM 추천 근거 제공 |
| `source_url` | text | 예 | 원문 확인 URL | `https://blog.naver.com/...` | 사용자 출처 링크 |
| `source_ref` | text | 아니오 | 청크 번호 없는 원본 문서 식별자 | 블로그 URL, `a:작성자|2026-09-01` | 같은 원문 청크 묶기 |
| `document_id` | text | 예 | 전처리 문서 고유 ID | `doc_eb76253e...` | 원문 단위 중복 제거 |
| `chunk_id` | text | 예 | 문서·본문·설정 기반 청크 고유 ID | `chunk_2d91...` | 신규 재적재 충돌 키 |
| `content_hash` | text | 예 | 전처리 원문 전체 SHA-256 | `bb51ffd5...` | 본문 변경 감지 |
| `preprocessing_version` | text | 예 | 원문 정제 규칙 버전 | `rag-prechunk-1.6.0` | 전처리 결과 구분 |
| `embedding_version` | text | 예 | 모델·청킹 운영 버전 | `jhgan-structural-120t-v4` | 서로 다른 벡터 세트 분리 |
| `published_at` | timestamptz | 예 | 원문 게시 시각 | `2026-08-01T13:00:00+09:00` | 최신 근거 우선 처리 |
| `embedding` | vector(768) | 아니오 | 정규화된 본문 임베딩 | `[0.01, ...]` | 코사인 유사도 검색 |
| `model_name` | text | 아니오 | 임베딩 모델명 | `jhgan/ko-sroberta-multitask` | 질의 모델 일치 검증 |
| `rating` | smallint | 예 | Google 리뷰 별점 | `5` | 고평점 근거 필터 |
| `language` | text | 예 | 원문 언어 | `ko`, `mixed` | 언어별 품질 확인 |
| `is_translated` | boolean | 예 | Google 자동번역 여부 | `true` | 번역문 품질 점검 |
| `chunk_index` | smallint | 예 | 원문 내 0부터 시작하는 청크 순번 | `2` | 원문 순서 복원 |
| `chunk_count` | smallint | 예 | 원문의 전체 청크 수 | `6` | 청킹 상태 확인 |
| `token_count` | smallint | 예 | jhgan 토크나이저 기준 청크 길이 | `117` | 120토큰 제한 검증 |
| `created_at` | timestamptz | 아니오 | DB 최초 적재 시각 | `2026-09-07T16:00:00Z` | 적재 이력 확인 |

## 적재 원칙

- 신규 행은 `document_id`, `chunk_id`, `content_hash`, 전처리·임베딩 버전을 채웁니다.
- 기존 행은 해당 컬럼이 NULL일 수 있습니다.
- 청킹 조건이 바뀐 지역구를 다시 넣을 때는 적재 스크립트의
  `--replace-existing --scope-content-ids <place_catalog.csv>`를 사용해 같은
  모델의 옛 청크를 먼저 제거합니다.
- 적재 전에는 반드시 `--dry-run`으로 벡터 차원, 메타데이터, 활성 장소 ID를 검사합니다.
