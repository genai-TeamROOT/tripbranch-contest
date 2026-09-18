/*
 * 역할: 체크 상태 저장·복원(useScheduleVisited)을 검증한다.
 *
 * 화면 쪽(ScheduleRoute·ScheduleRibbon) 테스트는 이 훅이 주는 값을 그대로
 * prop으로 받는다고 가정하고 렌더링만 본다 — 저장·복원 로직은 여기서 한 번만
 * 잠근다.
 */

import { act, renderHook } from "@testing-library/react";
import { beforeEach, expect, test } from "vitest";
import { useScheduleVisited } from "./useScheduleVisited";

beforeEach(() => {
  localStorage.clear();
});

test("처음엔 아무것도 체크돼 있지 않다", () => {
  const { result } = renderHook(() => useScheduleVisited("sched-1"));
  const [visited] = result.current;

  expect(visited.size).toBe(0);
});

test("켜고 끄면 그 인덱스만 바뀐다", () => {
  const { result } = renderHook(() => useScheduleVisited("sched-1"));

  act(() => result.current[1](0));
  expect(result.current[0].has(0)).toBe(true);

  act(() => result.current[1](2));
  expect([...result.current[0]].sort()).toEqual([0, 2]);

  act(() => result.current[1](0));
  expect(result.current[0].has(0)).toBe(false);
  expect(result.current[0].has(2)).toBe(true);
});

test("체크는 저장돼 다시 마운트해도 유지된다", () => {
  const first = renderHook(() => useScheduleVisited("sched-abc"));
  act(() => first.result.current[1](1));
  first.unmount();

  const second = renderHook(() => useScheduleVisited("sched-abc"));
  expect(second.result.current[0].has(1)).toBe(true);
});

test("다른 scheduleKey면 체크가 섞이지 않는다", () => {
  const a = renderHook(() => useScheduleVisited("sched-a"));
  act(() => a.result.current[1](0));
  a.unmount();

  const b = renderHook(() => useScheduleVisited("sched-b"));
  expect(b.result.current[0].size).toBe(0);
});
