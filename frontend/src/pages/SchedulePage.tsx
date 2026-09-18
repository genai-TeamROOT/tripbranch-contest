/*
 * 역할: 일정 탭. 기본은 **저장한 일정 목록**이고, 그중 하나를 누르면 그 일정의
 *   상세로 화면이 바뀐다. 상세 카드 레이아웃 자체는 Figma
 *   "Schedule (Sheet)"(29:82)를 그대로 옮긴 것이다.
 * 입력: `?saved=<id>`(=저장한 일정 상세, GET /api/schedules/:id).
 * 출력: 상세 화면에서는 요약 문구·정류장 타임라인(장소 상세는 기존
 *   RecommendationDetailPreviewModal 재사용)·도움이 됐는지 피드백(로컬
 *   표시만 — 세션/런 id가 이 메시지에 없어 실제 전송은 안 한다)과 목록으로
 *   돌아가는 버튼. 짠 일정이 하나도 없으면 홈으로 돌아가라는 안내.
 * 호출 시점: 사이드바 "일정"에서 열린다(DESIGN_SYSTEM.md §5).
 *
 * **목록과 상세를 한 화면에 같이 두지 않는다**(2026-09-07). 전에는 들어오자마자
 * 일정 상세가 펼쳐져 있고 그 아래 저장 목록이 붙어 있었는데, "왜 목록이 아니라
 * 이게 먼저 보이지"가 됐다. 지금은 목록이 기본 화면이고 눌러야 상세가 뜬다. 헤더
 * 화살표가 없는 화면이라(9/7, d70e85ce), 목록으로 돌아가는 버튼을 상세 화면
 * 맨 위에 직접 둔다 — `?saved=` 조회가 실패했을 때도 상세 자리에 오류와 함께
 * 이 버튼이 뜬다(목록을 같이 그리지 않는다).
 *
 * **"지금 일정" 줄을 없앴다(2026-09-16).** 저장하지 않은, 이번 대화의 마지막
 * 일정을 목록 맨 위에 한 줄로 얹고 있었다. 없앤 이유는 그 줄이 목록의 규칙을
 * 따르지 않아서다 — `SavedScheduleList` 밖에 있어 검색·날짜 필터가 걸리지 않았고,
 * 저장하면 같은 일정이 위아래로 두 번 떴으며, 기준 시각을 편성 시각이 아니라
 * **렌더 시각**(`new Date()`)으로 말했다(메시지에 생성 시각이 없다).
 *
 * **대가를 알고 없앴다.** 실측으로 편성된 일정 100건 중 저장된 것은 14건뿐이라
 * (`trace_records.schedule_quality` 100 : `saved_schedules` 14), 저장하지 않은
 * 일정은 이제 이 화면에서 볼 수 없다. 자동 저장은 두지 않기로 했다(D 결정) —
 * 남기고 싶으면 채팅의 일정 카드에서 저장 버튼을 누르는 것이 유일한 경로다.
 * 그래서 빈 화면 문구도 "짠 일정이 없어요"가 아니라 "저장한 일정이 없어요"다.
 *
 * **카드 레이아웃은 채팅과 같다**(2026-09-09). 예전에는 채팅이 ScheduleCard·
 * ScheduleTravelSegment 한 벌을 따로 갖고 있었고, 이 화면만 Figma가 그린 전용
 * 시트 레이아웃(이미지+도착 시각을 카드 안에 함께 두는 방식)이었다. 같은 일정이
 * 화면마다 다르게 보여서 이 화면 쪽으로 합쳤다 — 이제 둘 다 ScheduleRoute 를
 * 쓰고, 채팅은 체크(다녀왔어요)만 넘기지 않는다.
 */

