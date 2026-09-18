begin;

-- 상세 카드에 보여 줄 태그별 대표 후기 문장이다. 장소×태그×판정당 최대 두 문장만
-- 저장하며, 문서 전체나 원문 본문은 중복 보관하지 않는다.
create table public.place_preference_evidence (
  content_id text not null,
  preference_code text not null,
  polarity text not null,
  evidence_rank smallint not null,
  document_id text not null,
  source_evidence_id text not null,
  evidence_text text not null,
  source_type text not null,
  source_url text,
  match_strength smallint not null,
  extraction_version text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  primary key (content_id, preference_code, polarity, evidence_rank),
  constraint place_preference_evidence_tag_fk
    foreign key (content_id, preference_code)
    references public.place_preference_tags (content_id, preference_code)
    on delete cascade,
  constraint place_preference_evidence_polarity_valid
    check (polarity in ('positive', 'mixed', 'negative')),
  constraint place_preference_evidence_rank_valid
    check (evidence_rank between 1 and 2),
  constraint place_preference_evidence_text_not_blank
    check (btrim(evidence_text) <> ''),
  constraint place_preference_evidence_source_not_blank
    check (btrim(source_type) <> ''),
  constraint place_preference_evidence_match_strength_valid
    check (match_strength >= 0)
);

create index place_preference_evidence_content_tag_idx
  on public.place_preference_evidence (content_id, preference_code, polarity, evidence_rank);

create trigger place_preference_evidence_set_updated_at
before update on public.place_preference_evidence
for each row execute function public.set_updated_at();

alter table public.place_preference_evidence enable row level security;
revoke all on table public.place_preference_evidence from anon, authenticated;

comment on table public.place_preference_evidence is
  '장소 상세 카드에 표시하는 취향 태그별 대표 후기 근거 문장. 장소·태그·판정별 최대 2건.';

commit;
