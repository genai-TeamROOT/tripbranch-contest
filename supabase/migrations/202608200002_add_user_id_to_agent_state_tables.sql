begin;

-- Package B: 게스트 신원(user_id)을 세션에 연결한다 (TP-101 3단계, D-063).
-- D-062 Phase 1~2(PR #183)로 프론트가 신원을 발급해 보내고 백엔드가
-- 서명 검증까지는 이미 끝냈다. 여기서는 검증된 user_id를 실제로
-- 저장할 컬럼만 추가한다 — 채우는 로직(빈 값만 채우고 덮어쓰지 않음)은
-- 애플리케이션(state/session.py)이 담당한다.
--
-- auth.users(id)로 FK를 걸지 않는다(D-063 결정 4):
--   1) db-store-design-v2.md §2-3이 테이블 간 FK를 의도적으로 두지 않았다
--      (delete_state/delete_history가 독립적으로 호출되는 구조와 어긋남).
--   2) public 스키마가 우리 통제 밖인 auth 스키마에 의존하게 된다.
--   3) 오래된 익명 사용자 정리(guest-auth-design.md 10절)와 충돌한다 —
--      FK가 있으면 삭제가 막히거나(restrict) 세션까지 함께 지워진다(cascade).
--
-- STATE_STORE_BACKEND를 supabase로 전환하는 것은 이 작업 범위가 아니다
-- (D-063 결정 1). 컬럼·필드·경로만 준비해 두고, 전환 여부는 저장소
-- 소유자가 별도로 판단한다.
alter table public.agent_states
  add column user_id uuid;

alter table public.recommendation_histories
  add column user_id uuid;

-- "이 사용자의 세션 목록"을 최근 활동순으로 조회할 때 쓸 인덱스
-- (guest-auth-design.md 6절). recommendation_histories는 session_id로만
-- 조회되므로 별도 인덱스를 추가하지 않는다.
create index agent_states_user_id_last_active_at_idx
  on public.agent_states (user_id, last_active_at);

commit;
