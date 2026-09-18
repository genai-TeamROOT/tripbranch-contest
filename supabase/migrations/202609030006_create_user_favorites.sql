begin;

-- Package B: 계정 단위 즐겨찾기 (위치 설정 화면, PR #361 후속).
--
-- 즐겨찾기는 그동안 localStorage에만 있어 기기를 벗어나지 못했다. 게다가 서버에
-- 사본이 없어, 로그아웃으로 이 기기의 값을 지우면 되돌릴 방법이 없었다.
--
-- **user_preferences와 같은 모양으로 간다.** 세션이 아니라 사람에게 붙는 값이라
-- 키가 user_id이고, 세션 TTL과 함께 사라지면 안 된다. 대화를 새로 시작할 때마다
-- 즐겨찾기를 다시 담아야 한다면 즐겨찾기가 아니다.
create table public.user_favorites (
  -- user_preferences와 같은 이유로 auth.users에 직접 건다. 세션 단위 테이블은
  -- 만료 세션 정리가 행을 걷어가지만 이 값은 세션 수명에 묶이지 않아 아무것도
  -- 치우지 않는다 — 계정이 지워질 때 함께 사라지게 한다. 익명 계정 정리
  -- (cleanup_anonymous_users.py)도 별도 수정 없이 함께 정리된다.
  user_id uuid primary key references auth.users(id) on delete cascade,

  -- 즐겨찾기 배열. 각 항목은 {id, label, search_center_name, address}다.
  --
  -- **항목마다 행을 두지 않는다.** 화면이 목록 단위로 다루기 때문이다 — 순서가
  -- 있고(담은 순서), 이름을 바꾸고, 지우는 것이 전부 목록 전체를 다시 저장하는
  -- 흐름이다. 행으로 쪼개면 순서 컬럼과 항목별 엔드포인트가 따라붙는데 그것을
  -- 쓸 화면이 없다. user_preferences.items와 같은 판단이다.
  items jsonb not null default '[]'::jsonb,
  updated_at timestamptz not null default now(),

  constraint user_favorites_items_is_array
    check (jsonb_typeof(items) = 'array')
);

-- 클라이언트의 직접 접근은 차단하고 FastAPI의 서버 권한(secret key)을 통해서만
-- 사용한다. 정책을 만들지 않았으므로 anon/authenticated에는 허용되는 행이 없다
-- (user_preferences·agent_state 테이블들과 동일한 원칙).
alter table public.user_favorites enable row level security;
revoke all on table public.user_favorites from anon, authenticated;

commit;
