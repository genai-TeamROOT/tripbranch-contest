/*
 * 역할: 현재 신원(게스트/정식 계정)을 사람이 읽을 라벨로 바꾼다.
 * 입력: Supabase session.
 * 출력: "게스트로 이용 중" 또는 계정 식별자(이메일 등).
 * 호출 시점: AuthStatusBadge(헤더/개발자 화면 배지)와 SideDrawerContent(사이드바
 *   라벨)가 같은 판정 로직을 공유한다 — 계정 표시 후보 순서가 어긋나면 두 곳의
 *   표시가 갈린다.
 */

import type { Session } from "@supabase/supabase-js";

/* is_anonymous는 Supabase가 익명 사용자에게 붙이는 표식이다. 계정을 연결하면
   같은 uid를 유지한 채 false가 되므로(D-062 2절), 이 분기만으로 승격 후 표시가
   자동으로 바뀐다. */
export function isGuestSession(session: Session): boolean {
  return session.user?.is_anonymous === true;
}

/* 계정 표시에 쓸 값. provider마다 채워주는 필드가 달라서 후보를 순서대로 본다 —
   이메일 로그인은 email, 카카오·구글은 user_metadata의 이름 계열만 오는 경우가 있고
   전화번호 로그인은 phone만 온다. 전부 비면 신원이 있다는 사실만 알린다. */
function accountLabel(session: Session, isEn: boolean): string {
  const metadata = (session.user?.user_metadata ?? {}) as Record<string, unknown>;
  const candidates = [
    session.user?.email,
    metadata.name,
    metadata.nickname,
    metadata.preferred_username,
    session.user?.phone,
  ];
  const label = candidates.find((value) => typeof value === "string" && value.trim().length > 0);
  return (label as string | undefined) ?? (isEn ? "Signed in" : "로그인됨");
}

export function identityLabel(session: Session, language: "ko" | "en" = "ko"): string {
  const isEn = language === "en";
  return isGuestSession(session) ? (isEn ? "Using as guest" : "게스트로 이용 중") : accountLabel(session, isEn);
}

/*
 * 사이드바 계정 버튼이 쓰는 표시 값. 한 줄 라벨(`identityLabel`)로는 부족해서
 * 따로 뒀다 — 사이드바는 이름을 굵게, 그 아래 어느 계정인지를 작게 두 줄로 낸다.
 *
 * **`identityLabel`의 후보 순서를 뒤집지 않고 함수를 하나 더 만든 이유**는 그쪽을
 * `AuthStatusBadge`(개발자 화면)가 쓰기 때문이다. 배지는 한 줄에 다 담아야 해서
 * 가장 정확한 식별자(이메일)가 먼저인 게 맞고, 사이드바는 사람 이름이 먼저인 게
 * 맞다 — 같은 함수로 두 요구를 맞출 수 없다.
 */
export interface IdentityDisplay {
  /** 굵게 낼 이름. */
  name: string;
  /** 이름 아래 작게 붙는 줄 — 어느 계정으로 들어와 있는지 확인하는 용도다. */
  subtitle: string;
  /** 아바타 이미지가 없어서(프로필 사진을 받는 경로가 없다) 원 안에 넣을 한 글자. */
  initial: string;
}

/* 이름 후보에는 이메일 앞부분까지 넣는다. 가입 화면이 이름을 받지만
   (SignupPage "AI가 추천할 때 이 이름으로 불러드려요") 그전에 만든 계정과
   승계로 붙은 계정은 user_metadata가 비어 있다 — 그때 "로그인됨"을 이름 자리에
   두면 누구인지 알 수 없다. */
function accountName(session: Session): string | undefined {
  const metadata = (session.user?.user_metadata ?? {}) as Record<string, unknown>;
  const candidates = [
    metadata.name,
    metadata.nickname,
    metadata.preferred_username,
    session.user?.email?.split("@")[0],
    session.user?.phone,
  ];
  const found = candidates.find((value) => typeof value === "string" && value.trim().length > 0);
  return found as string | undefined;
}

export function identityDisplay(
  session: Session,
  language: "ko" | "en" = "ko",
): IdentityDisplay {
  const isEn = language === "en";
  const name = isGuestSession(session)
    ? isEn
      ? "Guest"
      : "게스트"
    : (accountName(session) ?? (isEn ? "Signed in" : "로그인됨"));
  /* 게스트의 부제는 "게스트로 이용 중" 그대로 둔다 — 계정이 없다는 사실 자체가
     알려야 할 상태다(계정 만들기 입구가 그것 때문에 있다). */
  const subtitle = isGuestSession(session)
    ? isEn
      ? "Using as guest"
      : "게스트로 이용 중"
    : (session.user?.email ?? session.user?.phone ?? (isEn ? "Signed in" : "로그인됨"));
  /* Array.from — "😀"처럼 서로게이트 쌍인 글자를 [0]으로 자르면 깨진 반쪽이 남는다. */
  return { name, subtitle, initial: (Array.from(name)[0] ?? "?").toUpperCase() };
}
