/*
 * 역할: 지금 진행 중인 채팅 스트리밍 요청 하나를 앱 전체에서 공유한다.
 * 입력: 없음(모듈 싱글턴).
 * 출력: 새 요청 시작 시 발급하는 AbortController, 중단 요청.
 * 호출 시점: HomePage 첫 발화·ChatPage 후속 발화가 streamChat을 부를 때, 그리고
 *   ChatComposer의 "중단" 버튼이 눌렸을 때(DESIGN_SYSTEM.md §7.2).
 *
 * HomePage는 발화를 보내자마자 /chat으로 이동한다(HomePage.tsx) — 실제 요청은
 * HomePage가 언마운트된 뒤에도 그 클로저 안에서 계속 진행된다. 컴포넌트 인스턴스
 * 마다 컨트롤러를 들고 있으면 ChatPage의 중단 버튼이 그 요청에 닿을 수 없다.
 * "지금 이 앱에 걸린 채팅 요청은 한 번에 하나뿐"이라는 실제 제약을 그대로
 * 모듈 싱글턴으로 표현한다.
 */

let currentController: AbortController | null = null;

/*
 * 사용자가 "중단"을 눌러 끊긴 요청. **중단에는 두 종류가 있고 뒷정리가 다르다.**
 *
 *   중단 버튼   지금 이 화면의 대화를 멈춘 것이다 → 오던 말풍선을 거기까지
 *               얼려서 남긴다(CANCEL_CHAT_TURN).
 *   화면을 떠남  다른 대화를 열었거나 새 대화를 시작한 것이다 → 화면에는 이미
 *               다른 대화가 그려져 있으므로 아무것도 건드리면 안 된다.
 *
 * 둘을 구분하지 않으면 지난 대화를 열었을 때 앞 대화의 뒷정리가 그 화면에서
 * 일어난다. WeakSet이라 컨트롤러가 버려지면 표시도 함께 사라진다.
 */
const userCancelled = new WeakSet<AbortController>();

/** 새 채팅 요청을 시작할 때 부른다. 남아 있던 이전 요청이 있으면 먼저 정리한다. */
export function beginChatRequest(): AbortController {
  currentController?.abort();
  const controller = new AbortController();
  currentController = controller;
  return controller;
}

/** 요청이 끝났을 때(성공·실패·취소 모두) 부른다. 다른 요청이 이미 시작됐으면 건드리지 않는다. */
export function endChatRequest(controller: AbortController): void {
  if (currentController === controller) currentController = null;
}

/** ChatComposer의 "중단" 버튼이 부른다. */
export function cancelChatRequest(): void {
  if (!currentController) return;
  userCancelled.add(currentController);
  currentController.abort();
}

/*
 * 화면에서 떼어낸 요청. **끊지는 않는다.**
 *
 * 답변을 기다리는 중에 다른 대화를 열면 예전에는 요청을 끊었다. 그러면 서버가
 * 실행을 취소해서(라우트가 연결 끊김을 보고 task.cancel) 턴이 저장되지 않고,
 * 제목도 안 붙어 그 대화가 히스토리에서 통째로 사라졌다 — 사용자는 방금 한
 * 질문이 없어진 것으로 본다.
 *
 * 그래서 화면에만 안 그리고 요청은 끝까지 둔다. 서버가 답변을 완성해 저장하므로
 * 나중에 그 대화를 열면 답변이 거기 있다. 대가는 아무도 안 읽을 수 있는 답변의
 * LLM 비용이고, 대화가 사라지는 것보다 낫다고 봤다.
 */
const detached = new WeakSet<AbortSignal>();

/**
 * 화면을 떠나며 진행 중인 요청을 화면에서 떼어낸다 — 지난 대화를 열거나 홈으로
 * 돌아갈 때다. 요청 자체는 계속 진행돼 서버가 답변을 저장한다.
 *
 * cancelChatRequest와 달리 표시를 남기지 않는다. 이 요청의 뒷정리는 이미 다른
 * 대화가 그려진 화면에서 일어나므로, 아무것도 하지 않는 것이 맞다.
 */
export function detachChatRequest(): void {
  if (!currentController) return;
  detached.add(currentController.signal);
  currentController = null;
}

/** 이 요청이 화면에서 떼어졌는지. 떼어졌으면 이벤트를 화면에 흘리지 않는다. */
export function isDetachedRequest(signal: AbortSignal | undefined): boolean {
  return signal !== undefined && detached.has(signal);
}

/** 이 요청이 "중단" 버튼으로 끊겼는지. 아니면 다른 것에 밀려난 것이다. */
export function wasCancelledByUser(controller: AbortController): boolean {
  return userCancelled.has(controller);
}
