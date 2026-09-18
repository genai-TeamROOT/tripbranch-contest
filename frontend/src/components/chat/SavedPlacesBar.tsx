/*
 * 역할: 담아둔 장소 개수와 "이 장소들로 일정 짜기" CTA를 하단에 고정 노출한다.
 * 입력: 보관함 상태(useSavedPlaces), 편성 요청 콜백.
 * 출력: 보관함 요약 바, 펼침 목록, 개별 빼기 버튼.
 * 호출 시점: ChatPage가 입력창 바로 위에 렌더링한다.
 *
 * 보관함이 비어 있으면 아무것도 그리지 않는다 — 담은 것이 없는데 "0곳"을
 * 띄우면 입력창 위 공간만 잡아먹는다.
 */

import { useState } from "react";
import { useSavedPlaces } from "../../hooks/useSavedPlaces";
import type { Language } from "../../types";

interface SavedPlacesBarProps {
  /** CTA 클릭. 담긴 장소들로 일정 편성을 요청한다. */
  onPlanFromSaved: () => void;
  /** 대화 턴이 진행 중이면 CTA를 잠근다. */
  isLoading?: boolean;
  language?: Language;
}

export function SavedPlacesBar({
  onPlanFromSaved,
  isLoading = false,
  language = "ko",
}: SavedPlacesBarProps) {
  const { savedPlaces, removeSaved } = useSavedPlaces();
  const [expanded, setExpanded] = useState(false);

  const text =
    language === "en"
      ? {
          count: (n: number) => `${n} place${n === 1 ? "" : "s"} saved`,
          plan: "Plan a trip with these",
          expand: "Show saved places",
          collapse: "Hide saved places",
          remove: (name: string) => `Remove ${name} from saved places`,
        }
      : {
          count: (n: number) => `보관함 ${n}곳`,
          plan: "이 장소들로 일정 짜기",
          expand: "담은 장소 펼치기",
          collapse: "담은 장소 접기",
          remove: (name: string) => `${name} 보관함에서 빼기`,
        };

  if (savedPlaces.length === 0) return null;

  return (
    <div className="flex flex-col gap-2 border-t border-gray-200 bg-white/95 px-4 py-2 backdrop-blur dark:border-gray-700 dark:bg-gray-900/95">
      {expanded && (
        <ul className="flex max-h-40 flex-col gap-1 overflow-y-auto">
          {savedPlaces.map((place) => (
            <li
              key={place.place_id}
              className="flex items-center justify-between gap-2 rounded px-2 py-1 text-sm text-gray-700 dark:text-gray-300"
            >
              <span className="truncate">{place.name}</span>
              <button
                type="button"
                aria-label={text.remove(place.name)}
                className="shrink-0 rounded px-2 py-0.5 text-xs text-gray-500 hover:text-red-600 dark:text-gray-400 dark:hover:text-red-400"
                onClick={() => void removeSaved(place.place_id)}
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          aria-expanded={expanded}
          className="rounded px-1 text-sm font-medium text-gray-700 hover:text-blue-700 dark:text-gray-300 dark:hover:text-blue-300"
          onClick={() => setExpanded((previous) => !previous)}
        >
          {text.count(savedPlaces.length)}
          <span className="sr-only"> — {expanded ? text.collapse : text.expand}</span>
          <span aria-hidden="true"> {expanded ? "▾" : "▸"}</span>
        </button>

        <button
          type="button"
          disabled={isLoading}
          className="shrink-0 rounded-full bg-blue-600 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-blue-500 dark:hover:bg-blue-600"
          onClick={onPlanFromSaved}
        >
          {text.plan}
        </button>
      </div>
    </div>
  );
}
