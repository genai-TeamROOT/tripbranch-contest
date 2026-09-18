/*
 * 역할: 앱이 뜰 때 화면을 잠깐 덮는 브랜드 화면. 신원 확인이 끝나면 걷힌다.
 * 입력: AuthContext의 status(신원 확인이 끝났는지).
 * 출력: 화면 전체를 덮는 오버레이. 다 걷히면 DOM에서 빠진다.
 * 호출 시점: App이 AuthProvider 안에서 라우터와 나란히 한 번 렌더한다.
 * 근거: package_D/DESIGN_SYSTEM.md §4(레이아웃 셸) — 셸 위에 얹히는 유일한 층.
 *
 * **로딩 표시가 아니라 오버레이다.** 뒤에서 메인은 이미 그려지고 있고 이건 그
 * 위를 덮을 뿐이다. 스플래시가 메인을 "대신 그리는" 구조로 만들면 걷히는 순간에
 * 메인이 처음부터 마운트되면서 첫 요청이 그때 나가고, 화면이 한 박자 늦게 붙는다.
 *
 * 그래서 **최소 노출 시간**이 필요하다. 준비되는 대로 걷으면 이미 세션이 있는
 * 재방문자에게는 한두 프레임만 번쩍이고 사라져 "뭔가 잘못 그려졌다"로 읽힌다.
 * 반대로 시간만 재고 준비 여부를 안 보면, 신원 확인이 느린 날에는 걷힌 자리에
 * 관문(RequireUser)의 로딩 문구가 드러난다. 둘 다 만족해야 걷는다.
 */

import { useEffect, useState } from "react";
import { useAuth } from "../auth/AuthContext";

/* 준비가 끝나도 이만큼은 보여준다. */
const MIN_VISIBLE_MS = 900;
/* 걷히는 데 걸리는 시간. 이만큼 지난 뒤에 DOM에서 뺀다 — 애니메이션 도중에
   지우면 반쯤 투명한 상태에서 툭 사라진다. CSS의 tb-splash-leave와 같은 값이다. */
const FADE_MS = 420;

export function SplashScreen() {
  const { status } = useAuth();
  const [minElapsed, setMinElapsed] = useState(false);
  const [removed, setRemoved] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(() => setMinElapsed(true), MIN_VISIBLE_MS);
    return () => window.clearTimeout(timer);
  }, []);

  /* unconfigured도 "확인이 끝난" 상태다 — 설정이 없다는 안내를 스플래시가 덮고
     있으면 사용자는 앱이 영영 안 열린다고 본다. */
  const leaving = minElapsed && status !== "loading";

  useEffect(() => {
    if (!leaving) return;
    const timer = window.setTimeout(() => setRemoved(true), FADE_MS);
    return () => window.clearTimeout(timer);
  }, [leaving]);

  if (removed) return null;

  return (
    /* aria-hidden이다. 뒤의 메인이 이미 접근 가능한 상태로 붙어 있어서, 여기까지
       읽히면 화면에 없는 것과 있는 것이 섞여 들린다 — 이건 눈으로만 보는 층이다. */
    <div aria-hidden="true" className={`tb-splash ${leaving ? "tb-splash--leaving" : ""}`}>
      <p className="tb-splash__mark">TripBranch</p>
    </div>
  );
}
