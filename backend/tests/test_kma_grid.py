from __future__ import annotations

from app.providers.kma_grid import latlon_to_grid


def test_latlon_to_grid_seoul_junggu() -> None:
    assert latlon_to_grid(37.5636, 126.9976) == (60, 127)


def test_latlon_to_grid_busan() -> None:
    assert latlon_to_grid(35.1796, 129.0756) == (98, 76)


def test_latlon_to_grid_returns_ints_within_korea_bounds() -> None:
    nx, ny = latlon_to_grid(36.5, 127.5)
    assert isinstance(nx, int)
    assert isinstance(ny, int)
    assert 0 < nx < 149
    assert 0 < ny < 253
