/*
 * 역할: 입력창 위 "N곳 일정 짜기" 칩이 언제 뜨고 무엇을 부르는지 검증한다.
 * 입력: 저장본으로 심어둔 세션과 saved_places, 모킹한 보관함 API.
 * 출력: 빈 보관함에서 숨김, 개수 문구, CTA 호출, 진행 중 잠금.
 *
 * 상태를 storage의 saveState로 심는 방식은 SavedPlacesBar.test.tsx와 같다 —
 * TripProvider가 초기값 위에 저장본을 덮어 복원하므로 리듀서에 테스트 전용
 * 액션을 더하지 않아도 된다.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { TripProvider } from "../../state/TripContext";
import type { TripState } from "../../state/TripContext";
import { saveState } from "../../state/storage";
import type { SavedPlaceItem } from "../../types";
import { SavedPlacesChip } from "./SavedPlacesChip";

vi.mock("../../api/trip", () => ({
  fetchSavedPlaces: vi.fn(() => Promise.resolve({ session_id: "s", items: [], changed: false })),
  savePlace: vi.fn(),
  removeSavedPlace: vi.fn(),
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

function renderChip(onPlanFromSaved = () => {}, isLoading = false) {
  return render(
    <TripProvider>
      <SavedPlacesChip onPlanFromSaved={onPlanFromSaved} isLoading={isLoading} />
    </TripProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
});

test("보관함이 비어 있으면 칩을 그리지 않는다", () => {
  seed([]);
  renderChip();

  // 자리도 잡지 않는다 — "0곳"은 알려줄 것이 없다.
  expect(screen.queryByRole("button")).toBeNull();
});

test("담은 개수를 문구에 담는다", () => {
  seed([saved("p1", "경복궁"), saved("p2", "북촌한옥마을"), saved("p3", "청계천")]);
  renderChip();

  expect(screen.getByRole("button", { name: "3곳 일정 짜기" })).toBeInTheDocument();
});

test("칩을 누르면 편성을 요청한다", async () => {
  const user = userEvent.setup();
  const onPlanFromSaved = vi.fn();
  seed([saved("p1", "경복궁")]);
  renderChip(onPlanFromSaved);

  await user.click(screen.getByRole("button", { name: "1곳 일정 짜기" }));

  expect(onPlanFromSaved).toHaveBeenCalledTimes(1);
});

test("턴이 진행 중이면 잠근다", () => {
  seed([saved("p1", "경복궁")]);
  renderChip(() => {}, true);

  // 앞 턴이 끝나기 전에 편성 요청이 겹치지 않게 한다.
  expect(screen.getByRole("button", { name: "1곳 일정 짜기" })).toBeDisabled();
});
