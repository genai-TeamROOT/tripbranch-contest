/*
 * 역할: PlaceCard의 담기/빼기 토글 동작을 검증한다(SCHEDULE-12 카드 3).
 * 입력: RecommendationItem과 onToggleSave/isSaved prop.
 * 출력: 토글 노출 조건, aria-pressed 반영, 상세 미리보기와의 클릭 분리 검증.
 * 호출 시점: vitest 실행 시.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

import type { RecommendationItem } from "../types";
import { PlaceCard } from "./PlaceCard";

function item(overrides: Partial<RecommendationItem> = {}): RecommendationItem {
  return {
    place_id: "place-1",
    name: "아키비스트 서촌",
    category: "cafe",
    distance_km: 0.54,
    remaining_minutes: 120,
    operating_hours_display: "11:00~21:00",
    environment_type: "indoor",
    recommendation_reason: "테스트 추천이에요.",
    explanations: [],
    warnings: [],
    score: 0.9,
    feature_scores: {},
    weights_used: {},
    taste_evidence: [],
    ...overrides,
  };
}

test("onToggleSave가 없으면 담기 버튼을 그리지 않는다", () => {
  render(
    <ul>
      <PlaceCard item={item()} />
    </ul>,
  );

  expect(screen.queryByRole("button", { name: /보관함에 담기/ })).toBeNull();
});

test("담기지 않은 장소는 담기로, 담긴 장소는 담김으로 보인다", () => {
  const { rerender } = render(
    <ul>
      <PlaceCard item={item()} onToggleSave={() => {}} isSaved={false} />
    </ul>,
  );

  const button = screen.getByRole("button", { name: /보관함에 담기/ });
  expect(button).toHaveAttribute("aria-pressed", "false");

  rerender(
    <ul>
      <PlaceCard item={item()} onToggleSave={() => {}} isSaved />
    </ul>,
  );

  const saved = screen.getByRole("button", { name: /보관함에 담기/ });
  expect(saved).toHaveAttribute("aria-pressed", "true");
});

/*
 * 카드 전체가 상세 미리보기 클릭 대상(role="button")이라, 담기 버튼이 이벤트를
 * 멈추지 않으면 한 번의 클릭으로 모달까지 열린다. 이 테스트가 그 회귀를 막는다.
 */
test("담기 클릭은 상세 미리보기를 열지 않는다", async () => {
  const user = userEvent.setup();
  const onToggleSave = vi.fn();
  const onOpenDetail = vi.fn();

  render(
    <ul>
      <PlaceCard item={item()} onToggleSave={onToggleSave} onOpenDetail={onOpenDetail} />
    </ul>,
  );

  await user.click(screen.getByRole("button", { name: /보관함에 담기/ }));

  expect(onToggleSave).toHaveBeenCalledTimes(1);
  expect(onToggleSave).toHaveBeenCalledWith(expect.objectContaining({ place_id: "place-1" }));
  expect(onOpenDetail).not.toHaveBeenCalled();
});

test("카드 본문 클릭은 여전히 상세 미리보기를 연다", async () => {
  const user = userEvent.setup();
  const onOpenDetail = vi.fn();

  render(
    <ul>
      <PlaceCard item={item()} onToggleSave={() => {}} onOpenDetail={onOpenDetail} />
    </ul>,
  );

  await user.click(screen.getByText("아키비스트 서촌"));

  expect(onOpenDetail).toHaveBeenCalledTimes(1);
});

test("image_url이 있으면 이미지를, 없으면 사진 없음 자리표시를 보여준다", () => {
  // 이미지는 장식용(alt="")이라 role="img"로 접근할 수 없다 — querySelector로 확인한다.
  const { container, rerender } = render(
    <ul>
      <PlaceCard item={item({ image_url: null })} />
    </ul>,
  );

  expect(container.querySelector("img")).not.toBeInTheDocument();
  expect(screen.getByTestId("place-thumbnail-placeholder")).toBeInTheDocument();

  rerender(
    <ul>
      <PlaceCard item={item({ image_url: "https://img.test/place-1.jpg" })} />
    </ul>,
  );

  expect(container.querySelector("img")).toHaveAttribute("src", "https://img.test/place-1.jpg");
  expect(screen.queryByTestId("place-thumbnail-placeholder")).not.toBeInTheDocument();
});

test("이미지를 못 불러오면 자리표시로 바꾸고, 다른 사진이 오면 다시 보여준다", () => {
  const { container, rerender } = render(
    <ul>
      <PlaceCard item={item({ image_url: "https://img.test/gone.jpg" })} />
    </ul>,
  );

  fireEvent.error(container.querySelector("img") as HTMLImageElement);

  expect(container.querySelector("img")).not.toBeInTheDocument();
  expect(screen.getByTestId("place-thumbnail-placeholder")).toBeInTheDocument();

  /*
   * 실패를 boolean으로 들고 있으면 여기서 자리표시가 그대로 남는다 — 목록은
   * key={place_id}로 그려져서 같은 장소의 사진만 바뀔 때 이 컴포넌트가 그대로
   * 살아 있기 때문이다. 실패한 주소 자체를 담아야 새 주소에서 풀린다.
   */
  rerender(
    <ul>
      <PlaceCard item={item({ image_url: "https://img.test/fresh.jpg" })} />
    </ul>,
  );

  expect(container.querySelector("img")).toHaveAttribute("src", "https://img.test/fresh.jpg");
  expect(screen.queryByTestId("place-thumbnail-placeholder")).not.toBeInTheDocument();
});

test("썸네일이 죽으면 폴백 주소로 갈아타고, 둘 다 죽어야 자리표시를 보여준다", () => {
  /*
   * 작은 썸네일(firstimage2)만 관광공사 서버에서 사라진 장소가 있다 — 아현시장이
   * 그렇고 원본(firstimage)은 살아 있다. 서버는 살아 있는지 확인하지 않고 두 주소를
   * 다 내려보낸다(추천 한 번에 확인 요청이 5~10건 붙는다).
   */
  const { container } = render(
    <ul>
      <PlaceCard
        item={item({
          image_url: "https://img.test/thumb_image3.jpg",
          image_url_fallback: "https://img.test/original_image2.jpg",
        })}
      />
    </ul>,
  );

  expect(container.querySelector("img")).toHaveAttribute(
    "src",
    "https://img.test/thumb_image3.jpg",
  );

  fireEvent.error(container.querySelector("img") as HTMLImageElement);

  // 자리표시로 가지 않고 두 번째 주소를 부른다.
  expect(container.querySelector("img")).toHaveAttribute(
    "src",
    "https://img.test/original_image2.jpg",
  );
  expect(screen.queryByTestId("place-thumbnail-placeholder")).not.toBeInTheDocument();

  fireEvent.error(container.querySelector("img") as HTMLImageElement);

  expect(container.querySelector("img")).not.toBeInTheDocument();
  expect(screen.getByTestId("place-thumbnail-placeholder")).toBeInTheDocument();
});

test("폴백 주소가 없으면 한 번 실패에 바로 자리표시로 간다", () => {
  const { container } = render(
    <ul>
      <PlaceCard item={item({ image_url: "https://img.test/gone.jpg" })} />
    </ul>,
  );

  fireEvent.error(container.querySelector("img") as HTMLImageElement);

  expect(container.querySelector("img")).not.toBeInTheDocument();
  expect(screen.getByTestId("place-thumbnail-placeholder")).toBeInTheDocument();
});
