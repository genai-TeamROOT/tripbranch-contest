begin;

-- search_place_mood에 반경 검색을 더한다. 후보 목록 대신 좌표와 반경으로 좁힌다.
--
-- 왜 필요한가. 지금은 호출부가 후보 content_id를 만들어 넘기는데, 그 목록이
-- TourAPI 상세 조회를 거쳐 오느라 최대 20곳이다(MAX_RECOMMENDATION_CANDIDATE_LIMIT).
-- 2,009곳을 적재해 두고 20곳 안에서만 고르는 셈이라, 어떤 사진을 올려도 같은
-- 대여섯 곳이 순서만 바뀐다.
--
-- 사진 유사도는 DB 안에서 끝나 사실상 공짜다. 반경 안 전부를 여기서 줄 세우고
-- 상위 N곳만 호출부가 상세를 확인하면, 비싼 조회를 "어차피 보여줄 곳"에만 쓴다.
--
-- 거리는 하버사인을 직접 계산한다. PostGIS가 설치돼 있지 않고, places.latitude/
-- longitude가 double precision이라 이 정도면 충분하다 — 반경 몇 km 안에서 오차가
-- 미터 단위다.
--
-- 1차로 위경도 사각형(bounding box)으로 먼저 걷어낸다. 하버사인은 행마다
-- 삼각함수를 네 번 부르므로, 반경 밖이 확실한 행을 산술 비교로 미리 빼면
-- 계산량이 크게 준다. 위도 1도는 약 111km로 고정이고, 경도 1도는 위도에 따라
-- 줄어 cos(위도)를 곱한다.
--
-- p_candidate_content_ids도 그대로 둔다. 좌표 없이 후보만 넘기는 기존 호출
-- (D-096)이 계속 동작해야 한다 — 둘 다 주면 교집합이고, 좌표만 주면 반경,
-- 후보만 주면 그 목록이다.
create or replace function public.search_place_mood(
  p_query_embedding vector(768),
  p_candidate_content_ids text[] default null,
  p_match_count int default 10,
  p_min_similarity float default 0.0,
  p_latitude float default null,
  p_longitude float default null,
  p_radius_km float default null
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

  return query
  with nearby as (
    select
      v.content_id,
      v.embedding,
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
    (1 - (nearby.embedding operator(public.<=>) p_query_embedding))::float as similarity,
    nearby.axis_scores,
    nearby.photo_count,
    nearby.distance_km
  from nearby
  where
    (p_latitude is null or nearby.distance_km <= p_radius_km)
    and (1 - (nearby.embedding operator(public.<=>) p_query_embedding))
        >= p_min_similarity
  order by nearby.embedding operator(public.<=>) p_query_embedding
  limit p_match_count;
end;
$$;

-- 반환 컬럼이 늘어 시그니처가 바뀌었다. 옛 함수를 남겨 두면 인자 수에 따라
-- 어느 쪽이 불릴지 헷갈리므로 지운다 — 호출부는 이 저장소 안에만 있다.
drop function if exists public.search_place_mood(vector, text[], int, float);

revoke execute on function public.search_place_mood(
  vector, text[], int, float, float, float, float
) from public, anon, authenticated;

grant execute on function public.search_place_mood(
  vector, text[], int, float, float, float, float
) to service_role;

commit;
