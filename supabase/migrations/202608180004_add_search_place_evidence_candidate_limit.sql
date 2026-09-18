begin;

-- 후보를 안 좁히고 부르면 40,389행 전체를 스캔해 수 초가 걸린다(§7.13).
-- anon/authenticated는 이 함수를 호출할 수 없어 외부 위험은 없지만, 내부
-- 호출 코드가 실수로 후보를 안 좁히면 커넥션을 오래 붙잡아 다른 요청과
-- 경합할 수 있다. 실측(200~400건은 100~300ms, 844건은 6~9초)을 근거로
-- 500건을 상한으로 두고, 넘으면 즉시 에러로 끝낸다 — 느린 쿼리로 커넥션을
-- 붙잡는 대신 호출부가 바로 알아채게 한다. 이 체크는 절차형 분기가 필요해
-- language sql에서 plpgsql로 바꾼다.
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
      e.source_text,
      e.source_url,
      e.source_ref,
      e.published_at,
      1 - (e.embedding operator(public.<=>) p_query_embedding) as similarity
    from public.place_embeddings e
    where e.content_id = any(p_candidate_content_ids)
  ),
  filtered as (
    select *
    from scored
    where similarity >= p_min_similarity
  ),
  -- 같은 글/리뷰 안에서는 대표 1건만 남긴다.
  deduped as (
    select
      filtered.*,
      row_number() over (
        partition by
          filtered.content_id,
          coalesce(filtered.source_url, filtered.source_ref)
        order by filtered.similarity desc
      ) as url_rank
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
    where deduped.url_rank = 1
  )
  select
    ranked.content_id,
    max(ranked.place_title) as place_title,
    avg(ranked.similarity)::float as avg_similarity,
    jsonb_agg(
      jsonb_build_object(
        'source_text', ranked.source_text,
        'source_url', ranked.source_url,
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

-- CREATE OR REPLACE가 기존 권한을 보존하긴 하지만, 이 마이그레이션 파일만
-- 봐도 권한 상태를 확인할 수 있도록 명시적으로 다시 선언한다.
revoke execute on function public.search_place_evidence(
  vector, text[], int, float
) from public, anon, authenticated;

grant execute on function public.search_place_evidence(
  vector, text[], int, float
) to service_role;

commit;
