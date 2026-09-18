begin;

-- 휠체어·유모차·시각안내 판정을 원문 대신 판정 컬럼에서 읽는다.
--
-- 지금까지의 규칙은 "원문이 있으면 그 편의가 있다"였고, `불가`가 든 행만 뺐다.
-- 그래서 `출입구까지 평지로 연결되어 있으나 턱이 있어 접근 불가능한 구간 있음`
-- 같은 문장이 통과했다 — `불가`가 들어 있어도 `접근 불가능한`은 like '%불가%'에
-- 걸리지만, `계단이 있어 접근이 어려움`처럼 다른 말로 쓴 문장은 걸리지 않는다.
--
-- 원문을 사람이 하나씩 읽어 판정을 매겼고(202609020001), 여기서 그 판정을 쓴다.
-- 판정 기준은 이렇다.
--
--   possible    들어갈 수단이 있다. 리프트·보조출입구·경사로도 수단이다.
--   partial     들어가긴 하지만 못 가는 구역이 남는다.
--   impossible  아예 들어갈 수 없다.
--
-- **partial은 후보에서 빼지 않는다.** 휠체어로 들어갈 수 있는데 팔각정 하나 못
-- 간다고 추천에서 빼는 것은 과하다. 대신 판정을 함께 돌려주어 답변이 "일부 구역은
-- 접근이 어렵다"고 말할 수 있게 한다. 값을 올리지 않으면 왜 후보인지 위에서 알 수
-- 없어, partial과 possible이 같은 것이 되어 판정 작업이 통째로 버려진다.
--
-- **null은 possible이 아니다.** 판단할 원문이 없다는 뜻이라 후보에서 뺀다.
-- 지금까지의 `is not null` 검사가 하던 일과 같다.
--
-- 판정이 붙는 어휘는 셋뿐이다. 나머지 여섯(화장실·주차장·유아·대여·좌석·저상버스)은
-- 판정표를 만들지 않았으므로 지금까지의 원문 규칙을 그대로 쓴다. 판정 블록 안에
-- 두 규칙이 섞이므로 어디까지가 판정이고 어디부터가 원문인지 아래에 선을 그어 둔다.
--
-- **create or replace가 아니라 drop 후 create다.** 돌려주는 열이 늘어 반환 타입이
-- 바뀌는데, Postgres는 create or replace로 반환 타입을 바꾸지 못한다. 새로 만드는
-- 것이라 실행 권한이 초기화되므로 아래에서 revoke/grant를 다시 한다 — 빠뜨리면
-- 이 함수만 anon에게 열린 채 남는다.
--
-- 적재 순서에 주의한다. 판정 컬럼을 채우기 전에 이 함수를 바꾸면 모든 장소의
-- 판정이 null이라 후보가 하나도 나오지 않는다.
--
--   202609020001 (컬럼 추가) → scripts/import_barrier_free_verdicts.py → 이 파일

drop function if exists public.search_places_barrier_free(
  float, float, float, text[], text, text, text, text, int
);

