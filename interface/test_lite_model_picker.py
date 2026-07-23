from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LITE_APP_PATH = REPO_ROOT / "interface/static/lite/app.js"


def test_model_picker_uses_deep_and_fast_display_names() -> None:
    source = LITE_APP_PATH.read_text(encoding="utf-8")
    start = source.index("const MODEL_DISPLAY_NAME_OVERRIDES =")
    end = source.index("const getModelKeyCandidates =")
    display_config = source[start:end]

    assert "'gpt-5.6-sol': 'Deep'" in display_config
    assert "'gpt-5.6-terra': 'Fast'" in display_config
    assert "const MODEL_DISPLAY_ORDER = [\n  'Deep',\n  'Fast',\n];" in display_config
    assert "GPT-5.5" not in display_config
    assert "GPT-5.5-alt" not in display_config
    assert "DeepSeek" not in display_config
    assert "GPT-5.6-sol" not in display_config
