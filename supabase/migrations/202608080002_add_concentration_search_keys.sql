begin;

-- D-057. 집중률 조회용 검색어를 하나만 두던 것을 순서 있는 목록으로 교체한다.
--
-- tAtsNm은 공백이 든 값에 0건을 돌려주므로 이름을 잘라 검색어 하나를 골라 왔다.
-- 그 결과 '서울 동대문 닭한마리 골목'의 검색어가 '닭한마리' 하나로 고정돼,
-- 사용자가 다른 표현으로 물으면 찾지 못한다. 토큰을 모두 갖고 순서대로 시도한다.
--
-- 단수 컬럼을 남겨 병행하지 않는다. 목록의 1순위는 기존 검색어와 같은 값이라
-- 진실의 원천이 둘이 되고, 한쪽만 갱신되면 같은 요청 안에서 결과가 갈린다
-- (저장소에서 반복된 "레거시 필드의 이중 경로" 유형). 같은 마이그레이션에서
-- 추가·backfill·삭제를 끝내고, 코드도 같은 PR에서 전부 이관한다.
alter table public.place_concentration_mappings
  add column if not exists concentration_search_keys text[] not null default '{}';

-- 기존 값을 1순위로 옮긴다. 검색어가 비어 있던 행은 정식 명칭을 그대로 조회하던
-- 것이므로 그 이름을 넣는다. 이후 build/import가 나머지 토큰을 덧붙인다.
update public.place_concentration_mappings
   set concentration_search_keys =
       array[coalesce(nullif(btrim(concentration_search_key), ''), primary_concentration_name)]
 where cardinality(concentration_search_keys) = 0;

-- 배열 원소에 null이나 공백이 들어가면 조회가 조용히 0건이 된다. 단수 컬럼에
-- 걸어둔 제약(202608040001)을 원소 단위로 옮긴다.
alter table public.place_concentration_mappings
  drop constraint if exists place_concentration_mappings_search_keys_no_null;

alter table public.place_concentration_mappings
  add constraint place_concentration_mappings_search_keys_no_null
    check (array_position(concentration_search_keys, null) is null);

alter table public.place_concentration_mappings
  drop constraint if exists place_concentration_mappings_search_keys_no_space;

-- 체크 제약에는 서브쿼리를 쓸 수 없어 원소를 이어붙여 검사한다. 구분자 ','에는
-- 공백이 없으므로, 이어붙인 문자열에 공백이 있으면 어떤 원소가 공백을 가진 것이다.
alter table public.place_concentration_mappings
  add constraint place_concentration_mappings_search_keys_no_space
    check (array_to_string(concentration_search_keys, ',') !~ '\s');

-- 빈 문자열은 위 검사에 걸리지 않으므로 따로 막는다.
alter table public.place_concentration_mappings
  drop constraint if exists place_concentration_mappings_search_keys_not_blank;

alter table public.place_concentration_mappings
  add constraint place_concentration_mappings_search_keys_not_blank
    check (not ('' = any(concentration_search_keys)));

-- 조회할 값이 하나도 없으면 그 매핑은 존재 의미가 없다.
alter table public.place_concentration_mappings
  drop constraint if exists place_concentration_mappings_search_keys_not_empty;

alter table public.place_concentration_mappings
  add constraint place_concentration_mappings_search_keys_not_empty
    check (cardinality(concentration_search_keys) > 0);

comment on column public.place_concentration_mappings.concentration_search_keys is
  'tAtsNm에 넣을 검색어 목록. 앞에서부터 시도하고 결과가 나오면 멈춘다. 1순위는 이관 전 concentration_search_key이며, 나머지는 긴 토큰 우선·동률 시 뒤쪽 우선으로 붙인다. 원소에 공백이 있으면 안 된다(D-057).';

-- 단수 컬럼과 그 제약을 걷어낸다.
alter table public.place_concentration_mappings
  drop constraint if exists place_concentration_mappings_search_key_not_blank;

alter table public.place_concentration_mappings
  drop constraint if exists place_concentration_mappings_search_key_no_space;

alter table public.place_concentration_mappings
  drop column if exists concentration_search_key;

commit;
