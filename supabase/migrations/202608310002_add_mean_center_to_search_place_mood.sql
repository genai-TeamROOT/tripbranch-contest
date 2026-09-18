begin;

-- 사진 검색에 평균 빼기를 더한다(D-115).
--
-- 왜. 사진 경로의 실패 11건이 전부 "종류·형태·구도는 맞고 분위기만 다름"이었다.
-- 능인선원(실내 불상)에 성덕사(한옥 외관), 어수선한 주택가 골목에 정돈된 익선동
-- 한옥거리가 나오는 식이다. 갈린 차원이 정확히 weathered·calm 축이 재는 것인데,
-- 사진 경로는 축을 쓰지 않고 벡터를 통째로 비교한다. 코사인 유사도는 768개
-- 차원을 똑같이 취급하므로 "이것이 무슨 장면인가"라는 큰 신호에 분위기 차이가
-- 묻힌다. 그 큰 신호가 곧 전체 평균이라 빼면 분위기 차이가 드러난다.
--
-- 사람 눈가림 채점으로 48.2% → 53.2%였고, 교체된 자리만 보면 빠진 곳 27.5% 대
-- 들어온 곳 45.0%다(p = 0.0812). 유의성이 0.05 언저리라 표본을 늘려 다시 봐야
-- 하지만, 방향이 세 시험에서 모두 같고 나빠진 지표가 없으며 되돌리는 비용이
-- 설정 하나라 켜 둔다.
--
-- **중심을 테이블에 저장해 둔다.** 매 요청마다 avg()로 구하면 1,314ms가 드는데
-- 저장해 두면 184ms다(빼지 않을 때 60ms). 미리 뺀 컬럼을 따로 두면 60ms를
-- 유지할 수 있지만 원본과 두 벌이 되어 적재 때마다 어긋날 수 있어 두지 않았다.
-- 전체 훑기라 장소 수에 정비례하므로, 응답이 문제가 되면 그때 옮긴다.
--
-- **발화 경로(축 점수)는 건드리지 않는다.** 축은 이미 계산돼 axis_scores에
-- 저장돼 있고, 축 점수는 방향과의 내적이라 중심을 빼면 값의 의미가 달라진다.

create table if not exists public.place_mood_center (
  -- 한 행만 둔다. 여러 벌을 두면 어느 것으로 검색했는지 결과만 보고 알 수 없다.
  id smallint primary key default 1 check (id = 1),
  embedding vector(768) not null,
  -- 어떤 표본에서 나온 중심인지. 적재 후 갱신을 잊으면 이 값이 실제 장소 수와
  -- 어긋나므로, 운영에서 갱신 누락을 잡는 단서가 된다.
  place_count int not null,
  updated_at timestamptz not null default now()
);

comment on table public.place_mood_center is
  '사진 검색에서 빼는 전체 평균 벡터(D-115). 적재 후 refresh_place_mood_center()로 갱신한다.';

-- place_mood_vectors와 같은 규칙이다. 클라이언트 직접 접근을 막고 FastAPI의
-- 서버 권한으로만 쓴다. 새 테이블은 기본이 열린 상태라 여기서 닫지 않으면
-- 이 테이블만 규칙에서 빠진다.
alter table public.place_mood_center enable row level security;
revoke all on table public.place_mood_center from anon, authenticated;

-- 적재 후 부르는 함수. 인자가 없고 멱등이다.
create or replace function public.refresh_place_mood_center()
returns table (place_count int, updated_at timestamptz)
language plpgsql
security definer
set search_path = ''
set statement_timeout = '60s'
as $$
declare
  -- search_path가 비어 있어 본문에서는 타입도 스키마를 붙여야 한다.
  v_center public.vector(768);
  v_count int;
begin
  -- avg도 뺄셈도 pgvector가 준 것이라 스키마를 붙인다. <=>를 이미 그렇게
  -- 쓰고 있는 것과 같은 이유다.
  select (public.avg(v.embedding))::public.vector(768), count(*)
    into v_center, v_count
  from public.place_mood_vectors v;

  if v_count = 0 then
    raise exception '적재된 장소 벡터가 없어 중심을 만들 수 없습니다.';
  end if;

  insert into public.place_mood_center (id, embedding, place_count, updated_at)
  values (1, v_center, v_count, now())
  on conflict (id) do update
    set embedding = excluded.embedding,
        place_count = excluded.place_count,
        updated_at = excluded.updated_at;

  return query
  select c.place_count, c.updated_at from public.place_mood_center c where c.id = 1;
end;
$$;

-- 갱신은 쓰기이고 5,465곳을 훑어 1.3초쯤 걸린다. 조회 함수들과 같은 규칙으로
-- 서버 권한에만 연다.
revoke execute on function public.refresh_place_mood_center()
  from public, anon, authenticated;
grant execute on function public.refresh_place_mood_center() to service_role;

-- 지금 적재된 5,465곳으로 첫 중심을 만든다.
select public.refresh_place_mood_center();

