begin;

-- Package B: RecommendationHistory.closed_excluded 필드 추가 (TP-82).
-- D의 하드 필터(_is_closed)가 폐점이라 걸러낸 후보는 지금까지 노출 이력
-- (recommended)에도, 거절 이력(rejected)에도 남지 않아 다음 회차 후보
-- 수집에서 매번 다시 뽑혔다 — 밤 시간대처럼 폐점 비율이 높을 때 "다른 곳
-- 보여줘"를 반복하면 추천 카드가 점점 줄다가 0장이 되는 원인이었다.
-- recommended/rejected와 같은 패턴(jsonb 배열, 기본값 빈 배열, 배열
-- 타입 체크 제약)으로 별도 컬럼을 추가한다 — 같은 이유로 InMemoryStateStore는
-- 영향이 없지만 SupabaseStateStore는 save_history()가 이 필드를 포함해서
-- 통째로 전송하므로 컬럼이 없으면 저장이 실패한다(202608130001과 동일한 이유).
alter table public.recommendation_histories
  add column closed_excluded jsonb not null default '[]'::jsonb;

alter table public.recommendation_histories
  add constraint recommendation_histories_closed_excluded_is_array
    check (jsonb_typeof(closed_excluded) = 'array');

commit;
