"""외부 API 거절 응답에서 원인 식별에 필요한 필드만 뽑는 공용 헬퍼.

역할: 4xx/5xx 응답 본문에서 upstream이 준 에러 코드·메시지를 추출한다.
입력: httpx.Response.
출력: 로그에 그대로 넣을 수 있는 한 줄 문자열.
호출 시점: provider가 HTTPStatusError를 잡아 로그를 남길 때 사용한다.

data.go.kr(기상청·KASI·TourAPI)은 인증 실패·활용기간 만료·트래픽 초과를 모두
같은 4xx로 내리고 구분은 본문 errMsg/returnReasonCode에만 담는다. Naver는
errorCode/errorMessage를 쓴다. 상태 코드만 봐서는 원인을 가릴 수 없어서 본문을
봐야 하지만, 통째로 남기면 불필요한 정보까지 로그에 들어가므로 알려진 키만 뽑는다.
"""

from __future__ import annotations

import re

import httpx

# JSON과 XML 응답 양쪽에서 같은 패턴으로 뽑는다 — data.go.kr은 dataType에 따라
# 둘 다 내려주고, 오류 응답은 요청한 형식을 무시하고 XML로 오는 경우도 있다.
_ERROR_KEYS = (
    "errMsg",
    "returnReasonCode",
    "returnAuthMsg",
    "resultCode",
    "resultMsg",
    "errorCode",
    "errorMessage",
    # Naver는 {"error":{"errorCode":.., "message":.., "details":..}} 형태로 준다.
    "message",
    "details",
)
_MAX_FALLBACK_CHARS = 200

# data.go.kr이 트래픽 초과를 두 종류로 나눠 내려준다. 둘 다 HTTP 429라 상태 코드로는
# 구분되지 않는다(2026-08-10 실측).
#   22 LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR            일일 한도
#   23 LIMITED_NUMBER_OF_SERVICE_REQUESTS_PER_SECOND_EXCEEDS_ERROR 초당 한도
# 구분이 중요한 이유는 대응이 반대이기 때문이다 — 초당 한도는 쉬었다 다시 부르면
# 성공하지만, 일일 한도는 그날 안에는 무엇을 해도 실패한다. 뭉뚱그리면 소진된 뒤에도
# 재시도를 계속 던져 한도를 더 빨리 태운다.
DAILY_QUOTA_REASON_CODE = "22"
PER_SECOND_LIMIT_REASON_CODE = "23"
_REASON_CODE_PATTERN = re.compile(r'[<"]returnReasonCode[>"]\s*:?\s*"?(\d+)')


def upstream_reason_code(detail: str) -> str | None:
    """`upstream_error_detail()`이 만든 문자열에서 returnReasonCode만 되찾는다.

    provider가 예외를 던질 때 이미 detail 문자열로 눌러 담기 때문에, 소비 측은
    응답 객체가 아니라 그 문자열에서 코드를 읽어야 한다.
    """
    match = _REASON_CODE_PATTERN.search(detail) or re.search(
        r"returnReasonCode=(\d+)", detail
    )
    return match.group(1) if match else None


def is_daily_quota_exceeded(detail: str) -> bool:
    """일일 한도 소진인지 판정한다. 초당 한도(23)와 구분한다."""
    return upstream_reason_code(detail) == DAILY_QUOTA_REASON_CODE


def upstream_error_detail(response: httpx.Response) -> str:
    """거절 응답에서 알려진 에러 필드만 뽑아 한 줄로 만든다."""
    try:
        text = response.text
    except Exception:  # noqa: BLE001 - 본문 접근 실패가 로깅 자체를 막으면 안 된다.
        return "body=<unavailable>"

    fields = {
        key: match.group(1).strip()
        for key in _ERROR_KEYS
        if (match := re.search(rf'[<"]{key}[>"]\s*:?\s*"?([^"<,}}]+)', text))
    }
    if fields:
        return ", ".join(f"{key}={value}" for key, value in fields.items())
    return f"body={' '.join(text.split())[:_MAX_FALLBACK_CHARS]}"
