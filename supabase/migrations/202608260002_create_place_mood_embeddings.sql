begin;

-- 장소 사진의 분위기 임베딩을 담는다. 사진별 테이블과 장소별 테이블 둘로 나눈다.
--
-- 텍스트 임베딩(place_embeddings)에 얹지 않은 이유는 좌표계가 다르기 때문이다.
-- 두 벡터가 우연히 둘 다 768차원이지만, 한쪽은 한국어 문장(jhgan/ko-sroberta-multitask),
-- 다른 쪽은 사진(google/siglip2-base-patch16-224)이 사는 공간이다. 섞으면 계산은
-- 되지만 결과가 무의미하고, place_embeddings에 걸린 HNSW 인덱스가 한 좌표계를
-- 가정하므로 기존 RAG 검색까지 망가진다. 컬럼 구조도 맞지 않는다 —
-- source_text가 not null인데 사진에는 본문이 없다.
--
-- 사진별과 장소별을 나눈 이유는 두 가지다.
--   1. 사진이 갱신될 때. places.first_image_url은 list_fetched_at 주기로 바뀌는데,
--      장소 평균만 저장하면 정규화 과정에서 원래 합을 잃어 부분 갱신을 할 수 없다.
--      사진 한 장이 늘 때마다 그 장소 사진을 전부 다시 임베딩해야 한다.
--   2. 추천 근거. "올리신 사진과 이 사진이 닮았습니다"를 보여주려면 사진 단위
--      벡터가 있어야 한다.
--
-- 인덱스는 이 파일에 넣지 않는다. 202608250002_restore_place_embeddings_hnsw_index의
-- 기록대로, HNSW가 걸린 상태에서 대량 upsert하면 인덱스 갱신 비용 때문에 매 요청이
-- statement_timeout(57014)에 걸린다. 그때는 인덱스를 지워 우회한 뒤 다시 만들지 않아
-- 57,331건이 인덱스 없이 쌓였다. 같은 일을 반복하지 않도록 순서를 파일로 강제한다 —
-- 이 마이그레이션으로 테이블을 만들고, 적재한 뒤, 202608260003으로 인덱스를 건다.
--
-- vector(768)은 모델에 묶인 숫자다. so400m 계열로 바꾸면 1152차원이 되어 컬럼 타입을
-- 바꿔야 한다. 어느 모델로 만든 벡터인지 model_name에 남겨 섞이지 않게 한다.


-- ── 사진별 ──────────────────────────────────────────────────────────
-- 검색 대상이 아니다. 평균을 다시 계산할 때와 추천 근거를 보여줄 때만 읽으므로
-- 인덱스를 걸지 않는다. 인덱스는 적재를 느리게 만들 뿐이다.
create table public.place_image_embeddings (
  id bigint generated always as identity primary key,
  content_id text not null,

  -- 장소 안에서 몇 번째 사진인가. detailImage2가 준 순서를 그대로 쓴다.
  -- TourAPI는 대표성이 높은 사진을 앞에 주는 편이어서, 앞 N장만 받는 현재
  -- 적재 방식에서 이 순서가 곧 "얼마나 대표적인가"에 가깝다.
  photo_order integer not null,

  -- 원본 주소. 파일을 저장하지 않고 이 주소로 다시 받을 수 있게 한다.
  origin_url text not null,

  -- detailImage2의 imgname. 대부분 장소명이지만, 무장애 실측 사진처럼
  -- 성격이 다른 사진이 이름으로 드러나는 경우가 있다
  -- (예: MouseRabbit_출입구자동문). 걸러낼 단서로 남긴다.
  image_name text,

  embedding vector(768) not null,
  model_name text not null,
  created_at timestamptz not null default now(),

  constraint place_image_embeddings_photo_order_positive
    check (photo_order >= 1),
  constraint place_image_embeddings_content_photo_unique
    unique (content_id, photo_order),
  constraint place_image_embeddings_content_id_fk
    foreign key (content_id)
    references public.places (content_id)
    on delete cascade
);

