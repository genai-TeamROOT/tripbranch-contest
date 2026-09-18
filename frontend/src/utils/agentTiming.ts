import type { AgentProgressEvent, AgentStageTiming } from "../types";

export interface StreamFirstTokenTiming {
  messageStartElapsedMs: number | null;
  firstMessageDeltaElapsedMs: number | null;
}

/**
 * SSE progress 이벤트의 서버 경과 시간을 단계별 구간으로 변환한다.
 * 다음 단계가 시작된 시점을 앞 단계의 종료로 보고, 마지막 단계는 done 이벤트 시점에
 * 닫는다. 따라서 네트워크 수신 시간 대신 서버에서 실제로 측정한 시간을 표시한다.
 *
 * 같은 stage가 연달아 여러 번 오면(예: SCHEDULE 편성처럼 오래 걸리는 단일 호출 동안
 * 로딩 화면이 멈춰 보이지 않게 주기적으로 흘려보내는 heartbeat progress) 한 구간으로
 * 합친다 — 매번 새 stage로 잘라 보여주면 같은 이름의 행이 여러 개 겹쳐 오히려
 * 헷갈린다. 표시 문구는 그 구간의 마지막 heartbeat 메시지를 쓴다.
 */
export function buildAgentStageTimings(
  progressEvents: AgentProgressEvent[],
  completedElapsedMs: number,
  streamTiming?: StreamFirstTokenTiming,
): AgentStageTiming[] {
  const grouped: { stage: AgentProgressEvent["stage"]; message: string; started_at_ms: number }[] =
    [];
  for (const event of progressEvents) {
    const last = grouped.at(-1);
    if (last && last.stage === event.stage) {
      last.message = event.message;
      continue;
    }
    grouped.push({ stage: event.stage, message: event.message, started_at_ms: event.elapsed_ms });
  }

  return grouped.map((group, index) => {
    const nextStartedAt = grouped[index + 1]?.started_at_ms ?? completedElapsedMs;
    const timeToFirstTokenMs =
      group.stage === "composing_message" &&
      streamTiming?.messageStartElapsedMs != null &&
      streamTiming.firstMessageDeltaElapsedMs != null
        ? Math.max(0, streamTiming.firstMessageDeltaElapsedMs - streamTiming.messageStartElapsedMs)
        : undefined;
    return {
      stage: group.stage,
      message: group.message,
      started_at_ms: group.started_at_ms,
      duration_ms: Math.max(0, nextStartedAt - group.started_at_ms),
      ...(timeToFirstTokenMs !== undefined ? { time_to_first_token_ms: timeToFirstTokenMs } : {}),
    };
  });
}
