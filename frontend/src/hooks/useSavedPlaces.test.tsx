/*
 * 역할: useSavedPlaces의 서버 재조회(refresh)를 검증한다.
 * 입력: 저장본으로 심어둔 세션(session_id 유무·saved_places), 모킹한 fetchSavedPlaces.
 * 출력: 세션이 있을 때만 조회가 일어나고, 그 결과로 화면 상태가 갱신되는지,
 *   조회가 실패해도 직전 목록이 유지되는지 확인.
 * 호출 시점: vitest 실행 시.
 *
 * 상태는 storage의 saveState로 심는다 — TripProvider가 초기값 위에 저장본을 덮어
 * 복원하므로, Probe 컴포넌트가 마운트되기 전에 session_id가 이미 반영돼 있다
 * (SavedPlacesBar.test.tsx와 같은 패턴).
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { TripProvider, useTripState } from "../state/TripContext";
import type { TripState } from "../state/TripContext";
import { saveState } from "../state/storage";
import type { SavedPlaceItem } from "../types";
import { useSavedPlaces } from "./useSavedPlaces";

const fetchSavedPlaces = vi.fn();
const savePlace = vi.fn();
const removeSavedPlace = vi.fn();

vi.mock("../api/trip", () => ({
  fetchSavedPlaces: (...args: unknown[]) => fetchSavedPlaces(...args),
  savePlace: (...args: unknown[]) => savePlace(...args),
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

function seed(sessionId: string | null, items: SavedPlaceItem[]): void {
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
    session_id: sessionId,
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

/* 훅을 직접 부르는 최소 화면. 버튼을 눌러야 조회가 나가므로 마운트 부수효과와
   섞이지 않는다 — 페이지가 언제 부르는지와 무관하게 훅 자체를 잰다. */
function Probe() {
  const { savedPlaces, refresh, toggleSaved } = useSavedPlaces();
  /* 실패는 state.error가 아니라 대화의 turn_error 한 줄로 간다(TP-245). */
  const { messages } = useTripState();
  const lastError = messages.filter((message) => message.type === "turn_error").at(-1);
  return (
    <div>
      <button type="button" onClick={() => void refresh()}>
        다시 읽기
      </button>
      <button type="button" onClick={() => void toggleSaved({ place_id: "p-new", name: "새 곳" })}>
        담기
      </button>
      <button type="button" onClick={() => void toggleSaved({ place_id: "p-1", name: "안국역" })}>
        빼기
      </button>
      <div data-testid="count">{savedPlaces.length}</div>
      <div data-testid="error">{lastError?.type === "turn_error" ? lastError.text : ""}</div>
    </div>
  );
}

function renderProbe() {
  return render(
    <TripProvider>
      <Probe />
    </TripProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  fetchSavedPlaces.mockReset();
  savePlace.mockReset();
  removeSavedPlace.mockReset();
});

test("세션이 있으면 서버 목록으로 교체한다", async () => {
  seed("session-1", [saved("p1", "아키비스트 서촌")]);
  fetchSavedPlaces.mockResolvedValue({
    session_id: "session-1",
    last_turn_at: null,
    items: [saved("p1", "아키비스트 서촌"), saved("p2", "통인시장")],
    changed: false,
  });

  renderProbe();
  await userEvent.click(screen.getByRole("button", { name: "다시 읽기" }));

  await waitFor(() => expect(fetchSavedPlaces).toHaveBeenCalledWith("session-1"));
  await waitFor(() => expect(screen.getByTestId("count")).toHaveTextContent("2"));
});

test("세션이 없으면 서버를 부르지 않는다", async () => {
  seed(null, []);

  renderProbe();
  await userEvent.click(screen.getByRole("button", { name: "다시 읽기" }));

  expect(fetchSavedPlaces).not.toHaveBeenCalled();
  expect(screen.getByTestId("count")).toHaveTextContent("0");
});

test("서버 조회가 실패해도 직전 목록을 그대로 보여준다", async () => {
  seed("session-1", [saved("p1", "아키비스트 서촌")]);
  fetchSavedPlaces.mockRejectedValue(new Error("network"));

  renderProbe();
  await userEvent.click(screen.getByRole("button", { name: "다시 읽기" }));

  await waitFor(() => expect(fetchSavedPlaces).toHaveBeenCalledWith("session-1"));
  expect(screen.getByTestId("count")).toHaveTextContent("1");
});

/*
 * 담기와 빼기가 같은 commit()을 타면서 실패 문구도 하나였다(TP-250). 방금 무엇을
 * 눌렀는지와 문구가 어긋나서, 빼기에 실패했는데 "담지 못했어요"가 뜰 수 있었다.
 */
test("담기에 실패하면 담지 못했다고 말한다", async () => {
  seed("session-1", []);
  savePlace.mockRejectedValue(new Error("boom"));
  renderProbe();

  await userEvent.click(screen.getByRole("button", { name: "담기" }));

  await waitFor(() =>
    expect(screen.getByTestId("error")).toHaveTextContent("보관함에 담지 못했어요."),
  );
});

test("빼기에 실패하면 빼지 못했다고 말한다", async () => {
  seed("session-1", [saved("p-1", "안국역")]);
  removeSavedPlace.mockRejectedValue(new Error("boom"));
  renderProbe();

  await userEvent.click(screen.getByRole("button", { name: "빼기" }));

  await waitFor(() =>
    expect(screen.getByTestId("error")).toHaveTextContent("보관함에서 빼지 못했어요."),
  );
});

/* 실패하면 낙관적으로 그렸던 것을 되돌린다 — 문구는 그 되돌아감이 무엇이었는지를
   설명하는 자리다. */
test("담기에 실패하면 목록이 원래대로 돌아간다", async () => {
  seed("session-1", []);
  savePlace.mockRejectedValue(new Error("boom"));
  renderProbe();

  await userEvent.click(screen.getByRole("button", { name: "담기" }));

  await waitFor(() => expect(screen.getByTestId("count")).toHaveTextContent("0"));
});
