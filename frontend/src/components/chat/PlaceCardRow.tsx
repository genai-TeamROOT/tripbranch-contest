/*
 * 역할: PlaceCard 목록을 가로로 스크롤·드래그할 수 있는 한 줄로 그린다(DESIGN_SYSTEM.md §6.5).
 * 입력: 캡션 텍스트와 자식으로 넘기는 PlaceCard(li) 목록.
 * 출력: 캡션 + 가로 스크롤 목록(마우스 드래그 스크롤 지원, 터치는 네이티브 스크롤).
 * 호출 시점: RecommendationResultMessage 등 여러 장소를 한 줄로 보여주는 곳에서 호출된다.
 *
 * setPointerCapture를 쓰지 않고 window에 리스너를 다는 이유는, 캡처가 카드
 * 버튼의 클릭 타겟을 가로채기 때문이다. 드래그로 3px 이상 움직였으면 클릭을
 * 막아 "드래그 끝에 카드가 눌리는" 오작동을 방지한다.
 */

import { motion } from "framer-motion";
import { useEffect, useRef, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";

interface PlaceCardRowProps {
  caption?: string;
  /** 캡션과 같은 줄, 오른쪽에 붙는 보조설명(예: 추천 기준). caption 없이는 안 쓴다. */
  note?: string;
  children: ReactNode;
}

export function PlaceCardRow({ caption, note, children }: PlaceCardRowProps) {
  const scrollRef = useRef<HTMLUListElement | null>(null);
  const drag = useRef({ active: false, startX: 0, startScroll: 0, moved: false });

  useEffect(() => {
    const handleMove = (event: PointerEvent) => {
      if (!drag.current.active || !scrollRef.current) return;
      const dx = event.clientX - drag.current.startX;
      if (Math.abs(dx) > 3) drag.current.moved = true;
      scrollRef.current.scrollLeft = drag.current.startScroll - dx;
    };
    const handleUp = () => {
      drag.current.active = false;
    };
    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", handleUp);
    return () => {
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", handleUp);
    };
  }, []);

  const handlePointerDown = (event: ReactPointerEvent<HTMLUListElement>) => {
    if (event.pointerType !== "mouse") return;
    drag.current = {
      active: true,
      startX: event.clientX,
      startScroll: event.currentTarget.scrollLeft,
      moved: false,
    };
  };

  return (
    <section className="flex flex-col gap-1.5">
      {caption && (
        <div className="flex items-baseline justify-between gap-2 px-1">
          <p className="text-[11px] font-semibold text-muted">{caption}</p>
          {note && <p className="text-[11px] text-muted">{note}</p>}
        </div>
      )}
      <motion.ul
        ref={scrollRef}
        onPointerDown={handlePointerDown}
        className="scrollbar-none -mx-1 flex max-w-full cursor-grab select-none gap-3 overflow-x-auto px-1 pb-1 active:cursor-grabbing"
        initial="hidden"
        animate="visible"
        variants={{ visible: { transition: { staggerChildren: 0.1 } } }}
      >
        {children}
      </motion.ul>
    </section>
  );
}
