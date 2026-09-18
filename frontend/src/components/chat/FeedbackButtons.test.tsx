import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, vi } from "vitest";

import { FeedbackButtons } from "./FeedbackButtons";

afterEach(() => {
  vi.unstubAllGlobals();
});

function successFetch() {
  return vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ recorded_at: "2026-08-21T00:00:00+09:00" }),
  });
}

it("좋아요를 누르면 클릭 즉시 /api/feedback에 rating=like로 기록한다", async () => {
  const user = userEvent.setup();
  const fetchMock = successFetch();
  vi.stubGlobal("fetch", fetchMock);

  render(<FeedbackButtons sessionId="sess_1" runId="run_1" />);
  await user.click(screen.getByRole("button", { name: "좋아요" }));

  expect(fetchMock).toHaveBeenCalledWith(
    "/api/feedback",
    expect.objectContaining({
      body: JSON.stringify({ session_id: "sess_1", run_id: "run_1", rating: "like" }),
    }),
  );
});

it("피드백을 남길 때 해당 턴의 발화·응답·Intent도 함께 보낸다", async () => {
  const user = userEvent.setup();
  const fetchMock = successFetch();
  vi.stubGlobal("fetch", fetchMock);

  render(
    <FeedbackButtons
      sessionId="sess_1"
      runId="run_1"
      intent="INFO"
      userInput="경복궁 지금 사람 많아?"
      assistantMessage="현재 혼잡도는 보통이에요."
    />,
  );
  await user.click(screen.getByRole("button", { name: "좋아요" }));

  expect(fetchMock).toHaveBeenCalledWith(
    "/api/feedback",
    expect.objectContaining({
      body: JSON.stringify({
        session_id: "sess_1",
        run_id: "run_1",
        rating: "like",
        intent: "INFO",
        user_input: "경복궁 지금 사람 많아?",
        assistant_message: "현재 혼잡도는 보통이에요.",
      }),
    }),
  );
});

it("싫어요를 누르면 표준 사유를 고를 수 있고, 사유를 고르기 전에는 전송하지 않는다", async () => {
  const user = userEvent.setup();
  const fetchMock = successFetch();
  vi.stubGlobal("fetch", fetchMock);

  render(<FeedbackButtons sessionId="sess_1" runId="run_1" />);
  await user.click(screen.getByRole("button", { name: "별로예요" }));

  expect(screen.getByText("되묻기나 선택지가 상황에 맞지 않아요")).toBeInTheDocument();
  expect(fetchMock).not.toHaveBeenCalled();
});

it("선택한 사유만 제출하면 reason_code를 저장하고 다음 질문으로 진행할 수 있다", async () => {
  const user = userEvent.setup();
  const fetchMock = successFetch();
  vi.stubGlobal("fetch", fetchMock);

  render(<FeedbackButtons sessionId="sess_1" runId="run_1" />);
  await user.click(screen.getByRole("button", { name: "별로예요" }));
  await user.click(screen.getByText("앞에서 말한 조건·맥락이 반영되지 않았어요"));
  await user.click(screen.getByRole("button", { name: "제출" }));

  expect(fetchMock).toHaveBeenCalledWith(
    "/api/feedback",
    expect.objectContaining({
      body: JSON.stringify({
        session_id: "sess_1",
        run_id: "run_1",
        rating: "dislike",
        reason_code: "context_not_preserved",
      }),
    }),
  );
  expect(screen.getByRole("button", { name: "별로예요" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.queryByRole("button", { name: "제출" })).not.toBeInTheDocument();
});

it("어느 사유에서도 선택적으로 자유 의견을 함께 저장한다", async () => {
  const user = userEvent.setup();
  const fetchMock = successFetch();
  vi.stubGlobal("fetch", fetchMock);

  render(<FeedbackButtons sessionId="sess_1" runId="run_1" />);
  await user.click(screen.getByRole("button", { name: "별로예요" }));
  await user.click(screen.getByText("현재 위치나 장소를 잘못 이해했어요"));
  await user.type(screen.getByPlaceholderText("추가 의견이 있다면 알려주세요. (선택)"), "출발 위치가 달라요");
  await user.click(screen.getByRole("button", { name: "제출" }));

  expect(fetchMock).toHaveBeenCalledWith(
    "/api/feedback",
    expect.objectContaining({
      body: JSON.stringify({
        session_id: "sess_1",
        run_id: "run_1",
        rating: "dislike",
        reason_code: "location_misunderstood",
        comment: "출발 위치가 달라요",
      }),
    }),
  );
});

it("기타도 같은 선택적 자유 입력 흐름으로 저장한다", async () => {
  const user = userEvent.setup();
  const fetchMock = successFetch();
  vi.stubGlobal("fetch", fetchMock);

  render(<FeedbackButtons sessionId="sess_1" runId="run_1" />);
  await user.click(screen.getByRole("button", { name: "별로예요" }));
  await user.click(screen.getByText("기타"));
  await user.click(screen.getByRole("button", { name: "제출" }));

  expect(fetchMock).toHaveBeenCalledWith(
    "/api/feedback",
    expect.objectContaining({
      body: JSON.stringify({
        session_id: "sess_1",
        run_id: "run_1",
        rating: "dislike",
        reason_code: "other",
      }),
    }),
  );
});

it("전송에 실패하면 에러 문구를 보여준다", async () => {
  const user = userEvent.setup();
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network down")));

  render(<FeedbackButtons sessionId="sess_1" runId="run_1" />);
  await user.click(screen.getByRole("button", { name: "좋아요" }));

  expect(await screen.findByText("피드백 전송에 실패했어요. 다시 시도해주세요.")).toBeInTheDocument();
});

it("userInput/assistantMessage/intent가 있으면 함께 전송한다", async () => {
  const user = userEvent.setup();
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ recorded_at: "2026-08-21T00:00:00+09:00" }),
  });
  vi.stubGlobal("fetch", fetchMock);

  render(
    <FeedbackButtons
      sessionId="sess_1"
      runId="run_1"
      userInput="경복궁 근처 카페 추천해줘"
      assistantMessage="이런 곳들을 찾아봤어요."
      intent="RECOMMEND"
    />,
  );
  await user.click(screen.getByRole("button", { name: "좋아요" }));

  const [, request] = fetchMock.mock.calls[0] as [string, { body: string }];
  expect(JSON.parse(request.body)).toEqual({
    session_id: "sess_1",
    run_id: "run_1",
    rating: "like",
    user_input: "경복궁 근처 카페 추천해줘",
    assistant_message: "이런 곳들을 찾아봤어요.",
    intent: "RECOMMEND",
  });
});

it("session_id나 run_id가 없으면 아무것도 렌더링하지 않는다", () => {
  const { container } = render(<FeedbackButtons sessionId="" runId="" />);
  expect(container).toBeEmptyDOMElement();
});
