begin;

-- "싫어요" 클릭 시 선택적으로 남기는 짧은 사유 텍스트. NULL 허용 — like에는
-- 입력창을 보여주지 않고, dislike도 건너뛰기를 누르면 comment 없이 기록된다.
alter table public.response_feedback
  add column comment text;

alter table public.response_feedback
  add constraint response_feedback_comment_max_length
  check (comment is null or char_length(comment) <= 500);

-- KST 조회 뷰도 새 컬럼을 함께 노출한다.
create or replace view public.response_feedback_kst as
select
  id,
  session_id,
  run_id,
  rating,
  comment,
  recorded_at,
  recorded_at at time zone 'Asia/Seoul' as recorded_at_kst
from public.response_feedback;

commit;
