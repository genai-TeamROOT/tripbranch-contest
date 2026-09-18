/*
 * 역할: 시간 띠 계산과 "지금" 판정을 잠근다.
 * 근거: utils/scheduleTimeline.ts — 자정을 넘는 일정과 일정 밖 시각이 섞여 있어
 *   화면 없이 값으로 확인해야 하는 부분이다.
 */

import { describe, expect, test } from "vitest";
import type { ScheduleItem } from "../types";
import {
  buildScheduleTimeline,
  clockLabel,
  locateNow,
  parseClock,
} from "./scheduleTimeline";

function stop(
  arrival: string,
  stay: number,
  travel: number | null,
  name = arrival,
): ScheduleItem {
  return {
    order: 1,
    place_id: `p-${name}`,
    place_name: name,
    estimated_arrival: arrival,
    estimated_duration_min: stay,
    travel_to_next_min: travel,
    reason: "",
  };
}

/* 시안에 쓴 그 일정이다 — 국립현대미술관 90분, 도보 6분, 공예박물관 45분,
   지하철 21분, 광장시장 65분. 합 227분, 14:10 → 17:57. */
const AFTERNOON = [
  stop("14:10", 90, 6, "국립현대미술관 서울"),
  stop("15:46", 45, 21, "서울공예박물관"),
  stop("16:52", 65, null, "광장시장"),
];

describe("parseClock", () => {
  test("HH:MM 을 자정 기준 분으로 읽는다", () => {
    expect(parseClock("14:10")).toBe(850);
    expect(parseClock("00:00")).toBe(0);
    expect(parseClock("23:59")).toBe(1439);
    /* 백엔드는 %H:%M 이라 한 자리 시가 오지 않지만, 세션 복원분에는 섞일 수 있다. */
    expect(parseClock("9:05")).toBe(545);
  });

  test("형식이 어긋나면 null 이다", () => {
    /* 여기서 null 이 나와야 buildScheduleTimeline 이 띠를 통째로 포기한다 —
       한 칸만 0분으로 그리면 나머지 폭이 전부 틀어진다. */
    expect(parseClock("오후 2:10")).toBeNull();
    expect(parseClock("25:00")).toBeNull();
    expect(parseClock("14:70")).toBeNull();
    expect(parseClock("")).toBeNull();
  });
});

describe("buildScheduleTimeline", () => {
  test("머무름과 이동이 번갈아 나오고 합이 맞는다", () => {
    const timeline = buildScheduleTimeline(AFTERNOON)!;

    expect(timeline.segments.map((s) => `${s.kind}:${s.minutes}`)).toEqual([
      "stay:90",
      "move:6",
      "stay:45",
      "move:21",
      "stay:65",
    ]);
    expect(timeline.totalMinutes).toBe(227);
  });

  test("마지막 항목의 이동 시간은 무시한다", () => {
    /* 갈 곳이 없다. 서버가 값을 채워 보내도 띠에 칸을 만들면 안 된다. */
    const withTail = [stop("14:10", 90, 6), stop("15:46", 45, 30)];
    const timeline = buildScheduleTimeline(withTail)!;

    expect(timeline.segments.map((s) => s.kind)).toEqual(["stay", "move", "stay"]);
    expect(timeline.totalMinutes).toBe(141);
  });

  test("종료 시각은 구간 합이 아니라 마지막 도착 시각에서 잰다", () => {
    /*
     * 서버는 **도착 시각만** 10분 단위로 올린다(planner._round_up_arrival).
     * 여기서는 90분 머물고 6분 걸으면 15:46 인데 서버가 15:50 으로 올려 보낸
     * 경우다 — 시작+구간합(16:31)과 실제 종료(16:35)가 4분 어긋난다.
     * 화면에 적는 시각은 서버가 보낸 도착 시각을 따라야 한다.
     */
    const rounded = [stop("14:10", 90, 6), stop("15:50", 45, null)];
    const timeline = buildScheduleTimeline(rounded)!;

    expect(timeline.startMinutes + timeline.totalMinutes).toBe(parseClock("16:31"));
    expect(timeline.endMinutes).toBe(parseClock("16:35"));
  });

  test("자정을 넘는 일정은 뒤 시각에 하루를 더해 편다", () => {
    /* 23:30 다음의 00:40 은 숫자로는 뒤로 간다. 펴지 않으면 띠 폭이 음수가 된다. */
    const overnight = [stop("23:30", 40, 30), stop("00:40", 50, null)];
    const timeline = buildScheduleTimeline(overnight)!;

    expect(timeline.arrivals).toEqual([23 * 60 + 30, 24 * 60 + 40]);
    expect(timeline.endMinutes).toBeGreaterThan(timeline.startMinutes);
    expect(timeline.endMinutes).toBe(24 * 60 + 40 + 50);
  });

  test("도착 시각을 못 읽으면 띠를 그리지 않는다", () => {
    expect(buildScheduleTimeline([stop("어제", 90, null)])).toBeNull();
  });

  test("항목이 없으면 null 이다", () => {
    expect(buildScheduleTimeline([])).toBeNull();
  });
});

