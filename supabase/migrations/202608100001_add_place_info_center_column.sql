begin;

-- INFO location_info 질의(전화번호)를 places 캐시만으로 답하기 위해 detailIntro2의
-- 안내처 원문을 보존한다. 외부 호출은 늘지 않는다 — place_sync가 이미 장소마다
-- detailIntro2를 부르고 있고(운영시간·주차 용도) 그 응답에서 더 읽기만 한다.
--
-- detailCommon2의 tel을 쓰지 않는 이유: 표본 35건 실측(2026-08-10)에서 tel이 채워진
-- 것은 축제(15) 5/5뿐이고 12·14·28·32·38·39는 전부 0/5였다. 같은 표본의
-- detailIntro2 infocenter* 계열은 33건 중 32건(97%)이 채워져 있다. 전화번호의 실제
-- 출처는 intro다.
--
-- 필드명은 contenttypeid마다 다르다. 하나로 모은다.
--   12 관광지 infocenter / 14 문화시설 infocenterculture
--   28 레포츠 infocenterleports / 32 숙박 infocenterlodging
--   38 쇼핑 infocentershopping / 39 음식점 infocenterfood
-- 축제(15)에는 infocenter 계열이 없고 sponsor1tel을 쓴다. 축제는 detailCommon2의
-- tel도 함께 채워지므로 이 컬럼이 비어도 전화번호를 답할 수 있다.
--
-- 값이 전화번호만 오지는 않는다(2026-08-10 실측).
--   "02-2262-6541" / "흥인지문 관리소 02-2148-4166" / "02-735-4431~2"
-- 기관명이 앞에 붙거나 번호가 범위로 적히므로 파싱하지 않고 원문을 그대로 담는다.
-- 기존 _raw 접미사 관습과 같다.
alter table public.places
  add column if not exists info_center_raw text;

comment on column public.places.info_center_raw is
  'detailIntro2 안내처 원문. contenttypeid별 필드(infocenter/infocenterculture/...)를 모은다. 전화번호 외에 기관명이 섞일 수 있다. 축제(15)는 sponsor1tel을 쓰므로 항상 null이다.';

commit;
