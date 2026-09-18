-- 무장애 원문을 읽어 내린 접근 가능 판정을 장소마다 담는다.
--
-- 왜 필요한가. search_places_barrier_free(202609010001)의 판정은 "원문이 있으면
-- 그 편의가 있다"였다. `턱이 있어 접근 불가능한 구간 있음`처럼 원문이 스스로
-- 부정을 말하는 문장도 통과한다. 그렇다고 단어로 거를 수도 없다 — 접근로와
-- 주출입구는 문장의 주어가 턱·단차라서 `없다`가 긍정이다.
--
-- 그래서 원문 678문장을 사람이 하나씩 판정해
-- supabase/data/barrier_free_sentence_verdicts.csv에 남겼다. 그 판정을 여기 옮긴다.
--
-- **판정표가 원본이고 이 컬럼은 파생물이다.** 컬럼을 손으로 고치지 않는다.
-- CSV를 고치고 scripts/import_barrier_free_verdicts.py를 다시 돌린다. 그러지
-- 않으면 같은 판정이 두 곳에 서로 다르게 남는다.
--
-- 문장 판정표를 테이블로 넣고 조인하지 않는 이유는, 조인 열쇠가 긴 원문이라
-- 조회마다 그 값을 비교하게 되기 때문이다. 장소마다 펼쳐 두면 RPC의 판정이
-- `verdict <> 'impossible'` 한 줄로 끝난다.
--
-- 판정이 붙는 어휘는 셋뿐이다. 나머지 여섯(화장실·주차장·유아·대여·좌석·저상버스)은
-- 판정표를 만들지 않았으므로 RPC에서 지금까지의 원문 규칙을 그대로 쓴다.
alter table public.place_barrier_free
  add column if not exists wheelchair_access_verdict text,
  add column if not exists stroller_access_verdict text,
  add column if not exists visual_guide_verdict text;

-- 어휘를 못 박는다. 오타가 든 값이 들어가면 RPC의 `<> 'impossible'`을 통과해
-- 접근 불가인 장소가 후보로 나간다 — 오류는 나지 않고 결과만 틀린다.
alter table public.place_barrier_free
  add constraint place_barrier_free_wheelchair_verdict_valid
    check (wheelchair_access_verdict is null
           or wheelchair_access_verdict in ('possible', 'partial', 'impossible')),
  add constraint place_barrier_free_stroller_verdict_valid
    check (stroller_access_verdict is null
           or stroller_access_verdict in ('possible', 'partial', 'impossible')),
  add constraint place_barrier_free_visual_guide_verdict_valid
    check (visual_guide_verdict is null
           or visual_guide_verdict in ('possible', 'partial', 'impossible'));

comment on column public.place_barrier_free.wheelchair_access_verdict is
  '접근로·주출입구·엘리베이터 원문을 읽어 내린 휠체어 접근 판정. null은 판단할 원문이 없다는 뜻이라 후보에서 빠진다. 원본은 supabase/data/barrier_free_sentence_verdicts.csv이고 이 컬럼은 파생물이다.';
comment on column public.place_barrier_free.stroller_access_verdict is
  '같은 원문을 유모차 기준으로 읽은 판정. 통로가 좁아 휠체어가 막히는 곳도 유모차는 지나가고, 턱·계단은 둘 다 막힌다.';
comment on column public.place_barrier_free.visual_guide_verdict is
  '점자블록·점자안내·음성안내·안내견 원문을 읽어 내린 판정. 설치 위치를 적은 것(`점자블록 있음(주출입구)`)은 제한이 아니라 possible이고, 원문이 스스로 모자람을 말할 때만 partial이다.';
