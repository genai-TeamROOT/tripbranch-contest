/*
 * 역할: 화면 상단의 프로스티드 헤더 — 모바일 전용 햄버거(드로어 열기)와, 라벨이
 *   있을 때만 위치 pill을 그린다. 뒤로가기 화살표는 그리지 않는다(2026-09-07).
 * 입력: 표시할 위치 라벨, 띠를 남길지 여부.
 * 호출 시점: 신원이 필요한 화면들이 상단에 렌더링할 때.
 * 근거: DESIGN_SYSTEM.md §6.1.
 */

import { useRef } from "react";
import { ArrowRight, MapPinned, Menu, Navigation } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { cn } from "../../utils/cn";
import type { LocationChipModel } from "../../utils/locationChip";
import { useElementHeightVar } from "../../hooks/useElementHeightVar";
import { useAppShell } from "./AppShellContext";

interface AppHeaderProps {
  /*
   * 위치 칩에 그릴 모양(utils/locationChip). 문자열 하나가 아니라 모델을 받는
   * 이유는, 출발지와 검색 기준이 다를 때 둘 다 보여야 하기 때문이다 — 하나만
   * 고르면 카드의 이동시간을 어디서 쟀는지가 화면에서 사라진다(D-067).
   */
  location?: LocationChipModel | null;
  /**
   * 위치 pill이 없어도 헤더 띠를 남긴다. 취향·위치·일정처럼 제목이 바로
   * 시작하는 하위 화면이 쓴다 — 띠가 접히면 제목이 화면 맨 위에 붙는다.
   */
  keepStrip?: boolean;
}

const FROSTED_BUTTON_CLASS =
  "flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-white bg-white/60 text-ink shadow-resting backdrop-blur-md transition-colors hover:bg-white/80";

export function AppHeader({ location: locationChip = null, keepStrip = false }: AppHeaderProps) {
  const { drawerOpen, openDrawer, closeDrawer } = useAppShell();
  const navigate = useNavigate();

  /*
   * 홈·채팅은 이 헤더를 스크롤 영역 **위에 겹쳐** 둔다(그래야 내용이 헤더 뒤로
   * 지나간다). 겹치는 만큼 그쪽이 위를 비워야 하므로 높이를 재서 알려준다 —
   * 위치 칩 유무나 접힘으로 높이가 달라져서 숫자로 박아 둘 수 없다.
   * 헤더가 흐름 안에 있는 화면(위치·일정)에서는 이 값을 아무도 안 읽는다.
   */
  const headerRef = useRef<HTMLDivElement>(null);
  useElementHeightVar(headerRef, "--tb-header-h");

  /*
   * **뒤로가기 화살표는 어디서도 그리지 않는다**(2026-09-07). 데스크톱은 전부터
   * 안 그렸고(사이드바가 이미 돌아갈 길이라 화살표는 같은 일을 두 번 함),
   * 모바일도 같은 이유로 뺐다 — 돌아가는 길은 브라우저/제스처 뒤로가기와
   * 햄버거 드로어(새 채팅 등)다.
   */

  return (
    <div
      ref={headerRef}
      className={cn(
        "sticky top-0 z-20 bg-gradient-to-b from-black/5 to-transparent",
        // 위치 pill도 없고 띠를 붙잡는 화면도 아닐 때(홈·채팅에서 위치를 아직 못
        // 정한 경우)만 접는다 — 거기서는 데스크톱에 그릴 것이 정말 없다. 모바일은
        // 햄버거가 항상 있어야 하므로 어느 쪽이든 그대로 둔다.
        !locationChip && !keepStrip && "md:hidden",
      )}
    >
      <div className="relative flex items-center justify-between px-4 pb-3 pt-6">
        <div className="flex items-center gap-2">
          {/*
           * 여닫이다 — 열려 있을 때 다시 누르면 닫힌다(2026-09-07). 예전에는
           * openDrawer 만 불러서, 열어 둔 채로 누르면 아무 일도 안 났다.
           *
           * 셸이 이미 "밀려난 본문 아무 곳이나 누르면 닫기"를 갖고 있는데
           * (AppShell 의 onClickCapture), 그 캡처가 이 버튼보다 **먼저** 돌기
           * 때문에 둘이 서로를 무효화했다. data-drawer-toggle 표식을 보고 셸이
           * 이 버튼만 건너뛴다 — 여닫이는 이 버튼 하나가 온전히 갖는다.
           */}
          <button
            type="button"
            /* 셸의 탭-투-클로즈가 이 버튼은 건너뛰게 하는 표식이다(AppShell). */
            data-drawer-toggle=""
            onClick={() => (drawerOpen ? closeDrawer() : openDrawer())}
            aria-label={drawerOpen ? "메뉴 닫기" : "메뉴 열기"}
            aria-expanded={drawerOpen}
            className={cn(FROSTED_BUTTON_CLASS, "md:hidden")}
          >
            <Menu size={18} />
          </button>

          {locationChip && (
            <button
              type="button"
              onClick={() => navigate("/location")}
              aria-label={`위치 설정으로 이동 (${locationChip.description})`}
              /* min-w-0을 두어야 안쪽 이름이 줄어들 수 있다. 없으면 칩이 제 내용
                 폭을 고집해 좁은 화면에서 헤더 밖으로 밀려난다. */
              className="flex min-w-0 items-center gap-1.5 rounded-full border border-white bg-white/60 px-3 py-1.5 text-sm font-medium text-ink shadow-resting backdrop-blur-md transition-colors hover:bg-white/80"
            >
              {locationChip.kind === "pair" && (
                <>
                  <LocationChipIcon role="origin" />
                  <span className="truncate">{locationChip.origin}</span>
                  <ArrowRight size={13} className="shrink-0 text-muted" aria-hidden />
                </>
              )}
              <LocationChipIcon
                role={locationChip.kind === "single" ? "single" : "center"}
                isUnset={locationChip.kind === "single" && locationChip.isUnset}
              />
              <span className="truncate">
                {locationChip.kind === "single" ? locationChip.name : locationChip.center}
              </span>
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/*
 * 칩 안의 아이콘. 위치 설정 화면과 같은 것을 쓴다 — 출발지는 Navigation, 검색
 * 기준은 MapPinned(바닥 원이 깔린 핀, "그 지점"이 아니라 "그 자리 주변"이라는 뜻).
 * 두 화면이 같은 모양을 써야 한쪽에서 배운 뜻이 다른 쪽에서도 통한다.
 *
 * 예전에는 기기 GPS를 쓰는 자리에 깜빡이는 초록 점(쓰는 중)과 회색 점(아직 못 받음)을
 * 붙였다. 이 버전은 GPS를 받지 않아 그 두 상태가 없다. 아무것도 정하지 않았으면
 * ("위치 미설정") 같은 핀을 흐리게 그려, 누르면 정하러 간다는 것만 남긴다.
 */
function LocationChipIcon({
  role,
  isUnset = false,
}: {
  role: "origin" | "center" | "single";
  isUnset?: boolean;
}) {
  const Icon = role === "origin" ? Navigation : MapPinned;
  return (
    <Icon size={13} className={cn("shrink-0", isUnset ? "text-muted" : "text-brand")} aria-hidden />
  );
}
