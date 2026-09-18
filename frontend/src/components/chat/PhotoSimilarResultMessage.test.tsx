/*
 * 역할: 사진 검색 결과 말풍선의 진행 표시·빈 결과 구분·상세 열기를 검증한다.
 *
 * **유사도를 화면에 숫자로 내보내지 않는 것**도 함께 못 박는다 — 그 값은 순위를
 * 위한 것이지 "얼마나 닮았다"의 눈금이 아니다(D-094).
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { PhotoSimilarPlace } from "../../types";
import { PhotoSimilarResultMessage } from "./PhotoSimilarResultMessage";

// 상세 모달은 별도 컴포넌트이고 API를 부른다. 여기서는 "열렸는가"만 본다.
vi.mock("./RecommendationDetailPreviewModal", () => ({
  RecommendationDetailPreviewModal: ({ placeName }: { placeName?: string }) => (
    <div data-testid="detail-modal">{placeName}</div>
  ),
}));

function place(overrides: Partial<PhotoSimilarPlace> = {}): PhotoSimilarPlace {
  return {
    content_id: "2946087",
    title: "마우스래빗",
    similarity: 0.8939,
    photo_count: 5,
    ...overrides,
  };
}

describe("PhotoSimilarResultMessage", () => {
  it("장소를 순서대로 보여준다", () => {
    render(
      <PhotoSimilarResultMessage
        centerName="성수동"
        candidateCount={40}
        places={[place(), place({ content_id: "1", title: "공근혜갤러리" })]}
      />,
    );

    expect(screen.getByText("마우스래빗")).toBeTruthy();
    expect(screen.getByText("공근혜갤러리")).toBeTruthy();
    expect(screen.getByText("1")).toBeTruthy();
    expect(screen.getByText("2")).toBeTruthy();
  });

  it("유사도를 숫자로 보여주지 않는다", () => {
    const { container } = render(
      <PhotoSimilarResultMessage centerName="성수동" candidateCount={40} places={[place()]} />,
    );

    // 0.8939 · 89 · 89% 어느 형태로도 나오면 안 된다.
    expect(container.textContent).not.toMatch(/89|0\.8/);
  });

  it("찾는 중에는 사진과 진행 표시만 보여준다", () => {
    const { container } = render(
      <PhotoSimilarResultMessage
        imageUrl="data:image/jpeg;base64,AAAA"
        status="loading"
        centerName=""
        candidateCount={0}
        places={[]}
      />,
    );

    expect(container.textContent).toContain("찾고 있어요");
    // 아직 안 끝났는데 "못 찾았어요"가 뜨면 안 된다.
    expect(container.textContent).not.toContain("찾지 못했어요");
    expect(screen.getByAltText("올린 사진")).toBeTruthy();
  });

  it("올린 사진을 보여준다", () => {
    render(
      <PhotoSimilarResultMessage
        imageUrl="data:image/jpeg;base64,AAAA"
        centerName="성수동"
        candidateCount={40}
        places={[place()]}
      />,
    );

    const image = screen.getByAltText("올린 사진") as HTMLImageElement;
    expect(image.src).toContain("data:image/jpeg");
  });

  it("사진 축소본이 없어도 결과는 그대로 나온다", () => {
    render(
      <PhotoSimilarResultMessage
        imageUrl={null}
        centerName="성수동"
        candidateCount={40}
        places={[place()]}
      />,
    );

    expect(screen.queryByAltText("올린 사진")).toBeNull();
    expect(screen.getByText("마우스래빗")).toBeTruthy();
  });

  it("장소를 누르면 상세가 열린다", () => {
    render(
      <PhotoSimilarResultMessage centerName="성수동" candidateCount={40} places={[place()]} />,
    );

    expect(screen.queryByTestId("detail-modal")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /마우스래빗/ }));

    expect(screen.getByTestId("detail-modal").textContent).toBe("마우스래빗");
  });

  it("장소마다 비교에 쓴 사진을 보여준다", () => {
    render(
      <PhotoSimilarResultMessage
        centerName="성수동"
        candidateCount={40}
        places={[place({ image_url: "https://tong.visitkorea.or.kr/x.jpg" })]}
      />,
    );

    const images = screen.getAllByRole("presentation");
    expect(images.some((img) => (img as HTMLImageElement).src.includes("visitkorea"))).toBe(true);
  });

  it("결과가 없어도 어디서 찾았는지 알려준다", () => {
    const { container } = render(
      <PhotoSimilarResultMessage centerName="강서구" candidateCount={12} places={[]} />,
    );

    expect(container.textContent).toContain("강서구");
  });

  it("사진 1장으로 만든 장소는 그 사실을 밝힌다", () => {
    render(
      <PhotoSimilarResultMessage
        centerName="성수동"
        candidateCount={40}
        places={[place({ photo_count: 1 })]}
      />,
    );

    expect(screen.getByText("사진 1장 비교")).toBeTruthy();
  });

  it("후보가 0곳인 것과 닮은 곳이 없는 것을 구분한다", () => {
    const { rerender, container } = render(
      <PhotoSimilarResultMessage centerName="성수동" candidateCount={0} places={[]} />,
    );
    expect(container.textContent).toContain("갈 수 있는 곳을 찾지 못했어요");

    rerender(<PhotoSimilarResultMessage centerName="성수동" candidateCount={40} places={[]} />);
    // 후보는 있었는데 벡터가 없는 경우 — 적재가 안 된 지역이라는 것을 밝힌다.
    expect(container.textContent).toContain("40곳을 봤는데");
    expect(container.textContent).toContain("아직 사진을 모으지 못한 지역");
  });

  it("사진과 함께 무엇을 요청한 턴인지 보여준다", () => {
    /* 사진만 있으면 지난 대화를 되돌렸을 때 사진 한 장이 맥락 없이 놓인다.
       서버도 같은 문장을 대화 기록에 남긴다(PHOTO_SEARCH_USER_INPUT). */
    render(
      <PhotoSimilarResultMessage centerName="성수동" candidateCount={40} places={[place()]} />,
    );

    expect(screen.getByText("이 사진과 비슷한 장소 추천해줘")).toBeTruthy();
  });

  it("사진이 없어도 발화 문구는 남는다", () => {
    /* 복원 화면이 이렇다 — 사진은 서버에 안 남기므로 문구만 되돌아온다. */
    render(
      <PhotoSimilarResultMessage
        imageUrl={null}
        centerName="현재 위치"
        candidateCount={40}
        places={[place()]}
      />,
    );

    expect(screen.getByText("이 사진과 비슷한 장소 추천해줘")).toBeTruthy();
    expect(screen.queryByAltText("올린 사진")).toBeNull();
  });

  it("위치를 몰라 멈췄으면 무엇을 해야 하는지 알려준다", () => {
    /* 실패와 나누는 이유는 사용자가 할 일이 달라서다 — 실패는 다시 해보면
       되고, 이쪽은 위치를 먼저 정해야 한다. */
    const onSetLocation = vi.fn();
    render(
      <PhotoSimilarResultMessage
        status="location_required"
        centerName=""
        candidateCount={0}
        places={[]}
        onSetLocation={onSetLocation}
      />,
    );

    expect(screen.getByText(/위치를 정하고 사진을 다시 올려/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "위치 정하기" }));
    expect(onSetLocation).toHaveBeenCalled();
  });

  it("위치를 몰라 멈췄어도 결과 문구는 안 그린다", () => {
    /* centerName이 비어 있어서 "주변에서 분위기가 닮은 곳이에요"를 그리면
       "  주변에서..."가 된다. */
    render(
      <PhotoSimilarResultMessage
        status="location_required"
        centerName=""
        candidateCount={0}
        places={[]}
      />,
    );

    expect(screen.queryByText(/분위기가 닮은 곳이에요/)).toBeNull();
  });

  it("되돌린 대화에서는 사진 자리에 왜 없는지를 적는다", () => {
    /* 빈 자리만 두면 사진이 사라진 것인지 원래 없던 것인지 알 수 없다. */
    render(
      <PhotoSimilarResultMessage
        imageUrl={null}
        restored
        centerName="성수동"
        candidateCount={40}
        places={[place()]}
      />,
    );

    expect(screen.getByText("[사용자 입력 사진]")).toBeTruthy();
    expect(screen.getByText(/따로 저장하지 않아서/)).toBeTruthy();
  });

  it("실시간에 축소본만 없는 경우에는 그 안내를 하지 않는다", () => {
    /* 브라우저가 못 여는 형식(HEIC 등)이면 실시간에도 축소본이 없다. 그때
       "저장하지 않아서"라고 말하면 틀린 설명이 된다. */
    render(
      <PhotoSimilarResultMessage
        imageUrl={null}
        centerName="성수동"
        candidateCount={40}
        places={[place()]}
      />,
    );

    expect(screen.queryByText("[사용자 입력 사진]")).toBeNull();
    expect(screen.queryByText(/따로 저장하지 않아서/)).toBeNull();
  });

  it("사진이 있으면 그 안내를 하지 않는다", () => {
    render(
      <PhotoSimilarResultMessage
        imageUrl="data:image/jpeg;base64,x"
        restored
        centerName="성수동"
        candidateCount={40}
        places={[place()]}
      />,
    );

    expect(screen.getByAltText("올린 사진")).toBeTruthy();
    expect(screen.queryByText("[사용자 입력 사진]")).toBeNull();
  });
});
