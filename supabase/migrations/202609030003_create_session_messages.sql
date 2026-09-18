begin;

-- Package B: 화면 기록(transcript). 한 세션에서 화면에 나갔던 것을 턴 단위로
-- 그대로 보관한다. (TP-222 후속 — 채팅 히스토리)
--
-- ## 왜 새 테이블인가
--
-- 지금까지 "지난 대화 화면"은 저장소 두 개에서 짜깁기해 만들었는데, 그 둘은
-- 각자 다른 이유로 잘리고 비워진다.
--
--   recent_turns                       모델에 넣을 맥락  → MAX_RECENT_TURNS(5)에서 잘림
--   recommendation_histories.recommended  다음 추천 제외 목록 → 이어가기 때 비움
--
-- 화면에 다시 그릴 기록은 둘 중 어느 쪽도 아니다. 셋을 한 데이터로 겸하는 한
-- 손실은 구조적으로 생긴다 — 그래서 세 번째를 따로 둔다.
--
-- ## 계약과의 관계
--
-- agent-state-contract-v1.md는 두 가지를 금하고 있었다.
--   전제 : "사용자 원문 발화와 LLM 원문 응답은 저장하지 않는다"
--   3.2절: "B는 place_id만 저장하며 장소 상세 정보를 보관하지 않는다"
--          — 과거 정보가 현재 정보로 오인되는 것을 막기 위해서다.
--
-- 이 테이블은 둘 다 연다. 대신 원칙이 지키려던 것은 저장이 아니라 **표시**에서
-- 지킨다: 여기 담긴 값은 "그때 화면에 나갔던 것"으로만 쓰이고, 현재 상태로 다시
-- 읽는 소비자를 두지 않는다. 특히 운영시간·남은 시간처럼 시간이 지나면 틀리는
-- 값은 복원 화면에서 다시 그리지 않는다(프론트 PastRecommendationMessage 참고).
-- COMPARE 스냅샷(D-050)이 같은 논리로 예외를 얻은 선례다.
--
-- ## payload를 해석하지 않는 이유
--
-- A의 AgentResponse를 그대로 담는다. B가 그 구조를 파싱하면 A의 스키마가 바뀔
-- 때마다 B가 따라가야 하고, 지금 B는 app.schemas에 의존하지 않는다. "B는 값의
-- 의미를 판단하지 않는다"는 기존 원칙(trace_records의 step 등)과 같은 취급이다.
--
-- 크기 실측: 장소 1건 959바이트, 5건 한 턴 4.8KB. 세션당 턴 수는 평균 1.9 ·
-- p95 5 · 최대 25라 세션 하나가 평균 ~10KB, 최악 ~120KB다. agent_states에
-- 컬럼으로 넣지 않은 이유가 여기 있다 — 그 테이블은 매 턴 행 전체를 다시 쓰므로
-- (read-modify-write) 25턴짜리 세션은 120KB를 매 턴 재기록하게 된다.
create table public.session_messages (
  id bigserial primary key,
  session_id text not null,
  -- 그 턴의 run_id. 같은 턴의 다른 기록(trace_records, recommendation_histories)과
  -- 잇는 열쇠다. 응답이 run_id 없이 끝나는 경로가 있어 not null이 아니다.
  run_id text,
  -- agent_states.user_id와 같은 규칙으로 채운다. FK를 걸지 않는 이유도 같다
  -- (D-063 결정 4 — 익명 사용자 정리와 충돌).
  user_id uuid,
  -- 그 턴의 사용자 발화. payload 안에도 들어 있지만 밖으로 꺼내 둔다 — 목록을
  -- 훑을 때 payload 전체를 열지 않으려는 것이다.
  user_input text,
  -- A의 AgentResponse를 직렬화한 그대로.
  payload jsonb not null,
  recorded_at timestamptz not null default now(),

  constraint session_messages_session_id_not_blank
    check (btrim(session_id) <> ''),
  constraint session_messages_payload_is_object
    check (jsonb_typeof(payload) = 'object')
);

-- 조회는 "이 세션의 기록을 오래된 순으로" 하나뿐이다. append-only 두 테이블
-- (condition_change_logs/trace_records)과 같은 모양의 인덱스다.
create index session_messages_session_id_id_idx
  on public.session_messages (session_id, id);

-- 클라이언트 직접 접근은 막고 FastAPI의 서버 권한으로만 쓴다. 정책을 만들지
-- 않았으므로 anon/authenticated에 허용되는 행이 없다(다른 B 테이블과 동일).
alter table public.session_messages enable row level security;
revoke all on table public.session_messages from anon, authenticated;

commit;
