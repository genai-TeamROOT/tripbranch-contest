"""GET/PUT /api/favorites — 계정 단위 즐겨찾기. (위치 설정 화면, PR #361 후속)

`/api/preferences`와 같은 자리의 API다 — 신원이 곧 저장 키라 토큰이 없으면 어디에
저장할지가 정해지지 않는다. 그래서 이 파일에서 가장 중요한 것도 "토큰 없이 부르면
401"과 "남의 즐겨찾기가 보이지 않는다" 둘이다.

즐겨찾기에만 있는 것은 `search_center_name`이다 — 사용자가 이름을 바꿔도 검색에
나가는 값은 담을 때의 장소 이름이라, 그 둘이 따로 보관되는지 확인한다.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from tests.auth.conftest import make_token

ME = "3f1a9c04-0000-4000-8000-000000000001"
OTHER = "3f1a9c04-0000-4000-8000-000000000002"

FAVORITES = [
    {
        "id": "fav-1",
        "label": "회사 (역삼동)",
        "search_center_name": "역삼역",
        "address": "서울특별시 강남구 테헤란로 152",
    },
    {"id": "fav-2", "label": "안국역", "search_center_name": "안국역", "address": None},
]


def _headers(signing_key, sub: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(signing_key, sub=sub)}"}


# ---------------------------------------------------------------- 신원

def test_토큰_없이_조회하면_401(signing_key) -> None:
    response = TestClient(app).get("/api/favorites")

    assert response.status_code == 401


def test_토큰_없이_저장하면_401이고_아무것도_안_남는다(signing_key) -> None:
    client = TestClient(app)

    response = client.put("/api/favorites", json={"items": FAVORITES})

    assert response.status_code == 401
    stored = client.get("/api/favorites", headers=_headers(signing_key, ME))
    assert stored.json()["items"] == []


def test_남의_즐겨찾기는_보이지_않는다(signing_key) -> None:
    client = TestClient(app)
    client.put("/api/favorites", json={"items": FAVORITES}, headers=_headers(signing_key, ME))

    response = client.get("/api/favorites", headers=_headers(signing_key, OTHER))

    assert response.status_code == 200
    assert response.json()["items"] == []


# ---------------------------------------------------------------- 저장·조회

def test_담은_순서와_장소_이름이_그대로_보관된다(signing_key) -> None:
    client = TestClient(app)

    response = client.put(
        "/api/favorites", json={"items": FAVORITES}, headers=_headers(signing_key, ME)
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["label"] for item in items] == ["회사 (역삼동)", "안국역"]
    # 이름을 바꿔도 검색에 나가는 값은 담을 때의 장소 이름이라 따로 보관한다.
    assert items[0]["search_center_name"] == "역삼역"
    assert items[0]["address"] == "서울특별시 강남구 테헤란로 152"


def test_한_번도_담은_적_없으면_빈_목록에_updated_at이_없다(signing_key) -> None:
    """프론트가 이 None으로 "이 기기의 값을 올릴지"를 판정한다(favoritesSync).

    목록 길이로 판정하면 다른 기기에서 전부 지운 사람의 빈 목록이 낡은 로컬
    값으로 되살아난다.
    """
    # 저장소가 파일 안에서 공유되므로 아무도 저장한 적 없는 계정으로 본다.
    response = TestClient(app).get("/api/favorites", headers=_headers(signing_key, OTHER))

    assert response.json() == {"items": [], "updated_at": None}


def test_전부_지워도_저장한_적_있다는_사실은_남는다(signing_key) -> None:
    client = TestClient(app)
    client.put("/api/favorites", json={"items": FAVORITES}, headers=_headers(signing_key, ME))

    client.put("/api/favorites", json={"items": []}, headers=_headers(signing_key, ME))

    response = client.get("/api/favorites", headers=_headers(signing_key, ME))
    body = response.json()
    assert body["items"] == []
    assert body["updated_at"] is not None


def test_자유_입력으로_만든_즐겨찾기는_장소_이름이_없다(signing_key) -> None:
    """사이드바에서 이름만 받아 만든 옛 항목이다. 화면이 label로 떨어뜨린다."""
    client = TestClient(app)

    response = client.put(
        "/api/favorites",
        json={"items": [{"id": "fav-9", "label": "집 (성수동)"}]},
        headers=_headers(signing_key, ME),
    )

    item = response.json()["items"][0]
    assert item["search_center_name"] is None
    assert item["address"] is None
