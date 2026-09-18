/*
 * 역할: 무음 감지/10초 무음 타임아웃/60초 상한(자동 종료 → 즉시 전송)과 정지 버튼
 * 수동 종료(→ 입력창에 텍스트만 채움) 분기를 검증한다.
 * getUserMedia/MediaRecorder/AudioContext/requestAnimationFrame을 jsdom에 없는
 * 브라우저 API라 이 파일에서 직접 가짜 구현으로 스텁한다.
 *
 * 클릭은 userEvent 대신 fireEvent를 쓴다 — userEvent의 fake-timer 연동은 내부적으로
 * "다음 타이머까지 통째로 진행"하는 방식이라, 이 컴포넌트처럼 requestAnimationFrame을
 * setTimeout으로 스텁해 계속 재귀 예약하는 루프가 있으면 10초 무음 타임아웃까지
 * 한 번에 건너뛰어버린다(실측: 클릭 한 번에 없어야 할 자동 종료가 발생).
 */

import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { transcribeAudio } from "../../api/trip";
import { VoiceInputButton } from "./VoiceInputButton";

vi.mock("../../utils/audioRecording", () => ({
  toWavBlob: vi.fn().mockResolvedValue(new Blob(["wav"], { type: "audio/wav" })),
}));
vi.mock("../../api/trip", () => ({
  transcribeAudio: vi.fn(),
}));

// checkVolume 루프가 매 tick 읽는 "현재 마이크 진폭"을 테스트에서 직접 조절한다.
let currentAmplitude = 0;

class FakeAnalyser {
  fftSize = 1_024;
  getByteTimeDomainData(samples: Uint8Array) {
    const offset = Math.round(currentAmplitude * 128);
    samples.fill(128 + offset);
  }
}

class FakeAudioContext {
  createAnalyser() {
    return new FakeAnalyser();
  }
  createMediaStreamSource() {
    return { connect: () => {} };
  }
  close() {
    return Promise.resolve();
  }
}

class FakeMediaRecorder {
  static isTypeSupported() {
    return true;
  }
  state: "inactive" | "recording" = "inactive";
  mimeType = "audio/webm";
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onerror: (() => void) | null = null;
  onstop: (() => void) | null = null;

  start() {
    this.state = "recording";
  }

