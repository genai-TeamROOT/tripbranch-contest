begin;

-- 무장애 정보가 있는 장소만 반경 안에서 거리순으로 찾는다.
--
-- 왜 저장소에서 찾는가. 추천 후보는 TourAPI locationBasedList2로 모으는데, 그
-- 요청에 실을 수 있는 조건이 반경과 분류 코드뿐이다. "무장애 정보가 있는 곳만"을
-- 표현할 자리가 없다. 무장애 정보는 place_barrier_free에만 있으므로(D-077),
-- 이 조건이 붙은 요청은 저장소에서 후보를 뽑는다.
--
-- 무장애 조건이 없는 요청은 이 함수를 부르지 않는다. 후보 출처가 TourAPI 실시간
-- 조회에서 저장소 스냅샷으로 바뀌는 것은 큰 변경이라, 무장애 요청에만 한정한다.
--
-- 거리 계산과 사각형 선걷어내기는 search_place_mood(202608310002)와 같은 방식이다.
-- 같은 얼개를 두 번 쓰는 것이라 상수와 순서를 그대로 맞춘다.
create or replace function public.search_places_barrier_free(
  p_latitude float,
  p_longitude float,
  p_radius_km float,
  -- 요구하는 무장애 편의. 어휘는 아래 판정 블록이 정의한 5개다.
  --
  -- **여럿이면 전부 만족해야 한다.** "유모차 끌고 갈 만한 곳"이
  -- step_free_access + infant_facilities로 오는데, 둘 다 필요하다고 말한 것이다.
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
    'step_free_access',
    'accessible_restroom',
    'accessible_parking',
    'visual_guide',
    'infant_facilities'
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

      -- ── 무장애 판정 ──────────────────────────────────────────────────────
      -- **판정은 여기 다섯 줄이 전부다.** 아래 where 절은 p_needs에 있는 것만
      -- 골라 쓰기만 한다. 흩어 놓으면 나중에 판정을 바꿀 때 다섯 군데를 고쳐야
      -- 하고, 하나를 빠뜨리면 그 묶음만 옛 규칙으로 남는다 — 오류는 나지 않고
      -- 결과만 틀린다.
      --
      -- 지금 규칙은 "값이 있으면 그 편의가 있다"이고, `불가`가 든 행만 뺀다.
      -- 명시적으로 접근 불가를 말하는 행이 6건뿐이라 이 규칙이 성립한다.
      --
      -- **`없`·`미설치` 같은 단어로 부정을 판정하지 않는다.** 접근로와 주출입구는
      -- 문장의 주어가 턱·단차라서 "없다"가 긍정이다 — `출입구까지 턱이 없어
      -- 휠체어 접근 가능함`. 단어로 거르면 894건을 잘못 버리고 4건을 맞게 버린다
      -- (2026-09-01 실측: 접근로 745행 중 494행, 주출입구 715행 중 400행이
      -- `없`을 담고 있는데 그중 진짜 부정은 0건이다).
      --
      -- 이 다섯 줄을 원문 대신 LLM 판정 컬럼으로 바꾸는 것이 TP-204다.
      (
        coalesce(b.approach_route_raw, b.entrance_access_raw, b.elevator_raw) is not null
        and coalesce(b.approach_route_raw, '') || coalesce(b.entrance_access_raw, '')
            || coalesce(b.elevator_raw, '') not like '%불가%'
      ) as has_step_free_access,
      (
        b.accessible_restroom_raw is not null
        and b.accessible_restroom_raw not like '%불가%'
      ) as has_accessible_restroom,
      (
        b.accessible_parking_raw is not null
        and b.accessible_parking_raw not like '%불가%'
      ) as has_accessible_parking,
      (
        coalesce(b.braille_block_raw, b.braille_promotion_raw, b.audio_guide_raw,
                 b.guide_dog_raw) is not null
        and coalesce(b.braille_block_raw, '') || coalesce(b.braille_promotion_raw, '')
            || coalesce(b.audio_guide_raw, '') || coalesce(b.guide_dog_raw, '')
            not like '%불가%'
      ) as has_visual_guide,
      (
        coalesce(b.stroller_rental_raw, b.nursing_room_raw, b.infant_family_etc_raw)
          is not null
        and coalesce(b.stroller_rental_raw, '') || coalesce(b.nursing_room_raw, '')
            || coalesce(b.infant_family_etc_raw, '') not like '%불가%'
      ) as has_infant_facilities
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
    nearby.distance_km
  from nearby
  where
    nearby.distance_km <= p_radius_km
    -- 요구한 것만 검사한다. 요구하지 않은 묶음은 값이 없어도 후보로 남는다.
    and (not ('step_free_access' = any(p_needs)) or nearby.has_step_free_access)
    and (not ('accessible_restroom' = any(p_needs)) or nearby.has_accessible_restroom)
    and (not ('accessible_parking' = any(p_needs)) or nearby.has_accessible_parking)
    and (not ('visual_guide' = any(p_needs)) or nearby.has_visual_guide)
    and (not ('infant_facilities' = any(p_needs)) or nearby.has_infant_facilities)
  order by nearby.distance_km
  limit p_limit;
end;
$$;

comment on function public.search_places_barrier_free is
  '무장애 편의를 요구한 요청의 후보를 반경 안에서 거리순으로 찾는다. p_needs가 여럿이면 전부 만족해야 한다. 판정은 함수 본문의 "무장애 판정" 블록 한 곳에 모여 있다.';

-- 다른 조회 함수와 같은 규칙이다. 클라이언트 직접 접근을 막고 FastAPI의 서버
-- 권한으로만 쓴다. 새 함수는 기본이 PUBLIC 실행 가능이라 여기서 닫지 않으면
-- 이 함수만 규칙에서 빠진다.
revoke execute on function public.search_places_barrier_free(
  float, float, float, text[], text, text, text, text, int
) from public, anon, authenticated;

grant execute on function public.search_places_barrier_free(
  float, float, float, text[], text, text, text, text, int
) to service_role;

commit;
