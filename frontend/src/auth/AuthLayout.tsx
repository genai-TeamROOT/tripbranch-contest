/*
 * 역할: 인증 화면 4종(로그인·회원가입·아이디찾기·비밀번호찾기)이 공유하는 레이아웃.
 * 근거: DESIGN_SYSTEM.md §12.2 — 사이드바 없음, 모바일/데스크톱 모두 폭 제한된 단일 컬럼.
 *
 * Figma 인증 프레임 4개는 골격이 둘로 갈린다.
 *
 * - **브랜드형**(Login 27:6): 가운데 정렬 브랜드 제목 + 부제. 진입 화면이라 서비스
 *   이름을 먼저 보여준다.
 * - **헤더형**(Signup 27:52 · ResetPassword 27:109): 56px 뒤로가기
 *   헤더에 화면 이름. 로그인에서 갈라져 나온 하위 화면이라 돌아갈 곳이 분명하다.
 *
 * backTo를 주면 헤더형, 안 주면 브랜드형이다. 넷 다 footer(하단 고정 동작 영역)를
 * 갖는다 — Figma의 BottomBar.
 */

import type { ReactNode } from "react";
import { ChevronLeft } from "lucide-react";
import { Link } from "react-router-dom";

interface AuthLayoutProps {
  title: string;
  description?: string;
  children: ReactNode;
  /** 화면 하단에 붙일 동작 영역(주 버튼·구분선 등). Figma의 BottomBar. */
  footer: ReactNode;
  /** 주면 뒤로가기 헤더로 그린다(Figma의 BackHeader). 값은 돌아갈 경로. */
  backTo?: string;
}

export function AuthLayout({ title, description, children, footer, backTo }: AuthLayoutProps) {
  return (
    <main className="min-h-dvh bg-bg">
      <div className="mx-auto flex min-h-dvh w-full max-w-sm flex-col px-4">
        {backTo ? (
          <>
            {/* BackHeader — 56px. 아이콘 버튼 40px가 좌우 여백 안쪽에 붙는다. */}
            <header className="-mx-1 flex h-14 shrink-0 items-center gap-1.5">
              <Link
                to={backTo}
                aria-label="뒤로 가기"
                className="flex h-10 w-10 items-center justify-center rounded-full text-ink transition-colors hover:bg-chip"
              >
                <ChevronLeft size={22} aria-hidden />
              </Link>
              <h1 className="text-lg font-bold text-ink">{title}</h1>
            </header>
            <div className="flex flex-1 flex-col gap-4 pt-6">
              {description && <p className="text-sm text-muted">{description}</p>}
              {children}
            </div>
          </>
        ) : (
          /* Body — 위에서부터 쌓는다(Figma: Column y=64, Brand와 본문 사이 32). */
          <div className="flex flex-1 flex-col gap-8 pt-16">
            <div className="flex flex-col gap-2 text-center">
              <h1 className="text-2xl font-bold text-brand">{title}</h1>
              {description && <p className="text-sm text-muted">{description}</p>}
            </div>
            {children}
          </div>
        )}

        {/* BottomBar — 화면 하단 고정. 본문이 길어지면 자연스럽게 아래로 밀린다. */}
        <div className="flex flex-col gap-3 pb-6 pt-4">{footer}</div>
      </div>
    </main>
  );
}
