/*
 * 역할: TripBranch 백엔드와 통신하는 공통 fetch 래퍼.
 * 입력: API path, 요청 body, fetch 옵션.
 * 출력: 파싱된 JSON 응답 또는 표준화된 ApiError.
 * 호출 시점: endpoint별 API 함수가 HTTP 요청을 보낼 때 호출된다.
 * TODO: retry 정책이 필요해지면 이 계층에서 추가한다(TP-240은 끊는 것까지만 했다).
 *
 * 오류 문구에 "다시 시도해주세요"를 넣지 않는다(TP-250). 채팅 화면은 실패한 턴에
 * "다시 시도" 버튼을 함께 그리므로(TP-245) 문구가 그 말을 반복하면 같은 화면에서
 * 두 번 말하게 된다. 문구는 무슨 일이 일어났는지만 말한다.
 */

import type { ApiErrorBody } from "../types";

const rawBaseUrl = import.meta.env.VITE_API_BASE_URL || "/api";
const API_BASE_URL = rawBaseUrl.replace(/\/$/, "");

export class ApiError extends Error {
  code: string;
  retryable: boolean;
  details: unknown;

  constructor(body: ApiErrorBody) {
    super(body.message);
    this.name = "ApiError";
    this.code = body.code;
    this.retryable = body.retryable;
    this.details = body.details;
  }
}

/*
 * 모든 요청에 시한을 둔다(TP-240).
 *
 * **왜 필요한가.** 응답이 영영 안 오면 호출부의 `await`가 안 끝나고, 그러면
 * `finally`에서 푸는 `isSearching`·`isSubmitting` 같은 플래그가 true로 굳는다.
 * 버튼은 disabled로 잠기고 재시도는 "요청 중이면 무시" 가드에 막혀 오류 문구
 * 하나 없이 조용히 사라진다. 새로고침 말고는 빠져나갈 길이 없고, 새 비밀번호
 * 설정 화면은 새로고침하면 폼 자체가 사라진다.
 *
 * **왜 한 값이 아닌가.** 추천·채팅은 원래 수십 초가 걸린다. 짧은 조회에 맞춘 값
 * 하나로 묶으면 멀쩡히 일하던 요청을 끊게 된다. 그래서 경로별로 나눈다.
 */
const DEFAULT_TIMEOUT_MS = 12_000;

/* 인증 헤더는 본 요청보다 먼저 끝나야 한다 — 여기서 물리면 요청이 시작도 못 한다. */
const AUTH_HEADER_TIMEOUT_MS = 5_000;

/* 스트리밍은 "연결까지"만 여기서 재고, 그 뒤는 기존 45초 무응답 타이머가 맡는다. */
const STREAM_CONNECT_TIMEOUT_MS = 20_000;

/* 앞에서부터 처음 맞는 접두사를 쓴다. `/chat`이 `/chat/place-details`도 덮는다. */
const TIMEOUT_BY_PREFIX: ReadonlyArray<readonly [string, number]> = [
  /* 장소 동기화·집중률 빌드는 한 요청 안에서 일을 다 한다. */
  ["/dev/", 120_000],
  ["/interpret", 60_000],
  ["/recommendations", 60_000],
  ["/chat", 60_000],
  ["/transcribe", 60_000],
  ["/places/similar-by-photo", 60_000],
  /* 백엔드 장소 검색 자체는 10초로 막혀 있지만(providers/local_search.py) 해석
     사다리(routes/place_search.py)를 더 타면 그보다 길어질 수 있다. */
  ["/places/search", 30_000],
];

function timeoutMsFor(path: string): number {
  for (const [prefix, ms] of TIMEOUT_BY_PREFIX) {
    if (path.startsWith(prefix)) return ms;
  }
  return DEFAULT_TIMEOUT_MS;
}

function timeoutError(): ApiError {
  return new ApiError({
    code: "request_timeout",
    message: "서버가 응답하지 않아 요청을 끊었어요.",
    retryable: true,
    details: null,
  });
}

function connectionError(): ApiError {
  return new ApiError({
    code: "internal_server_error",
    message: "인터넷 연결을 확인해주세요.",
    retryable: true,
    details: null,
  });
}

/*
 * `promise`를 정해진 시간까지만 기다린다.
 *
 * abort와 다르다 — **기다림만 끊고 하던 일은 못 끊는다.** getSession()처럼 fetch가
 * 아닌 대기에는 AbortSignal이 듣지 않아서, 호출자를 풀어주려면 이 방법뿐이다.
 */
function withDeadline<T>(promise: Promise<T>, ms: number, onTimeout: () => Error): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(onTimeout()), ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error: unknown) => {
        clearTimeout(timer);
        reject(error instanceof Error ? error : new Error(String(error)));
      },
    );
  });
}

