"""헬스체크 엔드포인트 회귀 테스트.

역할: FastAPI 앱이 /api/health에서 표준 성공 응답을 반환하는지 검증한다.
입력: TestClient가 보내는 GET /api/health 요청.
출력: 상태 코드와 JSON payload에 대한 pytest assertion.
호출 시점: 로컬 테스트와 CI에서 pytest 실행 시 호출된다.
TODO: 운영 의존성이 생기면 실패 상태 헬스체크 테스트를 추가한다.
"""

from fastapi.testclient import TestClient

from app.main import app


def test_health() -> None:
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
