begin;

-- 무장애 여행 정보(KorWithService2/detailWithTour2)를 장소별로 캐시한다(D-077).
--
-- places에 컬럼을 더하지 않고 테이블을 나눈 이유는 세 가지다.
--   1. 응답 필드가 28개인데 대부분 비어 있다. 5% 컷을 넘긴 15개만 담아도
--      places가 39 → 54컬럼이 된다.
--   2. 무장애 레코드가 있는 장소 자체가 적다. 4개 구 2,570건 중 496건(19%)이고,
--      그중 실제로 항목이 채워진 곳은 436건, 숙박(32)을 뺀 적재 대상은 427건이다.
--      places 행의 대부분이 전부 null이 된다.
--   3. 동기화 계보가 다르다. 대상 목록이 KorWithService2/areaBasedList2라는 다른
--      엔드포인트에서 오므로, places.detail_fetch_status(detailIntro2 조회 상태)에
--      얹으면 한 컬럼이 서로 다른 두 조회를 뜻하게 된다.
--
-- 컬럼 이름은 응답 키가 아니라 의미로 짓는다. 응답 키를 그대로 쓰면 두 필드가
-- 이름과 반대로 읽힌다 — `wheelchair`는 휠체어 출입이 아니라 **대여**이고,
-- `exit`는 출구가 아니라 **주출입구**다.
--
-- 채움률은 2026-08-25 실측이다(4개 구 무장애 등록 496건에서 숙박 69건을 뺀 427건).
-- 숙박 전용 필드였던 `room`(장애인 객실, 숙박 69건 중 42건)은 숙박을 관광 대상에서
-- 제외하기로 해 담지 않는다.
create table public.place_barrier_free (
  content_id text primary key,

  -- 휠체어 접근. 두 필드를 함께 읽어야 한다 — 접근로와 출입구를 나눈 필드인데
  -- 작성자가 뒤바꿔 넣은 사례가 있다(가나아트센터: approach에 출입구 서술,
  -- entrance에 접근로 서술). 한쪽만 보고 판정하면 그 장소를 놓친다.
  approach_route_raw text,      -- route 64.9%: 도로·주차장에서 출입문 앞까지
  entrance_access_raw text,     -- exit 62.1%: 주출입구의 단차·경사로·문 종류
  elevator_raw text,            -- elevator 42.2%

  accessible_restroom_raw text, -- restroom 52.2%: 장애인 화장실
  accessible_parking_raw text,  -- parking 47.1%: 장애인 주차구역

  -- 시각장애인 편의.
  braille_block_raw text,       -- braileblock 19.7% (응답 키의 철자가 이렇다)
  braille_promotion_raw text,   -- brailepromotion 10.5%
  audio_guide_raw text,         -- audioguide 9.6%
  guide_dog_raw text,           -- helpdog 9.1%

  -- 대여·동반.
  wheelchair_rental_raw text,   -- wheelchair 16.9%: 출입이 아니라 대여다
  stroller_rental_raw text,     -- stroller 13.6%
  nursing_room_raw text,        -- lactationroom 12.4%: 수유실
  infant_family_etc_raw text,   -- infantsfamilyetc 13.1%: 기저귀교환대·어린이실

  public_transport_raw text,    -- publictransport 13.6%: 저상버스·역 엘리베이터
  disability_etc_raw text,      -- handicapetc 22.2%: 음식점 88건 중 63건

  -- 이 장소를 확인한 시각. 행이 있다는 것 자체가 "무장애 목록에 있어서 불러봤다"는
  -- 뜻이고, 값이 전부 비어 있으면 "불러봤더니 항목이 비어 있더라"는 뜻이다.
  -- 후자가 4개 구에서 60건인데(전부 쇼핑몰 입점 매장, 2022·2024년 일괄 등록),
  -- 그 행을 남기지 않으면 실행할 때마다 같은 빈 응답을 다시 받게 된다.
  --
  -- 목록에 없는 장소는 행을 만들지 않는다. 없다는 사실은 목록 조회가 매번
  -- 알려주므로 저장할 이유가 없다 — 종로구에서 그런 행이 590개였다.
  fetched_at timestamptz not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  constraint place_barrier_free_content_id_fk
    foreign key (content_id)
    references public.places (content_id)
    on delete cascade
);

comment on table public.place_barrier_free is
  'KorWithService2/detailWithTour2 무장애 정보 원문 캐시. places와 1:1이며 무장애 목록에 등록된 장소만 값이 있다.';

comment on column public.place_barrier_free.approach_route_raw is
  'route: 도로·주차장에서 출입문 앞까지의 접근로. entrance_access_raw와 뒤바뀐 사례가 있어 휠체어 접근 판정은 두 값을 함께 읽는다.';
comment on column public.place_barrier_free.entrance_access_raw is
  'exit: 주출입구의 단차·경사로·문 종류. 출구가 아니다.';
comment on column public.place_barrier_free.wheelchair_rental_raw is
  'wheelchair: 휠체어 대여 여부다. 휠체어 출입 가능 여부가 아니다.';
comment on column public.place_barrier_free.accessible_restroom_raw is
  'restroom: 장애인 화장실. 일반 화장실은 places.restroom_raw(detailIntro2)가 따로 담는다.';
comment on column public.place_barrier_free.public_transport_raw is
  'publictransport: 저상버스·역 엘리베이터 안내. 원문에 <br/> 태그가 섞여 있다.';
comment on column public.place_barrier_free.fetched_at is
  '이 장소를 확인한 시각. 행의 존재가 곧 "무장애 목록에 있어 조회했다"는 뜻이고, 값이 전부 비면 항목이 미입력이라는 뜻이다.';

create trigger place_barrier_free_set_updated_at
before update on public.place_barrier_free
for each row
execute function public.set_updated_at();

-- places와 같은 규칙이다. 클라이언트 직접 접근을 막고 FastAPI의 서버 권한으로만 쓴다.
alter table public.place_barrier_free enable row level security;
revoke all on table public.place_barrier_free from anon, authenticated;

commit;
