/*
 * 역할: 공공데이터를 쓴 화면에 출처를 밝힌다.
 * 입력: 영어 화면 여부, 위쪽 여백을 줄지 여부.
 * 출력: "출처: ⓒ한국관광공사" · "출처: ⓒ서울 실시간 도시데이터" 한 줄.
 * 호출 시점: 답변 카드·상세 모달·추천 결과처럼 공공데이터 값이 실린 자리의 맨 아래.
 *
 * 공공데이터포털 이용 조건상 이 데이터를 화면에 쓰면 출처를 밝혀야 한다. 본문을
 * 방해하지 않도록 가장 작은 글씨·흐린 색으로 맨 아래에만 둔다 — 읽을거리가
 * 아니라 표기이기 때문이다.
 *
 * 두 기관의 표기를 같은 모양·같은 자리에 둔다. 예전에는 서울시만 본문 중간에
 * 파란 칩("서울시 데이터")으로 떠서, 같은 뜻의 표기가 화면마다 다른 것처럼
 * 보였다(2026-09-16 사용자 지적).
 */

export function TourApiSourceNote({ isEn = false }: { isEn?: boolean }) {
  return (
    <p className="text-[11px] leading-4 text-muted">
      {isEn ? "Source: ⓒ Korea Tourism Organization" : "출처: ⓒ한국관광공사"}
    </p>
  );
}

/*
 * 서울시 실시간 도시데이터. 이름은 "서울시 데이터"가 아니라 데이터셋 이름
 * 그대로 쓴다 — 서울시가 내는 데이터는 여럿이고, 이 화면의 값은 그중 실시간
 * 도시데이터 한 종류에서만 온다.
 */
export function SeoulRealtimeSourceNote({ isEn = false }: { isEn?: boolean }) {
  return (
    <p className="text-[11px] leading-4 text-muted">
      {isEn ? "Source: ⓒ Seoul Real-time City Data" : "출처: ⓒ서울 실시간 도시데이터"}
    </p>
  );
}
