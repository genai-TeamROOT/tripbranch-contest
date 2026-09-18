begin;

-- response_feedback.recorded_at는 timestamptz라 저장은 UTC로 정확하지만,
-- Supabase 테이블 에디터가 UTC 그대로 보여줘서 KST 기준으로 보기 불편하다.
-- 저장 방식(UTC)은 그대로 두고, 조회 편의를 위한 뷰만 하나 둔다.
create or replace view public.response_feedback_kst as
select
  id,
  session_id,
  run_id,
  rating,
  recorded_at,
  recorded_at at time zone 'Asia/Seoul' as recorded_at_kst
from public.response_feedback;

-- 원본 테이블과 같은 접근 정책을 유지한다 — 뷰라고 더 열어주지 않는다.
revoke all on public.response_feedback_kst from anon, authenticated;

commit;