/** 신호가 끊기면 기다림을 포기한다. `withDeadline`과 같은 한계를 가진다. */
function untilAborted<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const abortError = () => new DOMException("Aborted", "AbortError");
    if (signal.aborted) {
      reject(abortError());
      return;
    }
    const onAbort = () => reject(abortError());
    signal.addEventListener("abort", onAbort, { once: true });
    promise.then(
      (value) => {
        signal.removeEventListener("abort", onAbort);
        resolve(value);
      },
      (error: unknown) => {
        signal.removeEventListener("abort", onAbort);
        reject(error instanceof Error ? error : new Error(String(error)));
      },
    );
  });
}

/* 게스트/정식 신원의 access token 공급자. AuthProvider가 등록한다(D-062 4절).
   토큰을 여기 보관하지 않고 매번 물어보는 이유는 자동 갱신된 토큰을 놓치지 않기
   위해서다. 등록 전이거나 세션이 없으면 헤더를 붙이지 않는다 — 백엔드가 아직
   optional 인증이라 그대로 통과한다(Phase 4에서 필수화). */
type AuthTokenProvider = () => Promise<string | null>;

let authTokenProvider: AuthTokenProvider | null = null;

export function setAuthTokenProvider(provider: AuthTokenProvider | null): void {
  authTokenProvider = provider;
}

async function authHeaders(): Promise<Record<string, string>> {
  if (!authTokenProvider) return {};
  const token = await authTokenProvider();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/*
 * 토큰을 못 받으면 **헤더 없이 보내지 않고 요청을 끊는다.**
 *
 * 백엔드 인증이 아직 optional이라 토큰 없는 요청도 통과한다. 그래서 조용히 넘기면
 * 저장까지 성공하는데 남의 신원(혹은 신원 없음)으로 남는다 — 화면에는 아무것도
 * 안 드러나고 나중에 데이터로만 발견된다. 조용한 fake와 같은 실패라 막는다.
 */
function authUnavailableError(): ApiError {
  return new ApiError({
    code: "auth_unavailable",
    message: "로그인 정보를 확인하지 못했어요.",
    retryable: true,
    details: null,
  });
}

/**
 * 세 요청 경로(JSON·바이너리·multipart)가 공유하는 본체.
 *
 * `authHeaders()`를 여기 try 안에 두는 것이 요점이다. 예전에는 호출자 쪽 try
 * 바깥에 있어서, 인증에서 던지면 ApiError로 감싸이지도 않고 타임아웃도 안 걸렸다.
 */
async function send<T>(
  path: string,
  init: RequestInit,
  baseHeaders: Record<string, string>,
): Promise<T> {
  const budgetMs = timeoutMsFor(path);
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, budgetMs);

  try {
    const auth = await withDeadline(
      authHeaders(),
      Math.min(AUTH_HEADER_TIMEOUT_MS, budgetMs),
      authUnavailableError,
    );

    let response: Response;
    try {
      response = await fetch(`${API_BASE_URL}${path}`, {
        ...init,
        headers: { ...baseHeaders, ...auth, ...init.headers },
        signal: controller.signal,
      });
    } catch {
      throw timedOut ? timeoutError() : connectionError();
    }

    /* 본문 읽기도 시한 안에 있다. 여기서 매달리면 헤더만 받고 영영 안 끝난다. */
    let data: { error?: ApiErrorBody } | null;
    try {
      data = (await response.json()) as { error?: ApiErrorBody } | null;
    } catch {
      if (timedOut) throw timeoutError();
      data = null;
    }

    if (!response.ok) {
      throw new ApiError(
        data?.error ?? {
          code: "internal_server_error",
          message: "요청을 처리하지 못했어요.",
          retryable: false,
          details: null,
        },
      );
    }

    return data as T;
  } finally {
    clearTimeout(timer);
  }
}

function request<T>(path: string, options?: RequestInit): Promise<T> {
  return send<T>(path, options ?? {}, { "Content-Type": "application/json" });
}

function requestBinary<T>(path: string, body: Blob, contentType: string): Promise<T> {
  return send<T>(path, { method: "POST", body }, { "Content-Type": contentType });
}

/**
 * multipart/form-data 전송.
 *
 * Content-Type을 **직접 넣지 않는다.** FormData를 body로 주면 브라우저가
 * `multipart/form-data; boundary=...`를 알아서 붙이는데, 여기서 헤더를 지정하면
 * boundary가 빠져 서버가 본문을 못 나눈다.
 */
function requestForm<T>(path: string, body: FormData): Promise<T> {
  return send<T>(path, { method: "POST", body }, {});
}

