/*
 * 역할: TourAPI 개요 원문을 읽을 수 있는 문단으로 나눈다.
 * 입력: 줄바꿈 없는 개요 한 덩어리.
 * 출력: 문단 배열(원문 글자는 그대로).
 * 호출 시점: 상세 모달의 개요 절, 답변 카드의 개요 값.
 */

/*
 * TourAPI 개요는 줄바꿈이 전혀 없는 한 덩어리로 온다(경복궁은 900자가 한 문단).
 * 그대로 그리면 어디까지 읽었는지 눈이 자리를 잃어 실제로는 아무도 끝까지 읽지
 * 않는다. 원문의 글자는 하나도 바꾸지 않고 문단만 나눈다.
 *
 * 나누는 자리는 두 가지다.
 * - 원문이 이미 표시해 둔 구획(◎, ※). TourAPI에서 이 기호 뒤는 본문과 결이
 *   다른 덧붙임(홍보 문구·예외 안내)이라 반드시 새 문단으로 뗀다.
 * - 그 안에서 세 문장마다. 문장 단위로 끊으면 문단이 잘게 부서지고, 다섯 문장을
 *   넘기면 다시 벽이 된다.
 */
export function splitOverviewParagraphs(overview: string): string[] {
  const blocks = overview
    .split(/\n{2,}|(?=[◎※])/)
    .map((block) => block.replace(/\s+/g, " ").trim())
    .filter(Boolean);

  return blocks.flatMap((block) => {
    /* 문장 끝은 "다." 뒤의 공백으로 잡는다. 마침표만 보면 "1392년 조선 건국"의
       연도 표기나 "국보 경복궁 근정전" 같은 목록 사이에서도 끊긴다. */
    const sentences = block.split(/(?<=[.!?])\s+/).filter(Boolean);
    const paragraphs: string[] = [];
    for (let index = 0; index < sentences.length; index += 3) {
      paragraphs.push(sentences.slice(index, index + 3).join(" "));
    }
    return paragraphs.length > 0 ? paragraphs : [block];
  });
}
