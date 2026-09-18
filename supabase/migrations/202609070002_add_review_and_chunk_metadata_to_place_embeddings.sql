begin;

-- TripBranch_RAG_Distributed_Embedding 분산 임베딩(2026-09-07)에서 구별로
-- 만드는 문서에는 rating·language·is_translated와 청킹 메타데이터가 이미
-- 있는데, place_embeddings에는 담을 컬럼이 없어 지금까지는 적재 시 버려졌다.
-- 서비스에서 "번역 안 된 원문 우선", "고평점 근거만" 같은 필터링과 "이 청크가
-- 문서의 몇 번째 조각인지" 표시에 쓸 수 있도록 컬럼을 추가한다.
--
-- 전부 nullable이다 — 기존에 이미 적재된 57,331건(종로·중구)은 이 정보를
-- 재계산할 원본이 남아 있지 않아 NULL로 둔다. 새로 적재하는 행부터 채워진다.
alter table public.place_embeddings
  add column if not exists rating smallint,
  add column if not exists language text,
  add column if not exists is_translated boolean,
  add column if not exists chunk_index smallint,
  add column if not exists chunk_count smallint,
  add column if not exists token_count smallint;

alter table public.place_embeddings
  add constraint place_embeddings_rating_valid
  check (rating is null or rating between 1 and 5);

alter table public.place_embeddings
  add constraint place_embeddings_chunk_fields_nonnegative
  check (
    (chunk_index is null or chunk_index >= 0)
    and (chunk_count is null or chunk_count > 0)
    and (token_count is null or token_count > 0)
  );

alter table public.place_embeddings
  add constraint place_embeddings_chunk_index_within_count
  check (
    chunk_index is null or chunk_count is null or chunk_index < chunk_count
  );

comment on column public.place_embeddings.rating is
  'google_review 별점(1-5). naver_post·tour_overview는 NULL.';
comment on column public.place_embeddings.language is
  '청크 원문 언어 감지 결과(ko/en/mixed/und).';
comment on column public.place_embeddings.is_translated is
  'google_review가 Google에 의해 자동번역된 원문인지. naver_post·tour_overview는 항상 false에 가깝지만 원본 파이프라인 값을 그대로 담는다.';
comment on column public.place_embeddings.chunk_index is
  '원본 문서 내 이 청크의 0부터 시작하는 순번.';
comment on column public.place_embeddings.chunk_count is
  '원본 문서가 나뉜 전체 청크 수.';
comment on column public.place_embeddings.token_count is
  '이 청크의 jhgan 토크나이저 기준 토큰 수(최대 120).';

commit;
