/*
 * 역할: 인구 혼잡도 조회에서 "서울시 API는 지원하는데 우리 121곳 목록엔 없는
 * 지역"을 발견했을 때만 뜨는 개발자 전용 안내 배너(TP-141/D-084).
 * 입력: 마지막 DeveloperAuditTurn.
 * 출력: 조건에 맞을 때만 안내 문구 한 줄. 조건이 안 맞으면 아무것도 그리지 않는다.
 * 호출 시점: DeveloperChatPage가 TurnLocationBadges 바로 위에서 렌더링한다.
 *
 * ErrorBanner를 재사용하지 않는 이유: 이건 오류가 아니다. 응답은 이미 정상적으로
 * 대체 지역 값으로 나갔고, 이 배너는 "그 대체가 왜 필요했는지"를 알려주는 참고
 * 정보다. 빨간 alert 톤과 "다시 시도" 버튼은 이 맥락에 안 맞아 별도로 둔다.
 */

import type { DeveloperAuditTurn } from "../../types";

function findStaleAreaDetection(turn: DeveloperAuditTurn) {
  const executions = turn.response?.tool_executions ?? [];
  for (const execution of executions) {
    if (execution.operation === "info_realtime_population" && execution.stale_area_detected) {
      return execution.stale_area_detected;
    }
  }
  return null;
}

export function StaleAreaBanner({ turn }: { turn: DeveloperAuditTurn }) {
  const detected = findStaleAreaDetection(turn);
  if (!detected) return null;

  return (
    <div
      role="status"
      className="mb-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200"
    >
      서울시가 지원하는 &lsquo;{detected.probed_area_name}&rsquo;이 지역 목록에 없어요. 지금은{" "}
      {detected.matched_area_name}({detected.matched_area_distance_km.toFixed(2)}km) 값으로
      답했어요.
    </div>
  );
}
