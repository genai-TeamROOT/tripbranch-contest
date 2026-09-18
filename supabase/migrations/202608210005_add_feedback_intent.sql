begin;

-- Package B (roadmap.md 14번 후속, D-069). intent는 그 턴의 assistant_text
-- 메시지가 이미 들고 있는 값(예: RECOMMEND/INFO/COMPARE)을 그대로 복사해
-- 저장한다 — "어떤 인텐트가 싫어요를 많이 받는지" 필터링용. B는 값을
-- 검증하지 않는다(step/prompt_version과 같은 성격).
--
-- comment 컬럼은 202608210003_add_comment_to_response_feedback.sql(develop
-- PR, 같은 날 독립적으로 구현)에서 이미 추가됐다 — 여기서 다시 추가하지
-- 않는다.
alter table public.response_feedback
  add column if not exists intent text;

-- KST 조회 뷰에 이번에 추가된 intent와, 별도 마이그레이션(202608210004)에서
-- 추가된 user_input/assistant_message까지 함께 노출한다. comment는
-- 202608210003에서 이미 뷰에 포함해뒀다.
create or replace view public.response_feedback_kst as
select
  id,
  session_id,
  run_id,
  rating,
  user_input,
  assistant_message,
  intent,
  comment,
  recorded_at,
  recorded_at at time zone 'Asia/Seoul' as recorded_at_kst
from public.response_feedback;

commit;
