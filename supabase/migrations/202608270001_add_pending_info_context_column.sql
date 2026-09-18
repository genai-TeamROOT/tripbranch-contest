begin;

-- Package B: AgentState.pending_info_context 필드 추가 (D-088 INFO 되묻기
-- 상태 저장). INFO의 place_ambiguous 되묻기는 RECOMMEND의 location_ambiguous와
-- 달리 question_type/specific_question 등 원래 질문 자체를 세션에 저장해야
-- 버튼 클릭 시 재분류 없이 이어받을 수 있다 — pending_clarification 컬럼과
-- 같은 이유(202608030001)로, SupabaseStateStore.save_state()가 이 필드를
-- 포함해서 통째로 전송하므로 컬럼이 없으면 저장이 실패한다.
alter table public.agent_states
  add column pending_info_context jsonb;

commit;
