begin;

-- UUID 기본값 생성에 사용하는 gen_random_uuid()를 보장한다.
create extension if not exists pgcrypto;

-- 애플리케이션에서 updated_at을 직접 관리하지 않아도 실제 수정 시각이
-- 일관되게 기록되도록 공통 트리거 함수를 둔다.
create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- 한 시·군·구의 전체 목록·상세정보 동기화 한 번의 실행 결과를 기록한다.
-- 장소별 최신 상태와 별도로 배치 누락 및 부분 실패를 판단하기 위한 테이블이다.
create table public.place_sync_runs (
  id uuid primary key default gen_random_uuid(),
  area_code text not null,
  district_code text not null,
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  status text not null default 'running',
  api_total_count integer,
  processed_count integer not null default 0,
  success_count integer not null default 0,
  failed_count integer not null default 0,
  new_count integer not null default 0,
  updated_count integer not null default 0,
  deactivated_count integer not null default 0,
  error_summary jsonb,
  created_at timestamptz not null default now(),

  constraint place_sync_runs_area_code_not_blank
    check (btrim(area_code) <> ''),
  constraint place_sync_runs_district_code_not_blank
    check (btrim(district_code) <> ''),
  constraint place_sync_runs_status_valid
    check (status in ('running', 'success', 'partial_failure', 'failed')),
  constraint place_sync_runs_counts_nonnegative
    check (
      (api_total_count is null or api_total_count >= 0)
      and processed_count >= 0
      and success_count >= 0
      and failed_count >= 0
      and new_count >= 0
      and updated_count >= 0
      and deactivated_count >= 0
    ),
  -- 실행 중에는 종료 시각이 없어야 하고, 종료 상태에서는 유효한 종료 시각이
  -- 반드시 있어야 한다.
  constraint place_sync_runs_completion_valid
    check (
      (status = 'running' and completed_at is null)
      or (
        status in ('success', 'partial_failure', 'failed')
        and completed_at is not null
        and completed_at >= started_at
      )
    ),
  constraint place_sync_runs_success_has_no_failures
    check (status <> 'success' or failed_count = 0),
  constraint place_sync_runs_error_summary_is_object
    check (error_summary is null or jsonb_typeof(error_summary) = 'object')
);

-- TourAPI 목록의 장소 기본정보와 detailIntro2 운영정보를 함께 캐시한다.
-- 원문과 정규화 결과를 모두 보존해 파서 변경 시 API 재호출 없이 재처리한다.
create table public.places (
  content_id text primary key,
  content_type_id text not null,
  title text not null,
  address text,
  latitude double precision,
  longitude double precision,
  area_code text not null,
  district_code text not null,
  lcls_systm1 text,
  lcls_systm2 text,
  lcls_systm3 text,
  operating_hours_raw text,
  rest_date_raw text,
  operating_schedule jsonb,
  operating_parse_status text not null default 'unknown',
  operating_parser_version text not null default 'operating-hours-1.0.0',
  source_modified_at timestamptz,
  list_fetched_at timestamptz not null,
  detail_fetched_at timestamptz,
  last_seen_at timestamptz not null,
  detail_fetch_status text not null default 'pending',
  detail_error_code text,
  is_active boolean not null default true,
  inactive_reason text,
  inactive_at timestamptz,
  last_sync_run_id uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  constraint places_content_id_not_blank
    check (btrim(content_id) <> ''),
  constraint places_content_type_id_not_blank
    check (btrim(content_type_id) <> ''),
  constraint places_title_not_blank
    check (btrim(title) <> ''),
  constraint places_area_code_not_blank
    check (btrim(area_code) <> ''),
  constraint places_district_code_not_blank
    check (btrim(district_code) <> ''),
  constraint places_latitude_valid
    check (latitude is null or latitude between -90 and 90),
  constraint places_longitude_valid
    check (longitude is null or longitude between -180 and 180),
  constraint places_category_hierarchy_valid
    check (
      (lcls_systm2 is null or lcls_systm1 is not null)
      and (
        lcls_systm3 is null
        or (lcls_systm1 is not null and lcls_systm2 is not null)
      )
    ),
  -- 정규화 결과는 향후 필드가 확장되더라도 JSON 객체 형태를 유지한다.
  constraint places_operating_schedule_is_object
    check (
      operating_schedule is null
      or jsonb_typeof(operating_schedule) = 'object'
    ),
  constraint places_operating_parse_status_valid
    check (
      operating_parse_status in ('parsed', 'partial', 'unknown', 'assumed')
    ),
  constraint places_operating_parser_version_not_blank
    check (btrim(operating_parser_version) <> ''),
  constraint places_detail_fetch_status_valid
    check (detail_fetch_status in ('pending', 'success', 'empty', 'failed')),
  -- 상세조회 성공으로 표시한 장소는 실제 조회 완료 시각을 가져야 한다.
  constraint places_successful_detail_has_fetched_at
    check (detail_fetch_status <> 'success' or detail_fetched_at is not null),
  -- 내부 오류 코드는 실패 상태에서만 남겨 오래된 오류가 정상 상태에 섞이지
  -- 않도록 한다.
  constraint places_detail_error_matches_status
    check (
      (detail_fetch_status = 'failed' and detail_error_code is not null)
      or (detail_fetch_status <> 'failed' and detail_error_code is null)
    ),
  -- 활성 장소에는 비활성 메타데이터가 없어야 하며, 비활성 장소에는 사유와
  -- 전환 시각이 모두 있어야 한다.
  constraint places_active_state_valid
    check (
      (
        is_active
        and inactive_reason is null
        and inactive_at is null
      )
      or (
        not is_active
        and inactive_reason is not null
        and inactive_at is not null
      )
    ),
  constraint places_inactive_reason_valid
    check (
      inactive_reason is null
      or inactive_reason in (
        'missing_from_source',
        'closed',
        'moved_outside_area',
        'invalid_data',
        'manual_exclusion'
      )
    ),
  -- 마지막 동기화 실행 이력 정리는 장소 삭제로 이어지지 않도록 SET NULL을 쓴다.
  constraint places_last_sync_run_fk
    foreign key (last_sync_run_id)
    references public.place_sync_runs (id)
    on delete set null
);

