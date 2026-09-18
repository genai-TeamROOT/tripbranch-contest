/*
 * 역할: API 클라이언트가 등록된 신원 토큰을 세 경로 모두에 붙이는지 검증한다.
 * 입력: setAuthTokenProvider로 등록한 토큰 공급자, mock fetch.
 * 출력: 요청 헤더에 대한 assertion.
 * 호출 시점: vitest 실행 시 인증 회귀 테스트로 호출된다.
 * TODO: Phase 4에서 인증이 필수화되면 토큰 부재 시 동작도 여기서 고정한다.
 */

import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { apiClient, setAuthTokenProvider, streamPost } from "./client";

function headersOf(call: unknown): Record<string, string> {
  const [, init] = call as [string, RequestInit];
  return (init.headers ?? {}) as Record<string, string>;
}

function jsonResponse() {
  return { ok: true, json: async () => ({}) } as unknown as Response;
}

function streamResponse() {
  return {
    ok: true,
    body: { getReader: () => ({ read: async () => ({ done: true, value: undefined }) }) },
  } as unknown as Response;
}

beforeEach(() => {
  setAuthTokenProvider(async () => "test_access_token");
});

afterEach(() => {
  setAuthTokenProvider(null);
  vi.unstubAllGlobals();
});

test("post 요청에 Authorization 헤더가 붙는다", async () => {
  const fetchMock = vi.fn(async () => jsonResponse());
  vi.stubGlobal("fetch", fetchMock);

  await apiClient.post("/chat", { user_input: "안녕" });

  expect(headersOf(fetchMock.mock.calls[0]).Authorization).toBe("Bearer test_access_token");
});

/* /api/transcribe가 쓰는 경로다. JSON 경로와 별도 함수라 빠뜨리기 쉽다. */
test("바이너리 업로드에도 Authorization 헤더가 붙는다", async () => {
  const fetchMock = vi.fn(async () => jsonResponse());
  vi.stubGlobal("fetch", fetchMock);

  await apiClient.postBinary("/transcribe", new Blob(["x"]), "audio/wav");

  expect(headersOf(fetchMock.mock.calls[0]).Authorization).toBe("Bearer test_access_token");
});

test("SSE 스트리밍 요청에도 Authorization 헤더가 붙는다", async () => {
  const fetchMock = vi.fn(async () => streamResponse());
  vi.stubGlobal("fetch", fetchMock);

  await streamPost("/chat/stream", { user_input: "안녕" }, () => {});

  expect(headersOf(fetchMock.mock.calls[0]).Authorization).toBe("Bearer test_access_token");
});

test("세션이 없으면 헤더를 붙이지 않는다", async () => {
  setAuthTokenProvider(async () => null);
  const fetchMock = vi.fn(async () => jsonResponse());
  vi.stubGlobal("fetch", fetchMock);

  await apiClient.post("/chat", {});

  expect(headersOf(fetchMock.mock.calls[0]).Authorization).toBeUndefined();
});

test("공급자가 등록되기 전에도 요청은 그대로 나간다", async () => {
  setAuthTokenProvider(null);
  const fetchMock = vi.fn(async () => jsonResponse());
  vi.stubGlobal("fetch", fetchMock);

  await apiClient.post("/chat", {});

  const headers = headersOf(fetchMock.mock.calls[0]);
  expect(headers.Authorization).toBeUndefined();
  expect(headers["Content-Type"]).toBe("application/json");
});