describe("locateNow", () => {
  const timeline = buildScheduleTimeline(AFTERNOON)!;

  function at(hhmm: string): Date {
    const minutes = parseClock(hhmm)!;
    return new Date(2026, 8, 6, Math.floor(minutes / 60), minutes % 60);
  }

  test("머무는 중이면 그 정류장과 남은 시간을 준다", () => {
    /* 15:20 — 첫 곳에 도착한 지 70분, 90분 중 20분 남았다. */
    const now = locateNow(timeline, at("15:20"))!;

    expect(now.itemIndex).toBe(0);
    expect(now.minutesLeftHere).toBe(20);
    expect(now.ratio).toBeCloseTo(70 / 227, 4);
  });

  test("이동 중이면 정류장이 없다", () => {
    /* 15:42 — 첫 곳을 떠나 걷는 중(90분 지나 92분째). */
    const now = locateNow(timeline, at("15:42"))!;

    expect(now.itemIndex).toBeNull();
    expect(now.minutesLeftHere).toBeNull();
  });

  test("일정 시작 전이면 null 이다", () => {
    /* 0%에 "지금"을 붙이면 아직 출발도 안 했는데 시작한 것처럼 보인다. */
    expect(locateNow(timeline, at("13:00"))).toBeNull();
  });

  test("일정이 끝난 뒤면 null 이다", () => {
    expect(locateNow(timeline, at("18:30"))).toBeNull();
  });

  test("마지막 정류장의 끝 시각까지는 아직 그 안이다", () => {
    const now = locateNow(timeline, at("17:57"))!;

    expect(now.itemIndex).toBe(2);
    expect(now.minutesLeftHere).toBe(0);
  });

  test("자정을 넘는 일정에서 새벽 시각도 일정 안으로 본다", () => {
    /*
     * 00:10 은 자정 기준 10분이라 시작(23:30)보다 이르다. 하루를 더해 봐야
     * 일정 안이라는 것을 알 수 있다 — 안 그러면 새벽에 "지금"이 사라진다.
     */
    const overnight = buildScheduleTimeline([stop("23:30", 40, 30), stop("00:40", 50, null)])!;
    const now = locateNow(overnight, new Date(2026, 8, 7, 0, 10))!;

    expect(now.itemIndex).toBeNull(); // 40분 머물고 떠나 이동 중
    expect(now.ratio).toBeGreaterThan(0);
  });
});

describe("clockLabel", () => {
  test("자정 기준 분을 한국어 시각으로 적는다", () => {
    expect(clockLabel(parseClock("14:10")!, false)).toContain("2:10");
    expect(clockLabel(parseClock("14:10")!, false)).toContain("오후");
  });

  test("자정을 넘긴 값도 그날 시각으로 되돌려 적는다", () => {
    /* 1480분 = 다음 날 00:40. 24시간을 덜어내지 않으면 Date 가 넘쳐 엉뚱한 값이 된다. */
    expect(clockLabel(24 * 60 + 40, false)).toContain("12:40");
  });

  test("영어로도 적는다", () => {
    expect(clockLabel(parseClock("14:10")!, true)).toMatch(/PM/i);
  });
});
