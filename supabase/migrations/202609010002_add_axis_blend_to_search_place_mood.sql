begin;

-- 사진 검색 순위에 분위기 축을 섞는다(TP-206).
--
-- 왜. 사진 경로는 768차원 벡터를 통째로 비교해 순위를 매기고, axis_scores는
-- 돌려주기는 하지만 정렬에 쓰지 않는다. 그런데 사람이 전원 "아니다"라고 한 11건이
-- 모두 축이 가르는 차원에서 갈렸다 — 어수선한 골목에 정돈된 익선동 한옥거리,
-- 밝은 상권에 낡은 공장지대, 어두운 룸에 밝은 연습실이 나오는 식이다.
--
-- 코사인 유사도가 768개 차원을 똑같이 취급하는 것이 원인이다. "좁은 길이다"에
-- 동의하는 차원이 수백 개인데 "어수선하다/정돈됐다"를 말하는 차원은 몇 개뿐이라
-- 묻힌다. 축 5개는 "이 방향이 분위기다"라고 손으로 찍어 둔 방향이므로 따로 보면
-- 묻히지 않는다.
--
-- **순위로 바꿔 섞는다.** 두 값의 눈금이 다르다 — 실측에서 유사도는 폭이 1.253,
-- 축 거리는 0.662로 두 배 가까이 차이가 난다. 그냥 더하면 가중치 0.5가 반반이
-- 아니라 유사도 쪽이 두 배 세게 먹힌다. 순위로 바꾸면 가중치가 "몇 대 몇"으로
-- 곧이곧대로 읽히고 이상치에도 흔들리지 않는다.

-- 축 방향 벡터를 둘 자리. 지금은 어디에도 없어서 올린 사진의 축 점수를 계산할
-- 방법이 없다 — 장소 쪽은 적재할 때 미리 계산해 axis_scores에 넣어 두었다.
--
-- **벡터를 이 마이그레이션에 박지 않는다.** 적재 스크립트가 mood_anchors.json을
-- 읽어 anchors_version을 만들고 있으므로, 같은 파일로 이 표도 채우게 해 출처를
-- 하나로 둔다. 박아 두면 축 문구가 바뀔 때 두 곳이 어긋난다.
create table if not exists public.place_mood_axes (
  name text primary key,
  embedding vector(768) not null,
  -- 켜진 축만 순위에 쓴다. 정의는 여덟 개인데 다섯 개만 켜져 있다.
  enabled boolean not null default false,
  positive_text text not null,
  negative_text text not null,
  positive_label text,
  negative_label text,
  -- 어느 판본의 축인지. place_mood_vectors.anchors_version과 같은 값이어야 한다 —
  -- 다르면 장소의 axis_scores와 여기 벡터가 서로 다른 정의에서 나온 것이다.
  anchors_version text not null,
  updated_at timestamptz not null default now()
);

comment on table public.place_mood_axes is
  '분위기 축의 방향 벡터(TP-206). 적재 스크립트가 mood_anchors.json으로 채운다.';

-- place_mood_vectors와 같은 규칙이다. 클라이언트 직접 접근을 막고 FastAPI의
-- 서버 권한으로만 쓴다.
alter table public.place_mood_axes enable row level security;
revoke all on table public.place_mood_axes from anon, authenticated;

