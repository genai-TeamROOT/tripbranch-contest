/*
 * 역할: 사이드바 맨 아래 계정 자리. 로그인 안 했으면 로그인 입구, 했으면 아바타+
 *   이름 버튼과 그 팝업(로그아웃).
 * 입력: 없다(신원은 AuthContext에서 직접 읽는다).
 * 출력: /login으로의 이동, 로그아웃(신원 해제 + 이 기기 데이터 정리).
 * 호출 시점: 펼친 사이드바·모바일 드로어(`SideDrawerContent` §6)와 **접힌 레일**
 *   (`DesktopSidebar`)이 각각 렌더한다.
 * 근거: package_D/DESIGN_SYSTEM.md §6.17.
 *
 * **파일을 따로 뺀 이유가 접힌 레일이다**(2026-09-06). 레일은 SideDrawerContent를
 * 아예 렌더하지 않아서 계정에 닿을 길이 없었는데, 레일용으로 한 벌 더 만들면
 * 로그아웃이 두 곳에 생긴다 — 한쪽만 고쳐지면 접었을 때와 폈을 때가 갈린다.
 * 사이드바 안에서 "두 번 만들지 않는다"는 것은 6.17이 드로어와 데스크톱 패널에
 * 이미 적용해 둔 규칙이고, 여기도 같은 이유다.
 *
 * 표시가 갈리는 곳:
 * - **로그인 안 한 상태(게스트)**: 로그인 입구 하나다. 진입이 게스트로 자동으로
 *   열리게 바뀌면서(`RequireUser`) 여기가 로그인으로 가는 유일한 입구가 됐다.
 *   신원 표시를 그리지 않는 이유는, 게스트에게 보여줄 것이 "게스트 / 게스트로
 *   이용 중"뿐이라 이름 자리를 차지하고도 아무것도 알려주지 못하기 때문이다 —
 *   그 자리에는 할 수 있는 동작이 오는 게 낫다.
 * - **계정**: 아바타+이름 버튼 하나를 두고 나머지는 눌렀을 때 팝업으로 낸다
 *   (2026-09-04). 예전에는 신원 라벨 한 줄 + "계정 만들기" + "로그아웃"이 모두
 *   바닥에 펼쳐져 있었다 — 라벨이 `identityLabel`이라 **이메일이 상시 노출**됐고
 *   (이름이 있어도 이메일이 먼저 걸린다), **되돌릴 수 없는 로그아웃이 상시 눌리는
 *   자리**에 있었다.
 *
 * 게스트용 "계정 만들기" 줄은 팝업에서 뺐다(2026-09-06). 게스트는 이제 이 팝업
 * 자체를 보지 않고, 가입은 로그인 화면의 "회원가입" 링크로 닿는다 — 그 화면이
 * 게스트 세션을 그대로 승격시킨다(AuthContext.signUpWithEmail).
 *
 * 팝업에 "프로필"·"설정"·"도움말" 줄은 만들지 않는다. 그 화면이 없다 — 라우트는
 * /, /chat, /preferences, /location, /schedule 뿐이다. 없는 화면 이름을 메뉴에
 * 만들면 눌러도 아무 일이 일어나지 않는다.
 *
 * **닉네임 변경만은 예외다**(2026-09-07). 화면으로 가지 않고 팝업 안에서 신원
 * 헤더 자리를 입력칸으로 바꿔치기해 끝낸다 — 갈 화면이 없어도 되는 이유는 이동이
 * 필요 없는 한 줄짜리 동작이기 때문이다(SavedScheduleList의 이름 바꾸기와 같은
 * 모양). 게스트는 이 팝업 자체가 없어 대상이 아니다 — 게스트의 이름("게스트")은
 * `identityDisplay`가 메타데이터를 보지 않고 고정으로 낸다.
 */

import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { LogIn, LogOut, Pencil, UserX } from "lucide-react";
import { useAuth } from "../../auth/AuthContext";
import { identityDisplay, isGuestSession, type IdentityDisplay } from "../../auth/identityLabel";
import { deleteAccount } from "../../api/trip";
import { clearLocalUserData } from "../../state/localUserData";
import { useTripDispatch, useTripState } from "../../state/TripContext";

interface SidebarAccountProps {
  /** 모바일 드로어에서만 넘긴다 — 누르면 드로어를 닫기 위해서다. */
  onNavigate?: () => void;
  /**
   * 접힌 레일(72px)용. 아바타·아이콘 하나만 그리고, 팝업은 레일 밖 오른쪽으로
   * 편다 — 레일 폭에 맞추면 이메일이 한 글자씩 끊긴다.
   */
  compact?: boolean;
}

