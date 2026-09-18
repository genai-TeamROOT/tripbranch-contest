begin;

-- TripBranch_RAG_Distributed_Embedding(24개구 분산 임베딩)에서 naver_post·
-- google_review 외에 TourAPI 개요(tour_overview)도 청킹·임베딩해 검색
-- 후보로 쓰기로 했다(2026-09-07 결정). place_embeddings_source_type_valid가
-- naver_post/google_review만 허용해 그대로는 적재가 막히므로 값을 넓힌다.
--
-- tour_overview는 리뷰가 아니라 장소 공식 소개문이라 rating이 없고 문서당
-- 1건뿐이라는 점에서 다른 두 source_type과 성격이 다르지만, 컬럼 구조
-- (source_text/source_ref/source_url/published_at nullable)는 그대로
-- 수용 가능해 테이블을 새로 만들지 않고 허용값만 늘린다.
alter table public.place_embeddings
  drop constraint place_embeddings_source_type_valid;

alter table public.place_embeddings
  add constraint place_embeddings_source_type_valid
  check (source_type in ('naver_post', 'google_review', 'tour_overview'));

commit;
