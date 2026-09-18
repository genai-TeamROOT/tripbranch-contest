begin;

-- search_place_evidence는 후보 content_id로 좁혀도 전체 스캔이 필요해(§2.10)
-- 실측상 40,389행 코사인 거리 계산에 7.5~9.2초가 걸린다(2026-08-18, 활성
-- 844곳 전체를 후보로 넘긴 유사도 분포 측정 중 발견). PostgREST가 물리
-- 연결에 쓰는 authenticator 롤의 statement_timeout=8s가 이 경계에서 걸려
-- 500 에러가 난다. 이 함수는 anon/authenticated에서 호출할 수 없으므로
-- (202608180002에서 이미 막음) 전역 타임아웃은 그대로 두고 이 함수 실행
-- 중에만 여유를 준다.
alter function public.search_place_evidence(
  vector, text[], int, float
) set statement_timeout = '30s';

commit;