create function public.search_places_barrier_free(
  p_latitude float,
  p_longitude float,
  p_radius_km float,
  -- 요구하는 무장애 편의. 어휘는 아래 판정 블록이 정의한 9개다.
  --
  -- **여럿이면 전부 만족해야 한다.** "유모차 끌고 갈 만한 곳"이
  -- stroller_access + infant_facilities로 오는데, 둘 다 필요하다고 말한 것이다.
  p_needs text[],
  p_content_type_id text default null,
  p_lcls_systm1 text default null,
  p_lcls_systm2 text default null,
  p_lcls_systm3 text default null,
  p_limit int default 30
)
returns table (
  content_id text,
  title text,
  address text,
  latitude float,
  longitude float,
  content_type_id text,
  lcls_systm1 text,
  lcls_systm2 text,
  lcls_systm3 text,
  first_image_url text,
  distance_km float,
  -- 판정을 함께 돌려준다. 요구한 어휘만이 아니라 셋 다 준다 — 어느 것을 안내에
  -- 쓸지는 부르는 쪽이 정하고, 어휘마다 열을 갈아 끼우면 반환 타입이 요청마다
  -- 달라져 호출부가 그때그때 다른 모양을 받게 된다.
  wheelchair_access_verdict text,
  stroller_access_verdict text,
  visual_guide_verdict text
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
  unknown_needs text[];
begin
  if p_latitude is null or p_longitude is null or p_radius_km is null then
    raise exception '좌표와 반경이 필요합니다.';
  end if;
  if p_radius_km <= 0 then
    raise exception '반경은 0보다 커야 합니다. 받은 값: %', p_radius_km;
  end if;
  if p_limit is null or p_limit <= 0 then
    raise exception 'p_limit은 0보다 커야 합니다. 받은 값: %', p_limit;
  end if;

  -- 빈 배열을 조용히 통과시키지 않는다. 통과시키면 무장애 조건이 하나도 없는
  -- 전체 반경 검색이 되는데, 이 함수를 부른 쪽은 무장애를 요구한 요청이다.
  -- 조건이 사라진 결과를 조건이 걸린 결과인 줄 알고 쓰게 된다.
  if p_needs is null or array_length(p_needs, 1) is null then
    raise exception 'p_needs가 비어 있습니다. 무장애 조건이 없으면 이 함수를 부르지 않습니다.';
  end if;

  -- 모르는 어휘도 조용히 넘기지 않는다. 호출부가 이미 걸러서 보내므로 여기 도달하면
  -- 배선이 어긋난 것이고, 무시하면 요구한 조건 하나가 빠진 결과가 나간다.
  select array_agg(need)
    into unknown_needs
  from unnest(p_needs) as need
  where need not in (
    'wheelchair_access',
    'stroller_access',
    'accessible_restroom',
    'accessible_parking',
    'visual_guide',
    'infant_facilities',
    'wheelchair_rental',
    'seating_available',
    'low_floor_transit'
  );
  if unknown_needs is not null then
    raise exception '모르는 무장애 어휘입니다: %', array_to_string(unknown_needs, ', ');
  end if;

  lat_delta := p_radius_km / 111.0;
  -- 극지방에서 0으로 나누는 것을 막는다. 서울에서는 cos(37.5) = 0.79다.
  lng_delta := p_radius_km / greatest(111.0 * cos(radians(p_latitude)), 0.000001);

  return query
  with nearby as (
    select
      p.content_id,
      p.title,
      p.address,
      p.latitude,
      p.longitude,
      p.content_type_id,
      p.lcls_systm1,
      p.lcls_systm2,
      p.lcls_systm3,
      p.first_image_url,
      -- 하버사인. 지구 반지름 6371km.
      (2 * 6371 * asin(sqrt(
        power(sin(radians(p.latitude - p_latitude) / 2), 2)
        + cos(radians(p_latitude)) * cos(radians(p.latitude))
          * power(sin(radians(p.longitude - p_longitude) / 2), 2)
      )))::float as distance_km,

      b.wheelchair_access_verdict,
      b.stroller_access_verdict,
      b.visual_guide_verdict,

      -- ── 무장애 판정 (1) 사람이 매긴 판정을 읽는 셋 ────────────────────────
      -- 원문을 여기서 읽지 않는다. 원문 판정은 사람이 이미 내렸고
      -- supabase/data/barrier_free_sentence_verdicts.csv에 있다. 여기서 다시
      -- 읽으면 같은 문장에 두 규칙이 생기고, 한쪽만 고쳤을 때 후보 수와 안내
      -- 문구가 서로 다른 근거를 갖게 된다.
      --
      -- partial을 넣는 이유는 위 머리말에 있다. null은 판단할 원문이 없다는
      -- 뜻이라 뺀다.
      (
        b.wheelchair_access_verdict is not null
        and b.wheelchair_access_verdict <> 'impossible'
      ) as has_wheelchair_access,
      (
        b.stroller_access_verdict is not null
        and b.stroller_access_verdict <> 'impossible'
      ) as has_stroller_access,
      (
        b.visual_guide_verdict is not null
        and b.visual_guide_verdict <> 'impossible'
      ) as has_visual_guide,

      -- ── 무장애 판정 (2) 아직 원문 규칙을 쓰는 여섯 ────────────────────────
      -- 이 여섯은 판정표를 만들지 않았다. 규칙은 "값이 있으면 그 편의가 있다"이고
      -- `불가`가 든 행만 뺀다.
      --
      -- **`없`·`미설치` 같은 단어로 부정을 판정하지 않는다.** 위 셋을 판정으로
      -- 옮긴 뒤에도 이 함정은 남아 있다 — 나중에 여기에 단어를 더하면 뜻이
      -- 뒤집힌다. 접근로와 주출입구가 그랬듯 주어가 무엇인지 먼저 본다.
      (
        b.accessible_restroom_raw is not null
        and b.accessible_restroom_raw not like '%불가%'
      ) as has_accessible_restroom,
      (
        b.accessible_parking_raw is not null
        and b.accessible_parking_raw not like '%불가%'
      ) as has_accessible_parking,
      (
        coalesce(b.stroller_rental_raw, b.nursing_room_raw, b.infant_family_etc_raw)
          is not null
        and coalesce(b.stroller_rental_raw, '') || coalesce(b.nursing_room_raw, '')
            || coalesce(b.infant_family_etc_raw, '') not like '%불가%'
      ) as has_infant_facilities,
      -- 휠체어 대여다. 휠체어로 들어갈 수 있는지가 아니다(TourAPI의 `wheelchair`
      -- 응답 키가 대여를 뜻한다 — 202608250002 주석 참고). 오래 걷기 힘든
      -- 동행이 있을 때 쓴다.
      (
        b.wheelchair_rental_raw is not null
        and b.wheelchair_rental_raw not like '%불가%'
      ) as has_wheelchair_rental,
      -- 의자식(입식) 테이블. 좌식이 아니라 의자에 앉는다는 뜻이라, 바닥에 앉기
      -- 힘든 동행이 있을 때 쓴다.
      (b.disability_etc_raw ~ '의자식|입식') as has_seating,
      -- 저상버스·지하철역 엘리베이터 안내. 대중교통으로 닿기 쉬운지를 말한다.
      (b.public_transport_raw ~ '저상버스|엘리베이터') as has_low_floor_transit
      -- ── 판정 끝 ──────────────────────────────────────────────────────────
    from public.places p
    -- inner join이다. 무장애 행이 없는 장소는 애초에 후보가 아니다.
    join public.place_barrier_free b on b.content_id = p.content_id
    where
      p.is_active is true
      and p.latitude is not null
      and p.longitude is not null
      -- 사각형으로 먼저 걷어낸다. 아래 하버사인이 최종 판정이다.
      and p.latitude between p_latitude - lat_delta and p_latitude + lat_delta
      and p.longitude between p_longitude - lng_delta and p_longitude + lng_delta
      and (p_content_type_id is null or p.content_type_id = p_content_type_id)
      and (p_lcls_systm1 is null or p.lcls_systm1 = p_lcls_systm1)
      and (p_lcls_systm2 is null or p.lcls_systm2 = p_lcls_systm2)
      and (p_lcls_systm3 is null or p.lcls_systm3 = p_lcls_systm3)
  )
  select
    nearby.content_id,
    nearby.title,
    nearby.address,
    nearby.latitude,
    nearby.longitude,
    nearby.content_type_id,
    nearby.lcls_systm1,
    nearby.lcls_systm2,
    nearby.lcls_systm3,
    nearby.first_image_url,
    nearby.distance_km,
    nearby.wheelchair_access_verdict,
    nearby.stroller_access_verdict,
    nearby.visual_guide_verdict
  from nearby
  where
    nearby.distance_km <= p_radius_km
    -- 요구한 것만 검사한다. 요구하지 않은 묶음은 값이 없어도 후보로 남는다.
    and (not ('wheelchair_access' = any(p_needs)) or nearby.has_wheelchair_access)
    and (not ('stroller_access' = any(p_needs)) or nearby.has_stroller_access)
    and (not ('accessible_restroom' = any(p_needs)) or nearby.has_accessible_restroom)
    and (not ('accessible_parking' = any(p_needs)) or nearby.has_accessible_parking)
    and (not ('visual_guide' = any(p_needs)) or nearby.has_visual_guide)
    and (not ('infant_facilities' = any(p_needs)) or nearby.has_infant_facilities)
    and (not ('wheelchair_rental' = any(p_needs)) or nearby.has_wheelchair_rental)
    and (not ('seating_available' = any(p_needs)) or nearby.has_seating)
    and (not ('low_floor_transit' = any(p_needs)) or nearby.has_low_floor_transit)
  order by nearby.distance_km
  limit p_limit;
end;
$$;

comment on function public.search_places_barrier_free is
  '무장애 편의를 요구한 요청의 후보를 반경 안에서 거리순으로 찾는다. 어휘 9개이고 p_needs가 여럿이면 전부 만족해야 한다. 휠체어·유모차·시각안내는 사람이 매긴 판정 컬럼을 읽고, 나머지 여섯은 아직 원문 규칙을 쓴다. partial은 후보로 남기고 판정을 함께 돌려주어 안내에 쓴다.';

-- drop 후 create라 실행 권한이 초기화됐다. 다른 조회 함수와 같은 규칙으로 다시
-- 닫는다 — 클라이언트 직접 접근을 막고 FastAPI의 서버 권한으로만 쓴다. 새 함수는
-- 기본이 PUBLIC 실행 가능이라 여기서 닫지 않으면 이 함수만 규칙에서 빠진다.
revoke execute on function public.search_places_barrier_free(
  float, float, float, text[], text, text, text, text, int
) from public, anon, authenticated;

grant execute on function public.search_places_barrier_free(
  float, float, float, text[], text, text, text, text, int
) to service_role;

commit;
