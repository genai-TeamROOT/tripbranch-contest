begin;

-- 앱 코드·RPC·뷰 어디에서도 참조하지 않는 것을 확인했다(2026-09-11, D-A 협의 완료).
-- A(mintee)가 2026-08-25에 Google Places API로 적재했으나(2,256건) 실제로 쓰인 적이
-- 없다 — repositories/protocols.py에 대응 메서드가 없고, domain/models.py에 대응
-- 모델이 없다. 데이터 딕셔너리(supabase/data_dictionary/place_google_profiles.md)에
-- 활용 계획만 남아 있고 구현은 없었다.
drop table if exists public.place_google_profiles;

commit;
