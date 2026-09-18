begin;

-- 관광지별 연관 관광지 정보(TarRlteTarService1)를 content_id 기준 엣지로 저장한다.
-- tAtsCd/rlteTatsCd는 TourAPI content_id와 다른 해시코드라 원본 그대로는 못 쓰고,
-- build_place_association_mappings.py로 이름+구 매칭한 결과만 골라 적재한다
-- (import_place_associations.py). 수집 범위가 종로구·중구 "기준" 관광지뿐이라
-- from_content_id는 그 두 구로 한정되고, to_content_id는 연관 장소가 속한 다른 구도
-- 섞여 있는 단방향 데이터다 — 서울 전역 확장은 이후 별도 작업이다.
create table public.place_associations (
  from_content_id text not null,
  to_content_id text not null,
  category text not null,
  rank smallint not null,
  base_ym text not null,
  created_at timestamptz not null default now(),

  constraint place_associations_pk
    primary key (from_content_id, to_content_id, base_ym),
  constraint place_associations_from_fk
    foreign key (from_content_id)
    references public.places (content_id)
    on delete cascade,
  constraint place_associations_to_fk
    foreign key (to_content_id)
    references public.places (content_id)
    on delete cascade,
  constraint place_associations_not_self
    check (from_content_id <> to_content_id),
  constraint place_associations_rank_valid
    check (rank between 1 and 50),
  constraint place_associations_base_ym_valid
    check (base_ym ~ '^[0-9]{6}$'),
  constraint place_associations_category_valid
    check (category in ('전체', '관광지', '음식', '숙박'))
);

create index place_associations_from_content_id_idx
  on public.place_associations (from_content_id, rank);

create index place_associations_to_content_id_idx
  on public.place_associations (to_content_id);

alter table public.place_associations enable row level security;

revoke all
  on table public.place_associations
  from anon, authenticated;

grant select, insert, update, delete
  on table public.place_associations
  to service_role;

comment on table public.place_associations is
  '한국관광공사 TarRlteTarService1(관광지별 연관 관광지 정보)에서 수집한 실제 동선 기반 연관 관광지 엣지. base_ym 기준 월별 스냅샷을 이력으로 보존하며, 같은 base_ym을 재수집하면 upsert로 rank/category만 덮어쓴다(created_at은 최초 적재 시각을 유지).';

commit;
