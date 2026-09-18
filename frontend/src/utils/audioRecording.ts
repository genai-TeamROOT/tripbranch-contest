/*
 * 역할: 브라우저 MediaRecorder가 만든 녹음 파일을 Gemini 전송용 WAV로 정규화한다.
 * 입력: webm/mp4 등 브라우저별 녹음 Blob.
 * 출력: 16-bit PCM mono WAV Blob.
 * 호출 시점: 사용자가 음성 입력 녹음을 끝냈을 때.
 *
 * Gemini 공식 문서의 지원 형식에 WAV가 명시돼 있다. MediaRecorder의 기본 webm은
 * 브라우저마다 다르고 공식 목록에 없으므로, API에 보내기 전에 여기서 WAV로 바꾼다.
 */

const WAV_MIME_TYPE = "audio/wav";

export async function toWavBlob(recording: Blob): Promise<Blob> {
  const encodedAudio = await recording.arrayBuffer();
  const context = new AudioContext();
  try {
    const decoded = await context.decodeAudioData(encodedAudio.slice(0));
    return new Blob([encodeMonoWav(decoded)], { type: WAV_MIME_TYPE });
  } finally {
    await context.close();
  }
}

function encodeMonoWav(audio: AudioBuffer): ArrayBuffer {
  const sampleCount = audio.length;
  const bytesPerSample = 2;
  const headerBytes = 44;
  const buffer = new ArrayBuffer(headerBytes + sampleCount * bytesPerSample);
  const view = new DataView(buffer);

  writeAscii(view, 0, "RIFF");
  view.setUint32(4, 36 + sampleCount * bytesPerSample, true);
  writeAscii(view, 8, "WAVE");
  writeAscii(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, audio.sampleRate, true);
  view.setUint32(28, audio.sampleRate * bytesPerSample, true);
  view.setUint16(32, bytesPerSample, true);
  view.setUint16(34, 16, true);
  writeAscii(view, 36, "data");
  view.setUint32(40, sampleCount * bytesPerSample, true);

  const channels = Array.from({ length: audio.numberOfChannels }, (_, index) =>
    audio.getChannelData(index),
  );
  for (let index = 0; index < sampleCount; index += 1) {
    const mixed = channels.reduce((sum, channel) => sum + channel[index], 0) / channels.length;
    const clamped = Math.max(-1, Math.min(1, mixed));
    view.setInt16(44 + index * bytesPerSample, clamped * 0x7fff, true);
  }
  return buffer;
}

function writeAscii(view: DataView, offset: number, value: string) {
  for (let index = 0; index < value.length; index += 1) {
    view.setUint8(offset + index, value.charCodeAt(index));
  }
}
