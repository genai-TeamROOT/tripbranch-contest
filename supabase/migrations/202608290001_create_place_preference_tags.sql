begin;

create table public.place_preference_tags (
  content_id text not null,
  preference_code text not null,
  preference_label text not null,
  display_rank smallint not null,
  mention_count integer not null,
  positive_document_count integer not null default 0,
  negative_document_count integer not null default 0,
  source_count smallint not null default 0,
  confidence numeric(5, 4),
  extraction_version text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (content_id, preference_code),
  constraint place_preference_tags_content_id_fk
    foreign key (content_id) references public.places (content_id) on delete cascade,
  constraint place_preference_tags_code_not_blank check (btrim(preference_code) <> ''),
  constraint place_preference_tags_label_not_blank check (btrim(preference_label) <> ''),
  constraint place_preference_tags_rank_positive check (display_rank > 0),
  constraint place_preference_tags_counts_nonnegative check (
    mention_count >= 0 and positive_document_count >= 0
    and negative_document_count >= 0 and source_count >= 0
  ),
  constraint place_preference_tags_confidence_valid
    check (confidence is null or confidence between 0 and 1)
);

create index place_preference_tags_content_rank_idx
  on public.place_preference_tags (content_id, display_rank);

create trigger place_preference_tags_set_updated_at
before update on public.place_preference_tags
for each row execute function public.set_updated_at();

alter table public.place_preference_tags enable row level security;
revoke all on table public.place_preference_tags from anon, authenticated;

comment on table public.place_preference_tags is
  '리뷰와 블로그 문서에서 추출한 장소별 취향 태그와 문서 단위 언급 수.';

commit;
