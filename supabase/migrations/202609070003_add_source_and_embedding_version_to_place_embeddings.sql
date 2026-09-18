begin;

-- 분산 임베딩 v2부터 원문 식별자(source_ref)와 청크 식별자를 분리한다.
-- 기존 행은 새 컬럼이 NULL인 채 유지되며, 새 적재분부터 모두 채운다.
alter table public.place_embeddings
  add column if not exists document_id text,
  add column if not exists chunk_id text,
  add column if not exists content_hash text,
  add column if not exists preprocessing_version text,
  add column if not exists embedding_version text;

-- source_ref는 여러 청크가 공유하는 원본 문서 식별자이므로 기존 고유 제약을
-- 제거하고, 청크 자체의 안정적인 ID로 재실행 중복을 막는다. PostgreSQL의
-- UNIQUE는 NULL을 허용하므로 기존 행과도 호환된다.
alter table public.place_embeddings
  drop constraint if exists place_embeddings_content_source_ref_unique;

alter table public.place_embeddings
  add constraint place_embeddings_content_chunk_id_unique
    unique (content_id, chunk_id);

alter table public.place_embeddings
  add constraint place_embeddings_document_id_not_blank
    check (document_id is null or btrim(document_id) <> ''),
  add constraint place_embeddings_chunk_id_not_blank
    check (chunk_id is null or btrim(chunk_id) <> ''),
  add constraint place_embeddings_content_hash_not_blank
    check (content_hash is null or btrim(content_hash) <> ''),
  add constraint place_embeddings_preprocessing_version_not_blank
    check (preprocessing_version is null or btrim(preprocessing_version) <> ''),
  add constraint place_embeddings_embedding_version_not_blank
    check (embedding_version is null or btrim(embedding_version) <> '');

create index if not exists place_embeddings_document_id_idx
  on public.place_embeddings (document_id)
  where document_id is not null;

create index if not exists place_embeddings_embedding_version_idx
  on public.place_embeddings (embedding_version)
  where embedding_version is not null;

comment on column public.place_embeddings.source_ref is
  '원본 문서 식별자. 네이버 URL, Google review_key, TourAPI 고정 참조값이며 청크 번호를 붙이지 않는다.';
comment on column public.place_embeddings.document_id is
  '전처리 단계에서 생성한 원본 문서 고유 ID.';
comment on column public.place_embeddings.chunk_id is
  '문서·청크 본문·임베딩 설정으로 생성한 청크 고유 ID. v2 적재 충돌 키.';
comment on column public.place_embeddings.content_hash is
  '전처리된 원문 전체의 SHA-256 해시.';
comment on column public.place_embeddings.preprocessing_version is
  '원문 전처리 규칙 버전.';
comment on column public.place_embeddings.embedding_version is
  '모델·청킹 조건을 묶은 운영 임베딩 버전.';

commit;