import { ChevronLeft, Route as RouteIcon, ThumbsDown, ThumbsUp } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { AppHeader } from "../components/layout/AppHeader";
import { PageTransition } from "../components/layout/PageTransition";
import { useTripState } from "../state/TripContext";
import { fetchSavedSchedule } from "../api/trip";
import { SavedScheduleList } from "../components/schedule/SavedScheduleList";
import { useSavedSchedules } from "../hooks/useSavedSchedules";
import { useScheduleVisited } from "../hooks/useScheduleVisited";
import { ScheduleRibbon } from "../components/schedule/ScheduleRibbon";
import { ScheduleRoute } from "../components/schedule/ScheduleRoute";
import { buildScheduleTimeline, clockLabel } from "../utils/scheduleTimeline";
import type { SavedScheduleDetail } from "../types";

export function SchedulePage() {
  const navigate = useNavigate();
  const state = useTripState();
  const isEn = state.language === "en";
  const [searchParams] = useSearchParams();
  const [feedback, setFeedback] = useState<"up" | "down" | null>(null);
  /* 저장 목록이 비었는지는 빈 화면에서 무엇을 안내할지 정하는 데 쓴다.
     아직 못 받아온 동안(null)에는 안내를 띄우지 않는다 — 저장한 일정이 있는
     사람에게 "저장한 일정이 없어요"가 잠깐 스쳤다 사라지면 안 된다. */
  const savedList = useSavedSchedules();

  /*
   * ?saved=<id>로 들어오면 저장한 일정을 보여준다(SCHEDULE 카드 2). 없으면
   * 목록이 기본 화면이다.
   *
   * 저장한 일정을 여는 자리를 여기로 잡은 이유는 사이드바 "일정"이 이미 이
   * 화면을 열기 때문이다 — 목록에서 고른 일정이 다른 모양으로 열리면 같은
   * 것을 두 가지로 그리게 된다.
   */
  const savedId = searchParams.get("saved");
  const [saved, setSaved] = useState<SavedScheduleDetail | null>(null);
  const [savedError, setSavedError] = useState(false);

  useEffect(() => {
    if (!savedId) {
      setSaved(null);
      setSavedError(false);
      return;
    }
    let active = true;
    setSavedError(false);
    void fetchSavedSchedule(savedId)
      .then((detail) => {
        if (active) setSaved(detail);
      })
      .catch(() => {
        if (active) setSavedError(true);
      });
    return () => {
      active = false;
    };
  }, [savedId]);

  /* 상세로 보여줄 일정은 저장한 것뿐이다. 저장하지 않은 일정은 이 화면에
     오지 않는다 — 채팅의 일정 카드에서 저장해야 목록에 남는다(2026-09-16). */
  const schedule = saved?.payload;
  const showingDetail = Boolean(savedId);
  /* 체크 진행을 저장·복원할 열쇠(state/scheduleProgress.ts). */
  const scheduleKey = saved?.id ?? "";
  const [visited, toggleVisited] = useScheduleVisited(scheduleKey);
  /*
   * **"언제 기준인지"를 지금 시각으로 쓰지 않는다.** 저장한 일정의 도착 시각·
   * 이동 시간은 저장 시점 값이라, 지금 시각을 얹으면 사흘 전 일정이 방금 짠
   * 것처럼 보인다.
   */
  const basisAt = saved ? new Date(saved.created_at) : new Date();

  /*
   * 머리말·띠에 쓸 값. 항목이 없거나 도착 시각을 못 읽으면 전부 null 이고, 그때는
   * 머리말이 route_summary 로 떨어진다 — 못 읽은 시각을 지어내지 않는다.
   *
   * "지금"은 저장한 일정에 얹지 않는다(basisAt 주석과 같은 이유).
   */
  const timeline = schedule ? buildScheduleTimeline(schedule.items) : null;
  const endLabel = timeline ? clockLabel(timeline.endMinutes, isEn) : null;

  /* 예전 머리말이던 "몇 시 기준" 문장. 결정에 쓰이는 값이 아니라 근거 문단으로 내렸다. */
  const basisLine = saved
    ? isEn
      ? `Saved on ${basisAt.toLocaleDateString("en-US", { month: "long", day: "numeric" })}.`
      : `${basisAt.toLocaleDateString("ko-KR", { month: "long", day: "numeric" })}에 저장한 일정이에요.`
    : isEn
      ? `Planned as of ${basisAt.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })}.`
      : `${basisAt.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })} 기준으로 짠 동선이에요.`;

  function backToList() {
    if (savedId) navigate("/schedule", { replace: true });
  }

  /* 목록↔상세 전환에 쓰는 키다(2026-09-07). AppShell의 PageTransition은
     경로(pathname)로만 다시 재생되는데, 목록에서 상세로 들어가는 건 쿼리만
     바뀔 뿐이라(?saved=) 그 전환이 안 탔다. 여기서 한 겹 더 감싸 목록/상세/
     어느 저장 일정인지가 바뀔 때마다 같은 떠오르는 페이드를 재생한다. */
  /* `??`가 아니라 `||`다 — `?saved=`처럼 값이 빈 쿼리는 상세로 치지 않는다
     (`showingDetail`도 같은 기준이다). `??`는 빈 문자열을 그냥 통과시킨다. */
  const contentKey = savedId || "list";

  return (
    <main className="flex h-full flex-col overflow-y-auto">
      <AppHeader keepStrip />
      {/* pt-6: "저장한 일정" 표제를 없애면서(2026-09-07) 목록 첫 줄이 헤더 바로
          아래 붙었다 — PreferencesPage와 같은 여백으로 맞춘다. */}
      <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-5 px-4 pb-10 pt-6">
        <PageTransition pathKey={contentKey}>
          {/* PageTransition이 안의 내용을 div 한 겹으로 감싸면서, 바깥
              (.max-w-2xl 컨테이너)의 gap-5가 자식이 하나뿐이라 더 이상 안
              먹는다(2026-09-07) — 그 gap을 이 안쪽 div로 옮겨서 되살린다. */}
          <div className="flex flex-col gap-5">
            {showingDetail ? (
              <>
                {/* 헤더 화살표는 이 앱에서 없앤 지 오래다(9/7, d70e85ce) — 목록으로
                돌아가는 길은 이 화면 안에 직접 둔다. */}
                <button
                  type="button"
                  onClick={backToList}
                  className="mb-2 mt-1 flex w-fit items-center gap-1 text-sm font-semibold text-muted"
                >
                  <ChevronLeft size={16} />
                  {isEn ? "Back to list" : "목록으로"}
                </button>

                {savedError ? (
                  <div className="flex flex-col items-center gap-3 py-14 text-center">
                    <p className="text-sm text-muted">
                      {isEn ? "Couldn't load that schedule." : "그 일정을 불러오지 못했어요."}
                    </p>
                    <p className="text-xs text-muted">
                      {isEn
                        ? "It may have been deleted, or you may not have access."
                        : "이미 지워졌거나 접근 권한이 없을 수 있어요."}
                    </p>
                  </div>
                ) : !schedule || schedule.items.length === 0 ? (
                  /* 저장한 일정을 불러오는 동안의 짧은 틈이다. */
                  <p className="py-14 text-center text-sm text-muted">
                    {isEn ? "Loading…" : "불러오는 중이에요…"}
                  </p>
                ) : (
                  <>
                    {/*
                  머리말은 "언제 끝나는가"다. 남은 시간에 맞는지가 이 화면에서 사용자가
                  내리는 결정이라 가장 먼저 온다. 예전에는 "몇 시 기준으로 짠 동선"이
                  머리말이었는데, 그건 결정에 쓰이지 않는 값이라 근거 문단으로 내렸다.
                */}
                    <div className="flex flex-col gap-1.5">
                      <h2 className="text-2xl font-bold tracking-tight tabular-nums text-ink">
                        {endLabel
                          ? isEn
                            ? `Ends at ${endLabel}`
                            : `${endLabel}에 끝나요`
                          : schedule.route_summary}
                      </h2>
                      {endLabel && (
                        <p className="max-w-[44ch] text-sm leading-relaxed text-label">
                          {schedule.route_summary}
                        </p>
                      )}
                    </div>

                    <ScheduleRibbon items={schedule.items} isEn={isEn} visited={visited} />

                    <ScheduleRoute
                      items={schedule.items}
                      isEn={isEn}
                      visited={visited}
                      onToggleVisited={toggleVisited}
                    />

                    <div className="flex items-center gap-2">
                      <p className="text-[11px] text-muted">
                        {isEn ? "Was this schedule helpful?" : "이 일정이 도움이 됐나요?"}
                      </p>
                      <button
                        type="button"
                        aria-label={isEn ? "Helpful" : "도움이 됐어요"}
                        aria-pressed={feedback === "up"}
                        onClick={() => setFeedback((prev) => (prev === "up" ? null : "up"))}
                        className={`flex h-7 w-7 items-center justify-center transition-colors ${
                          feedback === "up" ? "text-brand" : "text-muted hover:text-brand"
                        }`}
                      >
                        <ThumbsUp size={13} />
                      </button>
                      <button
                        type="button"
                        aria-label={isEn ? "Not helpful" : "도움이 안 됐어요"}
                        aria-pressed={feedback === "down"}
                        onClick={() => setFeedback((prev) => (prev === "down" ? null : "down"))}
                        className={`flex h-7 w-7 items-center justify-center transition-colors ${
                          feedback === "down" ? "text-rust" : "text-muted hover:text-rust"
                        }`}
                      >
                        <ThumbsDown size={13} />
                      </button>
                    </div>

                    <p className="rounded-xl bg-chip px-3 py-2.5 text-[11px] leading-relaxed text-muted">
                      {basisLine}
                      {schedule.basis_note ? ` ${schedule.basis_note}` : ""}
                    </p>

                    <button
                      type="button"
                      onClick={() => navigate("/chat")}
                      className="flex h-12 w-full items-center justify-center rounded-full bg-white text-sm font-bold text-brand shadow-resting"
                    >
                      {isEn ? "Ask again in chat" : "채팅에서 다시 물어보기"}
                    </button>
                  </>
                )}
              </>
            ) : (
              <>
                <SavedScheduleList />

                {/*
              **저장한 일정이 없을 때만** 나온다. 검색바·달력 아래에 붙는다 —
              목록의 틀은 늘 보이고(SavedScheduleList 머리말) 그 안이 비었을 때
              무엇을 할지를 여기서 안내한다. 목록을 아직 못 받아온 동안(null)에는
              띄우지 않는다(잠깐 스쳤다 사라진다).

              문구가 "짠 일정이 없어요"가 아니라 "저장한 일정이 없어요"인 이유는
              2026-09-16에 "지금 일정" 줄을 없앴기 때문이다 — 이제 저장하지 않은
              일정은 여기 오지 않으므로, 방금 일정을 짠 사람도 이 화면을 본다.
              "짠 일정이 없어요"는 그 사람에게 거짓말이 된다.
            */}
                {savedList !== null && savedList.length === 0 && (
                  <div className="flex flex-1 flex-col items-center justify-center gap-3 py-14 text-center">
                    <span className="flex h-12 w-12 items-center justify-center rounded-full bg-chip text-brand">
                      <RouteIcon size={22} />
                    </span>
                    <p className="text-sm text-muted">
                      {isEn
                        ? "No saved schedules yet."
                        : "저장한 일정이 없어요. 채팅에서 일정을 저장하면 여기에 모여요."}
                    </p>
                    <button
                      type="button"
                      onClick={() => navigate("/")}
                      className="rounded-full bg-brand px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-deep active:scale-[0.98]"
                    >
                      {isEn ? "Plan a schedule from home" : "홈에서 일정 짜기"}
                    </button>
                  </div>
                )}
              </>
            )}
          </div>
        </PageTransition>
      </div>
    </main>
  );
}
