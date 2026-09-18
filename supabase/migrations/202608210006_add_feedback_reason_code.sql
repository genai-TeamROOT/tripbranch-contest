begin;

-- 싫어요 개선 사유를 자유 문장과 분리해 집계한다. 질문·답변 원문은
-- 202608210004, intent는 202608210005에서 이미 추가됐다.
-- 기존 feedback 행과 이전 클라이언트의 dislike는 NULL을 유지한다.
alter table public.response_feedback
  add column if not exists reason_code text;

alter table public.response_feedback
  add constraint response_feedback_reason_code_valid
  check (
    reason_code is null
    or reason_code in (
      'intent_mismatch',
      'clarification_unhelpful',
      'context_not_preserved',
      'location_misunderstood',
      'conditions_not_applied',
      'recommendation_not_suitable',
      'other'
    )
  );

create index response_feedback_dislike_reason_recorded_idx
  on public.response_feedback (reason_code, recorded_at desc)
  where rating = 'dislike';

-- PostgreSQL은 create or replace view에서 기존 컬럼의 이름·순서를 바꾸는 걸
-- 허용하지 않는다(42P16). 지금 운영 중인 뷰의 컬럼 순서는 202608210005가
-- 만든 순서(id/session_id/run_id/rating/user_input/assistant_message/
-- intent/comment/recorded_at/recorded_at_kst) 그대로다 — 그 순서를 그대로
-- 유지하고 reason_code는 맨 뒤에만 추가할 수 있다.
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
  recorded_at at time zone 'Asia/Seoul' as recorded_at_kst,
  reason_code
from public.response_feedback;

commit;
