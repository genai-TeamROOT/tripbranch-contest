begin;

-- Package B: 대화의 위치. 사이드바 채팅 히스토리가 날짜 옆에 보여준다.
-- (TP-222 후속 — 채팅 히스토리)
--
-- 그동안 그 자리에는 "그 대화에서 마지막으로 언급된 장소 이름"(recent_turns의
-- place_names)을 보여줬는데, 목록에서 대화를 알아보는 데는 위치가 낫다 —
-- "블루보틀 성수"는 그 대화가 무엇이었는지 말해주지 않지만 "성수동"은 말해준다.
--
-- **user_conditions에서 그때그때 읽지 않고 컬럼에 박는 이유.** 이어가기(resume)가
-- 낡은 조건을 버리면서 user_conditions를 통째로 비운다. 지난 대화를 한 번 열면
-- 그 대화의 위치가 목록에서 사라지게 된다.
--
-- 채우는 규칙은 title과 같다(D-063 결정 3): **비어 있으면 채우고, 값이 있으면
-- 절대 덮어쓰지 않는다.** 제목이 첫 질문인 것과 짝을 맞춰 위치도 처음 잡힌 값을
-- 쓴다 — 대화 도중에 지역을 옮겨도 목록의 한 줄이 저절로 바뀌지는 않는다.
alter table public.agent_states
  add column if not exists location text;

-- 이미 쌓인 대화에도 위치를 준다. 아직 조건이 살아 있는 세션에서만 얻을 수 있어
-- 전부 채워지지는 않는다(실측: 신원 붙은 대화 107개 중 50개).
update public.agent_states
   set location = left(btrim(user_conditions ->> 'search_center'), 200)
 where location is null
   and btrim(coalesce(user_conditions ->> 'search_center', '')) <> '';

commit;
