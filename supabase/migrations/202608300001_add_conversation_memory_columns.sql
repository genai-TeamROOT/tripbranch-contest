begin;

-- Package B: AgentState.recent_turns / situation_state 필드 추가
-- (docs/design/conversational-layer.md 4장 1단계 — 대화층).
--
-- 지금 세션은 누적 조건·추천 이력·직전 인텐트·되묻기 코드만 저장하고 주고받은
-- 말 자체는 남기지 않아, "다리 다쳤어 → 많이 다치셨어요? → 그냥 삐끗했어" 같은
-- 3턴 대화가 원리적으로 불가능하다. recent_turns가 그 공백을 메우고,
-- situation_state는 "이미 거절당한 제안을 다시 하지 않는다"는 규칙이 참조할
-- 저장 자리다(규칙만 있고 적어둘 곳이 없었다).
--
-- pending_clarification(202608030001)·pending_info_context(202608270001) 컬럼과
-- 같은 이유로 이 마이그레이션을 코드보다 **먼저** 배포해야 한다 —
-- SupabaseStateStore.save_state()가 AgentState를 통째로 upsert하므로 컬럼이
-- 없으면 저장 자체가 실패한다. 반대로 읽기는 안전하다(pydantic이 모르는 컬럼을
-- 무시하므로 "컬럼 먼저 → 코드 나중" 순서가 성립한다).
alter table public.agent_states
  add column recent_turns jsonb not null default '[]'::jsonb,
  add column situation_state jsonb;

commit;
