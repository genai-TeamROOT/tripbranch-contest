/*
 * 역할: 계정의 대화 목록을 한 번만 받아 사이드바 두 벌이 나눠 쓴다.
 * 입력: GET /api/sessions.
 * 출력: 사이드바가 그리는 ChatHistoryEntry 배열.
 * 호출 시점: SideDrawerContent가 마운트될 때, 이름 바꾸기·삭제가 실패했을 때.
 *
 * **캐시가 테스트 편의가 아니라 실제 요구다.** 데스크톱 사이드바와 모바일 드로어는
 * CSS로 하나만 보이게 하는 것이지 둘 다 마운트된다 — 캐시가 없으면 화면을 열
 * 때마다 같은 목록을 두 번 받아온다.
 *
 * 로컬 거울을 두지 않는다. 취향(preferenceSync)은 게스트가 가입할 때 넘겨줄 값이
 * 있어 localStorage를 남겼지만, 대화는 이미 서버에 있고 소유자도 서버가 안다 —
 * 목록만 로컬에 복사해두면 지운 대화가 되살아나는 쪽이 더 나쁘다.
 */

import { fetchChatSessions } from "../api/trip";
import type { ChatSessionSummary } from "../types";
import type { ChatHistoryEntry } from "./sidebarStorage";

/*
 * 서버 요약을 화면이 쓰는 모양으로 바꾼다.
 *
 * 날짜에서 연도를 뺀다 — 목록 한 줄에 제목·날짜·장소가 함께 들어가야 해서 자리가
 * 좁고, 대부분은 최근 대화다.
 */
function toHistoryEntry(session: ChatSessionSummary): ChatHistoryEntry {
  const at = new Date(session.last_active_at);
  return {
    id: session.session_id,
    label: session.title,
    date: Number.isNaN(at.getTime())
      ? ""
      : at.toLocaleDateString("ko-KR", { month: "long", day: "numeric" }),
    location: session.location,
  };
}

let cached: Promise<ChatHistoryEntry[]> | null = null;
/*
 * 담아 둔 목록이 **누구 것인지**. 이것 없이 목록만 들고 있으면 신원이 바뀌어도
 * 앞 신원의 목록이 그대로 나간다.
 *
 * 실제로 그랬다. 폰으로 처음 들어오면 게스트 신원이 발급되고(RequireUser) 그
 * 계정의 대화는 0건이라 빈 목록이 여기 담긴다. 그 뒤 로그인해도 담긴 것이 있으니
 * 그대로 돌려주어, 대화가 41건인 계정으로 들어왔는데도 사이드바가 비어 있었다.
 * 새로고침해야 보였다.
 *
 * 로그아웃 정리(localUserData의 clearLocalUserData)에 로그인 경로를 더하는 길도
 * 있었지만, 그러면 "계정에서 받아오는 목록을 새로 만들 때마다 비우기 목록에도
 * 넣기"를 사람이 기억해야 한다 — 그 당부가 그 파일 주석에 이미 적혀 있었는데도
 * 로그인 경로가 빠져 있었다. 캐시가 자기 주인을 알고 있으면 호출부는 신원만
 * 넘기면 된다.
 */
let cachedUserId: string | null = null;

async function load(): Promise<ChatHistoryEntry[]> {
  const response = await fetchChatSessions();
  return response.sessions.map(toHistoryEntry);
}

/**
 * 대화 목록. **같은 신원이면** 페이지 로드당 한 번만 실제로 요청한다.
 *
 * userId는 지금 로그인한 신원(`session?.user?.id`)이다. 담아 둔 것과 다르면
 * 버리고 서버에서 새로 받아온다 — 캐시에만 있는 값이 아니라 서버 응답을 잠깐
 * 재사용하는 것이므로, 처음 보는 신원이면 그냥 한 번 더 받아오면 된다.
 *
 * 실패는 던지지 않고 빈 목록으로 돌려준다 — 토큰이 없거나(401) 서버에 못 닿아도
 * 사이드바의 나머지 기능은 계속 써야 한다. 화면에는 "아직 대화 기록이 없어요"가
 * 뜬다.
 */
export function loadChatSessions(userId: string | null): Promise<ChatHistoryEntry[]> {
  if (!cached || cachedUserId !== userId) {
    cachedUserId = userId;
    cached = load().catch(() => []);
  }
  return cached;
}

let inflight: Promise<ChatHistoryEntry[]> | null = null;

/**
 * 서버에서 다시 받아온다. 이름 바꾸기·삭제가 실패해 화면과 서버가 갈렸을 때,
 * 그리고 새 대화가 생겨 목록에 넣어야 할 때 쓴다.
 *
 * **진행 중인 요청이 있으면 그것을 함께 쓴다.** 사이드바는 데스크톱과 모바일
 * 드로어 두 벌이 동시에 마운트돼 있어(CSS로 하나만 보일 뿐이다) 같은 계기에
 * 둘 다 이 함수를 부른다 — 막지 않으면 같은 목록을 두 번 받아온다.
 */
export function refreshChatSessions(): Promise<ChatHistoryEntry[]> {
  if (inflight) return inflight;
  const request = load()
    .catch(() => [] as ChatHistoryEntry[])
    .then((entries) => {
      inflight = null;
      return entries;
    });
  inflight = request;
  cached = request;
  return request;
}

/** 테스트가 페이지 로드 경계를 흉내 낼 수 있게 캐시를 비운다. */
export function resetChatSessionsCache(): void {
  cached = null;
  cachedUserId = null;
  inflight = null;
}