/*
 * 아바타 + 이름 + 부제. **계정 버튼과 팝업 헤더에 같은 모양이 두 번 들어간다** —
 * 따로 적어 두면 한쪽만 고쳐져 팝업을 열 때 이름이 달라 보인다.
 *
 * 아바타는 이름 첫 글자다. 프로필 사진을 받는 경로가 아예 없다(가입은 이름·이메일·
 * 비밀번호만 받고, 소셜 로그인도 없다) — 빈 회색 원을 두면 아직 안 불러온 것처럼
 * 보인다.
 *
 * 겉이 <button>인 자리에도 들어가므로 <div>가 아니라 <span>으로 짠다.
 */
export function IdentityRow({ identity }: { identity: IdentityDisplay }) {
  return (
    <>
      <IdentityAvatar identity={identity} />
      <span className="flex min-w-0 flex-col">
        <span className="truncate text-sm font-semibold text-ink">{identity.name}</span>
        <span className="truncate text-[11px] text-muted">{identity.subtitle}</span>
      </span>
    </>
  );
}

/* 일정 목록 카드(SchedulePage·SavedScheduleList)도 같은 아바타를 쓴다(2026-09-07) —
   장소 사진이 없는 카드에 고정 아이콘(RouteIcon) 대신 "누구의 일정인지"를
   보여주는 편이 낫다는 판단이다. 그쪽은 사이드바보다 살짝 큰 사이즈가 필요해
   `size`를 받는다 — 기본값(sm)은 사이드바 모양을 그대로 지킨다.
   **md는 처음에 h-11(44px)이었다가 카드 텍스트에 비해 크다는 지적으로 한 단계
   줄였다**(2026-09-07). */
const AVATAR_SIZE_CLASS = {
  sm: "h-8 w-8 text-xs",
  md: "h-9 w-9 text-xs",
} as const;

export function IdentityAvatar({
  identity,
  size = "sm",
}: {
  identity: IdentityDisplay;
  size?: keyof typeof AVATAR_SIZE_CLASS;
}) {
  return (
    <span
      aria-hidden
      className={`flex shrink-0 items-center justify-center rounded-full bg-brand font-bold text-white ${AVATAR_SIZE_CLASS[size]}`}
    >
      {identity.initial}
    </span>
  );
}

const MENU_ITEM_CLASS =
  "flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-sm font-medium transition-colors";

/* 레일에서는 다른 아이콘들과 같은 40px 원이다(DesktopSidebar의 RAIL_ICON_CLASS). */
const RAIL_BUTTON_CLASS =
  "flex h-10 w-10 items-center justify-center rounded-full transition-colors hover:bg-chip";

