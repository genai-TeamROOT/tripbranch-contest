# 지역구 임베딩 대량 적재 가이드

## 1. 목적

Colab에서 생성한 지역구별 `place_embeddings_*.jsonl.gz` 파일을 팀 Supabase의
`public.place_embeddings` 테이블에 안전하고 빠르게 적재하는 방법을 설명한다.

이번 적재 방식은 다음 조건을 기준으로 한다.

- 임베딩 모델: `jhgan/ko-sroberta-multitask`
- 벡터 차원: 768
- 운영 파일 예시:
  `place_embeddings_170_yongsan_jhgan_evidence_120t_v8.jsonl.gz`
- 충돌 및 중복 판정 키: `(content_id, chunk_id)`
- 전송 방식: Supabase PostgREST upsert
- 요청당 배치 크기: 200행
- 요청 타임아웃: 120초

## 2. 적재 방식을 변경한 이유

기존 스크립트는 HNSW 벡터 인덱스가 있는 상태에서도 안정적으로 동작하도록
10행씩 적재했다. 하지만 전체 지역구를 적재하면 HTTP 요청 횟수가 지나치게
많아진다.

서초구 v8 데이터로 같은 200행을 실제 측정한 결과는 다음과 같다.

| 조건 | 결과 |
| --- | --- |
| HNSW 인덱스 유지 | 11.08초 후 PostgreSQL `statement_timeout`(`57014`)으로 실패 |
| HNSW 인덱스 제거 | 2.36초에 200행 upsert 성공 |

따라서 전체 적재 기간에는 HNSW 인덱스를 한 번만 제거하고, 모든 지역구 적재가
끝난 뒤 한 번만 다시 생성한다. 일반 인덱스와 고유 제약은 제거하지 않는다.

## 3. 변경된 코드

### `backend/scripts/import_place_embeddings.py`

- `_UPSERT_CHUNK_SIZE`: `10`에서 `200`으로 변경
- `_UPSERT_TIMEOUT_SECONDS`: `60.0`에서 `120.0`으로 변경
- 200행 단위 PostgREST upsert
- 일시적인 `statement_timeout` 발생 시 해당 배치만 재시도
- `(content_id, chunk_id)` 기준으로 멱등 upsert

### `backend/scripts/import_district_embedding_archive.py`

- `.jsonl.gz` 압축 해제
- 실제 적재 전에 자동 dry-run 수행
- 지역구와 파일 형식 검증
- 사용자가 `IMPORT <지역구 폴더명>`을 정확히 입력해야 실제 적재
- 다음 두 파일명 유형 지원
  - `jhgan_structural_120t_v*`
  - `jhgan_evidence_120t_v*`
- 실행 시 200행 배치와 HNSW 운영 주의사항 출력

## 4. 전체 작업 순서

```text
1. 팀원별로 Colab에서 담당 지역구 임베딩 생성
2. 지역구별 .jsonl.gz 파일 다운로드 및 보관
3. 작업 담당자 한 명이 HNSW 인덱스를 한 번 제거
4. 지역구별 파일을 200행 단위 PostgREST 방식으로 적재
5. 모든 지역구의 파일·DB 건수 대조
6. 전체 적재가 끝난 뒤 HNSW 인덱스를 한 번 재생성
7. 벡터 검색과 서비스 동작 확인
```

HNSW가 없는 동안 데이터 추가와 일반 조회는 가능하지만, 전역 벡터 유사도
검색은 순차 스캔을 사용하므로 느려진다. 전체 적재가 끝날 때까지 운영 검색
테스트는 최소화한다.

## 5. HNSW 인덱스 제거

Supabase 대시보드의 SQL Editor에서 작업 담당자 한 명만 실행한다.

```sql
drop index if exists public.place_embeddings_embedding_hnsw_idx;
```

제거 여부 확인:

```sql
select indexname
from pg_indexes
where schemaname = 'public'
  and tablename = 'place_embeddings'
order by indexname;
```

결과에 `place_embeddings_embedding_hnsw_idx`가 없으면 제거된 상태다.

다음 객체는 그대로 유지해야 한다.

- `place_embeddings_content_chunk_id_unique`
- `place_embeddings_content_id_idx`
- `place_embeddings_document_id_idx`
- `place_embeddings_embedding_version_idx`
- `place_embeddings_pkey`

## 6. 지역구 파일 적재

평소 사용하는 `tripbranch/backend` 폴더에서 실행한다.

```bash
cd /각자경로/tripbranch/backend

.venv/bin/python -u scripts/import_district_embedding_archive.py \
  "/Users/본인계정/Downloads/place_embeddings_170_yongsan_jhgan_evidence_120t_v8.jsonl.gz"
```

`uv`를 사용한다면 다음과 같이 실행할 수도 있다.

```bash
uv run python -u scripts/import_district_embedding_archive.py \
  "/Users/본인계정/Downloads/place_embeddings_170_yongsan_jhgan_evidence_120t_v8.jsonl.gz"
```