  stop() {
    if (this.state !== "recording") return;
    this.state = "inactive";
    this.ondataavailable?.({ data: new Blob(["audio"], { type: "audio/webm" }) });
    setTimeout(() => this.onstop?.(), 0);
  }
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function click() {
  await act(async () => {
    fireEvent.click(getButton());
    // getUserMedia는 이미 resolve된 mock이지만, startRecording()의 await 이후
    // 이어지는 동기 코드(recorder.start/monitorSilence/타이머 등록)가 실제로
    // 끝날 때까지 마이크로태스크를 몇 차례 흘려보내야 한다 — 한 번만 흘리면
    // 아직 타이머가 등록되기 전에 advance()가 실행돼 큰 시간 점프를 그냥
    // 건너뛰어버리는 문제가 있었다(실측).
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

function getButton() {
  return screen.getByRole("button");
}

describe("VoiceInputButton", () => {
  beforeEach(() => {
    currentAmplitude = 0;
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "performance"] });
    vi.stubGlobal("MediaRecorder", FakeMediaRecorder as unknown as typeof MediaRecorder);
    vi.stubGlobal("AudioContext", FakeAudioContext as unknown as typeof AudioContext);
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) =>
      setTimeout(() => callback(performance.now()), 16) as unknown as number,
    );
    vi.stubGlobal("cancelAnimationFrame", (handle: number) => clearTimeout(handle));
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: vi.fn().mockResolvedValue({
          getTracks: () => [{ stop: vi.fn() }],
        }),
      },
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("10초간 아무 말도 없으면 인식 실패로 자동 종료하고, 자동 전송도 수동 정지도 하지 않는다", async () => {
    const onError = vi.fn();
    const onAutoSubmit = vi.fn();
    const onManualStop = vi.fn();
    render(
      <VoiceInputButton
        onTranscript={vi.fn()}
        onAutoSubmit={onAutoSubmit}
        onManualStop={onManualStop}
        onError={onError}
      />,
    );

    await click();
    await advance(10_050);

    expect(onError).toHaveBeenCalledWith("음성을 인식하지 못했어요. 조금 더 또렷하게 말씀해 주세요.");
    expect(onAutoSubmit).not.toHaveBeenCalled();
    expect(onManualStop).not.toHaveBeenCalled();
    expect(vi.mocked(transcribeAudio)).not.toHaveBeenCalled();
  });

  it("말한 뒤 자연스럽게 조용해지면(무음 감지) 즉시 자동 전송한다", async () => {
    vi.mocked(transcribeAudio).mockResolvedValue({
      text: "종로3가역 맛집 추천해줘",
      elapsed_ms: 120,
      model: "gemini",
    });
    const onAutoSubmit = vi.fn();
    const onManualStop = vi.fn();
    render(
      <VoiceInputButton onTranscript={vi.fn()} onAutoSubmit={onAutoSubmit} onManualStop={onManualStop} />,
    );

    await click();
    currentAmplitude = 0.5;
    await advance(50);
    currentAmplitude = 0;
    await advance(1_300);

    expect(onAutoSubmit).toHaveBeenCalledWith("종로3가역 맛집 추천해줘");
    expect(onManualStop).not.toHaveBeenCalled();
  });

  it("60초 상한까지 계속 말해도 자동 종료로 취급해 즉시 전송한다", async () => {
    vi.mocked(transcribeAudio).mockResolvedValue({
      text: "계속 말하는 중",
      elapsed_ms: 200,
      model: "gemini",
    });
    const onAutoSubmit = vi.fn();
    const onManualStop = vi.fn();
    render(
      <VoiceInputButton onTranscript={vi.fn()} onAutoSubmit={onAutoSubmit} onManualStop={onManualStop} />,
    );

    await click();
    currentAmplitude = 0.5;
    await advance(60_050);

    expect(onAutoSubmit).toHaveBeenCalledWith("계속 말하는 중");
    expect(onManualStop).not.toHaveBeenCalled();
  });

  it("녹음 중 정지 버튼을 누르면 인식된 텍스트로 수동 정지 콜백만 호출하고 자동 전송하지 않는다", async () => {
    vi.mocked(transcribeAudio).mockResolvedValue({
      text: "여기까지 인식된 내용",
      elapsed_ms: 90,
      model: "gemini",
    });
    const onAutoSubmit = vi.fn();
    const onManualStop = vi.fn();
    render(
      <VoiceInputButton onTranscript={vi.fn()} onAutoSubmit={onAutoSubmit} onManualStop={onManualStop} />,
    );

    await click();
    currentAmplitude = 0.5;
    await advance(50);
    await click();
    await advance(0);

    expect(onManualStop).toHaveBeenCalledWith("여기까지 인식된 내용");
    expect(onAutoSubmit).not.toHaveBeenCalled();
  });

  it("말 한마디 없이 바로 정지 버튼을 눌러도 인식 실패로 처리한다", async () => {
    const onError = vi.fn();
    const onAutoSubmit = vi.fn();
    const onManualStop = vi.fn();
    render(
      <VoiceInputButton
        onTranscript={vi.fn()}
        onAutoSubmit={onAutoSubmit}
        onManualStop={onManualStop}
        onError={onError}
      />,
    );

    await click();
    await click();
    await advance(0);

    expect(onError).toHaveBeenCalledWith("음성을 인식하지 못했어요. 조금 더 또렷하게 말씀해 주세요.");
    expect(onAutoSubmit).not.toHaveBeenCalled();
    expect(onManualStop).not.toHaveBeenCalled();
  });

  it("녹음 중에는 정지 아이콘/문구로 바뀌고, 전사 중에는 버튼을 비활성화한다", async () => {
    let resolveTranscribe: (value: { text: string; elapsed_ms: number; model: string }) => void =
      () => {};
    vi.mocked(transcribeAudio).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveTranscribe = resolve;
        }),
    );
    render(<VoiceInputButton onTranscript={vi.fn()} onManualStop={vi.fn()} />);

    await click();
    expect(getButton()).not.toBeDisabled();
    expect(getButton()).toHaveAttribute("title", "눌러서 녹음 마치기");

    currentAmplitude = 0.5;
    await advance(50);
    await click();
    await advance(0);

    expect(getButton()).toBeDisabled();

    resolveTranscribe({ text: "완료", elapsed_ms: 10, model: "gemini" });
    await advance(0);

    expect(getButton()).not.toBeDisabled();
  });
});
