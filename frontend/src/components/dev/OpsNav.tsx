/*
 * 역할: 운영 패널의 좌측 메뉴. 관찰과 갱신 두 갈래를 고른다.
 * 입력: 지금 탭, 갱신이 돌고 있는지.
 * 출력: 메뉴 버튼 두 개와 진행 표시 점.
 * 호출 시점: DeveloperOpsPage가 렌더링될 때.
 *
 * 패널이 일곱 개가 되면서 한 화면에 다 두면 무엇을 보러 왔는지 잃는다. 가르는
 * 기준은 "본다"와 "바꾼다"다 — 호출량·DB 상태·통계는 읽기만 하고, 동기화·보관은
 * 운영 DB와 파일을 바꾼다. 실수의 무게가 다른 것을 같은 자리에 두지 않는다.
 *
 * 갱신이 도는 동안 다른 탭에 있어도 알 수 있게 점을 찍는다. 전 구 순회는 25개 구를
 * 하나씩 도느라 오래 걸려서, 탭을 옮겨두고 잊기 쉽다.
 */

export type OpsTab = "observe" | "sync" | "categories";

const TABS: { id: OpsTab; label: string; hint: string }[] = [
  { id: "observe", label: "데이터 관찰", hint: "호출량 · DB 상태 · 통계" },
  { id: "sync", label: "데이터 갱신", hint: "동기화 · 스냅샷 보관" },
  { id: "categories", label: "구별 카테고리 현황", hint: "대·중·소분류 · 예시 장소" },
];

export function OpsNav({
  tab,
  syncRunning,
  onSelect,
}: {
  tab: OpsTab;
  /** 동기화가 돌고 있는지. 다른 탭에 있어도 보이게 점을 찍는다. */
  syncRunning: boolean;
  onSelect: (tab: OpsTab) => void;
}) {
  return (
    <nav aria-label="운영 패널 메뉴" className="flex shrink-0 gap-2 sm:w-44 sm:flex-col">
      {TABS.map((entry) => {
        const active = entry.id === tab;
        return (
          <button
            key={entry.id}
            type="button"
            aria-current={active ? "page" : undefined}
            onClick={() => onSelect(entry.id)}
            className={`flex-1 rounded-md border px-3 py-2 text-left text-sm sm:flex-none ${
              active
                ? "border-gray-900 bg-gray-900 text-white dark:border-gray-100 dark:bg-gray-100 dark:text-gray-900"
                : "border-gray-200 bg-white text-gray-700 dark:border-gray-800 dark:bg-gray-900 dark:text-gray-300"
            }`}
          >
            <span className="flex items-center gap-1.5 font-medium">
              {entry.label}
              {entry.id === "sync" && syncRunning && (
                <span
                  aria-label="갱신 진행 중"
                  className="inline-block h-1.5 w-1.5 rounded-full bg-blue-500"
                />
              )}
            </span>
            <span
              className={`mt-0.5 block text-[11px] ${
                active ? "text-gray-300 dark:text-gray-600" : "text-gray-500 dark:text-gray-400"
              }`}
            >
              {entry.hint}
            </span>
          </button>
        );
      })}
    </nav>
  );
}