스크립트는 다음 순서로 동작한다.

1. 파일명에서 지역구를 확인한다.
2. 임시 폴더에 JSONL을 압축 해제한다.
3. JSONL 형식, 벡터 차원, `content_id`, `chunk_id`, 활성 장소 여부를 검증한다.
4. dry-run 결과를 출력한다.
5. 사용자에게 `IMPORT 170_yongsan` 같은 확인 문구를 요구한다.
6. 확인 문구가 일치하면 200행씩 실제 upsert한다.
7. 임시 압축 해제 파일을 자동으로 삭제한다.

다운로드한 `.jsonl.gz` 원본은 재검증과 장애 복구를 위해 보관한다.

## 7. 검증만 실행하기

DB를 변경하지 않고 파일과 참조 관계만 확인하려면 다음 옵션을 사용한다.

```bash
.venv/bin/python -u scripts/import_district_embedding_archive.py \
  "/다운로드/경로/place_embeddings_<구>_jhgan_evidence_120t_v8.jsonl.gz" \
  --dry-run-only
```

## 8. 기존 버전 교체

같은 지역구를 새 청킹 또는 새 임베딩 버전으로 다시 적재하면 새 파일에 존재하지
않는 옛 청크가 남을 수 있다. 이때만 `--replace-existing`을 사용한다.

```bash
.venv/bin/python -u scripts/import_district_embedding_archive.py \
  "/다운로드/경로/place_embeddings_<구>_jhgan_evidence_120t_v8.jsonl.gz" \
  --replace-existing
```

이 옵션은 새 버전 적재가 전부 성공한 뒤, 해당 지역구·동일 모델의 이전
`embedding_version`만 제거한다. 자동으로 지역구의 `place_catalog.csv`를 찾지
못하면 다음처럼 직접 지정한다.

```bash
.venv/bin/python -u scripts/import_district_embedding_archive.py \
  "/다운로드/경로/place_embeddings_<구>_jhgan_evidence_120t_v8.jsonl.gz" \
  --replace-existing \
  --scope-content-ids "/경로/place_catalog.csv"
```

처음 적재하는 지역구에는 `--replace-existing`이 필요 없다.

## 9. 적재 결과 확인

지역구별 총 적재 건수:

```sql
select
  p.district_code,
  pe.embedding_version,
  count(*) as embedding_count
from public.place_embeddings pe
join public.places p
  on p.content_id = pe.content_id
group by p.district_code, pe.embedding_version
order by p.district_code, pe.embedding_version;
```

파일 manifest의 `output_chunks`와 해당 지역구·버전의 `embedding_count`가
일치하는지 확인한다.

용산구 v8 실측 결과:

| 항목 | 결과 |
| --- | ---: |
| JSONL 행 | 13,123 |
| 중복 `(content_id, chunk_id)` | 0 |
| PostgREST 배치 | 66회 |
| 실제 DB 적재 | 13,123 |
| DB 재조회 | 13,123 |
| 압축 해제·dry-run·적재 총시간 | 약 1분 10초 |

`published_at_nulled`는 작성 시각을 절대 날짜로 복원할 수 없는 문서 수다.
본문, URL, 임베딩 적재 실패 건수를 뜻하지 않는다.

## 10. 전체 적재 후 HNSW 재생성

모든 지역구의 적재와 건수 검증이 끝난 뒤 SQL Editor에서 한 번만 실행한다.

```sql
create index place_embeddings_embedding_hnsw_idx
on public.place_embeddings
using hnsw (embedding vector_cosine_ops);
```

재생성 확인:

```sql
select indexname
from pg_indexes
where schemaname = 'public'
  and tablename = 'place_embeddings'
  and indexname = 'place_embeddings_embedding_hnsw_idx';
```

한 행이 반환되면 복원된 것이다. 이후 대표 취향 질의로 벡터 검색 응답 시간과
검색 결과를 확인한다.

## 11. 운영 주의사항

- HNSW 제거와 재생성은 작업 담당자 한 명만 수행한다.
- 지역구마다 HNSW를 제거·재생성하지 않는다.
- HNSW가 없는 기간에는 여러 지역구를 적재할 수 있지만, DB 쓰기 경합을 줄이기
  위해 순차 적재를 기본으로 한다.
- 병렬 적재가 필요하면 최대 2개 작업부터 측정한다.
- 파일을 다시 실행해도 `(content_id, chunk_id)` upsert이므로 같은 청크가
  중복 생성되지 않는다.
- 청킹 방식이 바뀌어 `chunk_id` 자체가 달라진 경우에는 반드시
  `--replace-existing`으로 옛 버전을 정리한다.
- API 키가 아니라 `backend/.env`의 팀 Supabase 접속 정보를 사용한다.
- `.env`와 비밀값을 공유 파일, 로그 또는 Git에 포함하지 않는다.
