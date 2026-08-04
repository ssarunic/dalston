"""Cache headers on the web console's unhashed entry point.

index.html names the content-hashed JS bundle, so it is the one file that
must never be served stale: if a browser reuses a cached index.html after a
deploy, it also reuses the bundle that index.html names and never discovers
the new one. Starlette's FileResponse sets only etag/last-modified, which
leaves browsers applying heuristic freshness (~10% of the response's age) —
a days-old index.html then counts as fresh for hours.
"""

from pathlib import Path

import pytest
from starlette.testclient import TestClient

CONSOLE_DIST = Path(__file__).parent.parent.parent / "web" / "dist"

pytestmark = pytest.mark.skipif(
    not (CONSOLE_DIST / "index.html").exists(),
    reason="web console not built (run 'npm run build' in web/)",
)


@pytest.fixture(scope="module")
def client() -> TestClient:
    from dalston.gateway.main import app

    return TestClient(app)


def _assert_revalidates(cache_control: str | None) -> None:
    assert cache_control is not None, "no Cache-Control header — browser will guess"
    assert "no-cache" in cache_control.lower()


def test_console_index_must_revalidate(client: TestClient) -> None:
    """The SPA entry point is revalidated on every load."""
    response = client.get("/console")

    assert response.status_code == 200
    _assert_revalidates(response.headers.get("cache-control"))


def test_console_spa_fallback_must_revalidate(client: TestClient) -> None:
    """Deep links fall back to index.html and need the same treatment."""
    response = client.get("/console/jobs")

    assert response.status_code == 200
    _assert_revalidates(response.headers.get("cache-control"))


def test_console_unhashed_asset_must_revalidate(client: TestClient) -> None:
    """Unhashed siblings have stable names, so they can also go stale."""
    response = client.get("/console/vite.svg")

    assert response.status_code == 200
    _assert_revalidates(response.headers.get("cache-control"))
