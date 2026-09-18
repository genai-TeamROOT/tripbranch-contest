begin;

-- PostgREST 요청은 DB 세션을 계속 유지하지 않으므로 세션 기반 advisory lock 대신
-- 지역별 잠금 행을 사용해 동일 시·군·구의 동기화가 겹치지 않게 한다.
create table public.place_sync_locks (
  area_code text not null,
  district_code text not null,
  sync_run_id uuid not null,
  acquired_at timestamptz not null default now(),
  expires_at timestamptz not null,

  constraint place_sync_locks_pk
    primary key (area_code, district_code),
  constraint place_sync_locks_sync_run_unique
    unique (sync_run_id),
  constraint place_sync_locks_area_code_not_blank
    check (btrim(area_code) <> ''),
  constraint place_sync_locks_district_code_not_blank
    check (btrim(district_code) <> ''),
  constraint place_sync_locks_expiration_valid
    check (expires_at > acquired_at),
  constraint place_sync_locks_sync_run_fk
    foreign key (sync_run_id)
    references public.place_sync_runs (id)
    on delete cascade
);

-- INSERT ... ON CONFLICT의 조건부 UPDATE를 사용해 잠금 확인과 획득을 하나의
-- DB 트랜잭션에서 처리한다. 활성 잠금은 덮어쓰지 않고, 만료된 잠금 또는 같은
-- 실행의 갱신 요청만 허용한다.
create or replace function public.try_acquire_place_sync_lock(
  p_area_code text,
  p_district_code text,
  p_sync_run_id uuid,
  p_lock_ttl interval default interval '2 hours'
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  acquired boolean;
  replaced_sync_run_id uuid;
begin
  if p_lock_ttl <= interval '0 seconds' then
    raise exception 'place sync lock TTL must be positive';
  end if;

  select lock.sync_run_id
    into replaced_sync_run_id
    from public.place_sync_locks as lock
   where lock.area_code = p_area_code
     and lock.district_code = p_district_code
     and lock.expires_at <= now();

  insert into public.place_sync_locks (
    area_code,
    district_code,
    sync_run_id,
    acquired_at,
    expires_at
  )
  values (
    p_area_code,
    p_district_code,
    p_sync_run_id,
    now(),
    now() + p_lock_ttl
  )
  on conflict (area_code, district_code)
  do update
     set sync_run_id = excluded.sync_run_id,
         acquired_at = excluded.acquired_at,
         expires_at = excluded.expires_at
   where public.place_sync_locks.expires_at <= now()
      or public.place_sync_locks.sync_run_id = excluded.sync_run_id
  returning true into acquired;

  if coalesce(acquired, false) and replaced_sync_run_id is not null
     and replaced_sync_run_id <> p_sync_run_id then
    update public.place_sync_runs
       set status = 'failed',
           completed_at = now(),
           error_summary = coalesce(error_summary, '{}'::jsonb)
             || jsonb_build_object('STALE_SYNC_LOCK_REPLACED', 1)
     where id = replaced_sync_run_id
       and status = 'running';
  end if;

  return coalesce(acquired, false);
end;
$$;

-- 실행 ID까지 일치할 때만 삭제해, 늦게 종료된 이전 프로세스가 새 실행의 잠금을
-- 실수로 해제하지 못하게 한다.
create or replace function public.release_place_sync_lock(
  p_area_code text,
  p_district_code text,
  p_sync_run_id uuid
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  released boolean;
begin
  delete from public.place_sync_locks
   where area_code = p_area_code
     and district_code = p_district_code
     and sync_run_id = p_sync_run_id
  returning true into released;

  return coalesce(released, false);
end;
$$;

alter table public.place_sync_locks enable row level security;

revoke all on table public.place_sync_locks from anon, authenticated;
revoke execute on function public.try_acquire_place_sync_lock(
  text, text, uuid, interval
) from public, anon, authenticated;
revoke execute on function public.release_place_sync_lock(
  text, text, uuid
) from public, anon, authenticated;

grant select, insert, update, delete on table public.place_sync_locks to service_role;
grant execute on function public.try_acquire_place_sync_lock(
  text, text, uuid, interval
) to service_role;
grant execute on function public.release_place_sync_lock(
  text, text, uuid
) to service_role;

commit;
