/*
 * 역할: "+" 버튼으로 고른 사진과 분위기가 닮은 장소를 찾아 대화에 붙인다.
 * 입력: 없음(TripContext에서 기기 위치를 읽는다).
 * 출력: ChatComposer의 onPhotoSelect에 그대로 넘길 수 있는 함수.
 * 호출 시점: ChatPage와 DeveloperChatPage가 입력창을 조립할 때.
 *
 * 두 화면이 같은 동작을 해야 해서 훅으로 뺐다 — 한쪽만 고치면 개발자 화면에서
 * 재현한 것이 사용자 화면과 달라진다.
 *
 * **위치를 정하는 규칙은 일반 채팅과 같다.** 다른 것은 텍스트냐 사진이냐뿐이다 —
 * 위치 설정 화면의 검색 기준(center) → 출발지(origin) → 기기 GPS 순으로 쓴다.
 * 채팅이 selected_search_center·selected_current_location·device_location을
 * 함께 보내는 것과 같은 순서다(ChatPage·HomePage).
 *
 * 예전에는 위치 설정을 아예 안 읽고 기기 좌표만 보냈다. 그래서 검색 기준을
 * "성수동"으로 정해 둔 사용자가 사진을 올려도 그 값이 요청에 실리지 않았고,
 * 서버도 알 길이 없어(세션 조건은 채팅을 보내야 채워진다) 위치를 정했는데도
 * "어디 근처에서 찾을까요?"가 나왔다.
 *
 * **위치가 하나도 없으면 기기 위치를 한 번 물어본다.** 채팅도 같은 자리에서
 * 같은 일을 한다(HomePage). 거절당하면 요청을 보내지 않고 화면에서 위치를
 * 먼저 정하도록 안내한다 — 보내봐야 서버가 location_required로 되돌려줄 뿐이고,
 * 그 되돌림은 오류 배너로 나와서 사용자가 할 수 있는 일이 없다.
 *
 * START_PHOTO_SIMILAR 디스패치(대화에 메시지가 생기는 시점)는 축소본을 만들기
 * *전에* 동기적으로 실행된다 — HomePage가 발화 없이 사진만 고르고 바로 /chat으로
 * 넘어가는 경로에서, ChatPage가 마운트 시점에 hasConversation을 확인해 대화가
 * 없으면 "/"로 되돌리기 때문이다(ChatPage.tsx). 축소본 생성(createImageBitmap)이
 * 이 dispatch보다 늦게 끝나면, 그 순간엔 아직 메시지가 없어 홈으로 튕겨 나갔다.
 * 축소본은 뒤늦게 채워도 안전하므로(SET_PHOTO_SIMILAR_IMAGE) 순서를 바꿨다.
 */

import { useCallback } from "react";
import { ApiError } from "../api/client";
import { searchPlacesByPhoto } from "../api/trip";
import { loadLocationSettings } from "../state/locationSettings";
import { useTripDispatch, useTripState } from "../state/TripContext";
import { getBrowserDeviceLocation } from "../utils/geolocation";
import { createThumbnailDataUrl } from "../utils/imageThumbnail";

