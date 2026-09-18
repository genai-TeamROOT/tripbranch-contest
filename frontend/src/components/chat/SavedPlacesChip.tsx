/*
 * 역할: 담아둔 장소가 있을 때 "N곳 일정 짜기"를 띄운다.
 * 입력: 편성 요청 콜백, 진행 중 여부, 언어.
 * 출력: 담은 곳이 없으면 아무것도 그리지 않고, 있으면 칩 버튼 하나.
 * 호출 시점: ChatPage가 입력창 바로 위 띠의 오른쪽 끝에 넣는다.
 *
 * **하단 바(SavedPlacesBar)를 대신한다**(2026-09-08). 그 바는 대화 흐름의 맨
 * 끝에 있어서, 하트를 누른 뒤 맨 아래까지 내려가야 보였다 — 카드를 보며 담는
 * 동안에는 정작 안 보인다. 지금 자리는 스크롤과 무관하게 늘 떠 있다.
 *
 * **처음에는 헤더 오른쪽이었다가 입력창 위로 내려왔다**(사용자 결정, 2026-09-09).
 * 헤더에 두면 눌러야 하는 두 동작(입력과 편성)이 화면 위아래로 갈렸다. 지금은
 * 이동 버튼과 한 띠를 쓰며, 손이 가는 자리에 모인다.
 *
 * 개수를 문구에 담는다("3곳 일정 짜기"). 개수와 동작을 따로 두면 칩이 둘이 되고,
 * 어차피 담긴 곳 전부로 편성하므로 두 값이 늘 같은 것을 가리킨다.
 *
 * **모양은 위치 칩과 같다**(사용자 결정, 2026-09-08). 처음에는 브랜드 색을 채워
 * 동작임을 드러내려 했는데, 무게가 다른 칩 둘이 놓이면 어수선했다. 지금은 같은
 * 프로스티드에 아이콘만 브랜드 색이다 — 두 칩의 클래스가 같아야 하므로 한쪽을
 * 바꾸면 다른 쪽도 봐야 한다(AppHeader).
 *
 * **담은 장소를 여기서 뺄 수는 없다.** 하단 바에 있던 펼침 목록은 옮기지 않았다 —
 * 빼는 길은 카드의 하트다. 목록이 필요해지면 이 칩을 눌러 펼치는 형태로 넓힌다.
 */

import { CalendarPlus } from "lucide-react";
import { useSavedPlaces } from "../../hooks/useSavedPlaces";
import type { Language } from "../../types";

interface SavedPlacesChipProps {
  /** 칩 클릭. 담긴 장소들로 일정 편성을 요청한다. */
  onPlanFromSaved: () => void;
  /** 대화 턴이 진행 중이면 잠근다 — 편성 요청이 앞 턴과 겹치지 않게. */
  isLoading?: boolean;
  language?: Language;
}

export function SavedPlacesChip({
  onPlanFromSaved,
  isLoading = false,
  language = "ko",
}: SavedPlacesChipProps) {
  const { savedPlaces } = useSavedPlaces();
  const count = savedPlaces.length;

  // 담은 것이 없으면 자리도 잡지 않는다 — "0곳"은 알려줄 것이 없다.
  if (count === 0) return null;

  const label = language === "en" ? `Plan with ${count}` : `${count}곳 일정 짜기`;

  return (
    <button
      type="button"
      disabled={isLoading}
      onClick={onPlanFromSaved}
      /* 위치 칩(AppHeader)과 같은 클래스다. shrink-0인 것만 다르다 — 저쪽은 긴
         이름을 줄여야 해서 min-w-0이고, 이쪽은 "3곳 일정 짜기"라 줄일 것이 없다. */
      className="flex shrink-0 items-center gap-1.5 rounded-full border border-white bg-white/60 px-3 py-1.5 text-sm font-medium text-ink shadow-resting backdrop-blur-md transition-colors hover:bg-white/80 disabled:opacity-50"
    >
      <CalendarPlus size={13} className="shrink-0 text-brand" aria-hidden />
      {label}
    </button>
  );
}
