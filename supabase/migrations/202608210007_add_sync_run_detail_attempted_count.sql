-- 동기화 한 번이 detailIntro2를 몇 번 불렀는지 place_sync_runs에 남긴다.
--
-- 왜 필요한가: TourAPI는 오퍼레이션 단위로 일일 한도(1,000회)를 건다. 지금 호출량
-- 집계는 프로세스 메모리에만 있어(app/observability/api_usage.py) 서버를
-- 재시작하면 0이 되고, backend/scripts로 돈 실행분은 다른 프로세스라 아예 잡히지
-- 않는다. 그래서 "오늘 얼마나 썼는가"를 화면이 알 수 없었고, 상세조회 상한을
-- 정하려 해도 기준이 없었다.
--
-- 왜 이 테이블인가: detailIntro2를 부르는 코드는 PlaceSyncService 한 곳뿐이고
-- (추천 경로는 DB에서 읽는다), 그 경로는 실행마다 이 테이블에 행을 하나씩 이미
-- 남긴다. 호출마다 카운터를 올리면 상세조회 500건에 DB 쓰기가 500번 붙지만,
-- 여기 열을 하나 더하면 실행당 1회로 끝난다.
--
-- 왜 nullable인가: 기존 행과 중간에 죽어 완료 처리를 못 한 실행은 "0회 불렀다"가
-- 아니라 "재지 않았다"이다. 0으로 채우면 두 상태가 같아 보여, 합계를 실제보다
-- 정확한 것으로 오해하게 된다. 화면은 이 값이 비어 있는 실행 수를 함께 보여준다.
--
-- 이 값도 하한이다. 재시도(external_api_retry_count)는 한 장소를 여러 번 부를 수
-- 있는데 여기 세는 것은 장소 수다.
alter table public.place_sync_runs
  add column if not exists detail_attempted_count integer;

comment on column public.place_sync_runs.detail_attempted_count is
  '이 실행이 detailIntro2를 부른 장소 수. null이면 재지 않은 실행(열 추가 이전 또는 중단). 재시도는 세지 않아 하한이다.';

alter table public.place_sync_runs
  drop constraint if exists place_sync_runs_detail_attempted_count_nonnegative;

alter table public.place_sync_runs
  add constraint place_sync_runs_detail_attempted_count_nonnegative
  check (detail_attempted_count is null or detail_attempted_count >= 0);
