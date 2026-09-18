/*
 * 역할: 길어서 접어 둔 글을 펴고 다시 접는 버튼.
 * 입력: 지금 펴져 있는지, 토글 콜백, 영어 화면 여부.
 * 출력: 화살표가 뒤집히는 회색 알약 버튼.
 * 호출 시점: 답변 카드의 개요, 상세 모달의 개요처럼 접히는 글 아래.
 *
 * 브랜드 색 밑줄 대신 회색 알약이다. 파란 밑줄은 이 화면에서 "누르면 다른 곳으로
 * 간다"(장소 상세보기·홈페이지)는 뜻으로 이미 쓰고 있어서, 같은 자리에서 글만
 * 펴는 동작에 쓰면 어디로 이동하는 링크처럼 읽힌다. 이 버튼은 본문을 읽는 흐름을
 * 끊지 않아야 해서 눈에 덜 띄는 색을 쓰고, 방향은 화살표가 말한다.
 */

import { ChevronDown } from "lucide-react";

export function CollapseToggleButton({
  expanded,
  onToggle,
  isEn,
}: {
  expanded: boolean;
  onToggle: () => void;
  isEn: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={expanded}
      /* 오른쪽 끝에 붙는다. 본문 왼쪽 끝에 두면 다음 문단의 첫 글자와
         같은 선에 놓여 글의 일부로 읽혔다 — 이건 읽을 문장이 아니라 누르는
         것이다. 글이 끝나는 쪽에 두면 다 읽고 눈이 도착하는 자리와도 맞다. */
      className="inline-flex w-fit self-end items-center gap-1 rounded-full bg-chip px-2.5 py-1 text-xs font-medium text-muted transition-colors hover:bg-border hover:text-ink"
    >
      {expanded ? (isEn ? "Show less" : "접기") : isEn ? "Show more" : "더 보기"}
      <ChevronDown
        size={13}
        aria-hidden
        className={`transition-transform ${expanded ? "rotate-180" : ""}`}
      />
    </button>
  );
}
