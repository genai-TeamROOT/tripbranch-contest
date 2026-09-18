begin;

-- Package B: 세션 단위 장소 보관함 (SCHEDULE-12).
--
-- 사용자가 추천 카드에서 명시적으로 담은 장소를 보관한다.
-- recommendation_histories에 컬럼을 더하지 않고 별도 테이블로 둔 이유:
--
--  1. 이력은 append-only인데 보관함은 담기/빼기가 되는 가변 상태다.
--  2. history reset(계약 5.5절, clear_recommended())이 recommended와
--     closed_excluded를 비운다 — 보관함이 그 테이블에 얹혀 있으면 "다른 곳
--     보여줘" 한 번에 사용자가 담아둔 것이 함께 날아간다.
--  3. 정식 인증(D-062 Phase 5) 이후 계정 단위로 옮길 때, 세션 수명에 묶인
--     이력과 분리돼 있어야 이관 범위가 명확하다.
--
-- agent_states/recommendation_histories와 동일하게 read-modify-write로 통째로
-- 갱신된다(saved_places.py의 get_or_create → save_saved_places 패턴).
-- updated_at은 애플리케이션이 "실제로 담기거나 빠졌을 때만" 갱신하므로
-- 자동 트리거를 달지 않는다(agent_states와 같은 이유).
create table public.saved_place_lists (
  session_id text primary key,
  -- agent_states.user_id와 동일한 규칙(D-063 결정 3): 비어 있으면 채우고
  -- 값이 있으면 덮어쓰지 않는다. FK는 걸지 않는다.
  user_id text,
  -- SavedPlaceItem 배열. 순서가 담은 순서이며 의미를 갖는다 — 일정 편성에서
  -- 항목 수 상한을 넘을 때 무엇을 남길지 이 순서로 정한다.
  items jsonb not null default '[]'::jsonb,
  updated_at timestamptz not null default now(),

  constraint saved_place_lists_session_id_not_blank
    check (btrim(session_id) <> ''),
  constraint saved_place_lists_items_is_array
    check (jsonb_typeof(items) = 'array')
);

-- 클라이언트의 직접 접근은 차단하고 FastAPI의 서버 권한(secret key)을
-- 통해서만 사용한다. RLS 정책을 만들지 않은 상태이므로 anon/authenticated에는
-- 허용되는 행이 없다 (agent_state 테이블들과 동일한 원칙).
alter table public.saved_place_lists enable row level security;
revoke all on table public.saved_place_lists from anon, authenticated;

commit;
