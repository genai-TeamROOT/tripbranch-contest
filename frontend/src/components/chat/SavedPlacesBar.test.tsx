/*
 * 역할: 하단 보관함 바의 표시 조건과 CTA·개별 빼기 동작을 검증한다(SCHEDULE-12 카드 3).
 * 입력: 저장본으로 심어둔 세션과 saved_places, 모킹한 보관함 API.
 * 출력: 빈 보관함 숨김, 개수 표시, 펼침 목록 순서, CTA/빼기 호출 검증.
 * 호출 시점: vitest 실행 시.
 *
 * 상태는 storage의 saveState로 심는다 — TripProvider가 초기값 위에 저장본을 덮어
 * 복원하므로, 리듀서에 테스트 전용 액션을 더하지 않고 session_id까지 함께 줄 수 있다.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { TripProvider } from "../../state/TripContext";
import type { TripState } from "../../state/TripContext";
import { saveState } from "../../state/storage";
import type { SavedPlaceItem } from "../../types";
import { SavedPlacesBar } from "./SavedPlacesBar";

const removeSavedPlace = vi.fn();

vi.mock("../../api/trip", () => ({
  fetchSavedPlaces: vi.fn(() => Promise.resolve({ session_id: "s", items: [], changed: false })),
  savePlace: vi.fn(),
  removeSavedPlace: (...args: unknown[]) => removeSavedPlace(...args),
}));

function saved(placeId: string, name: string): SavedPlaceItem {
  return {
    place_id: placeId,
    name,
    saved_from_run_id: "run-1",
    saved_at: "2026-09-01T00:00:00+09:00",
  };
}

function seed(items: SavedPlaceItem[]): void {
  saveState({
    language: "ko",
    user_input: "",
    interpreted_conditions: null,
    recommendations: [],
    unverified_recommendations: [],
    shown_place_ids: [],
    messages: [],
    auditTurns: [],
    phase: "ready",
    error: null,
    session_id: "session-1",
    last_turn_at: null,
    device_location: null,
    device_location_captured_at: null,
    device_location_snoozed_until: null,
    awaiting_clarification: false,
    saved_places: items,
    recent_follow_ups: [],
    agentProgress: null,
    streamingIntent: null,
  } satisfies TripState);
}

function renderBar(onPlanFromSaved = () => {}, isLoading = false) {
  return render(
    <TripProvider>
      <SavedPlacesBar onPlanFromSaved={onPlanFromSaved} isLoading={isLoading} />
    </TripProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  removeSavedPlace.mockReset();
  removeSavedPlace.mockResolvedValue({ session_id: "session-1", items: [], changed: true });
});

test("보관함이 비어 있으면 바를 그리지 않는다", () => {
  seed([]);
  renderBar();

  expect(screen.queryByRole("button", { name: "이 장소들로 일정 짜기" })).toBeNull();
});

test("담긴 개수를 보여주고 CTA를 누르면 콜백이 불린다", async () => {
  const user = userEvent.setup();
  const onPlanFromSaved = vi.fn();
  seed([saved("p1", "아키비스트 서촌"), saved("p2", "통인시장")]);
  renderBar(onPlanFromSaved);

  expect(screen.getByText(/보관함 2곳/)).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "이 장소들로 일정 짜기" }));

  expect(onPlanFromSaved).toHaveBeenCalledTimes(1);
});

test("턴이 진행 중이면 CTA를 잠근다", () => {
  seed([saved("p1", "아키비스트 서촌")]);
  renderBar(() => {}, true);

  expect(screen.getByRole("button", { name: "이 장소들로 일정 짜기" })).toBeDisabled();
});

/*
 * 목록 순서는 담은 순서이고, 개수 상한을 넘을 때 무엇을 남길지 이 순서로 정해진다.
 * 화면이 임의로 정렬하면 "왜 그 곳이 빠졌는지" 설명이 어긋나므로 순서까지 본다.
 */
test("펼치면 담은 순서대로 목록이 보이고 개별로 뺄 수 있다", async () => {
  const user = userEvent.setup();
  seed([saved("p1", "아키비스트 서촌"), saved("p2", "통인시장")]);
  renderBar();

  await user.click(screen.getByRole("button", { expanded: false }));

  const names = screen.getAllByRole("listitem").map((node) => node.textContent ?? "");
  expect(names[0]).toContain("아키비스트 서촌");
  expect(names[1]).toContain("통인시장");

  await user.click(screen.getByRole("button", { name: "통인시장 보관함에서 빼기" }));

  await waitFor(() => expect(removeSavedPlace).toHaveBeenCalledWith("session-1", "p2"));
});
