begin;

-- Package D: 취향 근거 RAG용 벡터 저장소. naver_post/google_review에서 뽑은
-- 근거 문장을 문장 단위로 청킹해 임베딩한 결과를 담는다(계획 문서 §2.9,
-- §7.12). content_id당 근거가 여러 건이어야 검색이 성립하므로 content_id는
-- PK가 아니다.
create table public.place_embeddings (
  id bigint generated always as identity primary key,
  content_id text not null,
  place_title text not null,
  source_type text not null,
  source_text text not null,
  source_url text,
  source_ref text not null,
  published_at timestamptz,
  embedding vector(768) not null,
  model_name text not null,
  created_at timestamptz not null default now(),

  constraint place_embeddings_content_id_fk
    foreign key (content_id)
    references public.places (content_id)
    on delete cascade,
  constraint place_embeddings_source_type_valid
    check (source_type in ('naver_post', 'google_review')),
  constraint place_embeddings_source_text_not_blank
    check (btrim(source_text) <> ''),
  constraint place_embeddings_source_ref_not_blank
    check (btrim(source_ref) <> ''),
  -- 재실행해도 source_ref(내용 해시)가 같아 이 제약으로 재적재 시 중복이
  -- 쌓이지 않는다. source_url은 구글 리뷰처럼 한 장소의 여러 리뷰가 같은
  -- URL을 공유할 수 있어 고유하지 않으므로 제약에 쓰지 않는다(§2.9).
  constraint place_embeddings_content_source_ref_unique
    unique (content_id, source_ref)
);

-- 후보 content_id로 먼저 좁힌 뒤 집계하는 조회 패턴을 위한 인덱스다. 장소별
-- top-N 집계는 전체 스캔이 필요해 HNSW를 안 타므로(§2.10) 이 인덱스가
-- 먼저 걸린다.
create index place_embeddings_content_id_idx
  on public.place_embeddings (content_id);

-- 후보 좁히기 없이 전역에서 최근접 이웃을 바로 찾을 때를 위한 근사 인덱스.
create index place_embeddings_embedding_hnsw_idx
  on public.place_embeddings
  using hnsw (embedding vector_cosine_ops);

-- 클라이언트의 직접 접근은 차단하고 FastAPI의 서버 권한을 통해서만 사용한다.
alter table public.place_embeddings enable row level security;

revoke all on table public.place_embeddings from anon, authenticated;

commit;