export function SidebarAccount({ onNavigate, compact = false }: SidebarAccountProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const dispatch = useTripDispatch();
  const isEn = useTripState().language === "en";
  const { session, status, signOut, updateNickname } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  /* 닉네임 입력 상태. 팝업이 닫히면(바깥 클릭 등) 함께 접는다 — 다음에 열었을 때
     지난 초안이 남아 있으면 안 된다. */
  const [renaming, setRenaming] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const [nicknameError, setNicknameError] = useState<string | null>(null);
  /* 탈퇴는 되돌릴 수 없다. 메뉴에서 바로 실행하지 않고 확인 단계를 한 번 거친다 —
     로그아웃과 같은 자리에 있어서 잘못 누르기 쉽다. */
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const nameInputRef = useRef<HTMLInputElement>(null);

  /* SavedScheduleList의 이름 바꾸기와 같은 동작이다 — 입력칸이 뜨면 바로 입력할
     수 있어야 한다. */
  useEffect(() => {
    if (renaming) nameInputRef.current?.focus();
  }, [renaming]);

  if (status !== "ready" || !session) return null;

  const isGuest = isGuestSession(session);
  const identity = identityDisplay(session, isEn ? "en" : "ko");
  const signInLabel = isEn ? "Sign in" : "로그인";
  const railName = `${identity.name} ${identity.subtitle}`;

  function closeMenu() {
    setMenuOpen(false);
    setRenaming(false);
    setNicknameError(null);
    setConfirmingDelete(false);
    setDeleteError(null);
  }

  function startRenaming() {
    setNameDraft(identity.name);
    setNicknameError(null);
    setRenaming(true);
  }

  async function commitNickname() {
    const trimmed = nameDraft.trim();
    if (!trimmed) {
      setNicknameError(isEn ? "Enter a nickname." : "닉네임을 입력해 주세요.");
      return;
    }
    if (trimmed === identity.name) {
      setRenaming(false);
      return;
    }
    try {
      await updateNickname(trimmed);
      closeMenu();
    } catch (nicknameUpdateError) {
      setNicknameError(
        nicknameUpdateError instanceof Error
          ? nicknameUpdateError.message
          : isEn
            ? "Couldn't update nickname."
            : "닉네임을 바꾸지 못했어요.",
      );
    }
  }

  /*
   * **서버에서 지운 뒤 반드시 signOut까지 한다.** 토큰 검증은 서명과 만료만 보고
   * 계정이 아직 있는지는 묻지 않아서(backend/app/auth/verify.py), 계정을 지워도
   * 이 브라우저의 토큰은 만료까지 그대로 통한다. 그 사이에 요청이 한 번이라도
   * 나가면 방금 지운 user_id로 행이 다시 생긴다.
   *
   * 실패하면 아무것도 정리하지 않는다. 서버가 데이터를 먼저 지우고 계정을 마지막에
   * 지우므로, 여기서 실패했다면 계정은 살아 있고 다시 누르면 이어서 마칠 수 있다.
   */
  async function handleDeleteAccount() {
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteAccount();
    } catch (accountDeletionError) {
      setDeleteError(
        accountDeletionError instanceof Error && accountDeletionError.message
          ? accountDeletionError.message
          : isEn
            ? "Couldn't delete your account."
            : "탈퇴를 마치지 못했어요.",
      );
      setDeleting(false);
      return;
    }
    await signOut();
    clearLocalUserData();
    dispatch({ type: "RESET" });
    setDeleting(false);
    closeMenu();
    onNavigate?.();
  }

  async function handleSignOut() {
    try {
      await signOut();
      /* 신원만 끊고 이 기기의 데이터를 두면 다음 신원의 화면에 앞사람의 대화·취향·
         즐겨찾기·검색 위치가 그대로 남는다. 함께 비운다(state/localUserData.ts). */
      clearLocalUserData();
      dispatch({ type: "RESET" });
      /* 이동은 따로 시키지 않는다 — 세션이 사라지면 RequireUser가 게스트 신원을
         새로 발급해 같은 자리에서 앱이 계속 열려 있다. */
    } finally {
      closeMenu();
      onNavigate?.();
    }
  }

  if (isGuest) {
    return (
      <div className="mt-auto">
        {/* 목적지를 state로 실어 보낸다 — 로그인을 마쳤을 때 홈이 아니라 보던
            화면으로 돌아온다. LoginPage가 이 값을 `from`으로 읽는다. */}
        <button
          type="button"
          title={compact ? signInLabel : undefined}
          aria-label={compact ? signInLabel : undefined}
          onClick={() => {
            navigate("/login", { state: { from: location.pathname } });
            onNavigate?.();
          }}
          className={
            compact
              ? `${RAIL_BUTTON_CLASS} text-brand`
              : "flex w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-left text-sm font-semibold text-ink transition-colors hover:bg-chip"
          }
        >
          <LogIn size={compact ? 18 : 17} aria-hidden />
          {!compact && signInLabel}
        </button>
      </div>
    );
  }

  return (
    <div className="relative mt-auto">
      {menuOpen && (
        <>
          {/* 대화 줄 메뉴와 같은 방식이다 — 바깥을 누르면 닫힌다. */}
          <button
            type="button"
            aria-label={isEn ? "Close account menu" : "계정 메뉴 닫기"}
            onClick={closeMenu}
            className="fixed inset-0 z-20 cursor-default"
          />
          {/*
           * 계정 버튼이 사이드바 맨 아래라 위로 띄운다(bottom-full). 접힌 레일에서는
           * 폭을 레일에 맞출 수 없어(72px) 고정 폭으로 오른쪽에 편다 — .tb-sidebar에
           * overflow가 없어 밖으로 나갈 수 있다.
           */}
          <div
            className={`absolute bottom-full z-30 mb-2 flex flex-col rounded-2xl bg-white p-1.5 shadow-card ${
              compact ? "left-0 w-60" : "left-0 right-0"
            }`}
          >
            {renaming ? (
              /* 신원 헤더 자리를 입력칸으로 바꿔치기한다 — 새 팝업을 만들지 않고
                 같은 자리에서 끝낸다(SavedScheduleList 이름 바꾸기와 같은 방식). */
              <div className="flex flex-col gap-2 p-2">
                <label
                  htmlFor="sidebar-nickname-input"
                  className="text-xs font-semibold text-muted"
                >
                  {isEn ? "Nickname" : "닉네임"}
                </label>
                <input
                  ref={nameInputRef}
                  id="sidebar-nickname-input"
                  value={nameDraft}
                  onChange={(event) => setNameDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") void commitNickname();
                    if (event.key === "Escape") setRenaming(false);
                  }}
                  maxLength={30}
                  className="rounded-xl border border-border px-3 py-2 text-sm text-ink outline-none focus:border-brand"
                />
                {nicknameError && <p className="px-0.5 text-xs text-rust">{nicknameError}</p>}
                <div className="flex justify-end gap-1.5">
                  <button
                    type="button"
                    onClick={() => setRenaming(false)}
                    className="rounded-xl px-3 py-1.5 text-xs font-semibold text-muted hover:bg-chip"
                  >
                    {isEn ? "Cancel" : "취소"}
                  </button>
                  <button
                    type="button"
                    onClick={() => void commitNickname()}
                    className="rounded-xl bg-brand px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-deep"
                  >
                    {isEn ? "Save" : "저장"}
                  </button>
                </div>
              </div>
            ) : (
              <>
                {/* 어느 계정의 메뉴인지 팝업 안에서도 보인다 — 팝업이 계정 버튼을 덮는
                    자리에 뜨기 때문이다. 누를 수는 없다(계정 화면이 없다). */}
                <div className="flex items-center gap-2.5 px-2 py-2">
                  <IdentityRow identity={identity} />
                </div>
                <div className="mx-2 my-1 h-px bg-border" />
                {/* role="menu" 는 menuitem 만 감싼다 — 위의 신원 헤더는 menuitem 이 아니다. */}
                <div role="menu" aria-label={isEn ? "Account" : "계정"} className="flex flex-col">
                  <button
                    type="button"
                    role="menuitem"
                    onClick={startRenaming}
                    className={`${MENU_ITEM_CLASS} text-ink hover:bg-chip`}
                  >
                    <Pencil size={15} aria-hidden />
                    {isEn ? "Change nickname" : "닉네임 변경"}
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => void handleSignOut()}
                    className={`${MENU_ITEM_CLASS} text-rust hover:bg-chip`}
                  >
                    <LogOut size={15} aria-hidden />
                    {isEn ? "Sign out" : "로그아웃"}
                  </button>
                  {/* 탈퇴는 로그아웃보다 아래, 구분선 뒤에 둔다 — 두 줄이 붙어 있으면
                      로그아웃을 누르려다 탈퇴를 누른다. */}
                  <div className="mx-2 my-1 h-px bg-border" />
                  {confirmingDelete ? (
                    <div className="flex flex-col gap-2 px-3 py-2">
                      <p className="text-xs leading-relaxed text-muted">
                        {isEn
                          ? "Your account, chats, schedules, preferences and favorites are deleted. This can't be undone."
                          : "계정과 대화·일정·취향·즐겨찾기가 모두 지워져요. 되돌릴 수 없어요."}
                      </p>
                      {deleteError ? (
                        <p role="alert" className="text-xs leading-relaxed text-rust">
                          {deleteError}
                        </p>
                      ) : null}
                      <div className="flex gap-2">
                        <button
                          type="button"
                          onClick={() => setConfirmingDelete(false)}
                          disabled={deleting}
                          className="flex-1 rounded-full border border-border px-3 py-1.5 text-xs font-bold text-ink transition-colors hover:bg-chip disabled:opacity-50"
                        >
                          {isEn ? "Cancel" : "취소"}
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleDeleteAccount()}
                          disabled={deleting}
                          className="flex-1 rounded-full bg-rust px-3 py-1.5 text-xs font-bold text-white transition-colors disabled:opacity-50"
                        >
                          {deleting
                            ? isEn
                              ? "Deleting…"
                              : "탈퇴하는 중…"
                            : isEn
                              ? "Delete account"
                              : "탈퇴하기"}
                        </button>
                      </div>
                    </div>
                  ) : (
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => setConfirmingDelete(true)}
                      className={`${MENU_ITEM_CLASS} text-muted hover:bg-chip`}
                    >
                      <UserX size={15} aria-hidden />
                      {isEn ? "Delete account" : "회원 탈퇴"}
                    </button>
                  )}
                </div>
              </>
            )}
          </div>
        </>
      )}
      {/*
       * 이름을 읽어주는 것이 이 버튼의 이름이다 — aria-label로 "계정 메뉴"라고 덮으면
       * 어느 계정인지 소리로 확인할 방법이 없어진다. 무엇이 열리는지는 aria-haspopup이
       * 알린다.
       *
       * 레일에는 글자가 없어 이름이 저절로 붙지 않으므로 그때만 직접 준다. **펼친
       * 쪽과 같은 문구**여야 한다 — 펼친 버튼은 이름과 부제를 둘 다 품고 있어 그
       * 둘이 이어져 읽힌다. 같은 버튼이 접힘/펼침에 따라 다른 이름을 가지면 안 된다
       * (DesktopSidebar의 레일 라벨과 같은 근거).
       */}
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={menuOpen}
        title={compact ? railName : undefined}
        aria-label={compact ? railName : undefined}
        onClick={() => (menuOpen ? closeMenu() : setMenuOpen(true))}
        className={
          compact
            ? RAIL_BUTTON_CLASS
            : "flex w-full items-center gap-2.5 rounded-xl px-2 py-2 text-left transition-colors hover:bg-chip"
        }
      >
        {compact ? <IdentityAvatar identity={identity} /> : <IdentityRow identity={identity} />}
      </button>
    </div>
  );
}
