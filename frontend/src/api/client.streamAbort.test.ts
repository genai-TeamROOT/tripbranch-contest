/*
 * 역할: streamPost의 externalSignal(중단 버튼)이 실제로 fetch를 끊고, AbortError가
 *   그대로(내부 오류로 뭉개지지 않고) 호출자에게 전달되는지 검증한다.
 * 입력: mock fetch, 외부에서 만든 AbortController.
 * 출력: 중단 시 streamPost 반환 Promise가 AbortError로 reject되는지, 그리고 요청이
 *   나가기 전이면 fetch에 닿지도 않는지에 대한 assertion.
 * 호출 시점: vitest 실행 시. ChatPage/HomePage의 "중단" 버튼(state/activeChatTurn.ts)이
 *   이 계약에 의존한다 — AbortError가 아닌 다른 에러로 바뀌면 취소가 조용히
 *   처리되지 못하고 화면에 오류 배너가 뜬다.
 */

import { afterEach, expect, test, vi } from "vitest";
import { streamPost } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

/** 실제 fetch처럼: 호출 시점에 이미 aborted면 즉시 reject, 아니면 이후 abort를 기다린다. */
function stubAbortAwareFetch() {
  let capturedSignal: AbortSignal | undefined;
  const fetchMock = vi.fn((_url: string, init: RequestInit) => {
    capturedSignal = init.signal as AbortSignal;
    if (capturedSignal.aborted) {
      return Promise.reject(new DOMException("aborted", "AbortError"));
    }
    return new Promise((_resolve, reject) => {
      capturedSignal!.addEventListener("abort", () => {
        reject(new DOMException("aborted", "AbortError"));
      });
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return () => capturedSignal;
}

test("헤더를 받기 전에 중단하면 AbortError로 끝난다(연결 실패로 뭉개지지 않는다)", async () => {
  const getCapturedSignal = stubAbortAwareFetch();

  const external = new AbortController();
  const promise = streamPost("/chat/stream", { user_input: "안녕" }, () => {}, external.signal);
  /* 요청이 실제로 나간 뒤에 끊는다. 인증 헤더를 붙이는 구간이 fetch보다 앞에 있어서,
     그 전에 끊으면 fetch가 아예 호출되지 않는다(아래 테스트가 그 경우다). */
  await vi.waitFor(() => expect(getCapturedSignal()).toBeDefined());
  external.abort();

  await expect(promise).rejects.toMatchObject({ name: "AbortError" });
  expect(getCapturedSignal()?.aborted).toBe(true);
});

test("이미 중단된 signal을 넘기면 요청을 아예 보내지 않는다", async () => {
  const getCapturedSignal = stubAbortAwareFetch();

  const external = new AbortController();
  external.abort();

  await expect(
    streamPost("/chat/stream", { user_input: "안녕" }, () => {}, external.signal),
  ).rejects.toMatchObject({ name: "AbortError" });
  /* 중단 신호가 인증 헤더 대기까지 끊게 되면서(TP-240) 이 경우 fetch에 닿지 않는다.
     예전에는 헤더를 붙인 뒤 중단된 signal로 한 번 부르고 브라우저가 즉시 거절했다. */
  expect(getCapturedSignal()).toBeUndefined();
});
