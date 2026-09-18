begin;

-- 추천 카드에 주차·요금·썸네일을 노출하기 위해 detailIntro2와 목록 응답의 원문을
-- 추가로 보존한다. 외부 호출은 늘지 않는다 — place_sync는 이미 장소마다
-- detailIntro2를 부르고 있고(운영시간·휴무일 용도) 그 응답에서 더 읽기만 한다.
-- 썸네일은 areaBasedList2 목록 응답에 이미 들어 있어 상세조회조차 필요 없다.
--
-- 별도 테이블로 분리하지 않는 이유: 출처(detailIntro2)·관계(1:1)·갱신 주기
-- (detail_fetched_at TTL)가 operating_hours_raw와 동일하다. 분리하면 수명주기
-- 컬럼을 복제하거나 두 테이블의 신선도가 어긋난다.
--
-- 기존 _raw 접미사 관습을 따른다. 정규화하지 않은 API 원문을 그대로 담고, 파서가
-- 바뀌어도 API 재호출 없이 재처리할 수 있게 한다(places 테이블 주석 참고).

-- detailIntro2의 주차 필드는 contenttypeid마다 이름이 다르다. 하나로 모은다.
--   12 관광지 parking / 14 문화시설 parkingculture / 32 숙박 parkinglodging
--   38 쇼핑 parkingshopping / 39 음식점 parkingfood / 28 레포츠 parkingleports
-- 15 축제에는 주차 필드가 아예 없다(2026-08-08 실측). 종로구 844건 중 38건이
-- 해당해 이 컬럼은 최대 806건까지만 채워진다.
alter table public.places
  add column if not exists parking_info_raw text;

-- 주차비는 문화시설(parkingfee)과 레포츠(parkingfeeleports)에만 있다. 이용요금과
-- 성격이 달라 한 컬럼에 합치지 않는다 — 같은 장소에서 주차비 '무료', 입장료
-- '3,000원'이 동시에 나온다.
alter table public.places
  add column if not exists parking_fee_raw text;

-- 이용요금: 14 문화시설 usefee / 28 레포츠 usefeeleports / 15 축제 usetimefestival.
--
-- 축제의 요금 필드명이 usetimefestival이다. 이름은 시간처럼 보이지만 내용은 요금이라
-- 운영시간으로 읽으면 영업시간 자리에 '5,000원'이 들어간다. real_place.py의
-- _OPERATING_HOURS_KEYS가 이 키를 일부러 제외하고 축제는 playtime을 쓰는 이유이므로,
-- 요금 매핑을 추가할 때 그 구분을 깨지 않아야 한다.
--
-- 12 관광지·32 숙박·38 쇼핑에는 요금 필드가 없다. 요금이 detailCommon2의 overview
-- 산문에 섞여 있어 별도 파싱이나 수동 보강이 필요하다. 이 컬럼이 채워지는 건 종로구
-- 844건 중 204건(24%)뿐이다.
alter table public.places
  add column if not exists use_fee_raw text;

-- 할인정보: 14 discountinfo / 15 discountinfofestival / 39 discountinfofood.
alter table public.places
  add column if not exists discount_info_raw text;

-- 이미지 두 개는 areaBasedList2 목록 응답에서 온다. 다른 컬럼과 달리
-- detail_fetched_at이 아니라 list_fetched_at 주기를 따르므로, 상세조회가 실패한
-- 장소에서도 이미지는 최신일 수 있다.
alter table public.places
  add column if not exists first_image_url text;

alter table public.places
  add column if not exists thumbnail_url text;

comment on column public.places.parking_info_raw is
  'detailIntro2 주차 안내 원문. contenttypeid별 필드(parking/parkingculture/...)를 모은다. 축제(15)는 해당 필드가 없어 항상 null이다.';

comment on column public.places.parking_fee_raw is
  'detailIntro2 주차 요금 원문(parkingfee/parkingfeeleports). 이용요금과 구분한다.';

comment on column public.places.use_fee_raw is
  'detailIntro2 이용요금 원문(usefee/usefeeleports/usetimefestival). 축제는 필드명이 usetimefestival이지만 내용은 요금이다.';

comment on column public.places.discount_info_raw is
  'detailIntro2 할인정보 원문(discountinfo/discountinfofestival/discountinfofood).';

comment on column public.places.first_image_url is
  'areaBasedList2 firstimage. 대표 이미지 URL이며 list_fetched_at 주기로 갱신된다.';

comment on column public.places.thumbnail_url is
  'areaBasedList2 firstimage2. 썸네일 이미지 URL이며 list_fetched_at 주기로 갱신된다.';

commit;
