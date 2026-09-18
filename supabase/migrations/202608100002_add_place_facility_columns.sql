begin;

-- INFO facility 질의("화장실 있어?", "카드 되나요?")를 places 캐시만으로 답하기 위해
-- detailIntro2의 편의시설 원문을 보존한다. 외부 호출은 늘지 않는다 — place_sync가
-- 이미 장소마다 detailIntro2를 부르고 있고 그 응답에서 더 읽기만 한다(D-056과 같은
-- 논리).
--
-- 컬럼 4개로 나누고 jsonb 하나로 합치지 않는다. 합치면 소비 측이 키 이름을 알아야
-- 하고 "키가 없다"와 "그 장소에 정보가 없다"가 구분되지 않는다 — D-060이 raw_intro
-- 조회를 걷어낸 이유와 같은 문제를 되살리게 된다.
--
-- 이름은 A에게 나가는 계약 키(info_field_rules.INFO_FIELD_KEYS)와 1:1로 맞춘다.
--
-- 필드명은 contenttypeid마다 다르다. 각 컬럼에 하나로 모은다.
--   유모차  chkbabycarriage / ~culture / ~leports / ~shopping
--   반려동물 chkpet / ~culture / ~leports / ~shopping
--   카드결제 chkcreditcard / ~culture / ~leports / ~shopping / ~food
--   화장실  restroom (유형 구분 없이 한 키)
-- 숙박(32)·축제(15)에는 이 필드들이 아예 없다.
--
-- 커버리지(2026-08-10 실측, 필드가 존재하는 유형 5종에서 55건 표본):
--   하나라도 값이 있는 장소 22/55(40%)
--   카드결제 19/55 — 쇼핑 12/12, 음식점 6/12, 관광지 1/12
--   화장실   12/55 — 쇼핑 12/12, 나머지 0
--   유모차    4/55 — 관광지 2/12, 문화시설 2/12 (값은 모두 "없음")
--   반려동물  0/55 — 종로구 표본에서는 한 건도 채워지지 않았다
-- 쇼핑에 몰려 있다는 점이 중요하다. 인사동·광장시장 같은 곳의 "화장실 있어?"가
-- 이 컬럼 없이는 답할 수 없다.
--
-- 값은 "가능" / "없음" / "있음" / "모든 카드 사용 가능" 같은 단답이다. 해석하지 않고
-- 원문을 그대로 담는다. 특히 "없음"은 값이 비어 있는 것과 다르다 — 정보가 없는 게
-- 아니라 "없다고 답한" 것이므로 NULL로 눌러쓰지 않는다.
alter table public.places
  add column if not exists baby_carriage_raw text;

alter table public.places
  add column if not exists pet_raw text;

alter table public.places
  add column if not exists credit_card_raw text;

alter table public.places
  add column if not exists restroom_raw text;

comment on column public.places.baby_carriage_raw is
  'detailIntro2 유모차 대여 원문(chkbabycarriage/~culture/~leports/~shopping). 숙박·축제는 항상 null이다.';

comment on column public.places.pet_raw is
  'detailIntro2 반려동물 동반 원문(chkpet/~culture/~leports/~shopping). 종로구 표본에서는 채워진 건이 없었다.';

comment on column public.places.credit_card_raw is
  'detailIntro2 신용카드 가능 원문(chkcreditcard/~culture/~leports/~shopping/~food). 쇼핑(38)에 집중돼 있다.';

comment on column public.places.restroom_raw is
  'detailIntro2 화장실 설명 원문(restroom). 유형 구분 없이 한 키이며 쇼핑(38)에 집중돼 있다.';

commit;