/** POST 본문을 유지한 SSE 응답 파서. EventSource는 GET만 지원해 채팅 요청에 맞지 않는다. */
export async function streamPost<T>(
  path: string,
  body: unknown,
  onEvent: (event: string, data: T) => void,
  /** 호출자가 요청을 중단할 수 있게 하는 외부 신호(응답 중단 기능, §7.2). */
  externalSignal?: AbortSignal,
): Promise<void> {
  const controller = new AbortController();
  if (externalSignal) {
    if (externalSignal.aborted) controller.abort();
    else externalSignal.addEventListener("abort", () => controller.abort(), { once: true });
  }

  /* 연결 구간(인증 헤더 + 응답 헤더 도착)에도 시한을 둔다. 아래 무응답 타이머는
     본문을 읽기 시작한 뒤에야 걸리므로 그 앞은 예전에 무방비였다. */
  let connectTimedOut = false;
  let connectTimer: ReturnType<typeof setTimeout> | null = setTimeout(() => {
    connectTimedOut = true;
    controller.abort();
  }, STREAM_CONNECT_TIMEOUT_MS);
  const clearConnectTimer = () => {
    if (connectTimer) {
      clearTimeout(connectTimer);
      connectTimer = null;
    }
  };

  // 서버가 첫 progress를 보낸 뒤 프로세스 재시작·네트워크 단절 등으로 다음 이벤트를
  // 못 보내면 fetch 스트림은 닫히지 않은 채 대기할 수 있다. 단순 ping이 아니라 실제
  // 업무 이벤트(progress/result/delta/done/error) 기준으로만 시간을 갱신한다.
  let inactivityTimer: ReturnType<typeof setTimeout> | null = null;
  let inactivityTimedOut = false;
  const armInactivityTimer = () => {
    if (inactivityTimer) clearTimeout(inactivityTimer);
    inactivityTimer = setTimeout(() => {
      inactivityTimedOut = true;
      controller.abort();
    }, 45_000);
  };

  let response: Response;
  try {
    /* 중단 버튼이 여기서도 듣게 한다. abort는 fetch만 끊고 getSession()은 못 끊어서,
       인증 대기 중에 물리면 예전에는 중단조차 먹지 않았다. */
    const auth = await untilAborted(authHeaders(), controller.signal);
    response = await fetch(`${API_BASE_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream", ...auth },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
  } catch (error) {
    clearConnectTimer();
    if (connectTimedOut) throw timeoutError();
    // 응답이 오기 전에 사용자가 중단했을 수 있다 — AbortError를 일반 오류로
    // 감싸면 호출자가 "중단"과 "연결 실패"를 구분하지 못한다.
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw connectionError();
  }

  if (!response.ok || !response.body) {
    /* 오류 본문도 연결 시한 안에서 읽는다 — 헤더만 오고 본문이 안 오면 여기서 멈춘다. */
    const data = await response.json().catch(() => null);
    clearConnectTimer();
    const errorBody = data?.error as ApiErrorBody | undefined;
    throw new ApiError(
      errorBody ?? {
        code: "internal_server_error",
        message: "요청을 처리하지 못했어요.",
        retryable: false,
        details: null,
      },
    );
  }
  clearConnectTimer();

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const consumeFrame = (frame: string) => {
    const lines = frame.replace(/\r/g, "").split("\n");
    const event = lines
      .find((line) => line.startsWith("event:"))
      ?.slice(6)
      .trim();
    const data = lines
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart())
      .join("\n");
    if (event && data) {
      armInactivityTimer();
      onEvent(event, JSON.parse(data) as T);
    }
  };

  armInactivityTimer();
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
      // sse-starlette는 환경에 따라 CRLF(\r\n)를 쓸 수 있다. 프레임 경계를 찾기 전에
      // LF로 통일하지 않으면 "\r\n\r\n"을 "\n\n"으로 인식하지 못해 이벤트가
      // 마지막까지 화면에 전달되지 않는다.
      buffer = buffer.replace(/\r\n/g, "\n");
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        consumeFrame(buffer.slice(0, boundary));
        buffer = buffer.slice(boundary + 2);
        boundary = buffer.indexOf("\n\n");
      }
      if (done) break;
    }
    if (buffer.trim()) consumeFrame(buffer);
  } catch (error) {
    if (inactivityTimedOut) {
      throw new ApiError({
        code: "stream_inactive",
        message: "응답이 45초 동안 멈춰서 끊었어요.",
        retryable: true,
        details: null,
      });
    }
    throw error;
  } finally {
    if (inactivityTimer) clearTimeout(inactivityTimer);
    clearConnectTimer();
  }
}

export const apiClient = {
  get: <T>(path: string) => request<T>(path, { method: "GET" }),
  post: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body) }),
  postBinary: <T>(path: string, body: Blob, contentType: string) =>
    requestBinary<T>(path, body, contentType),
  postForm: <T>(path: string, body: FormData) => requestForm<T>(path, body),
  /* 취향 저장(PUT /preferences)이 첫 사용처다. 항목 단위 추가가 아니라 전체
     교체라서 POST가 아니라 PUT이다 — 같은 요청을 두 번 보내도 결과가 같다. */
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  /* 대화 이름 바꾸기(PATCH /state/{id}/title)가 첫 사용처다. 자원의 일부만
     바꾸므로 PUT이 아니라 PATCH다. */
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  /* 보관함 빼기(DELETE .../saved-places/{place_id})가 첫 사용처다. 본문 없는
     DELETE라 request()의 JSON 파싱 경로를 그대로 탄다 — 서버가 목록을 돌려준다. */
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};
