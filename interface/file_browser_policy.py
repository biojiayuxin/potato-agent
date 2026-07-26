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

_SENSITIVE_FILE_BASENAMES = frozenset(
    {
        ".anthropic_oauth.json",
        ".git-credentials",
        ".envrc",
        "application_default_credentials.json",
        "auth.json",
        "auth.lock",
        "bws_cache.json",
        "client_secret.json",
        "config.yaml",
        "credentials",
        "google_client_secret.json",
        "google_oauth.json",
        "webhook_subscriptions.json",
    }
)
_SENSITIVE_DIRECTORY_NAMES = frozenset({"mcp-tokens", "pairing"})


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


def is_sensitive_file_browser_path(path: Path) -> bool:
    """Return whether a lexical or resolved path is hidden from the browser."""

    candidates = [path.expanduser()]
    try:
        resolved = path.expanduser().resolve()
    except (OSError, RuntimeError):
        resolved = None
    if resolved is not None and resolved != candidates[0]:
        candidates.append(resolved)

    for candidate in candidates:
        folded_parts = tuple(part.casefold() for part in candidate.parts)
        if any(part in _SENSITIVE_DIRECTORY_NAMES for part in folded_parts):
            return True
        basename = candidate.name.casefold()
        if basename == ".env" or basename.startswith(".env."):
            return True
        if basename in _SENSITIVE_FILE_BASENAMES:
            return True
    return False


def raise_if_file_browser_path_sensitive(path: Path) -> None:
    if is_sensitive_file_browser_path(path):
        raise FileBrowserAccessError("Opening sensitive credential files is disabled")


def _restricted_mode_allowed_roots(
    *,
    home: Path,
    mode: str,
    public_data_root: Path | None,
) -> tuple[Path, ...]:
    roots = [home.resolve()]
    if mode == HOME_AND_PUBLIC_DATA_MODE:
        roots.append((public_data_root or DEFAULT_PUBLIC_DATA_PATH).resolve())
    return tuple(roots)


def build_file_stream_policy(
    *,
    home: Path,
    browser_root: Path,
    mode: str | None,
    public_data_root: Path | None = None,
) -> dict[str, object]:
    """Return the complete path policy for the isolated file-stream worker."""

    normalized_mode = normalize_file_browser_mode(mode)
    if normalized_mode == USER_READABLE_MODE:
        allowed_roots = (browser_root.resolve(),)
    else:
        allowed_roots = _restricted_mode_allowed_roots(
            home=home,
            mode=normalized_mode,
            public_data_root=public_data_root,
        )
    return {
        "allowed_roots": [str(root) for root in allowed_roots],
        "sensitive_file_basenames": sorted(_SENSITIVE_FILE_BASENAMES),
        "sensitive_directory_names": sorted(_SENSITIVE_DIRECTORY_NAMES),
    }


def authorize_file_browser_path(
    path: Path,
    *,
    home: Path,
    mode: str | None,
    public_data_root: Path | None = None,
) -> Path:
    raise_if_file_browser_path_sensitive(path)
    resolved_path = path.resolve()
    raise_if_file_browser_path_sensitive(resolved_path)
    normalized_mode = normalize_file_browser_mode(mode)
    if normalized_mode == USER_READABLE_MODE:
        return resolved_path

    allowed_roots = _restricted_mode_allowed_roots(
        home=home,
        mode=normalized_mode,
        public_data_root=public_data_root,
    )

    if any(_is_within(resolved_path, root) for root in allowed_roots):
        return resolved_path
    raise FileBrowserAccessError("Opening paths outside the allowed browser roots is disabled")
