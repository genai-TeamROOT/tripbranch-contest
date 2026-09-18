/*
 * 역할: 계정에 저장한 일정 목록 — 검색·날짜로 훑어보기, 열기·이름 바꾸기·삭제.
 * 입력: 없다(계정에서 직접 받아온다, GET /api/schedules).
 * 출력: `?saved=<id>`로의 이동, 이름 변경·삭제 요청.
 * 호출 시점: SchedulePage가 화면 아래에 렌더한다.
 *
 * **원래 사이드바(`SideDrawerContent` §5)에 있었다**(2026-09-04, lth2295, PR #369).
 * 일정 탭이 이미 있는데 목록만 사이드바에 있어서, 일정을 관리하려면 화면을 벗어나야
 * 했다. 목록을 이 컴포넌트로 떼어 일정 화면으로 옮겼다.
 *
 * 사이드바에 있을 때는 대화 목록과 메뉴·이름변경 상태를 **한 벌로 공유**했다
 * (`MenuTarget = { kind, id }`) — 한 번에 하나만 열려야 하는데 상태를 두 벌 두면
 * 대화 메뉴를 열어둔 채 일정 메뉴도 열렸기 때문이다. 분리하면 그 이유가 사라져
 * `id` 하나만 든다.
 *
 * **검색·달력 필터는 순수 프론트 필터다**(2026-09-07). `GET /api/schedules`는
 * 제목·날짜만 주고 장소 사진은 없어서, 카드 아이콘은 실제 장소 사진이 아니라
 * 고정 아이콘이다 — 실제 사진을 쓰려면 B(`state/`) 쪽에 필드가 필요하다.
 */

import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { MoreHorizontal, Search } from "lucide-react";
import { deleteSavedSchedule, renameSavedSchedule } from "../../api/trip";
import { useAuth } from "../../auth/AuthContext";
import { identityDisplay } from "../../auth/identityLabel";
import { IdentityAvatar } from "../layout/SidebarAccount";
import { useSavedSchedules } from "../../hooks/useSavedSchedules";
import { refreshSavedSchedules, type SavedScheduleEntry } from "../../state/savedSchedules";
import { useTripState } from "../../state/TripContext";
import { ScheduleCalendarStrip } from "./ScheduleCalendarStrip";
import { startOfWeek, toDateKey } from "../../utils/scheduleDates";

function matchesQuery(label: string, query: string): boolean {
  const trimmed = query.trim().toLowerCase();
  if (!trimmed) return true;
  return label.toLowerCase().includes(trimmed);
}

function matchesDate(createdAt: Date | null, selectedDateKey: string | null): boolean {
  if (!selectedDateKey) return true;
  return createdAt !== null && toDateKey(createdAt) === selectedDateKey;
}

