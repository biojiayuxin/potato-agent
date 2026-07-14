from __future__ import annotations

from pathlib import Path


HOME_ONLY_MODE = "home_only"
HOME_AND_PUBLIC_DATA_MODE = "home_and_public_data"
USER_READABLE_MODE = "user_readable"
FILE_BROWSER_MODES = frozenset(
    {
        HOME_ONLY_MODE,
        HOME_AND_PUBLIC_DATA_MODE,
        USER_READABLE_MODE,
    }
)
DEFAULT_PUBLIC_DATA_PATH = Path("/mnt/data/public_data")


class FileBrowserAccessError(RuntimeError):
    pass


def normalize_file_browser_mode(mode: str | None) -> str:
    normalized = str(mode or "").strip().lower()
    if normalized in FILE_BROWSER_MODES:
        return normalized
    return HOME_ONLY_MODE


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def authorize_file_browser_path(
    path: Path,
    *,
    home: Path,
    mode: str | None,
    public_data_root: Path | None = None,
) -> Path:
    resolved_path = path.resolve()
    normalized_mode = normalize_file_browser_mode(mode)
    if normalized_mode == USER_READABLE_MODE:
        return resolved_path

    allowed_roots = [home.resolve()]
    if normalized_mode == HOME_AND_PUBLIC_DATA_MODE:
        allowed_roots.append((public_data_root or DEFAULT_PUBLIC_DATA_PATH).resolve())

    if any(_is_within(resolved_path, root) for root in allowed_roots):
        return resolved_path
    raise FileBrowserAccessError("Opening paths outside the allowed browser roots is disabled")
