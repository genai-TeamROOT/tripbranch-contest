# 지오코딩 / 날씨 API 요청·응답 샘플

`GeocodingProvider`(Naver Geocoding), `WeatherProvider`(KMA 단기예보) 구현 시
실제로 확인한 요청·응답 형태를 기록한다. Provider 코드에서 이 필드명들을 그대로
파싱하므로, 업스트림 스펙이 바뀌면 이 문서도 함께 갱신할 것.

## KMA 단기예보 조회서비스 - getUltraSrtFcst(초단기예보)

실제 서비스키로 호출해 확인함(2026-07-22, 서울 중구 nx=60, ny=127).

- 엔드포인트: `GET https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtFcst`
- 필수 쿼리 파라미터: `serviceKey`(디코딩 키), `pageNo`, `numOfRows`, `dataType=JSON`, `base_date`(YYYYMMDD), `base_time`(HHMM, 매시 30분 발표·40분부터 조회 가능), `nx`, `ny`
- 초단기실황(getUltraSrtNcst)과 달리 SKY(하늘상태)를 PTY(강수형태)와 함께 제공하므로, 날씨 판정에 필요한 두 값을 한 번의 호출로 얻을 수 있다. (작성 당시에는 C가 이 두 값으로 `good/neutral/bad`를 판정했으나, D-051에서 판정을 D로 이관하고 D-056에서 `condition` 필드를 제거했다. 지금 C는 SKY·PTY 사실만 전달한다.)

응답(일부, 카테고리당 6시간치 시간별 값이 반복됨):

```json
{
  "response": {
    "header": { "resultCode": "00", "resultMsg": "NORMAL_SERVICE" },
    "body": {
      "dataType": "JSON",
      "items": {
        "item": [
          { "baseDate": "20260722", "baseTime": "1030", "category": "LGT", "fcstDate": "20260722", "fcstTime": "1100", "fcstValue": "0", "nx": 60, "ny": 127 },
          { "baseDate": "20260722", "baseTime": "1030", "category": "PTY", "fcstDate": "20260722", "fcstTime": "1100", "fcstValue": "1", "nx": 60, "ny": 127 },
          { "baseDate": "20260722", "baseTime": "1030", "category": "RN1", "fcstDate": "20260722", "fcstTime": "1100", "fcstValue": "1mm 미만", "nx": 60, "ny": 127 },
          { "baseDate": "20260722", "baseTime": "1030", "category": "SKY", "fcstDate": "20260722", "fcstTime": "1100", "fcstValue": "4", "nx": 60, "ny": 127 }
        ]
      }
    }
  }
}
```

- 카테고리: `SKY`(1 맑음/3 구름많음/4 흐림), `PTY`(0 없음/1 비/2 비·눈/3 눈/4 소나기/5 빗방울/6 빗방울눈날림/7 눈날림), `LGT`(낙뢰), `RN1`(1시간 강수량, 숫자가 아닌 "강수없음"/"1mm 미만" 같은 문자열도 옴 - 현재 매퍼는 SKY/PTY만 사용).
- 실패 시 `response.header.resultCode`가 `"00"`이 아님. `"03"`(NODATA_ERROR)은 요청 자체는 정상이고 해당 격자·발표시각에 자료만 없는 경우라 `AppError(code="weather_no_data", retryable=False)`, 그 밖의 코드(예: `"22"` 호출 한도 초과, `"30"` 서비스키 미등록)는 `AppError(code="weather_unavailable", retryable=True)`.
- SKY/PTY 어느 한쪽도 응답에 없는 좌표(관측 범위 밖 등) → `AppError(code="weather_no_data")`.

구현: [`app/providers/weather.py`](../app/providers/weather.py), [`app/providers/kma_grid.py`](../app/providers/kma_grid.py)(위경도→nx,ny 변환).

## Naver Cloud Platform Geocoding API

- 엔드포인트: `GET https://maps.apigw.ntruss.com/map-geocode/v2/geocode`
  (레거시 `naveropenapi.apigw.ntruss.com`은 별도 구독이 필요해 `401 Permission
  Denied(errorCode 210)`가 남 - 신버전 도메인을 써야 함. 2026-07-22 실제 키로 확인.)
- 필수 헤더: `Accept: application/json`, `x-ncp-apigw-api-key-id`, `x-ncp-apigw-api-key`
- 쿼리 파라미터: `query`(필수), `count`(결과 개수, provider는 `1`로 고정해 top1을 채택)

응답 샘플(2026-07-22 실제 키로 확인):

```json
// GET ...?query=서울특별시 종로구 사직로 161&count=1
{
  "status": "OK",
  "meta": { "totalCount": 1, "count": 1 },
  "addresses": [
    {
      "roadAddress": "서울특별시 종로구 사직로 161 경복궁",
      "x": "126.9770162",
      "y": "37.5788408"
    }
  ],
  "errorMessage": ""
}

// GET ...?query=경복궁&count=1  (결과 없음)
{ "status": "OK", "meta": { "totalCount": 0, "count": 0 }, "addresses": [], "errorMessage": "" }
```

- `x`는 경도, `y`는 위도(둘 다 문자열) - 순서가 직관과 반대라 실수하기 쉬움.
- 결과 없음은 `status: "OK"` + `addresses: []`로 확인됨(HTTP 200) → `AppError(code="location_not_found")`.

**중요한 한계(2026-07-22 실제 키로 확인)**: 이 API는 도로명/지번 주소와
행정동/법정동 이름(`인사동`, `익선동`은 그대로 통함)은 인식하지만, 궁궐·공원·
상가 같은 개별 장소명(POI)은 인식하지 못한다. `경복궁`, `창덕궁`, `탑골공원`,
`강남역`, `부산 해운대`, `제주도청`, `판교역` 등은 전부 0건이었다.

**MVP 범위 결정 (2026-07-22)**: 팀 결정으로 MVP는 서울 종로구로 한정하기로
했다. 이에 따라 `RealGeocodingProvider`는 종로구의 잘 알려진 장소명을 실제
Naver Geocoding 호출로 검증된 도로명주소로 치환한 뒤 조회하는 별칭 테이블
(`_JONGNO_LANDMARK_ADDRESS_ALIASES`)을 둔다. 예:

| 질의 | 치환된 주소 | 확인된 결과 |
| --- | --- | --- |
| 경복궁 / 광화문 | 서울특별시 종로구 사직로 161 | `...사직로 161 경복궁` (37.5788408, 126.9770162) |
| 창덕궁 | 서울특별시 종로구 율곡로 99 | `...율곡로 99 창덕궁` (37.5826041, 126.9919376) |
| 탑골공원 | 서울특별시 종로구 종로 99 | `...종로 99 탑골공원` (37.5711236, 126.9886480) |

종로구 범위 밖 장소명이나 별칭 테이블에 없는 장소명은 여전히 지원하지 않는다
(주소를 직접 입력하면 됨). 범위를 넓힐 때는 지역검색 API(Naver 별도 앱/Kakao
Local 등)를 보조로 붙이는 걸 고려할 것. `FakeGeocodingProvider`는 로컬
개발용으로 같은 종로구 장소들을 고정 좌표로 매핑해 둔다.

구현: [`app/providers/geocoding.py`](../app/providers/geocoding.py).
