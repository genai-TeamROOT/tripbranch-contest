/*
 * 역할: 낡음 감지 배너가 stale_area_detected가 있을 때만 뜨는지 검증한다.
 * 입력: info_realtime_population 실행 단계를 담은 감사 turn.
 * 출력: 조건부 렌더링, 문구 조합에 대한 assertion.
 * 호출 시점: vitest 실행 시 호출된다.
 */

import { render, screen } from "@testing-library/react";
import type { DeveloperAuditTurn, ToolExecutionDebug } from "../../types";
import { StaleAreaBanner } from "./StaleAreaBanner";

function _execution(fields: Partial<ToolExecutionDebug>): ToolExecutionDebug {
  return {
    operation: "info_realtime_population",
    request_id: "req-1",
    status: "success",
    latency_ms: 120,
    providers: [],
    context_items: [],
    rule_versions: {},
    resolved_location_name: null,
    resolved_location_address: null,
    error_code: null,
    clarification_code: null,
    is_proxy: null,
    candidate_status_counts: {},
    ...fields,
  };
}

function _turn(execution: ToolExecutionDebug): DeveloperAuditTurn {
  return {
    id: "turn-1",
    userInput: "경복궁 사람 많아?",
    intent: "INFO",
    status: "complete",
    message: "지금은 보통이에요.",
    sessionId: "session-1",
    runId: "run-1",
    deviceLocation: null,
    elapsedMsClient: 800,
    serverElapsedMs: 700,
    stageTimings: [],
    extractedConditions: null,
    beforeConditions: null,
    afterConditions: null,
    recommendations: null,
    failure: null,
    response: {
      tool_executions: [execution],
      state: { api_context: null },
    } as unknown as DeveloperAuditTurn["response"],
  };
}

it("stale_area_detected가 없으면 아무것도 그리지 않는다", () => {
  const { container } = render(<StaleAreaBanner turn={_turn(_execution({}))} />);

  expect(container).toBeEmptyDOMElement();
});

it("info_realtime_population 외 단계의 stale_area_detected는 무시한다", () => {
  const { container } = render(
    <StaleAreaBanner
      turn={_turn(
        _execution({
          operation: "context_fetch",
          stale_area_detected: {
            probed_area_name: "경복궁",
            probed_area_code: null,
            matched_area_name: "북촌한옥마을",
            matched_area_distance_km: 0.85,
          },
        }),
      )}
    />,
  );

  expect(container).toBeEmptyDOMElement();
});

it("stale_area_detected가 있으면 대체 지역과 거리를 알린다", () => {
  render(
    <StaleAreaBanner
      turn={_turn(
        _execution({
          stale_area_detected: {
            probed_area_name: "경복궁",
            probed_area_code: null,
            matched_area_name: "북촌한옥마을",
            matched_area_distance_km: 0.85,
          },
        }),
      )}
    />,
  );

  expect(screen.getByRole("status")).toHaveTextContent("경복궁");
  expect(screen.getByRole("status")).toHaveTextContent("북촌한옥마을");
  expect(screen.getByRole("status")).toHaveTextContent("0.85km");
});
