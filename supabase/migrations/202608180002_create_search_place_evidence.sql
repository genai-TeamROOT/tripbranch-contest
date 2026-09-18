begin;

-- 취향 발화 임베딩과 후보 content_id를 받아 장소별 상위 근거를 한 번의
-- 왕복으로 집계한다(계획 문서 §2.10).
--   1) p_candidate_content_ids로 범위를 좁혀 전체 스캔을 피한다
--   2) 같은 글/리뷰(source_url)에서는 유사도가 가장 높은 1건만 남긴다 —
--      naver_post 한 글이 최대 3조각까지 나뉘어 "한 사람 의견이 세 명치"로
--      잡히는 걸 막는 다양성 규칙이다
--   3) 장소별 상위 p_match_count건의 유사도 평균으로 정렬한다
-- p_min_similarity 기본값 0.0은 실측 전 임시값이다 — 적재 후
-- measure_similarity_distribution.py로 분포를 재서 호출부에서 실제 컷값을
-- 넘기기 전까지는 필터링이 걸리지 않는다(§7.12 미해결 항목).
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
language sql
stable
security definer
set search_path = ''
as $$
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
$$;

revoke execute on function public.search_place_evidence(
  vector, text[], int, float
) from public, anon, authenticated;

grant execute on function public.search_place_evidence(
  vector, text[], int, float
) to service_role;

commit;