-- TourAPI가 제공하지 않는 체류시간·추천 특성 등 TripBranch 자체 정보를 분리한다.
-- 외부 데이터 재동기화가 팀의 수동 보완값을 덮어쓰지 않게 하는 경계다.
create table public.place_enrichments (
  content_id text primary key,
  place_type text not null,
  place_tags text[] not null default '{}',
  estimated_visit_minutes integer,
  recommendation_tags text[] not null default '{}',
  weather_tags text[] not null default '{}',
  reservation_required boolean,
  source_type text not null,
  verified_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  -- 기준 장소가 실제 삭제되면 더 이상 의미가 없는 보완정보도 함께 제거한다.
  constraint place_enrichments_content_id_fk
    foreign key (content_id)
    references public.places (content_id)
    on delete cascade,
  constraint place_enrichments_place_type_valid
    check (
      place_type in (
        'attraction',
        'cultural_facility',
        'festival',
        'leisure',
        'shopping',
        'restaurant'
      )
    ),
  constraint place_enrichments_estimated_visit_minutes_positive
    check (
      estimated_visit_minutes is null
      or estimated_visit_minutes > 0
    ),
  constraint place_enrichments_source_type_valid
    check (source_type in ('manual_research', 'external_data', 'derived')),
  constraint place_enrichments_place_tags_no_null
    check (array_position(place_tags, null) is null),
  constraint place_enrichments_recommendation_tags_no_null
    check (array_position(recommendation_tags, null) is null),
  constraint place_enrichments_weather_tags_no_null
    check (array_position(weather_tags, null) is null)
);

-- 활성 장소를 지역과 유형으로 필터링하는 추천 후보 조회를 위한 인덱스다.
create index places_active_area_district_idx
  on public.places (area_code, district_code)
  where is_active;

create index places_active_content_type_idx
  on public.places (content_type_id)
  where is_active;

create index places_active_lcls_systm3_idx
  on public.places (lcls_systm3)
  where is_active;

-- TourAPI 변경 시각과 상세조회 TTL을 기준으로 갱신 대상을 찾는다.
create index places_source_modified_at_idx
  on public.places (source_modified_at);

create index places_detail_refresh_idx
  on public.places (detail_fetch_status, detail_fetched_at);

create index places_last_sync_run_id_idx
  on public.places (last_sync_run_id);

-- 최근 동기화 결과와 특정 지역의 동기화 이력을 빠르게 확인한다.
create index place_sync_runs_started_at_idx
  on public.place_sync_runs (started_at desc);

create index place_sync_runs_area_district_started_at_idx
  on public.place_sync_runs (area_code, district_code, started_at desc);

create index place_enrichments_place_type_idx
  on public.place_enrichments (place_type);

-- 배열 포함 검색을 사용하는 장소 세부 분류와 추천 특성에 GIN 인덱스를 둔다.
create index place_enrichments_place_tags_idx
  on public.place_enrichments using gin (place_tags);

create index place_enrichments_recommendation_tags_idx
  on public.place_enrichments using gin (recommendation_tags);

-- 기본 장소와 수동 보완정보가 수정될 때 updated_at을 자동 갱신한다.
create trigger places_set_updated_at
before update on public.places
for each row
execute function public.set_updated_at();

create trigger place_enrichments_set_updated_at
before update on public.place_enrichments
for each row
execute function public.set_updated_at();

-- 클라이언트의 직접 접근은 차단하고 FastAPI의 서버 권한을 통해서만 사용한다.
-- RLS 정책을 만들지 않은 상태이므로 anon/authenticated에는 허용되는 행이 없다.
alter table public.place_sync_runs enable row level security;
alter table public.places enable row level security;
alter table public.place_enrichments enable row level security;

revoke all on table public.place_sync_runs from anon, authenticated;
revoke all on table public.places from anon, authenticated;
revoke all on table public.place_enrichments from anon, authenticated;

-- 세 테이블과 관련 객체가 모두 생성된 경우에만 변경을 확정한다.
commit;
