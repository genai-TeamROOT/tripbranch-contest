/*
 * 역할: 응답이 오지 않는 요청이 유한 시간 안에 끊기고, 끊긴 뒤 재시도가 되는지 검증한다.
 * 입력: 영원히 응답하지 않는 mock fetch와 매달리는 토큰 공급자, 가짜 타이머.
 * 출력: ApiError 코드와 시한에 대한 assertion.
 * 호출 시점: vitest 실행 시 TP-240 회귀 테스트로 호출된다.
 */

import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ApiError, apiClient, setAuthTokenProvider, streamPost } from "./client";

/** 응답을 주지 않는 서버. abort될 때만 거절한다 — 실제 fetch와 같은 모양이다. */
function hangingFetch() {
  return vi.fn(
    (_url: string, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          reject(new DOMException("Aborted", "AbortError"));
        });
      }),
  );
}

function jsonResponse(body: unknown = {}) {
  return { ok: true, json: async () => body } as unknown as Response;
}

beforeEach(() => {
  vi.useFakeTimers();
  setAuthTokenProvider(async () => "test_access_token");
});

afterEach(() => {
  vi.useRealTimers();
  setAuthTokenProvider(null);
  vi.unstubAllGlobals();
});

test("응답이 오지 않는 요청은 기본 시한에 끊기고 오류를 준다", async () => {
  vi.stubGlobal("fetch", hangingFetch());

  const pending = apiClient.get<unknown>("/favorites");
  const rejected = expect(pending).rejects.toBeInstanceOf(ApiError);

  await vi.advanceTimersByTimeAsync(12_000);
  await rejected;
  await expect(pending).rejects.toMatchObject({
    code: "request_timeout",
    retryable: true,
  });
});

/* 카드의 핵심 증상은 "새로고침해야 풀린다"였다. 같은 화면에서 다시 되는지를 못 박는다. */
test("끊긴 뒤 같은 화면에서 재시도가 된다", async () => {
  vi.stubGlobal("fetch", hangingFetch());

  const first = apiClient.get<unknown>("/favorites");
  const firstRejected = expect(first).rejects.toMatchObject({ code: "request_timeout" });
  await vi.advanceTimersByTimeAsync(12_000);
  await firstRejected;

  vi.stubGlobal(
    "fetch",
    vi.fn(async () => jsonResponse({ items: [] })),
  );
  await expect(apiClient.get<unknown>("/favorites")).resolves.toEqual({ items: [] });
});

test("오래 걸리는 추천 요청은 기본 시한에 끊기지 않는다", async () => {
  const fetchMock = hangingFetch();
  vi.stubGlobal("fetch", fetchMock);

  const pending = apiClient.post<unknown>("/recommendations", {});
  let settled = false;
  void pending.catch(() => {
    settled = true;
  });

  await vi.advanceTimersByTimeAsync(30_000);
  expect(settled).toBe(false);

  const rejected = expect(pending).rejects.toMatchObject({ code: "request_timeout" });
  await vi.advanceTimersByTimeAsync(30_000);
  await rejected;
});

/* getSession()은 fetch가 아니라 abort로 끊을 수 없다. 기다림만 끊어도 화면은 풀린다. */
test("토큰 조회가 응답하지 않으면 요청을 보내지 않고 끊는다", async () => {
  setAuthTokenProvider(() => new Promise<string | null>(() => {}));
  const fetchMock = hangingFetch();
  vi.stubGlobal("fetch", fetchMock);

  const pending = apiClient.get<unknown>("/favorites");
  const rejected = expect(pending).rejects.toMatchObject({ code: "auth_unavailable" });

  await vi.advanceTimersByTimeAsync(5_000);
  await rejected;
  expect(fetchMock).not.toHaveBeenCalled();
});

test("스트리밍도 연결 구간에서 매달리면 끊긴다", async () => {
  vi.stubGlobal("fetch", hangingFetch());

  const pending = streamPost("/chat/stream", { user_input: "안녕" }, () => {});
  const rejected = expect(pending).rejects.toMatchObject({ code: "request_timeout" });

  await vi.advanceTimersByTimeAsync(20_000);
  await rejected;
});

/* 중단 버튼이 듣지 않던 자리다 — abort는 fetch만 끊고 getSession()은 못 끊는다. */
test("토큰 조회 중에도 외부 중단 신호가 듣는다", async () => {
  setAuthTokenProvider(() => new Promise<string | null>(() => {}));
  const fetchMock = hangingFetch();
  vi.stubGlobal("fetch", fetchMock);

  const controller = new AbortController();
  const pending = streamPost("/chat/stream", {}, () => {}, controller.signal);
  const rejected = expect(pending).rejects.toMatchObject({ name: "AbortError" });

  controller.abort();
  await vi.advanceTimersByTimeAsync(0);
  await rejected;
  expect(fetchMock).not.toHaveBeenCalled();
});
