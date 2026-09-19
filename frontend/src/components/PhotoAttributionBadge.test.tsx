import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PhotoAttributionBadge } from "./PhotoAttributionBadge";

const attribution = {
  provider: "Google Maps",
  author_name: "북극돼지",
  author_uri: "https://maps.google.com/maps/contrib/1",
  source_uri: "https://www.google.com/maps/place/x",
};

describe("PhotoAttributionBadge", () => {
  it("작성자와 출처를 링크로 그린다", () => {
    // Google 정책이 요구하는 것: 작성자 크레딧, 작성자 프로필 링크,
    // 원본 사진을 볼 수 있는 Google 지도 링크.
    render(<PhotoAttributionBadge attribution={attribution} />);

    const author = screen.getByRole("link", { name: "북극돼지" });
    expect(author).toHaveAttribute("href", attribution.author_uri);
    const source = screen.getByRole("link", { name: "Google Maps" });
    expect(source).toHaveAttribute("href", attribution.source_uri);
  });

  it("관광공사 사진에는 아무것도 그리지 않는다", () => {
    const { container } = render(<PhotoAttributionBadge attribution={null} />);

    expect(container).toBeEmptyDOMElement();
  });

  it("링크가 없어도 작성자 이름은 남긴다", () => {
    // 정책이 요구하는 최소치는 작성자 크레딧이다. 링크가 비었다고 이름까지
    // 빼면 표기를 안 한 것과 같아진다.
    render(
      <PhotoAttributionBadge
        attribution={{ provider: "Google Maps", author_name: "북극돼지" }}
      />,
    );

    expect(screen.getByTestId("photo-attribution")).toHaveTextContent("북극돼지");
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("좁은 자리에서도 작성자 이름을 지우지 않는다", () => {
    render(<PhotoAttributionBadge attribution={attribution} compact />);

    expect(screen.getByRole("link", { name: "북극돼지" })).toBeInTheDocument();
  });
});
