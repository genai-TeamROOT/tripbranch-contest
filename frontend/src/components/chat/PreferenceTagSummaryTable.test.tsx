/*
 * 장소별 취향 태그 표. 추천 결과 메시지에서 갈라져 나오면서 이 파일로 옮겨왔다
 * (원래는 RecommendationResultMessage.test.tsx에 있었다).
 */

import { fireEvent, render, screen, within } from "@testing-library/react";

import { PreferenceTagSummaryTable } from "./PreferenceTagSummaryTable";

it("장소별 취향 태그와 문서 단위 언급 수를 표로 표시한다", () => {
  render(
    <PreferenceTagSummaryTable
      items={[
        {
          place_id: "place-1",
          name: "아키비스트 서촌",
          preference_tags: [
            { code: "quiet", label: "조용히 머물기 좋은", mention_count: 7 },
            { code: "date", label: "데이트하기 좋은", mention_count: 4 },
            { code: "walk", label: "산책하기 좋은", mention_count: 3 },
            { code: "nature", label: "자연을 즐기기 좋은", mention_count: 2 },
          ],
        },
      ]}
      language="ko"
    />,
  );

  const table = screen.getByRole("table", { name: "장소별 방문자 취향 태그" });
  const sourceButton = screen.getByRole("button", { name: "태그 출처 보기" });
  expect(sourceButton).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  fireEvent.click(sourceButton);
  const sourceNote = screen.getByRole("tooltip");
  expect(sourceButton).toHaveAttribute("aria-expanded", "true");
  expect(within(sourceNote).getByText("출처: 네이버 블로그 후기 · 구글 지도 리뷰")).toBeInTheDocument();
  expect(within(sourceNote).getByText("장소별 약 30건")).toBeInTheDocument();
  expect(table.parentElement).toContainElement(sourceNote);
  expect(within(table).getByText("아키비스트 서촌")).toBeInTheDocument();
  expect(within(table).getByText("조용히 머물기 좋은")).toBeInTheDocument();
  expect(within(table).getByText("(7)")).toBeInTheDocument();
  expect(within(table).getByText("데이트하기 좋은")).toBeInTheDocument();
  expect(within(table).getByText("(4)")).toBeInTheDocument();
  // 카드가 좁아 두 개까지만 싣는다 — 나머지는 상세에서 본다.
  expect(within(table).queryByText("산책하기 좋은")).not.toBeInTheDocument();
  expect(within(table).queryByText("자연을 즐기기 좋은")).not.toBeInTheDocument();
});

it("태그가 하나도 없으면 표 자체를 그리지 않는다", () => {
  const { container } = render(
    <PreferenceTagSummaryTable
      items={[{ place_id: "place-1", name: "아키비스트 서촌" }]}
      language="ko"
    />,
  );

  expect(container).toBeEmptyDOMElement();
});

it("질문과 일치한 취향 태그만 강조한다", () => {
  render(
    <PreferenceTagSummaryTable
      items={[{
        place_id: "place-1",
        name: "가족식당",
        preference_tags: [
          { code: "with_kids", label: "아이와 함께하기 좋은", mention_count: 5, is_query_match: true },
          { code: "group_gathering", label: "모임하기 좋은", mention_count: 9 },
        ],
      }]}
      language="ko"
    />,
  );

  expect(screen.getByText("아이와 함께하기 좋은").closest("span")).toHaveAttribute(
    "data-query-match",
    "true",
  );
  expect(screen.getByText("모임하기 좋은").closest("span")).not.toHaveAttribute(
    "data-query-match",
  );

  /*
   * **강조가 "채움"으로 보이는지까지 본다(2026-09-16).** 위 두 줄은
   * data-query-match만 보므로, 두 칩이 같은 색이 되어도 통과한다 — 실제로
   * 전에는 옅은 브랜드 배경 대 하늘색 배경이라 갈리지 않았다. 걸린 칩은
   * 채우고 나머지는 흰 바탕에 브랜드 글자라는 것을 여기서 고정한다.
   */
  expect(screen.getByText("아이와 함께하기 좋은").closest("span")).toHaveClass(
    "bg-brand",
    "text-white",
  );
  expect(screen.getByText("모임하기 좋은").closest("span")).toHaveClass(
    "bg-white",
    "text-brand",
  );
});

/*
 * **좁은 화면에서 둘째 태그가 잘리던 것을 막는다(2026-09-16).**
 *
 * 표가 table-fixed에 장소 칸이 w-1/3이라 태그 칸이 고정되는데, 칩은
 * whitespace-nowrap이라 넘치면 바깥 카드의 overflow-hidden이 말없이 잘라냈다
 * (기기 390px에서 20~31px, 320px에서 66~77px — 실제 브라우저 측정).
 *
 * jsdom은 레이아웃을 계산하지 않아 "정말 잘리는지"는 여기서 잴 수 없다. 그래서
 * 줄바꿈을 막는 클래스가 되돌아오는 것만 막는다 — 잘림 자체의 근거는 위 실측이다.
 */
it("좁은 화면에서 태그가 다음 줄로 내려갈 수 있어야 한다", () => {
  render(
    <PreferenceTagSummaryTable
      items={[{
        place_id: "place-1",
        name: "이한열기념관",
        preference_tags: [
          { code: "culture", label: "문화·예술을 즐기기 좋은", mention_count: 8 },
          { code: "photo", label: "사진 찍기 좋은", mention_count: 5 },
        ],
      }]}
      language="ko"
    />,
  );

  const row = screen.getByText("문화·예술을 즐기기 좋은").closest("div");
  expect(row).toHaveClass("flex-wrap");
  expect(row).not.toHaveClass("flex-nowrap");
});
