begin;

-- TourAPI에 없거나 비어 있는 값을 공식 출처로 보강해 담는 칸이다.
--
-- **현재 이 컬럼을 읽는 코드는 없다.** 무장애 정보를 여기 담으려던 접근이
-- `place_barrier_free` 전용 테이블로 나뉘면서(D-077) 보류됐다. 다만 컬럼과 116행의
-- 값은 남겨둔다 — 전용 테이블이 담지 못하는 정보가 있어 나중에 반영할 수 있다.
-- 현재 들어 있는 키는 places 원문 8종(operating_hours_raw, rest_date_raw,
-- parking_info_raw, parking_fee_raw, use_fee_raw, info_center_raw,
-- baby_carriage_raw, pet_raw)과 무장애 6종(accessible_parking_raw,
-- accessible_restroom_raw, nursing_room_raw, visitor_access_raw,
-- wheelchair_access_raw, wheelchair_rental_raw)이다.
--
-- 이 파일은 원격 DB에 적용된 정의를 저장소에 되살린 것이다. 2026-08-24에 Supabase
-- MCP로 적용해 원격 이력에는 `20260824234532_add_official_facts_to_place_enrichments`로
-- 기록됐는데 저장소에는 파일이 없었다. 저장소만 보고 DB를 새로 세우면 이 컬럼이
-- 빠지므로 되살린다. 스키마는 이미 적용돼 있어 다시 실행해도 바뀌는 것이 없다
-- (`add column if not exists`).
alter table public.place_enrichments
  add column if not exists official_facts jsonb not null default '{}'::jsonb;

alter table public.place_enrichments
  add constraint place_enrichments_official_facts_is_object
  check (jsonb_typeof(official_facts) = 'object');

comment on column public.place_enrichments.official_facts is
  'TourAPI 누락을 공식 출처로 보강한 필드별 JSON 객체. 최상위 키는 places 필드명과 같고 각 값에 value, merge_policy, verified_at, sources를 보존한다.';

commit;
