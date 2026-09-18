begin;

-- place_mood_vectors에 최근접 이웃 검색용 HNSW 인덱스를 건다.
--
-- 이 파일을 202608260002에서 떼어낸 이유는 실행 순서를 강제하기 위해서다.
-- 202608250002_restore_place_embeddings_hnsw_index에 남은 기록대로, HNSW가 걸린
-- 상태에서 대량 upsert하면 인덱스 갱신 비용 때문에 매 요청이
-- statement_timeout(57014)에 걸린다. 텍스트 임베딩 쪽에서는 그때 인덱스를 지워
-- 우회한 뒤 다시 만들지 않아, 57,331건이 인덱스 없이 쌓인 채로 발견됐다.
--
--   테이블 생성(202608260002) → 적재(scripts/import_mood_embeddings.py) → 이 파일
--
-- 대량 재적재를 할 때도 같은 순서를 따른다. 인덱스를 drop하고, 넣고, 이 파일의
-- create를 다시 실행한다. 지운 채로 두지 않도록 이력을 파일로 남긴다.
--
-- 코사인 유사도를 쓰므로 vector_cosine_ops다. 벡터가 이미 정규화돼 있어
-- vector_ip_ops(내적)로도 같은 순서가 나오지만, 나중에 정규화되지 않은 벡터가
-- 섞여 들어와도 결과가 무너지지 않도록 코사인 쪽을 쓴다.
--
-- 종로구만 적재한 631행 규모에서는 순차 스캔도 충분히 빠르므로 이 인덱스가
-- 당장 필요하지는 않다. 서울 25개 구로 넓히면 6,000~10,000행이 되고 그때부터
-- 의미가 생긴다. 지금 파일을 만들어 두는 것은 그 시점에 잊지 않기 위해서다.
create index if not exists place_mood_vectors_embedding_hnsw_idx
  on public.place_mood_vectors
  using hnsw (embedding vector_cosine_ops);

-- place_image_embeddings에는 인덱스를 걸지 않는다. 검색 대상이 아니라 평균을 다시
-- 계산하고 추천 근거를 보여주기 위한 보관용이고, 인덱스가 있으면 적재만 느려진다.

commit;
