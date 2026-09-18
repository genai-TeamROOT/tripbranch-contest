/*
 * 역할: 현재 신원을 표시하고, 눌렀을 때 세션 해제까지 할 수 있는 계정 메뉴를 연다.
 * 입력: AuthContext의 session/signOut, 배지 클릭.
 * 출력: 신원 배지와 드롭다운. 신원이 없으면 아무것도 그리지 않는다.
 * 호출 시점: HomePage/ChatPage/DeveloperChatPage 헤더에서 렌더링된다.
 * TODO: 정식 로그인(D-062 Phase 5)이 들어오면 이 메뉴에 계정 연결 항목을 추가하고,
 *       "세션 해제" 라벨을 "로그아웃"으로 바꾼다 — 그때는 다시 들어올 수단이 생긴다.
 */

import { useEffect, useRef, useState } from "react";
import { clearLocalUserData } from "../state/localUserData";
import { useTripDispatch } from "../state/TripContext";
import { useAuth } from "./AuthContext";
import { identityLabel, isGuestSession } from "./identityLabel";

export function AuthStatusBadge() {
  const { session, status, signOut } = useAuth();
  const dispatch = useTripDispatch();

  const [isOpen, setIsOpen] = useState(false);
  const [isConfirming, setIsConfirming] = useState(false);
  const [isWorking, setIsWorking] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  /* 바깥 클릭과 Esc로 닫는다. 확인 단계까지 갔다면 함께 되돌려서, 다음에 열었을 때
     곧바로 해제 버튼이 눌리는 상태로 남지 않게 한다. */
  useEffect(() => {
    if (!isOpen) return;

    function close() {
      setIsOpen(false);
      setIsConfirming(false);
      setErrorMessage(null);
    }
    function handlePointerDown(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) close();
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") close();
    }

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [isOpen]);

  if (status !== "ready" || !session) return null;

  const isGuest = isGuestSession(session);
  const label = identityLabel(session);

  async function handleSignOut() {
    if (isWorking) return;
    setIsWorking(true);
    setErrorMessage(null);
    try {
      await signOut();
      /* 신원만 끊고 이 기기의 데이터를 두면 다음 신원의 화면에 앞사람의 대화·취향·
         즐겨찾기·검색 위치가 그대로 남는다. 함께 비운다(state/localUserData.ts). */
      clearLocalUserData();
      dispatch({ type: "RESET" });
      /* 이동은 따로 시키지 않는다 — 세션이 사라지면 RequireUser가 관문으로 보낸다. */
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "세션을 해제하지 못했어요.");
      setIsWorking(false);
      setIsConfirming(false);
    }
  }

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={isOpen}
        onClick={() => setIsOpen((open) => !open)}
        className="shrink-0 rounded-full border border-gray-300 px-3 py-1.5 text-xs text-gray-700 dark:border-gray-700 dark:text-gray-300"
      >
        {label}
      </button>

      {isOpen ? (
        <div
          role="menu"
          className="absolute right-0 z-10 mt-1 w-64 rounded-md border border-gray-200 bg-white p-3 text-xs text-gray-700 shadow-md dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300"
        >
          {isGuest ? (
            <p className="mb-2">
              가입 없이 이용 중이에요. 나중에 계정을 연결하면 지금까지의 기록을 그대로 이어서 쓸 수
              있어요.
            </p>
          ) : (
            <p className="mb-2">{label} 계정으로 이용 중이에요.</p>
          )}

          {errorMessage ? (
            <p role="alert" className="mb-2 text-red-700 dark:text-red-400">
              {errorMessage}
            </p>
          ) : null}

          {isConfirming ? (
            <div className="flex flex-col gap-2">
              <p className="text-red-700 dark:text-red-400">
                해제하면 지금 기록으로 돌아올 수 없어요. 계속할까요?
              </p>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={isWorking}
                  onClick={() => void handleSignOut()}
                  className="rounded-md border border-red-300 px-3 py-1 font-medium text-red-700 disabled:opacity-50"
                >
                  {isWorking ? "해제하는 중이에요…" : "해제"}
                </button>
                <button
                  type="button"
                  disabled={isWorking}
                  onClick={() => setIsConfirming(false)}
                  className="rounded-md border border-gray-300 px-3 py-1 disabled:opacity-50 dark:border-gray-700"
                >
                  취소
                </button>
              </div>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setIsConfirming(true)}
              className="rounded-md border border-gray-300 px-3 py-1 font-medium dark:border-gray-700"
            >
              세션 해제
            </button>
          )}
        </div>
      ) : null}
    </div>
  );
}
