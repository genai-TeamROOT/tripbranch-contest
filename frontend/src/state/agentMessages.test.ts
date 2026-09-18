/*
 * 역할: buildAgentMessages가 한 턴을 어떤 화면 메시지들로 펴는지 검증한다.
 * 특히 **피드백 버튼이 붙는 턴과 안 붙는 턴**을 잠근다 — 이 판정은 되묻기에
 * 좋아요/싫어요가 매겨지는지를 가르고, 그 점수가 추천 품질 자료로 쓰인다.
 *
 * 화면 기록 하나를 되돌릴 때 사진 검색 턴을 알아보는지도 함께 본다. 사진 검색은
 * 조건 병합을 타지 않아 payload가 AgentResponse가 아니라, 가르지 못하면
 * buildAgentMessages가 llm_output을 읽다가 터져 그 대화 전체가 복원되지 않는다.
 */

import { describe, expect, it } from "vitest";

import type { AgentResponse, ClarificationOption, PhotoSimilarPlacesResponse } from "../types";
import {
  buildAgentMessages,
  buildPhotoSimilarMessage,
  isPhotoSimilarRecord,
} from "./agentMessages";

/*
 * AgentResponse는 필드가 많고 대부분 이 함수가 보지 않는다(UserConditions,
 * api_context 등). 이 함수가 실제로 읽는 것만 채우고 한 번만 캐스팅한다 —
 * 전부 채우면 픽스처가 계약 변경마다 깨지는데, 여기서 보는 것은 메시지 조립
 * 규칙이지 계약의 완전성이 아니다.
 */
function response(overrides: Partial<AgentResponse> = {}): AgentResponse {
  return {
    llm_output: { intent: "RECOMMEND", status: "complete", clarification: null },
    state: { session_id: "sess_1", run_id: "run_1" },
    recommendations: null,
    message: "답변이에요",
    ...overrides,
  } as unknown as AgentResponse;
}

function clarification(options: ClarificationOption[]) {
  return {
    llm_output: {
      intent: "RECOMMEND",
      status: "needs_clarification",
      clarification: { missing_fields: [], ambiguous_fields: [], message: "", options },
    },
  } as unknown as Partial<AgentResponse>;
}

const OPTIONS: ClarificationOption[] = [
  { id: "indoor", label: "실내", resolved_intent: "RECOMMEND" },
];

function types(messages: ReturnType<typeof buildAgentMessages>) {
  return messages.map((message) => message.type);
}

it("보통 답변에는 피드백 버튼이 붙는다", () => {
  const messages = buildAgentMessages(response(), { userInput: "질문", elapsedMsClient: 0 });

  expect(types(messages)).toEqual(["assistant_text", "feedback"]);
});

it("되묻기만 한 턴에는 피드백 버튼을 붙이지 않는다", () => {
  /* 2026-09-08. 되묻기는 답이 아니라 질문이라, 좋아요/싫어요를 매기면 무엇에
     대한 평가인지 알 수 없다. mintee가 2d29192c에서 노출시킨 것을 되돌린 것이다. */
  const messages = buildAgentMessages(response(clarification(OPTIONS)), {
    userInput: "어디 갈지 모르겠어",
    elapsedMsClient: 0,
  });

  expect(types(messages)).toEqual(["clarification"]);
  expect(types(messages)).not.toContain("feedback");
});

it("되묻기와 결과 카드가 함께 오면 피드백 버튼이 그대로 붙는다", () => {
  /* 그 턴에는 평가할 대상(카드)이 있다. 없애면 카드에 대한 피드백까지 사라진다. */
  const messages = buildAgentMessages(
    response({
      ...clarification(OPTIONS),
      recommendations: {
        recommendations: [],
        unverified_recommendations: [],
        elapsed_ms: 10,
      },
    } as Partial<AgentResponse>),
    { userInput: "어디 갈지 모르겠어", elapsedMsClient: 0 },
  );

  expect(types(messages)).toContain("clarification");
  expect(types(messages)).toContain("feedback");
});

it("run_id가 없으면 어떤 턴이든 피드백 버튼이 없다", () => {
  const messages = buildAgentMessages(
    response({ state: { session_id: "sess_1", run_id: "" } } as Partial<AgentResponse>),
    { userInput: "질문", elapsedMsClient: 0 },
  );

  expect(types(messages)).not.toContain("feedback");
});

function record(): PhotoSimilarPlacesResponse & { kind: string } {
  return {
    kind: "photo_similar",
    session_id: "s-1",
    center_name: "성수동",
    candidate_count: 42,
    truncated_count: 0,
    elapsed_ms: 1200,
    places: [{ content_id: "2946087", title: "마우스래빗", similarity: 0.89, photo_count: 5 }],
  };
}

describe("isPhotoSimilarRecord", () => {
  it("표시가 붙은 기록을 알아본다", () => {
    expect(isPhotoSimilarRecord(record())).toBe(true);
  });

  it("표시가 없으면 AgentResponse로 본다", () => {
    // 이 키가 생기기 전의 기록에는 표시가 없다. 없는 쪽이 기존 동작이어야
    // 옛 대화가 그대로 복원된다.
    expect(isPhotoSimilarRecord({ message: "박물관을 찾아봤어요" })).toBe(false);
    expect(isPhotoSimilarRecord(null)).toBe(false);
    expect(isPhotoSimilarRecord(undefined)).toBe(false);
  });
});

describe("buildPhotoSimilarMessage", () => {
  it("결과와 기준점을 그대로 되돌린다", () => {
    const message = buildPhotoSimilarMessage(record());

    expect(message.type).toBe("photo_similar_result");
    if (message.type !== "photo_similar_result") return;
    expect(message.centerName).toBe("성수동");
    expect(message.candidateCount).toBe(42);
    expect(message.places[0].title).toBe("마우스래빗");
    expect(message.status).toBe("done");
  });

  it("되돌린 말풍선임을 표시한다", () => {
    /* 이 표시가 있어야 화면이 사진 자리에 "저장하지 않아 못 보여준다"를 놓는다.
       imageUrl이 비었다는 것만으로는 실시간의 축소본 실패와 갈라낼 수 없다. */
    const message = buildPhotoSimilarMessage(record());

    expect(message.type).toBe("photo_similar_result");
    if (message.type !== "photo_similar_result") return;
    expect(message.restored).toBe(true);
  });

  it("사진은 비운다", () => {
    // 원본은 서버가 임베딩만 하고 버렸고 축소본은 그 브라우저에만 있다.
    // 다른 기기에서 열면 가져올 데가 없으므로 사진 자리를 건너뛴다.
    const message = buildPhotoSimilarMessage(record());

    expect(message.type).toBe("photo_similar_result");
    if (message.type !== "photo_similar_result") return;
    expect(message.imageUrl).toBeNull();
  });
});
