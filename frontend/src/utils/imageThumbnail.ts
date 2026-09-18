/*
 * 역할: 올린 사진을 대화에 보여줄 작은 미리보기로 줄인다.
 * 입력: 사용자가 고른 파일.
 * 출력: data URL 문자열.
 *
 * **`URL.createObjectURL`을 쓰지 않는 이유**는 그 주소가 탭 수명에 묶여 있기
 * 때문이다. 대화는 sessionStorage에 저장돼 새로고침 뒤에 복원되는데, 그때
 * object URL은 이미 무효라 깨진 이미지가 남는다.
 *
 * data URL은 문자열이라 그대로 저장된다. 대신 원본을 그대로 담으면
 * sessionStorage 한도(보통 5MB)를 넘기므로 긴 변을 기준으로 줄인다 — 대화
 * 말풍선에 보이는 크기라 원본 해상도가 필요 없다.
 */

/** 긴 변 기준 최대 픽셀. 말풍선 폭이 이보다 크지 않다. */
const MAX_EDGE = 320;

/** JPEG 품질. 0.7이면 320px에서 대략 15~25KB다. */
const QUALITY = 0.7;

export async function createThumbnailDataUrl(file: File): Promise<string | null> {
  try {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, MAX_EDGE / Math.max(bitmap.width, bitmap.height));
    const width = Math.round(bitmap.width * scale);
    const height = Math.round(bitmap.height * scale);

    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context) return null;
    context.drawImage(bitmap, 0, 0, width, height);
    bitmap.close();

    return canvas.toDataURL("image/jpeg", QUALITY);
  } catch {
    // 미리보기는 없어도 검색은 되어야 한다. HEIC처럼 브라우저가 못 여는 형식이
    // 있고, 그때 사진 검색까지 막을 이유가 없다.
    return null;
  }
}
