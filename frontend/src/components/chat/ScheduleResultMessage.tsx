/*
 * 역할: 일정 편성 API 응답을 채팅 메시지 안에서 세로 타임라인으로 렌더링한다.
 * 입력: ScheduleResult(items/route_summary/total_duration_min/basis_note),
 *   저장에 함께 보낼 run_id·session_id.
 * 출력: 제목과 저장 버튼, 정류장 목록(ScheduleRoute — 일정 상세 화면과 같은
 *   컴포넌트다), 근거 시각 안내.
 *   재편성 버튼("다른 코스 보기"·"검색 범위 넓혀서 다시 찾기")은 여기 없다 —
 *   턴이 지나면 걷어내야 해서 ScheduleActionsMessage로 갈라져 있다.
 *   총 소요 시간·동선 요약 문구는 여기서 만들지 않는다 —
 *   바로 위 assistant_text 말풍선(백엔드 compose_schedule_message)이 이미
 *   보여주고 있어서 중복 렌더링이었다(SCHEDULE-10 후속: 요청 시간과 실제
 *   편성 시간이 다를 때 "N분 코스를 짜봤어요"가 어색해 보이는 문제를 고치며
 *   같은 문구가 말풍선·카드 두 곳에서 각자 계산되고 있던 걸 발견해 정리함).
 * 호출 시점: ChatPage가 schedule_result 메시지를 렌더링할 때 호출된다.
 *
 * 재조정 버튼은 새 기능이 아니라 RECOMMEND가 이미 쓰던 것과 같은 문구를
 * 재사용한다(REQUEST_MORE_PROMPT="다른 곳 보여줘", RELAX_RADIUS_PROMPT="검색
 * 범위를 넓혀서 다시 추천해줘") — SCHEDULE-06이 last_intent="SCHEDULE"일 때
 * 이 문구들을 이미 재편성 경로로 라우팅하므로 백엔드 변경 없이 버튼만 잇는다
 * (SCHEDULE-08).
 *
 * showElapsedTime이 true면(개발자 화면) 지연시간을 보여준다 — RecommendationResultMessage와
 * 같은 방식이다. ScheduleResult.elapsed_ms(planner.py plan_schedule()/
 * plan_partial_schedule()이 측정)를 서버 소요로, elapsedMs를 클라이언트 소요로 쓴다.
 */

import { Bookmark } from "lucide-react";
import { useState } from "react";
import { deleteSavedSchedule, saveSchedule } from "../../api/trip";
import { refreshSavedSchedules } from "../../state/savedSchedules";
import type { ScheduleResult } from "../../types";
import { defaultScheduleTitle } from "../../utils/scheduleTitle";
import { ScheduleRoute } from "../schedule/ScheduleRoute";

function formatDuration(milliseconds: number | undefined) {
  if (typeof milliseconds !== "number" || !Number.isFinite(milliseconds)) return "-";
  return milliseconds >= 1000
    ? `${(milliseconds / 1000).toFixed(1)}초`
    : `${Math.round(milliseconds)}ms`;
}

interface ScheduleResultMessageProps {
  schedule: ScheduleResult;
  elapsedMs?: number;
  showElapsedTime?: boolean;
  /* 저장에 함께 보낸다. run_id는 같은 턴을 두 번 저장하지 않기 위한 열쇠이고
     session_id는 출처 표시다. 응답이 run_id 없이 끝나는 경로가 있어 둘 다 선택. */
  runId?: string;
  sessionId?: string;
}

