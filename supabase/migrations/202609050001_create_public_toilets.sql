-- 서울시 mgisToiletPoi(공중화장실 위치정보)는 지역 필터 파라미터가 없어 매 조회마다
-- 전체 4,400여 건을 받아야 한다. 실시간성이 없는 위치 목록이라(적재주기 "비정기")
-- 동기화 때 한 번 적재하고, 조회는 이 표에서만 한다.
-- 좌표가 원본에 이미 들어 있어 주차장과 달리 지오코딩 단계가 없다.
create table if not exists public.public_toilets (
  toilet_id text primary key,
  name text,
  address_new text,
  address_old text,
  latitude double precision not null,
  longitude double precision not null,
  district text,
  tel text,
  -- 공공개방 / 민간개방. 민간개방은 건물주가 시민에게 열어준 화장실이라
  -- 개방시간이 건물 영업시간을 따라가는 경우가 많다.
  open_type text,
  -- 원문 표기를 그대로 둔다. `상시(24시간)`·`정시(09:00~18:00)`·`기타|05:00~익일01:00`
  -- 처럼 형식이 제각각이고 11%는 자동 해석이 안 되므로, 해석은 조회 시점에 하고
  -- 실패하면 이 원문을 사용자에게 보여준다. 파서를 고쳐도 재적재가 필요 없다.
  open_hours_raw text,
  restroom_status text,
  accessible_status text,
  amenities text,
  safety_signs text,
  location_type text,
  manager text,
  updated_at timestamptz not null default now()
);

-- 좌표 카탈로그는 백엔드의 Secret Key로만 동기화·조회한다. anon/authenticated
-- 클라이언트에는 정책을 두지 않아 직접 접근을 기본 차단한다.
alter table public.public_toilets enable row level security;

create index if not exists public_toilets_district_idx
  on public.public_toilets (district);

-- "근처 화장실"은 좌표 바운딩 박스로 1차 추린 뒤 파이썬에서 정확한 거리로 정렬한다.
create index if not exists public_toilets_coordinates_idx
  on public.public_toilets (latitude, longitude);
