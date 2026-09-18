begin;

-- Package B: 세션 단위 현재 상태. read-modify-write로 통째로 갱신된다
-- (agent_states/store.py의 get_state/save_state와 1:1 대응).
-- updated_at/last_active_at은 애플리케이션(session.touch() 등)이 필드별로
-- 다르게 관리한다 (예: GPS 갱신은 last_active_at만 건드리고 updated_at은
-- 건드리지 않음) — 그래서 이 테이블에는 자동 updated_at 트리거를 달지
-- 않는다. 트리거가 있으면 이 구분이 깨진다.
create table public.agent_states (
  session_id text primary key,
  user_conditions jsonb not null default '{}'::jsonb,
  api_context jsonb not null default '{}'::jsonb,
  condition_version integer not null default 0,
  last_run_id text,
  last_intent text,
  status text not null default 'active',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  last_active_at timestamptz not null default now(),

  constraint agent_states_session_id_not_blank
    check (btrim(session_id) <> ''),
  constraint agent_states_status_valid
    check (status in ('active', 'expired')),
  constraint agent_states_condition_version_nonnegative
    check (condition_version >= 0),
  constraint agent_states_user_conditions_is_object
    check (jsonb_typeof(user_conditions) = 'object'),
  constraint agent_states_api_context_is_object
    check (jsonb_typeof(api_context) = 'object')
);

-- Package B: 세션 단위 추천·거절 이력. 마찬가지로 통째로 읽고 통째로
-- 다시 쓰는 구조다(history.py의 get_or_create → save_history 패턴).
create table public.recommendation_histories (
  session_id text primary key,
  recommended jsonb not null default '[]'::jsonb,
  rejected jsonb not null default '[]'::jsonb,
  updated_at timestamptz not null default now(),

  constraint recommendation_histories_session_id_not_blank
    check (btrim(session_id) <> ''),
  constraint recommendation_histories_recommended_is_array
    check (jsonb_typeof(recommended) = 'array'),
  constraint recommendation_histories_rejected_is_array
    check (jsonb_typeof(rejected) = 'array')
);

-- Package B: 조건 변경 기록. append-only — 수정·삭제 없이 행만 추가된다.
create table public.condition_change_logs (
  id bigserial primary key,
  session_id text not null,
  run_id text not null,
  seq integer not null,
  op text not null,
  field text,
  before_value jsonb,
  after_value jsonb,
  reset_scope text,
  applied_at timestamptz not null default now(),

  constraint condition_change_logs_session_id_not_blank
    check (btrim(session_id) <> ''),
  constraint condition_change_logs_run_id_not_blank
    check (btrim(run_id) <> ''),
  constraint condition_change_logs_op_not_blank
    check (btrim(op) <> '')
);

-- Package B (AF-12/LLMOps): 실행 단계 기록. append-only.
-- step/prompt_version/scoring_version/variant_id/error_type은 호출자(A/C/D)가
-- 해석한 값을 그대로 저장하며 값의 허용 목록을 강제하지 않는다
-- (docs/package-b/llmops-trace-contract-v1.md — B는 값의 의미를 판단하지 않는다).
create table public.trace_records (
  id bigserial primary key,
  session_id text not null,
  run_id text not null,
  trace_id text not null,
  step text not null,
  prompt_version text,
  scoring_version text,
  variant_id text,
  latency_ms integer,
  token_usage integer,
  error_type text,
  recorded_at timestamptz not null default now(),

  constraint trace_records_session_id_not_blank
    check (btrim(session_id) <> ''),
  constraint trace_records_run_id_not_blank
    check (btrim(run_id) <> ''),
  constraint trace_records_trace_id_not_blank
    check (btrim(trace_id) <> ''),
  constraint trace_records_step_not_blank
    check (btrim(step) <> ''),
  constraint trace_records_latency_ms_nonnegative
    check (latency_ms is null or latency_ms >= 0),
  constraint trace_records_token_usage_nonnegative
    check (token_usage is null or token_usage >= 0)
);

-- append-only 두 테이블은 session_id로 조회하고 id 순으로 정렬해서 꺼낸다
-- (get_change_logs/get_traces).
create index condition_change_logs_session_id_id_idx
  on public.condition_change_logs (session_id, id);

create index trace_records_session_id_id_idx
  on public.trace_records (session_id, id);

-- 클라이언트의 직접 접근은 차단하고 FastAPI의 서버 권한(secret key)을
-- 통해서만 사용한다. RLS 정책을 만들지 않은 상태이므로 anon/authenticated에는
-- 허용되는 행이 없다 (place 테이블들과 동일한 원칙).
alter table public.agent_states enable row level security;
alter table public.recommendation_histories enable row level security;
alter table public.condition_change_logs enable row level security;
alter table public.trace_records enable row level security;

revoke all on table public.agent_states from anon, authenticated;
revoke all on table public.recommendation_histories from anon, authenticated;
revoke all on table public.condition_change_logs from anon, authenticated;
revoke all on table public.trace_records from anon, authenticated;

commit;
