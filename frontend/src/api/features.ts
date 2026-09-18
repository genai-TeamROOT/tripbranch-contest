/*
 * 역할: 서버 설정에 따라 켜지고 꺼지는 기능을 묻는다(GET /api/features).
 * 입력: 없음. 인증도 필요 없다(서버 전역 설정이다).
 * 출력: FeaturesResponse — 지금은 taste_enabled 하나다.
 * 호출 시점: FeatureFlagsProvider가 앱이 뜰 때 한 번 부른다.
 *
 * taste_enabled는 서버의 TASTE_EVIDENCE_ENABLED다. 꺼지면 서버가 후기·블로그
 * 데이터를 쓰는 기능(취향 순위·취향 태그 표·상세 카드 후기 절·AI 추천 이유)을
 * 모두 멈추므로, 화면도 "취향 설정" 메뉴와 /preferences를 숨긴다.
 */

import { apiClient } from "./client";

export type FeaturesResponse = {
  taste_enabled: boolean;
};

export async function fetchFeatures(): Promise<FeaturesResponse> {
  return apiClient.get<FeaturesResponse>("/features");
}
