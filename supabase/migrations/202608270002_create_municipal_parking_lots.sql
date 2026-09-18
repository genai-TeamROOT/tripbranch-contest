-- 서울시 GetParkingInfo는 주소·주차장 코드만 제공한다. 주소는 동기화 시 한 번
-- 지오코딩해 저장하고, 잔여 대수/기준 시각은 조회 때 API에서 갱신한다.
create table if not exists public.municipal_parking_lots (
  parking_code text primary key,
  name text not null,
  address text,
  district text,
  latitude double precision,
  longitude double precision,
  capacity integer,
  paid boolean,
  updated_at timestamptz not null default now()
);

-- 좌표 카탈로그는 백엔드의 Secret Key로만 동기화·조회한다. anon/authenticated
-- 클라이언트에는 정책을 두지 않아 직접 접근을 기본 차단한다.
alter table public.municipal_parking_lots enable row level security;

create index if not exists municipal_parking_lots_district_idx
  on public.municipal_parking_lots (district);

create index if not exists municipal_parking_lots_coordinates_idx
  on public.municipal_parking_lots (latitude, longitude)
  where latitude is not null and longitude is not null;
