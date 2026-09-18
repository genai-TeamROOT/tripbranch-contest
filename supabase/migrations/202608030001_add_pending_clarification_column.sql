begin;

-- Package B: AgentState.pending_clarification 필드 추가 (PR #64, C 선반영).
-- agent_states 테이블이 202607280001에서 만들어질 당시엔 이 필드가
-- schema.py에 없었어서 컬럼이 누락됐다. InMemoryStateStore를 쓰는 동안은
-- 영향이 없지만, SupabaseStateStore로 전환하면 save_state()가 이 필드를
-- 포함해서 통째로 전송하므로 컬럼이 없으면 저장이 실패한다.
alter table public.agent_states
  add column pending_clarification text;

commit;