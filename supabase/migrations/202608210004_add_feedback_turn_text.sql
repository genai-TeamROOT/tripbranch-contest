begin;

-- Package B (roadmap.md 14번 후속): 좋아요/싫어요를 남긴 턴에 한해서만
-- 질문·답변 원문을 함께 남긴다. 테스트 중 피드백을 검토할 때 session_id/
-- run_id/버전 정보만으로는 "무엇에 대한 반응인지" 알 수 없어서 불편하다는
-- 요청 반영.
--
-- 대화 전체를 로그로 남기는 것과는 다르다 — 사용자가 명시적으로 반응한
-- 턴만 남으므로 노출 범위가 훨씬 좁다. 그래도 자유 텍스트(개인정보·민감정보
-- 포함 가능)라는 성질은 같으므로 nullable로 두고, 값이 없어도(과거 행,
-- 프론트가 텍스트를 못 찾은 경우) 기존 rating 기록 자체는 그대로 유효하다.
--
-- 보관기간 정책은 아직 없다 — 지금은 개발/테스트 단계 전제다. 실서비스
-- 공개 전에는 guest-auth-design.md 9-3절(보관기간·자동삭제·동의 지점)을
-- 이 컬럼에도 적용할지 다시 결정해야 한다.
--
-- 원래 202608210002 번호로 작성했으나, develop에 먼저 merge된 다른 PR이
-- 같은 날짜에 202608210002/202608210003 번호를 이미 써서 004로 재번호.
alter table public.response_feedback
  add column if not exists user_input text,
  add column if not exists assistant_message text;

commit;
