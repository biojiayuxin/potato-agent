from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent
LEGAL_DIR = ROOT_DIR / "static" / "legal"

CURRENT_AGREEMENT_VERSION = "2026-08-25"
CURRENT_AGREEMENT_EFFECTIVE_DATE = "2026-08-25"
CURRENT_AGREEMENT_TITLE = (
    "Potato Agent Research Preview Terms, Privacy Notice, and Risk Acknowledgment"
)
CURRENT_AGREEMENT_URL = f"/user-agreement/{CURRENT_AGREEMENT_VERSION}"
CURRENT_AGREEMENT_PATH = (
    LEGAL_DIR / f"potato-agent-research-preview-terms-{CURRENT_AGREEMENT_VERSION}.html"
)


def agreement_document_path(version: str) -> Path | None:
    normalized_version = version.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized_version) is None:
        return None
    path = LEGAL_DIR / f"potato-agent-research-preview-terms-{normalized_version}.html"
    return path if path.is_file() else None


def agreement_document_sha256(path: Path = CURRENT_AGREEMENT_PATH) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def current_agreement_metadata() -> dict[str, Any]:
    return {
        "version": CURRENT_AGREEMENT_VERSION,
        "effective_date": CURRENT_AGREEMENT_EFFECTIVE_DATE,
        "title": CURRENT_AGREEMENT_TITLE,
        "url": CURRENT_AGREEMENT_URL,
        "sha256": agreement_document_sha256(),
    }
