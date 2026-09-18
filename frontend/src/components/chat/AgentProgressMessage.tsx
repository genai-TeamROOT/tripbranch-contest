/*
 * 역할: 답변을 기다리는 동안 지금 무슨 일이 일어나는지 한 줄로 알려준다.
 * 입력: 실제 SSE progress 이벤트(있으면), 일정 편성 여부, 언어.
 * 출력: 문구 한 줄(role=status). 박스·체크리스트·경과 시간은 두지 않는다.
 * 호출 시점: ChatMessageList가 응답 대기 중일 때.
 *
 * 카드형 진행 상황 UI를 걷어내고 문구만 남겼다 — 대기 화면에서까지 단계
 * 목록과 초시계를 보여줄 이유가 없다. 정확한 단계별 소요시간은 응답이 끝난 뒤
 * 개발자 Audit의 소요시간 탭에서 확인한다.
 *
 * 한 단계 안에서도 문구가 계속 바뀐다. 서버가 한 단계에서 몇 초씩 머물 때
 * (특히 LLM·일정 편성) 화면이 멈춘 것처럼 보이지 않게 하려는 것으로, 기존
 * 카드 UI가 단계를 순차적으로 굴리던 것과 같은 목적이다.
 */

import { useEffect, useState } from "react";
import type { AgentProgressEvent, Language } from "../../types";

/** 서버가 알려주는 단계 순서. 실제 progress 이벤트가 없을 때 이 순서대로 넘어간다. */
const STAGE_ORDER = [
  "interpreting",
  "merging_conditions",
  "fetching_context",
  "scoring",
  "composing_message",
] as const;

type Stage = (typeof STAGE_ORDER)[number] | "scheduling";

const STAGE_LINES: Record<Stage, readonly string[]> = {
  interpreting: ["무슨 말씀인지 곰곰이 읽는 중…", "요청을 요모조모 뜯어보는 중…"],
  merging_conditions: ["아까 하신 말씀도 같이 챙기는 중…", "조건들을 한 줄로 세우는 중…"],
  fetching_context: [
    "서울 구석구석 뒤지는 중…",
    "문 열었는지 하나씩 확인하는 중…",
    "하늘도 한 번 올려다보는 중…",
  ],
  scoring: ["어디가 제일 나은지 저울질하는 중…", "순위표를 고쳐 쓰는 중…"],
  scheduling: ["지도를 접었다 폈다 하는 중…", "몇 시에 어디 있을지 세어보는 중…"],
  composing_message: ["예쁘게 정리해서 말씀드릴 준비 중…", "마지막으로 다시 읽어보는 중…"],
};

const STAGE_LINES_EN: Record<Stage, readonly string[]> = {
  interpreting: ["Reading your message closely…", "Turning your request over…"],
  merging_conditions: ["Remembering what you said earlier…", "Lining up your conditions…"],
  fetching_context: [
    "Digging around Seoul…",
    "Checking which doors are open…",
    "Glancing up at the sky…",
  ],
  scoring: ["Weighing which place wins…", "Rewriting the ranking…"],
  scheduling: ["Folding and unfolding the map…", "Counting out where you'll be, and when…"],
  composing_message: ["Wrapping it up nicely…", "Giving it one last read…"],
};

/** 문구가 바뀌는 주기. 단계 전환 주기도 겸한다(실제 progress가 없을 때). */
const LINE_ROTATION_INTERVAL_MS = 1_800;

export function AgentProgressMessage({
  schedulePlanning = false,
  progress = null,
  language = "ko",
}: {
  /** 실제 SCHEDULE 플래너 호출 이벤트를 받은 뒤에만 일정 단계를 순서에 넣는다. */
  schedulePlanning?: boolean;
  /**
   * 실제 SSE progress 이벤트. 값이 있으면 그 단계의 문구를 보여준다. 이벤트가
   * 아직 없거나(스트리밍 시작 직전) SSE를 못 쓰는 환경(구버전 배포·프록시라
   * 단발 POST /chat로 폴백한 경우, trip.ts 참고)엔 null로 유지되고, 그때만
   * 시간에 따라 단계를 넘긴다.
   */
  progress?: AgentProgressEvent | null;
  language?: Language;
}) {
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setTick((value) => value + 1);
    }, LINE_ROTATION_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, []);

  const stages: Stage[] = schedulePlanning
    ? [...STAGE_ORDER.slice(0, -1), "scheduling", STAGE_ORDER.at(-1)!]
    : [...STAGE_ORDER];
  const liveStage = progress?.stage as Stage | undefined;
  const stage =
    liveStage && (STAGE_LINES[liveStage] as readonly string[] | undefined)
      ? liveStage
      : stages[Math.min(tick, stages.length - 1)];

  const lines = (language === "en" ? STAGE_LINES_EN : STAGE_LINES)[stage];

  return (
    <p role="status" aria-live="polite" className="mr-auto animate-pulse text-sm text-muted">
      {lines[tick % lines.length]}
    </p>
  );
}
