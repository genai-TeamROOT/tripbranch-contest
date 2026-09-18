/*
 * 역할: 저장할 일정의 기본 제목을 만든다.
 * 입력: ScheduleResult의 항목 목록.
 * 출력: "경복궁 외 2곳" 같은 한 줄.
 * 호출 시점: ScheduleResultMessage가 저장 요청을 보내기 직전.
 *
 * **제목을 서버가 만들지 않는 이유가 여기 있다.** 백엔드는 저장한 일정의
 * payload를 열어보지 않기로 되어 있어(app/state/saved_schedules.py) 일정
 * 내용에서 제목을 뽑으려면 그 전제를 깨야 한다. 일정을 이미 그리고 있는
 * 화면이 만드는 것이 맞다.
 *
 * route_summary를 쓰지 않는다 — 그것은 LLM이 쓴 안내 문장이라 길고 매번
 * 모양이 달라 목록 한 줄에 들어가지 않는다. 사용자가 목록에서 찾을 때 단서가
 * 되는 것은 "어디를 갔는지"다.
 */

import type { ScheduleItem } from "../types";

const FALLBACK = "저장한 일정";

export function defaultScheduleTitle(items: ScheduleItem[]): string {
  const names = items.map((item) => item.place_name?.trim()).filter(Boolean) as string[];
  if (names.length === 0) return FALLBACK;
  if (names.length === 1) return names[0];
  return `${names[0]} 외 ${names.length - 1}곳`;
}
