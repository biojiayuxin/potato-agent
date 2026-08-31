from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LITE_APP_PATH = REPO_ROOT / "interface/static/lite/app.js"
LITE_INDEX_PATH = REPO_ROOT / "interface/static/lite/index.html"


def test_auto_title_uses_bounded_canonical_detail_reconciliation() -> None:
    app = LITE_APP_PATH.read_text(encoding="utf-8")

    assert "const TITLE_RECONCILE_DELAYS_MS = [1000, 3000, 8000, 20000, 35000];" in app
    assert "session.title.updated" in app
    assert "reconcileCanonicalSessionTitle" in app
    assert "updateSessionSnapshot(normalizedKey" in app
    assert "message?.payload?.title" not in app
    assert "}, 1200);" not in app


def test_auto_title_cache_buster_matches_updated_frontend() -> None:
    index = LITE_INDEX_PATH.read_text(encoding="utf-8")

    assert "app.js?v=20260831-update-history" in index