export function usePhotoSimilarSearch() {
  const state = useTripState();
  const dispatch = useTripDispatch();

  return useCallback(
    async (file: File) => {
      /* 위치 설정 화면이 정한 값이다. 대화가 위치를 잡으면 매 턴 여기로 되돌아와
         갱신되므로(syncLocationSettingsFromConditions), 앞 턴에서 "안국역"이라고
         말했으면 center에 그 값이 들어 있다. */
      const settings = loadLocationSettings();
      const locationQuery = settings.center ?? settings.origin;

      dispatch({ type: "CLEAR_ERROR" });

      // 사진을 먼저 띄운다(축소본 없이). 응답이 1~2초라 아무것도 없으면 멈춘 것처럼
      // 보인다 — 위 주석대로 이 dispatch는 축소본을 기다리지 않는다.
      const messageId = `photo-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      dispatch({ type: "START_PHOTO_SIMILAR", payload: { messageId, imageUrl: null } });

      // 축소본은 준비되는 대로 뒤늦게 채운다. 실패해도(HEIC 등) 검색 자체는 이미
      // 시작됐으니 그대로 진행한다.
      void createThumbnailDataUrl(file).then((imageUrl) => {
        if (imageUrl)
          dispatch({ type: "SET_PHOTO_SIMILAR_IMAGE", payload: { messageId, imageUrl } });
      });

      /* 지명이 하나도 없을 때만 기기 위치를 묻는다 — 설정해 둔 사용자에게 권한
         팝업을 띄울 이유가 없다(HomePage가 origin을 보고 같은 판단을 한다).
         이 호출은 사용자가 사진을 고른 직후라 브라우저가 팝업을 정상적으로
         띄운다. 위 dispatch는 동기라 그 사이에 끼어들지 않는다. */
      let deviceLocation = state.device_location;
      if (!locationQuery && !deviceLocation) {
        try {
          deviceLocation = await getBrowserDeviceLocation();
        } catch {
          deviceLocation = null;
        }
      }

      // "37.5788,126.9770" 형식이다. 값이 없거나 깨졌으면 좌표를 안 보낸다 —
      // NaN을 실어 보내면 서버가 좌표가 있는 줄 알고 되묻지 않는다.
      const [latitude, longitude] = (deviceLocation ?? "")
        .split(",")
        .map((value) => Number(value.trim()));
      const hasCoordinates = Number.isFinite(latitude) && Number.isFinite(longitude);

      /* 보낼 위치가 없으면 요청하지 않는다. 서버는 location_required로 되돌려줄
         뿐이고 그것은 오류 배너로 나와서 사용자가 할 수 있는 일이 없다. 대신
         화면에서 위치를 먼저 정하도록 안내한다. */
      if (!locationQuery && !hasCoordinates) {
        dispatch({ type: "PHOTO_SIMILAR_NEEDS_LOCATION", payload: { messageId } });
        return;
      }

      try {
        const response = await searchPlacesByPhoto({
          image: file,
          // 앞 턴에서 "안국역"이라고 말했으면 서버가 그 위치를 이어받는다.
          // 좌표는 대화가 위치를 안 잡았을 때의 기본값이다.
          sessionId: state.session_id,
          locationQuery,
          latitude: hasCoordinates ? latitude : null,
          longitude: hasCoordinates ? longitude : null,
          // 다섯 곳만 보여준다. 일반 추천과 개수를 맞추고, 무엇보다 **품질이
          // 아래로 갈수록 떨어진다** — 사람 눈가림 채점에서 상위 3곳과 5곳의
          // 성적 차이가 뚜렷했다. 서버 기본값은 10이라 이 줄이 없으면 재본 적
          // 없는 6~10위까지 화면에 실린다.
          limit: 5,
        });
        dispatch({
          type: "RESOLVE_PHOTO_SIMILAR",
          payload: {
            messageId,
            sessionId: response.session_id,
            centerName: response.center_name,
            places: response.places,
            candidateCount: response.candidate_count,
            elapsedMs: response.elapsed_ms,
          },
        });
      } catch (error) {
        dispatch({ type: "FAIL_PHOTO_SIMILAR", payload: { messageId } });
        /* 사유는 올린 사진 바로 아래에 붙는다. 다시 보낼 발화가 없으므로(파일이라
           상태에 안 남는다) retryInput은 주지 않는다 — 사진을 다시 고르면 된다. */
        dispatch({
          type: "FAIL_TURN",
          payload: {
            message: error instanceof ApiError ? error.message : "사진으로 장소를 찾지 못했어요.",
          },
        });
      }
    },
    [dispatch, state.device_location, state.session_id],
  );
}