create or replace function public.search_place_mood(
  p_query_embedding vector(768),
  p_candidate_content_ids text[] default null,
  p_match_count int default 10,
  p_min_similarity float default 0.0,
  p_latitude float default null,
  p_longitude float default null,
  p_radius_km float default null,
  p_mean_center boolean default false,
  -- 벡터 유사도와 축 거리를 섞는 비율(TP-206). 1.0이면 지금과 같이 유사도만 본다.
  -- 0.5면 반반이다. 0은 허용하지 않는다 — 축이 다섯 개뿐이라 "애초에 같은 종류인가"를
  -- 구분하지 못한다.
  --
  -- 기본을 1.0으로 둔 이유는 p_mean_center를 false로 둔 것과 같다. 이 인자를 모르는
  -- 기존 호출이 동작을 바꾸지 않아야 하고, 켜고 끄는 판단은 호출부(설정)가 한다.
  p_axis_weight float default 1.0
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
  v_axis_names text[];
  v_query_axis float[];
begin
  if coalesce(array_length(p_candidate_content_ids, 1), 0) > 500 then
    raise exception
      '후보 content_id가 %건입니다. 500건 이하로 좁혀서 호출하세요.',
      array_length(p_candidate_content_ids, 1);
  end if;

  if p_axis_weight <= 0.0 or p_axis_weight > 1.0 then
    raise exception
      'p_axis_weight는 0 초과 1 이하여야 합니다(받은 값 %). 1.0이면 축을 쓰지 않습니다.',
      p_axis_weight;
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

  if p_axis_weight < 1.0 then
    -- 올린 사진의 축 점수를 여기서 만든다. 장소 쪽은 적재할 때 미리 계산해
    -- axis_scores에 들어 있다.
    --
    -- **평균을 빼지 않은 원본으로 잰다.** 축 점수는 방향과의 내적이고, 장소의
    -- axis_scores도 원본으로 계산한 값이다. 한쪽만 중심을 빼면 두 값이 서로 다른
    -- 기준이 되어 비교가 무의미해진다.
    select array_agg(a.name order by a.name),
           array_agg((p_query_embedding operator(public.<#>) a.embedding) * -1
                     order by a.name)
      into v_axis_names, v_query_axis
    from public.place_mood_axes a
    where a.enabled is true;

    -- 조용히 축 없이 돌지 않는다. 섞으라고 했는데 섞이지 않은 결과가 나오면
    -- 왜 순위가 그대로인지 추적할 수 없다.
    if v_axis_names is null then
      raise exception
        '켜진 분위기 축이 없습니다. place_mood_axes를 먼저 채우세요.';
    end if;
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
  ),
  scored as (
    -- **거르기를 순위 매기기 전에 끝낸다.** rank()를 먼저 매기면 반경 밖 장소가
    -- 순위 자리를 차지해, 남는 후보들의 두 순위가 서로 다른 만큼 밀린다. 섞는
    -- 값이 그만큼 뒤틀린다.
    select
      nearby.*,
      (1 - (nearby.embedding operator(public.<=>) v_query))::float as sim,
      case when p_axis_weight = 1.0 then 0.0
      else
        -- 켜진 축마다 |올린 사진 점수 − 장소 점수|를 더한다. 축끼리 무게는
        -- 같게 둔다 — 다르게 두면 손잡이가 다섯 개 더 생겨 무엇이 효과였는지
        -- 갈라낼 수 없다(TP-206에서 별건으로 뺐다).
        (select coalesce(sum(abs(
                  v_query_axis[i]
                  - coalesce((nearby.axis_scores ->> v_axis_names[i])::float, 0.0)
                )), 0.0)
         from generate_subscripts(v_axis_names, 1) i)
      end as axis_dist
    from nearby
    where
      (p_latitude is null or nearby.distance_km <= p_radius_km)
      and (1 - (nearby.embedding operator(public.<=>) v_query)) >= p_min_similarity
  ),
  ranked as (
    select
      scored.*,
      -- **순위로 바꿔 섞는다.** 두 값의 눈금이 달라 그냥 더하면 가중치가
      -- 곧이곧대로 읽히지 않는다. 위 모듈 주석 참고.
      rank() over (order by scored.sim desc) as sim_rank,
      rank() over (order by scored.axis_dist asc) as axis_rank
    from scored
  )
  select
    ranked.content_id,
    ranked.sim as similarity,
    ranked.axis_scores,
    ranked.photo_count,
    ranked.distance_km
  from ranked
  order by
    p_axis_weight * ranked.sim_rank + (1.0 - p_axis_weight) * ranked.axis_rank,
    -- 섞은 값이 같을 때는 유사도가 높은 쪽을 앞에 둔다. 정하지 않으면 같은
    -- 질의가 실행할 때마다 다른 순서를 낸다.
    ranked.sim desc
  limit p_match_count;
end;
$$;

-- 인자가 하나 늘어 시그니처가 바뀌었다. 옛 함수를 남겨 두면 인자 수에 따라
-- 어느 쪽이 불릴지 헷갈리므로 지운다 — 호출부는 이 저장소 안에만 있다.
drop function if exists public.search_place_mood(
  vector, text[], int, float, float, float, float, boolean
);

-- **인자가 늘면 새 함수라 권한을 물려받지 못한다.** create or replace가 아니라
-- 새로 만드는 것과 같아 기본값(PUBLIC 실행 가능)이 붙는다. 여기서 다시 닫지
-- 않으면 지금까지 service_role에만 열려 있던 조회가 익명에게 열린다.
revoke execute on function public.search_place_mood(
  vector, text[], int, float, float, float, float, boolean, float
) from public, anon, authenticated;

grant execute on function public.search_place_mood(
  vector, text[], int, float, float, float, float, boolean, float
) to service_role;

commit;
