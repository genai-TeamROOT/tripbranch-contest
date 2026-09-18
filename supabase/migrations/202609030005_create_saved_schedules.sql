begin;

-- Package B: 사용자가 저장한 일정. (SCHEDULE, 카드 2)
--
-- ## session_messages와 무엇이 다른가
--
-- TP-222 후속으로 session_messages가 생기면서 일정은 이미 저장되고 있다 —
-- AgentResponse를 통째로 담으므로 그 안에 schedule이 들어 있다. 그런데 그것은
-- **"그때 화면에 나갔던 것"**이고, 그 테이블 주석이 밝혔듯 "현재 상태로 다시 읽는
-- 소비자를 두지 않는다"는 전제 위에 서 있다.
--
-- 사용자가 "이 일정을 쓰겠다"고 고른 것은 성격이 다르다. 이름을 붙이고, 나중에
-- 열고, 고칠 수 있어야 한다. 스냅샷을 편집 대상으로 겸하게 하면 그 전제가 깨진다 —
-- 보관함(saved_place_lists)을 추천 이력과 분리한 것과 같은 이유다.
--
-- 수명도 다르다. session_messages는 30일 정리 스크립트의 대상이지만, 사용자가
-- 이름 붙여 저장한 일정이 30일 뒤 조용히 사라지면 그것은 저장이 아니다.
--
-- ## FK를 건다 — saved_place_lists와 반대 선택이다
--
-- D-063 결정 4는 auth.users에 FK를 걸지 않기로 했고 세션 단위 테이블들은 그대로
-- 따른다. 그 근거는 익명 사용자 정리와 충돌한다는 것이었는데, 그것이 성립하려면
-- **다른 무언가가 그 행을 치워야** 한다 — 세션 단위 테이블은 만료 세션 정리
-- (cleanup_expired_sessions.py)가 걷어간다.
--
-- 저장한 일정은 세션 수명에 묶이지 않으므로 아무것도 이 행을 치우지 않는다.
-- user_preferences가 같은 이유로 이미 FK를 걸었다(202609030001 주석). 계정 단위
-- 저장소는 계정과 함께 사라지는 것이 맞다.
create table public.saved_schedules (
  -- 프론트가 URL로 지목하는 자원이라 순번 대신 uuid를 쓴다. session_messages가
  -- bigserial인 것은 그쪽이 session_id로만 조회되고 개별 행을 지목할 일이
  -- 없기 때문이다.
  id uuid primary key default gen_random_uuid(),

  user_id uuid not null references auth.users(id) on delete cascade,

  -- 어느 대화의 어느 턴에서 나온 일정인지. **FK를 걸지 않고 null도 허용한다** —
  -- 세션은 30일 뒤 정리되지만 저장한 일정은 남아야 하므로, 세션이 사라진 뒤에도
  -- 이 값은 "출처 표시"로만 남는다. 화면이 이 값으로 원본 대화를 열어보려 할 때는
  -- 없을 수 있다는 전제로 다뤄야 한다.
  session_id text,
  run_id text,

  -- 목록에 보여줄 이름. 사용자가 바꿀 수 있다. payload 안에도 route_summary가
  -- 있지만 그것은 LLM이 쓴 문장이고 이것은 사용자의 것이다.
  title text not null,

  -- ScheduleResult를 직렬화한 그대로. **B는 열어보지 않는다** — session_messages의
  -- payload와 같은 취급이다(app.schemas에 의존하지 않는다).
  payload jsonb not null,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  constraint saved_schedules_title_not_blank
    check (btrim(title) <> ''),
  constraint saved_schedules_payload_is_object
    check (jsonb_typeof(payload) = 'object')
);

-- 조회는 "내 일정을 최근 저장순으로" 하나뿐이다.
create index saved_schedules_user_created_idx
  on public.saved_schedules (user_id, created_at desc);

-- 같은 턴의 일정을 두 번 저장하지 못하게 막는다. 저장 버튼을 두 번 누르거나
-- 요청이 재시도되면 목록에 같은 일정이 두 줄로 보이는데, 사용자에게 그것은
-- 그 자체로 버그다(saved_place_lists의 멱등 처리와 같은 판단).
-- run_id가 없는 경로도 있어 부분 인덱스로 둔다.
create unique index saved_schedules_user_run_idx
  on public.saved_schedules (user_id, run_id)
  where run_id is not null;

-- 클라이언트 직접 접근은 막고 FastAPI의 서버 권한으로만 쓴다. 정책을 만들지
-- 않았으므로 anon/authenticated에 허용되는 행이 없다(다른 B 테이블과 동일).
alter table public.saved_schedules enable row level security;
revoke all on table public.saved_schedules from anon, authenticated;

commit;
