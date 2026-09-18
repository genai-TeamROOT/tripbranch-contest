begin;

-- Package B: 계정 단위 취향 설정 (D-062 Phase 5 후속).
--
-- 취향 설정 화면에서 고른 값은 그동안 localStorage에만 있어 기기를 벗어나지
-- 못했다. 이메일 회원가입이 들어오면서(TP-222) 계정에 붙일 수 있게 됐다.
--
-- **이 저장소만 session_id가 아니라 user_id로 키를 잡는다.** agent_states ·
-- recommendation_histories · saved_place_lists는 전부 세션 단위이고 세션 TTL과
-- 함께 소멸하지만, 취향은 세션을 넘어 사람에게 붙는 값이다. 세션에 얹으면
-- 대화를 새로 시작할 때마다 취향을 다시 골라야 한다.
create table public.user_preferences (
  -- saved_place_lists.user_id는 text이고 FK가 없다. **여기서는 다르게 간다.**
  --
  -- 세션 단위 테이블은 만료 세션 정리(cleanup_expired_sessions.py)가 행을
  -- 걷어가므로 고아가 오래 남지 않는다. 취향은 세션 수명에 묶이지 않아
  -- **아무것도 이 행을 치우지 않는다** — 계정이 지워져도 남는다. 그래서
  -- auth.users에 직접 걸어 계정과 함께 사라지게 한다. 익명 계정 정리
  -- (cleanup_anonymous_users.py)도 별도 수정 없이 함께 정리된다.
  user_id uuid primary key references auth.users(id) on delete cascade,

  -- SavedPreference 배열. 각 항목은 {label, source, codes}이며 source는
  -- preference | place_tag | custom 셋 중 하나다(frontend preferenceOptions.ts).
  -- 순서는 사용자가 고른 순서이고 화면이 그대로 보여준다.
  items jsonb not null default '[]'::jsonb,
  updated_at timestamptz not null default now(),

  constraint user_preferences_items_is_array
    check (jsonb_typeof(items) = 'array')
);

-- 클라이언트의 직접 접근은 차단하고 FastAPI의 서버 권한(secret key)을 통해서만
-- 사용한다. RLS 정책을 만들지 않은 상태이므로 anon/authenticated에는 허용되는
-- 행이 없다 (agent_state 테이블들과 동일한 원칙).
--
-- 프론트가 Supabase에 직접 붙는 방식도 검토했으나 택하지 않았다 — 이 프로젝트
-- 최초의 RLS 정책이 생기고 데이터 경로가 둘로 갈린다. 취향은 나중에 추천 요청에
-- 실을 값이라 결국 백엔드가 읽어야 하는데, 그때 백엔드가 또 다른 길로 같은 값을
-- 읽게 된다.
alter table public.user_preferences enable row level security;
revoke all on table public.user_preferences from anon, authenticated;

commit;
