from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LITE_APP_PATH = REPO_ROOT / "interface/static/lite/app.js"


def test_lite_preserves_supported_file_browser_modes_and_defaults_unknown() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")

    assert "const SUPPORTED_FILE_BROWSER_MODES = new Set([" in source
    assert "'home_only'," in source
    assert "'home_and_public_data'," in source
    assert "'user_readable'," in source
    assert "SUPPORTED_FILE_BROWSER_MODES.has(normalized) ? normalized : 'home_only'" in source
    assert "state.fileBrowserMode = normalizeFileBrowserMode(configJson?.mode);" in source


def test_lite_only_shows_free_path_input_for_user_readable_mode() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")

    assert "const isUserReadable = state.fileBrowserMode === 'user_readable';" in source
    assert "dom.filePathDisplay.hidden = isUserReadable;" in source
    assert "dom.filePathInput.hidden = !isUserReadable;" in source
