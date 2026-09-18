begin;

-- Package B: 대화 제목. (TP-222 후속 — 채팅 히스토리)
--
-- 사이드바의 채팅 히스토리가 그동안 localStorage 목업이었고, **항목을 추가하는
-- 코드가 아예 없어** 목록이 늘 비어 있었다. 계정에서 실제 대화를 불러오려면
-- 목록에 보여줄 제목이 필요하다.
--
-- **recent_turns에서 파생하지 않는 이유.** 그 배열은 MAX_RECENT_TURNS(=5)개만
-- 남는다. 첫 질문을 제목으로 쓰려 해도 6번째 턴부터는 밀려나 사라지므로, 대화를
-- 이어갈수록 사이드바의 제목이 저절로 바뀐다. 실측으로 신원이 붙은 대화 105개 중
-- 22개(21%)가 이미 5턴을 채워 첫 질문을 잃은 상태다.
--
-- 채우는 규칙은 user_id와 같다(D-063 결정 3): **비어 있으면 채우고, 값이 있으면
-- 절대 덮어쓰지 않는다.** 첫 턴의 사용자 발화가 제목이 되고, 사용자가 이름을
-- 바꾸면 그 값이 남는다.
alter table public.agent_states
  add column if not exists title text;

-- 이미 쌓인 대화에도 제목을 준다. **근사치다** — 남아 있는 가장 오래된 턴이라
-- 5턴을 채운 22개에서는 실제 첫 질문이 아니다. 그래도 제목이 없어 목록에서
-- 통째로 빠지는 것보다는 낫고, 이 값도 한 번 정해지면 덮어쓰지 않는다.
update public.agent_states
   set title = left(btrim(recent_turns -> 0 ->> 'user_input'), 200)
 where title is null
   and jsonb_typeof(recent_turns) = 'array'
   and jsonb_array_length(recent_turns) > 0
   and btrim(coalesce(recent_turns -> 0 ->> 'user_input', '')) <> '';

-- 사이드바는 "내 대화를 최근 순으로"만 읽는다. user_id로 걸러 last_active_at으로
-- 정렬하는 질의라 이 순서의 인덱스가 그대로 쓰인다.
create index if not exists agent_states_user_recent_idx
  on public.agent_states (user_id, last_active_at desc)
  where user_id is not null;

commit;
