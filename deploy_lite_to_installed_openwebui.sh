#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE_BACKEND_DEFAULT="$ROOT_DIR/open-webui/backend/open_webui"
DEST_PACKAGE_DEFAULT="/opt/open-webui-venv/lib64/python3.11/site-packages/open_webui"
SERVICE_DEFAULT="open-webui.service"

WORKSPACE_BACKEND="$WORKSPACE_BACKEND_DEFAULT"
DEST_PACKAGE="$DEST_PACKAGE_DEFAULT"
SERVICE_NAME="$SERVICE_DEFAULT"
RESTART_SERVICE=1

usage() {
  cat <<'EOF'
Usage:
  ./deploy_lite_to_installed_openwebui.sh [options]

Options:
  --src DIR            Workspace open_webui package directory
  --dest DIR           Installed open_webui package directory
  --service NAME       systemd service name to restart
  --no-restart         Copy files only, do not restart service
  --help               Show this help

This script is for fast iteration of Lite frontend related changes only.
It syncs:
  - main.py
  - static/lite/index.html
  - static/lite/styles.css
  - static/lite/app.js
  - static/lite/icons/attachment.png
EOF
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    printf 'This script must be run as root.\n' >&2
    exit 1
  fi
}

require_path() {
  local path="$1"
  local label="$2"
  if [[ ! -e "$path" ]]; then
    printf '%s not found: %s\n' "$label" "$path" >&2
    exit 1
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --src)
      WORKSPACE_BACKEND="$2"
      shift 2
      ;;
    --dest)
      DEST_PACKAGE="$2"
      shift 2
      ;;
    --service)
      SERVICE_NAME="$2"
      shift 2
      ;;
    --no-restart)
      RESTART_SERVICE=0
      shift
      ;;
    --help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown option: %s\n' "$1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

require_root
require_path "$WORKSPACE_BACKEND/main.py" "Workspace main.py"
require_path "$WORKSPACE_BACKEND/static/lite/index.html" "Lite index.html"
require_path "$WORKSPACE_BACKEND/static/lite/styles.css" "Lite styles.css"
require_path "$WORKSPACE_BACKEND/static/lite/app.js" "Lite app.js"
require_path "$WORKSPACE_BACKEND/static/lite/icons/attachment.png" "Lite attachment icon"
require_path "$DEST_PACKAGE" "Installed open_webui package directory"

mkdir -p "$DEST_PACKAGE/static/lite"
mkdir -p "$DEST_PACKAGE/static/lite/icons"

cp "$WORKSPACE_BACKEND/main.py" "$DEST_PACKAGE/main.py"
cp "$WORKSPACE_BACKEND/static/lite/index.html" "$DEST_PACKAGE/static/lite/index.html"
cp "$WORKSPACE_BACKEND/static/lite/styles.css" "$DEST_PACKAGE/static/lite/styles.css"
cp "$WORKSPACE_BACKEND/static/lite/app.js" "$DEST_PACKAGE/static/lite/app.js"
cp "$WORKSPACE_BACKEND/static/lite/icons/attachment.png" "$DEST_PACKAGE/static/lite/icons/attachment.png"

printf 'Lite frontend files deployed to %s\n' "$DEST_PACKAGE"

if [[ "$RESTART_SERVICE" -eq 1 ]]; then
  printf 'Restarting %s\n' "$SERVICE_NAME"
  systemctl restart "$SERVICE_NAME"
  systemctl status "$SERVICE_NAME" --no-pager
else
  printf 'Skipping service restart\n'
fi

printf 'Lite frontend deployment complete.\n'
