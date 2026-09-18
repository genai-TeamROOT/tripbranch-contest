begin;

-- Package B (AF-12/LLMOps, roadmap.md 14번): 응답 1건에 대한 사용자 반응
-- (좋아요/싫어요). append-only — trace_records/condition_change_logs와
-- 동일한 패턴이다.
--
-- trace_records와 달리 trace_id가 아니라 run_id 단위로 붙는다 — 사용자는
-- "이 답변"에 반응하는 것이지, 그 답변을 만든 개별 실행 단계(LLM 호출/
-- Tool 호출/Scoring)에 반응하는 게 아니다. run_id로 trace_records와 조인하면
-- "이 반응이 어떤 prompt_version/scoring_version에서 나왔는지" 추적할 수 있다.
create table public.response_feedback (
  id bigserial primary key,
  session_id text not null,
  run_id text not null,
  rating text not null,
  recorded_at timestamptz not null default now(),

  constraint response_feedback_session_id_not_blank
    check (btrim(session_id) <> ''),
  constraint response_feedback_run_id_not_blank
    check (btrim(run_id) <> ''),
  constraint response_feedback_rating_valid
    check (rating in ('like', 'dislike'))
);

-- trace_records와 동일한 조회 패턴(session_id로 조회, id 순 정렬)에 더해,
-- run_id로 trace_records와 조인해 "어떤 버전이 만든 답변이 반응을 받았는지"
-- 찾는 조회도 필요하므로 run_id에도 인덱스를 둔다.
create index response_feedback_session_id_id_idx
  on public.response_feedback (session_id, id);

create index response_feedback_run_id_idx
  on public.response_feedback (run_id);

alter table public.response_feedback enable row level security;
revoke all on table public.response_feedback from anon, authenticated;

commit;
