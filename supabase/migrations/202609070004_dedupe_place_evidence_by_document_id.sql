begin;

-- v2 행은 source_ref를 청크가 아닌 원문 식별자로 보존하고 document_id를
-- 명시한다. 같은 장소의 Google 리뷰가 같은 지도 URL을 공유할 수 있으므로
-- source_url만으로 묶으면 서로 다른 리뷰가 한 건으로 축약된다. v2는
-- document_id를 우선하고, 기존 행만 과거 방식(source_url/source_ref)으로
-- 폴백한다.
create or replace function public.search_place_evidence(
  p_query_embedding vector(768),
  p_candidate_content_ids text[],
  p_match_count int default 3,
  p_min_similarity float default 0.0
)
returns table (
  content_id text,
  place_title text,
  avg_similarity float,
  evidence jsonb
)
language plpgsql
stable
security definer
set search_path = ''
set statement_timeout = '30s'
as $$
begin
  if coalesce(array_length(p_candidate_content_ids, 1), 0) > 500 then
    raise exception
      '후보 content_id가 %건입니다. 500건 이하로 좁혀서 호출하세요.',
      array_length(p_candidate_content_ids, 1);
  end if;

  return query
  with scored as (
    select
      e.content_id,
      e.place_title,
      e.source_type,
      e.source_text,
      e.source_url,
      e.source_ref,
      e.document_id,
      e.published_at,
      1 - (e.embedding operator(public.<=>) p_query_embedding) as similarity
    from public.place_embeddings e
    where e.content_id = any(p_candidate_content_ids)
  ),
  filtered as (
    select * from scored where similarity >= p_min_similarity
  ),
  deduped as (
    select
      filtered.*,
      row_number() over (
        partition by
          filtered.content_id,
          coalesce(filtered.document_id, filtered.source_url, filtered.source_ref)
        order by filtered.similarity desc
      ) as document_rank
    from filtered
  ),
  ranked as (
    select
      deduped.*,
      row_number() over (
        partition by deduped.content_id
        order by deduped.similarity desc
      ) as place_rank
    from deduped
    where deduped.document_rank = 1
  )
  select
    ranked.content_id,
    max(ranked.place_title) as place_title,
    avg(ranked.similarity)::float as avg_similarity,
    jsonb_agg(
      jsonb_build_object(
        'source_type', ranked.source_type,
        'source_text', ranked.source_text,
        'source_url', ranked.source_url,
        'source_ref', ranked.source_ref,
        'document_id', ranked.document_id,
        'similarity', ranked.similarity,
        'published_at', ranked.published_at
      )
      order by ranked.similarity desc
    ) as evidence
  from ranked
  where ranked.place_rank <= p_match_count
  group by ranked.content_id
  order by avg_similarity desc;
end;
$$;

revoke execute on function public.search_place_evidence(
  vector, text[], int, float
) from public, anon, authenticated;

grant execute on function public.search_place_evidence(
  vector, text[], int, float
) to service_role;

commit;
