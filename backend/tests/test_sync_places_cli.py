from __future__ import annotations

import pytest

from app.config import Settings
from scripts.sync_places import build_parser, run


def test_sync_places_parser_accepts_safety_options() -> None:
    args = build_parser().parse_args(
        [
            "--area-code",
            "11",
            "--district-code",
            "110",
            "--dry-run",
            "--details-limit",
            "3",
            "--force-details",
        ]
    )

    assert args.area_code == "11"
    assert args.district_code == "110"
    assert args.dry_run is True
    assert args.details_limit == 3
    assert args.force_details is True


@pytest.mark.asyncio
async def test_sync_places_requires_server_secrets_before_network() -> None:
    settings = Settings(
        _env_file=None,
        tour_api_service_key="",
        supabase_url="https://project.supabase.co",
        supabase_secret_key="",
    )
    args = build_parser().parse_args(["--dry-run"])

    with pytest.raises(ValueError, match="TOUR_API_SERVICE_KEY"):
        await run(args, settings)


@pytest.mark.asyncio
async def test_sync_places_passes_closure_extractor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """명령행 동기화도 휴무 추출기를 넘기는지. (TP-231)

    반영 경로가 둘이라(개발 패널과 이 스크립트) 한쪽만 넘기면 어느 쪽으로
    돌렸느냐에 따라 저장되는 휴무가 달라진다.
    """
    from scripts import sync_places

    sentinel = object()
    captured: dict[str, object] = {}

    class _FakeService:
        def __init__(self, *args, **kwargs) -> None:
            captured.update(kwargs)

        async def sync(self, *args, **kwargs):
            raise _Done

    class _Done(Exception):
        pass

    monkeypatch.setattr(sync_places, "PlaceSyncService", _FakeService)
    monkeypatch.setattr(sync_places, "get_closure_extractor", lambda: sentinel)

    settings = Settings(
        _env_file=None,
        tour_api_service_key="present",
        supabase_url="https://project.supabase.co",
        supabase_secret_key="present",
    )
    args = build_parser().parse_args(["--dry-run"])

    with pytest.raises(_Done):
        await run(args, settings)

    assert captured["closure_extractor"] is sentinel
