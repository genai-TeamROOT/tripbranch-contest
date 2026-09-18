begin;

-- Package B: AgentState.ignore_operating_hours_until 필드 추가.
-- no_data_closed 되묻기의 "운영 중이 아닌 곳도 볼게요"를 한 번 선택하면 이
-- 시각까지는 매 턴 다시 묻지 않고 폐점 후보도 계속 포함한다(실사용 피드백,
-- 2026-08-13). InMemoryStateStore를 쓰는 동안은 영향이 없지만,
-- SupabaseStateStore로 전환하면 save_state()가 이 필드를 포함해서 통째로
-- 전송하므로 컬럼이 없으면 저장이 실패한다(202608030001과 동일한 이유).
alter table public.agent_states
  add column ignore_operating_hours_until timestamptz;

commit;
