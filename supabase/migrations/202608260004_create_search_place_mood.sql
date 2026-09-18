begin;

-- 사진 임베딩으로 "분위기가 닮은 장소"를 찾는 RPC.
--
-- PostgREST로는 벡터 거리 연산자(<=>)를 정렬 기준으로 쓸 수 없어 함수로 뺀다.
-- search_place_evidence와 같은 이유이고 호출 규약도 맞춘다.
--
-- place_mood_vectors.embedding은 적재 때 길이 1로 정규화했으므로 코사인
-- 거리(<=>)를 1에서 빼면 그대로 코사인 유사도다. 질의 벡터도 같은 조건으로
-- 정규화해서 넘겨야 한다 — 안 하면 값의 범위가 −1~1을 벗어난다.
--
-- 후보를 좁히는 방식이 search_place_evidence와 다르다. 저쪽은 40,389행이라
-- 좁히지 않으면 6~9초가 걸려 500건 상한을 강제한다. 이쪽은 장소당 한 행이라
-- 종로구만 적재한 지금 631행이고 서울 25개 구로 넓혀도 6,000~10,000행이다.
-- 그래서 후보 배열이 비어 있으면 전체를 훑는 것을 허용한다 — "이 사진과 닮은
-- 곳 아무데나"가 실제로 있을 수 있는 질문이고, HNSW 인덱스가 그 경로를
-- 받쳐준다(202608260003).
--
-- 다만 상한은 둔다. 후보를 넘길 때는 배열이 인덱스를 무력화해 순차 스캔이
-- 되므로, 저쪽과 같은 500건에서 끊는다.
--
-- p_min_similarity 기본값 0.0은 실측 전 임시값이다. 사진끼리의 유사도 컷을
-- 아직 재지 않았다 — 분위기 축(axis_scores) 쪽은 사람 정답표 77곳으로 AUC를
-- 쟀지만(D-087), 사진-사진 유사도의 "이 정도면 닮았다" 경계는 표본이 없다.
-- 재기 전까지는 필터를 걸지 않고 순위만 쓴다.
create or replace function public.search_place_mood(
  p_query_embedding vector(768),
  p_candidate_content_ids text[] default null,
  p_match_count int default 10,
  p_min_similarity float default 0.0
)
returns table (
  content_id text,
  similarity float,
  axis_scores jsonb,
  photo_count int
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
  select
    v.content_id,
    (1 - (v.embedding operator(public.<=>) p_query_embedding))::float as similarity,
    v.axis_scores,
    v.photo_count
  from public.place_mood_vectors v
  where
    -- null이면 전체, 배열이면 그 안에서만. coalesce로 접지 않는 이유는
    -- 빈 배열과 null을 구분해야 하기 때문이다 — 후보를 좁히려다 빈 배열을
    -- 넘긴 호출이 전체 검색으로 둔갑하면 안 된다.
    (p_candidate_content_ids is null
     or v.content_id = any(p_candidate_content_ids))
    and (1 - (v.embedding operator(public.<=>) p_query_embedding))
        >= p_min_similarity
  order by v.embedding operator(public.<=>) p_query_embedding
  limit p_match_count;
end;
$$;

revoke execute on function public.search_place_mood(
  vector, text[], int, float
) from public, anon, authenticated;

grant execute on function public.search_place_mood(
  vector, text[], int, float
) to service_role;

commit;
