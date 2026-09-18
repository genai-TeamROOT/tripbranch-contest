begin;

-- 집중률 API의 tAtsNm은 부분 일치 검색인데, 공백이 든 값을 넘기면 무엇을 넣든 0건이
-- 돌아온다(2026-08-04 실측: '운현궁' 30건, '서울 운현궁' 0건). 그래서 조회에 쓸 값과
-- 응답을 대조할 정식 명칭이 서로 달라야 한다.
--
-- primary_concentration_name에 '앞길'·'100주년' 같은 검색어를 넣으면 컬럼 의미가
-- 어긋나고, 별칭 배열 첫 항목을 정식 명칭으로 쓰는 암묵 규약이 생긴다. 조회용 값에
-- 전용 컬럼을 준다.
--
--   primary_concentration_name  '종묘 [유네스코 세계유산]'  정식 명칭(응답 대조용)
--   concentration_search_key    '종묘'                     검색어(tAtsNm 전달용)
--   concentration_aliases       '{}'                       다른 표기(본래 용도)
--
-- nullable로 둔다. 값이 없으면 호출자가 정식 명칭을 그대로 쓰므로 적재 전 기존 행이
-- 그대로 동작하고, 마이그레이션과 코드 배포 순서를 따지지 않아도 된다.
alter table public.place_concentration_mappings
  add column if not exists concentration_search_key text;

alter table public.place_concentration_mappings
  drop constraint if exists place_concentration_mappings_search_key_not_blank;

alter table public.place_concentration_mappings
  add constraint place_concentration_mappings_search_key_not_blank
    check (
      concentration_search_key is null
      or btrim(concentration_search_key) <> ''
    );

-- 공백이 든 검색어는 조회가 0건이 되므로 애초에 저장하지 않는다.
alter table public.place_concentration_mappings
  drop constraint if exists place_concentration_mappings_search_key_no_space;

alter table public.place_concentration_mappings
  add constraint place_concentration_mappings_search_key_no_space
    check (
      concentration_search_key is null
      or concentration_search_key !~ '\s'
    );

comment on column public.place_concentration_mappings.concentration_search_key is
  '집중률 API tAtsNm에 넣을 검색어. 공백이 없어야 한다. 비어 있으면 primary_concentration_name을 쓴다.';

commit;