export function ScheduleResultMessage({
  schedule,
  elapsedMs,
  showElapsedTime = false,
  runId,
  sessionId,
}: ScheduleResultMessageProps) {
  const hasItems = schedule.items.length > 0;
  /*
   * 저장된 일정의 id를 들고 있는 것이 곧 "저장됨" 표시다. 해제할 때 이 id가
   * 필요해서이기도 하다.
   *
   * **새로고침하면 잊는다.** 저장 목록(SavedScheduleSummary)에 run_id가 없어
   * "이 턴의 일정이 이미 저장됐는지"를 목록에서 되찾을 방법이 없다. 백엔드가
   * run_id를 함께 내려주면 그때 이어붙일 수 있다. 그때까지는 표시만 틀리고
   * 데이터는 어긋나지 않는다 — 저장은 (신원, run_id)에 멱등이라 모르고 다시
   * 저장해도 새 줄이 생기지 않는다.
   *
   * 저장 목록을 구독해 맞추지는 않는다. loadSavedSchedules()가 실패해도 빈
   * 목록을 돌려주도록 되어 있어서, 401이나 네트워크 실패에 저장 표시가 조용히
   * 풀린다.
   */
  const [savedId, setSavedId] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /*
   * 저장은 낙관적으로 그리지 않는다. 보관함 담기와 달리 이건 목록에 새 줄을
   * 만드는 동작이라, 실패했는데 저장된 것처럼 보이면 사용자가 나중에 목록에서
   * 찾다가 없는 것을 겪는다. 해제도 같은 이유로 서버 응답을 받고 나서 바꾼다.
   */
  async function handleToggleSave() {
    if (isBusy) return;
    setIsBusy(true);
    setError(null);
    try {
      if (savedId === null) {
        const saved = await saveSchedule({
          title: defaultScheduleTitle(schedule.items),
          payload: schedule,
          sessionId,
          runId,
        });
        setSavedId(saved.id);
      } else {
        await deleteSavedSchedule(savedId);
        setSavedId(null);
      }
      /* 저장 목록을 바로 갱신한다. 저장하고 목록을 봤는데 없으면 사용자는
         저장이 안 된 줄 안다 — 실제로 새로고침해야 보였다. */
      void refreshSavedSchedules();
    } catch {
      setError(savedId === null ? "저장하지 못했어요." : "저장을 해제하지 못했어요.");
    } finally {
      setIsBusy(false);
    }
  }

  return (
    <article className="mr-auto flex w-full flex-col gap-3.5">
      {/* 총 소요 시간·동선 요약은 바로 위 assistant_text 말풍선(compose_schedule_message)이
          이미 보여주므로 여기서 다시 반복하지 않는다 — 카드는 타임라인/액션만 맡는다. */}
      {showElapsedTime && (
        <p className="text-right text-xs text-muted">
          {formatDuration(elapsedMs)} 소요 (서버 {formatDuration(schedule.elapsed_ms)})
        </p>
      )}
      {hasItems ? (
        <>
          {/*
            제목은 저장할 때 쓰던 것을 미리 보여주는 것이다(defaultScheduleTitle) —
            눌러서 저장하면 저장 목록에 이 이름 그대로 들어간다. 이름 바꾸기는
            여기서 하지 않는다.

            저장 버튼을 여기 둔 이유는 재편성 버튼과 성격이 다르기 때문이다. 저건
            새 요청이라 턴이 지나면 걷어내지만, 저장은 이 턴의 일정을 run_id로
            남기는 것이라 나중에 눌러도 맞다. 제목 옆이면 무엇을 저장하는지도 분명하다.
          */}
          {/* 책갈피는 제목 바로 옆에 붙인다. 오른쪽 끝으로 밀면 제목과 멀어져
              무엇을 저장하는 버튼인지 눈에 덜 걸린다. */}
          <div className="flex items-center gap-1.5">
            <h3 className="min-w-0 truncate text-sm font-bold text-ink">
              {defaultScheduleTitle(schedule.items)}
            </h3>
            {/* 이름은 보이는 글자가 맡는다 — aria-label 을 따로 주면 화면에 보이는
                말과 스크린리더가 읽는 말이 갈리고, 음성 명령으로 보이는 대로
                말해도 걸리지 않는다. 누르면 무엇이 되는지를 말해야 해서 문구는
                상태에 따라 바뀐다. */}
            <button
              type="button"
              disabled={isBusy}
              onClick={() => void handleToggleSave()}
              aria-pressed={savedId !== null}
              className={`flex shrink-0 items-center gap-1 text-xs font-semibold transition-colors disabled:opacity-50 ${
                savedId !== null ? "text-brand" : "text-muted hover:text-brand"
              }`}
            >
              {/* 저장되면 같은 모양이 색으로 찬다 — 모양이 바뀌면 다른 버튼처럼
                  보여서, 누를 때마다 오가는 토글이라는 것이 덜 읽힌다. */}
              <Bookmark
                size={14}
                className={
                  savedId !== null
                    ? "fill-brand text-brand"
                    : isBusy
                      ? "animate-pulse"
                      : undefined
                }
              />
              {savedId === null ? "일정 저장하기" : "저장 취소"}
            </button>
          </div>
          {error && (
            <p role="alert" className="text-xs text-rust">
              {error} 다시 눌러주세요.
            </p>
          )}

          {/* 일정 상세(/schedule)와 같은 카드를 쓴다 — 예전에는 채팅만 쓰는
              ScheduleCard·ScheduleTravelSegment 한 벌이 따로 있어서 같은 일정이
              화면마다 다르게 보였다(사진 유무, 도착 시각 자리, 경고 표시,
              상세보기 위치). 체크는 채팅에 없으므로 onToggleVisited 를 넘기지
              않는다 — 그러면 사진이 버튼이 아니고 체크 배지도 빠진다.

              isEn 은 false 로 고정한다. 이 컴포넌트에는 언어 분기가 없다. */}
          <ScheduleRoute items={schedule.items} isEn={false} />

          {schedule.basis_note && (
            <p className="rounded-xl bg-chip px-3 py-2.5 text-[11px] leading-relaxed text-muted">
              {schedule.basis_note}
            </p>
          )}

        </>
      ) : null}
    </article>
  );
}