create or replace function public.search_place_mood(
  p_query_embedding vector(768),
  p_candidate_content_ids text[] default null,
  p_match_count int default 10,
  p_min_similarity float default 0.0,
  p_latitude float default null,
  p_longitude float default null,
  p_radius_km float default null,
  -- true면 질의와 장소 벡터에서 각각 전체 평균을 빼고 비교한다(D-115).
  -- 기본을 false로 둔 이유는 이 인자를 모르는 기존 호출이 동작을 바꾸지 않게
  -- 하려는 것이다 — 켜고 끄는 판단은 호출부(설정)가 한다.
  p_mean_center boolean default false
)
returns table (
  content_id text,
  similarity float,
  axis_scores jsonb,
  photo_count int,
  distance_km float
)
language plpgsql
stable
security definer
set search_path = ''
set statement_timeout = '30s'
as $$
declare
  lat_delta float;
  lng_delta float;
  -- search_path가 비어 있어 본문에서는 타입도 스키마를 붙여야 한다.
  v_center public.vector(768);
  v_query public.vector(768);
begin
  if coalesce(array_length(p_candidate_content_ids, 1), 0) > 500 then
    raise exception
      '후보 content_id가 %건입니다. 500건 이하로 좁혀서 호출하세요.',
      array_length(p_candidate_content_ids, 1);
  end if;

  -- 좌표를 줬으면 반경도 있어야 한다. 하나만 오면 조용히 전체를 훑게 되는데,
  -- 그건 호출부가 의도한 적 없는 동작이다.
  if (p_latitude is null) <> (p_longitude is null)
     or (p_latitude is not null and p_radius_km is null) then
    raise exception '좌표와 반경은 함께 주어야 합니다.';
  end if;

  if p_latitude is not null then
    lat_delta := p_radius_km / 111.0;
    -- 극지방에서 0으로 나누는 것을 막는다. 서울에서는 cos(37.5) = 0.79다.
    lng_delta := p_radius_km / greatest(111.0 * cos(radians(p_latitude)), 0.000001);
  end if;

  if p_mean_center then
    select c.embedding into v_center from public.place_mood_center c where c.id = 1;
    -- 조용히 빼지 않은 결과를 주지 않는다. 중심이 없는데 결과가 나오면 켠 줄
    -- 알고 쓰는데 실제로는 옛 순위라, 왜 좋아지지 않는지 추적할 수 없다.
    if v_center is null then
      raise exception
        '평균 벡터가 없습니다. refresh_place_mood_center()를 먼저 실행하세요.';
    end if;
    v_query := (p_query_embedding operator(public.-) v_center)::public.vector(768);
  else
    v_query := p_query_embedding;
  end if;

  return query
  with nearby as (
    select
      v.content_id,
      -- 여기서 한 번만 빼고 아래에서는 이 값을 쓴다. 정렬과 필터가 같은 식을
      -- 두 번 계산하면 훑는 비용이 그대로 두 배가 된다.
      case when p_mean_center
           then (v.embedding operator(public.-) v_center)::public.vector(768)
           else v.embedding
      end as embedding,
      v.axis_scores,
      v.photo_count,
      case
        when p_latitude is null then null::float
        else
          -- 하버사인. 지구 반지름 6371km.
          2 * 6371 * asin(sqrt(
            power(sin(radians(p.latitude - p_latitude) / 2), 2)
            + cos(radians(p_latitude)) * cos(radians(p.latitude))
              * power(sin(radians(p.longitude - p_longitude) / 2), 2)
          ))
      end as distance_km
    from public.place_mood_vectors v
    join public.places p on p.content_id = v.content_id
    where
      p.is_active is true
      and (p_candidate_content_ids is null
           or v.content_id = any(p_candidate_content_ids))
      and (
        p_latitude is null
        or (
          -- 사각형으로 먼저 걷어낸다. 아래 하버사인이 최종 판정이다.
          p.latitude between p_latitude - lat_delta and p_latitude + lat_delta
          and p.longitude between p_longitude - lng_delta and p_longitude + lng_delta
        )
      )
  )
  select
    nearby.content_id,
    (1 - (nearby.embedding operator(public.<=>) v_query))::float as similarity,
    nearby.axis_scores,
    nearby.photo_count,
    nearby.distance_km
  from nearby
  where
    (p_latitude is null or nearby.distance_km <= p_radius_km)
    and (1 - (nearby.embedding operator(public.<=>) v_query)) >= p_min_similarity
  order by nearby.embedding operator(public.<=>) v_query
  limit p_match_count;
end;
$$;

-- 인자가 하나 늘어 시그니처가 바뀌었다. 옛 함수를 남겨 두면 인자 수에 따라
-- 어느 쪽이 불릴지 헷갈리므로 지운다 — 호출부는 이 저장소 안에만 있다.
drop function if exists public.search_place_mood(
  vector, text[], int, float, float, float, float
);

-- **인자가 늘면 새 함수라 권한을 물려받지 못한다.** create or replace가 아니라
-- 새로 만드는 것과 같아 기본값(PUBLIC 실행 가능)이 붙는다. 여기서 다시 닫지
-- 않으면 지금까지 service_role에만 열려 있던 조회가 익명에게 열린다.
revoke execute on function public.search_place_mood(
  vector, text[], int, float, float, float, float, boolean
) from public, anon, authenticated;

grant execute on function public.search_place_mood(
  vector, text[], int, float, float, float, float, boolean
) to service_role;

commit;
