begin;

-- 집중률 API의 장소명과 TourAPI 장소를 연결한다.
-- 집중률 API에 별도 고유 ID가 없어 TripBranch 기준 content_id를 PK로 사용한다.
create table public.place_concentration_mappings (
  content_id text primary key,
  primary_concentration_name text not null,
  concentration_aliases text[] not null default '{}',
  match_method text not null,
  confidence_score numeric(5, 4),
  verified_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  constraint place_concentration_mappings_place_fk
    foreign key (content_id)
    references public.places (content_id)
    on delete cascade,
  constraint place_concentration_mappings_primary_name_not_blank
    check (btrim(primary_concentration_name) <> ''),
  constraint place_concentration_mappings_aliases_no_null
    check (array_position(concentration_aliases, null) is null),
  constraint place_concentration_mappings_method_valid
    check (
      match_method in (
        'exact',
        'normalized',
        'manual',
        'exact_with_alias'
      )
    ),
  constraint place_concentration_mappings_confidence_valid
    check (
      confidence_score is null
      or confidence_score between 0 and 1
    )
);

create index place_concentration_mappings_primary_name_idx
  on public.place_concentration_mappings (primary_concentration_name);

create trigger place_concentration_mappings_set_updated_at
before update on public.place_concentration_mappings
for each row
execute function public.set_updated_at();

alter table public.place_concentration_mappings enable row level security;

revoke all
  on table public.place_concentration_mappings
  from anon, authenticated;

grant select, insert, update, delete
  on table public.place_concentration_mappings
  to service_role;

commit;