export function SavedScheduleList() {
  const navigate = useNavigate();
  const isEn = useTripState().language === "en";
  const { session } = useAuth();
  /* 카드 아이콘은 고정 아이콘 대신 계정 아바타를 쓴다(2026-09-07) — 목록의
     일정들이 전부 이 계정 것이라 "누구의 것인지"를 보여주는 게 더 쓸모있다.
     RequireUser가 이 화면 앞에서 이미 세션(게스트 포함)을 보장하지만, 타입상
     null일 수 있어 없을 때는 그리지 않는다. */
  const identity = session ? identityDisplay(session, isEn ? "en" : "ko") : null;
  const [query, setQuery] = useState("");
  const [weekStart, setWeekStart] = useState(() => startOfWeek(new Date()));
  /*
   * **목록은 오늘 날짜로 시작한다**(2026-09-08). 전에는 필터가 풀린 채로 열려서
   * 며칠에 걸쳐 저장한 것이 한꺼번에 쏟아졌다 — 일정 탭을 여는 사람이 가장 자주
   * 찾는 것은 오늘 쓸 일정인데, 그게 지난 것들 사이에 섞여 있었다.
   *
   * 오늘 저장한 것이 없으면 "조건에 맞는 저장한 일정이 없어요."가 뜬다. 빈 화면이
   * 아니라 달력 띠와 검색창은 그대로 남으므로, 다른 날짜를 누르거나 **선택된
   * 날짜를 한 번 더 눌러**(ScheduleCalendarStrip이 그때 null을 준다) 전체 보기로
   * 돌아갈 수 있다.
   */
  const [selectedDateKey, setSelectedDateKey] = useState<string | null>(() => toDateKey(new Date()));

  /*
   * 서버 목록은 훅이 들고, 여기서는 그것을 지역 상태로 받아 **낙관적 편집**(이름
   * 바꾸기·삭제)을 얹는다. 훅 값을 바로 그리면 이름을 바꾼 순간이 아니라 서버
   * 응답이 온 뒤에야 화면이 바뀐다.
   */
  const loaded = useSavedSchedules();
  const [schedules, setSchedules] = useState<SavedScheduleEntry[]>([]);
  const [openMenu, setOpenMenu] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const renameInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (loaded) setSchedules(loaded);
  }, [loaded]);

  /* 원본(사이드바)과 같은 동작이다 — 이름 바꾸기를 누르면 바로 입력할 수 있어야 한다. */
  useEffect(() => {
    if (renaming) renameInputRef.current?.focus();
  }, [renaming]);

  function commitRename(id: string) {
    const trimmed = renameDraft.trim();
    if (trimmed) {
      /* 화면을 먼저 바꾸고 서버에 보낸다 — 이름 바꾸기는 되돌릴 수 있는 동작이라
         응답을 기다리며 입력칸을 붙잡아 둘 이유가 없다. 실패하면 서버 값으로
         되돌린다 — 바뀐 척 남겨두면 다음에 열었을 때 예전 이름이 돌아와 있어 더
         혼란스럽다.

         **성공해도 캐시는 갱신해야 한다.** 위 setSchedules는 이 컴포넌트의 로컬
         state일 뿐, state/savedSchedules.ts의 캐시(cached 프라미스)는 그대로 옛
         이름을 들고 있다. 다른 화면에 다녀와 이 컴포넌트가 다시 마운트되면
         useSavedSchedules()가 그 캐시를 그대로 돌려줘 이름이 되돌아간
         것처럼 보인다 — 2026-09-10에 삭제에서 같은 증상이 보고됐다(실패해야만
         부르던 자리에 성공 경로가 없었다). finally로 성공·실패 모두 갱신한다. */
      setSchedules((prev) =>
        prev.map((item) => (item.id === id ? { ...item, label: trimmed } : item)),
      );
      // .catch()로 실패를 먼저 삼킨다 — .finally()는 원래 거부를 그대로
      // 물려주므로, 삼키지 않으면 실패했을 때 처리되지 않은 프라미스 거부가
      // 남는다. 성공·실패 어느 쪽이든 재조회 하나로 화면을 서버 상태에 맞춘다.
      void renameSavedSchedule(id, trimmed)
        .catch(() => {})
        .finally(() => {
          void refreshSavedSchedules();
        });
    }
    /* 빈 제목은 취소로 친다. 서버도 빈 제목을 거부하므로 보내봐야 400이다. */
    setRenaming(null);
  }

  /*
   * **저장한 일정이 없어도 검색바와 달력은 그린다(2026-09-16).** 전에는
   * `schedules.length === 0`이면 이 구획을 통째로 접었는데, 그러면 저장이 하나
   * 생기는 순간 검색바와 달력이 갑자기 나타나 화면이 다른 구조로 바뀌었다.
   * 목록의 틀은 늘 같은 자리에 두고, 내용만 비운다.
   *
   * **대신 "비었다"를 두 가지로 나눠 말한다.** 저장이 0건인 것과 검색·날짜에
   * 걸린 것이 없는 것은 사용자가 할 일이 다르다.
   *   - 저장 0건        → 화면(SchedulePage)이 "홈에서 일정 짜기"까지 안내한다
   *   - 필터 결과 0건   → 여기서 "조건에 맞는 …이 없어요"만 낸다
   * 둘을 같이 내면 **비었다는 안내가 두 개 겹쳐 보인다** — 예전에 실제로 그랬고,
   * 그래서 구획을 통째로 접었던 것이다. 접는 대신 아래 조건으로 가른다.
   */

  const markedDateKeys = new Set(
    schedules
      .map((entry) => entry.createdAt)
      .filter((createdAt): createdAt is Date => createdAt !== null)
      .map(toDateKey),
  );
  const visible = schedules.filter(
    (entry) => matchesQuery(entry.label, query) && matchesDate(entry.createdAt, selectedDateKey),
  );

  return (
    <section className="flex flex-col gap-3">
      <div className="flex h-11 items-center gap-2 rounded-xl border border-border bg-white px-3">
        <Search size={15} className="shrink-0 text-muted" />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          aria-label={isEn ? "Search saved schedules" : "저장한 일정 검색"}
          placeholder={isEn ? "Search saved schedules" : "저장한 일정 이름으로 검색"}
          className="min-w-0 flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-muted"
        />
      </div>

      <ScheduleCalendarStrip
        weekStart={weekStart}
        onWeekChange={setWeekStart}
        selectedDateKey={selectedDateKey}
        onSelectDate={setSelectedDateKey}
        markedDateKeys={markedDateKeys}
        isEn={isEn}
      />

      {/* 저장이 0건일 때는 내지 않는다 — 그 안내는 화면이 CTA와 함께 낸다(위 주석). */}
      {schedules.length > 0 && visible.length === 0 && (
        <p className="py-4 text-center text-[13px] text-muted">
          {isEn ? "No saved schedules match." : "조건에 맞는 저장한 일정이 없어요."}
        </p>
      )}

      <ul className="flex flex-col gap-2">
        {visible.map((entry) => (
          <li key={entry.id} className="relative rounded-2xl border border-border p-3">
            {renaming === entry.id ? (
              <input
                ref={renameInputRef}
                aria-label={isEn ? "Schedule name" : "일정 이름"}
                value={renameDraft}
                onChange={(event) => setRenameDraft(event.target.value)}
                onBlur={() => commitRename(entry.id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") commitRename(entry.id);
                  if (event.key === "Escape") setRenaming(null);
                }}
                className="w-full rounded-md border border-border px-2 py-1 text-sm"
              />
            ) : (
              <div className="flex items-center gap-3">
                {identity && <IdentityAvatar identity={identity} size="md" />}
                {/* 한 줄 전체가 버튼이다 — 날짜 쪽을 눌렀을 때 아무 일도 안 나면
                    고장으로 보인다. */}
                <button
                  type="button"
                  aria-label={isEn ? `Open schedule ${entry.label}` : `${entry.label} 일정 열기`}
                  /* 여기는 이미 /schedule 안이다 — 같은 경로를 새로 push 하면
                       뒤로가기가 한 번 더 필요해진다. 쿼리만 바꿔(replace) 같은
                       화면에서 갈아 끼운다. */
                  onClick={() =>
                    navigate(`/schedule?saved=${encodeURIComponent(entry.id)}`, {
                      replace: true,
                    })
                  }
                  className="min-w-0 flex-1 text-left"
                >
                  <p className="truncate text-sm font-medium text-ink">{entry.label}</p>
                  {entry.date && (
                    <p className="truncate text-[11px] text-muted">
                      {isEn ? `Saved ${entry.date}` : `${entry.date} 저장`}
                    </p>
                  )}
                </button>
                <button
                  type="button"
                  aria-label={isEn ? `${entry.label} menu` : `${entry.label} 메뉴`}
                  onClick={() => setOpenMenu((open) => (open === entry.id ? null : entry.id))}
                  className="shrink-0 text-muted hover:text-ink"
                >
                  <MoreHorizontal size={15} />
                </button>
              </div>
            )}

            {openMenu === entry.id && (
              <>
                <button
                  type="button"
                  aria-label={isEn ? "Close menu" : "메뉴 닫기"}
                  onClick={() => setOpenMenu(null)}
                  className="fixed inset-0 z-20 cursor-default"
                />
                <div
                  role="menu"
                  className="absolute right-0 top-full z-30 flex w-36 flex-col gap-0.5 rounded-2xl bg-white p-1.5 shadow-card"
                >
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setRenameDraft(entry.label);
                      setRenaming(entry.id);
                      setOpenMenu(null);
                    }}
                    className="rounded-xl px-3 py-2 text-left text-sm font-medium text-ink transition-colors hover:bg-chip"
                  >
                    {isEn ? "Rename" : "이름 바꾸기"}
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      /* 화면에서 먼저 빼고 서버에 보낸다. 실패하면 서버 목록으로
                         되돌린다.

                         **성공해도 캐시는 갱신해야 한다** — commitRename과 같은
                         이유다. 여기 setSchedules는 로컬 state일 뿐이라, 삭제가
                         성공해도 state/savedSchedules.ts의 캐시는 지운 일정을
                         계속 들고 있었다. 다른 화면에 다녀와 이 목록이 다시
                         마운트되면 그 캐시가 그대로 돌아와 지운 일정이 되살아
                         났다(2026-09-10 실사용 보고 — 화면에선 지워지는데 다른
                         페이지를 다녀오면 남아 있었다). */
                      setSchedules((prev) => prev.filter((item) => item.id !== entry.id));
                      setOpenMenu(null);
                      // .catch()로 실패를 먼저 삼킨다 — .finally()는 원래 거부를
                      // 그대로 물려주므로, 삼키지 않으면 실패했을 때 처리되지
                      // 않은 프라미스 거부가 남는다.
                      void deleteSavedSchedule(entry.id)
                        .catch(() => {})
                        .finally(() => {
                          void refreshSavedSchedules();
                        });
                    }}
                    className="rounded-xl px-3 py-2 text-left text-sm font-medium text-rust transition-colors hover:bg-chip"
                  >
                    {isEn ? "Delete" : "삭제"}
                  </button>
                </div>
              </>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