comment on table public.place_image_embeddings is
  '장소 사진 한 장당 한 행인 이미지 임베딩. 검색용이 아니라 장소 평균을 다시 계산하고 추천 근거를 보여주기 위한 보관용이다.';
comment on column public.place_image_embeddings.photo_order is
  'detailImage2가 준 사진 순서. TourAPI가 대표성 높은 사진을 앞에 주므로 1이 가장 대표적이다.';
comment on column public.place_image_embeddings.image_name is
  'detailImage2의 imgname. 무장애 실측 사진 등 성격이 다른 사진이 이름으로 드러나는 경우가 있어 걸러낼 단서로 남긴다.';
comment on column public.place_image_embeddings.model_name is
  '이 벡터를 만든 모델. 모델이 바뀌면 좌표계가 달라져 기존 벡터와 섞어 쓸 수 없다.';


-- ── 장소별 ──────────────────────────────────────────────────────────
-- 서비스가 실제로 읽는 테이블이다. 사진 벡터들의 평균을 다시 정규화해 담는다.
create table public.place_mood_vectors (
  content_id text primary key,

  -- 사진 벡터의 평균을 길이 1로 다시 맞춘 값. 정규화해 두면 내적이 곧 코사인
  -- 유사도가 되어 조회 때 나눗셈이 없다.
  embedding vector(768) not null,

  -- 분위기 축 점수를 미리 계산해 둔다. 발화 경로는 이 값만 있으면 벡터 연산 없이
  -- SQL 정렬로 끝난다. 축이 늘거나 줄어도 마이그레이션이 필요 없도록 jsonb로 둔다 —
  -- 지금 켠 축은 다섯이지만 mood_anchors.json에는 여덟이 들어 있고, 나중에 켤 수 있다.
  axis_scores jsonb not null default '{}'::jsonb,

  -- 평균에 쓴 사진 수. 1이면 detailImage2가 비어 대표 이미지 한 장으로 대체된
  -- 장소이고, 그런 곳은 벡터가 그 한 장에 좌우되어 불안정하다. 종로 613곳 중
  -- 170곳이 여기 해당한다.
  photo_count integer not null,

  model_name text not null,

  -- 어느 축 정의로 axis_scores를 계산했는지. 문구를 고치면 점수가 달라지므로
  -- 갱신이 필요한 행을 골라낼 수 있어야 한다.
  anchors_version text not null,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  constraint place_mood_vectors_photo_count_positive
    check (photo_count >= 1),
  constraint place_mood_vectors_content_id_fk
    foreign key (content_id)
    references public.places (content_id)
    on delete cascade
);

comment on table public.place_mood_vectors is
  '장소별 분위기 벡터. 사진 벡터의 평균을 정규화한 값이며 서비스가 조회하는 대상이다. 사진 단위 원본은 place_image_embeddings에 있다.';
comment on column public.place_mood_vectors.embedding is
  '사진 벡터 평균을 길이 1로 정규화한 값. 정규화돼 있어 내적이 곧 코사인 유사도다.';
comment on column public.place_mood_vectors.axis_scores is
  '분위기 축 점수를 미리 계산한 값. 장소벡터와 축벡터의 내적이다. 축을 켜고 끄는 일이 잦아 컬럼이 아니라 jsonb로 둔다.';
comment on column public.place_mood_vectors.photo_count is
  '평균에 쓴 사진 수. 1이면 detailImage2가 비어 대표 이미지로 대체된 장소이고 벡터가 불안정하다.';
comment on column public.place_mood_vectors.anchors_version is
  'axis_scores를 계산한 축 정의 판본. 축 문구를 고치면 점수가 달라지므로 갱신 대상을 고를 때 쓴다.';

create trigger place_mood_vectors_set_updated_at
before update on public.place_mood_vectors
for each row
execute function public.set_updated_at();


-- places와 같은 규칙이다. 클라이언트 직접 접근을 막고 FastAPI의 서버 권한으로만 쓴다.
alter table public.place_image_embeddings enable row level security;
revoke all on table public.place_image_embeddings from anon, authenticated;

alter table public.place_mood_vectors enable row level security;
revoke all on table public.place_mood_vectors from anon, authenticated;

commit;
