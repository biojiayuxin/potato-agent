#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC_DIR_DEFAULT="$ROOT_DIR/open-webui/backend/open_webui"
DEST_DIR_DEFAULT="/opt/open-webui-venv/lib64/python3.11/site-packages/open_webui"
SERVICE_DEFAULT="open-webui.service"

SRC_DIR="$SRC_DIR_DEFAULT"
DEST_DIR="$DEST_DIR_DEFAULT"
SERVICE_NAME="$SERVICE_DEFAULT"
RESTART_SERVICE=1

usage() {
  cat <<'EOF'
Usage:
  ./deploy_openwebui_from_workspace.sh [options]

Options:
  --src DIR            Source open_webui package directory
  --dest DIR           Installed open_webui package directory
  --service NAME       systemd service name to restart
  --no-restart         Copy files only, do not restart service
  --help               Show this help

Default source:
  ./open-webui/backend/open_webui

Default destination:
  /opt/open-webui-venv/lib64/python3.11/site-packages/open_webui
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

sync_package_tree() {
  local src="$1"
  local dest="$2"

  local entries=(
    "alembic.ini"
    "config.py"
    "constants.py"
    "data"
    "env.py"
    "functions.py"
    "internal"
    "main.py"
    "migrations"
    "models"
    "retrieval"
    "routers"
    "socket"
    "static"
    "storage"
    "tasks.py"
    "tools"
    "utils"
    "__init__.py"
  )

  for entry in "${entries[@]}"; do
    require_path "$src/$entry" "Workspace entry"
    if [[ -d "$src/$entry" ]]; then
      rm -rf "$dest/$entry"
      cp -a "$src/$entry" "$dest/$entry"
    else
      cp "$src/$entry" "$dest/$entry"
    fi
  done
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --src)
      SRC_DIR="$2"
      shift 2
      ;;
    --dest)
      DEST_DIR="$2"
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
require_path "$SRC_DIR" "Workspace source directory"
require_path "$DEST_DIR" "Installed destination directory"

printf 'Deploying Open WebUI package\n'
printf '  source: %s\n' "$SRC_DIR"
printf '  destination: %s\n' "$DEST_DIR"

sync_package_tree "$SRC_DIR" "$DEST_DIR"

if [[ "$RESTART_SERVICE" -eq 1 ]]; then
  printf 'Restarting %s\n' "$SERVICE_NAME"
  systemctl restart "$SERVICE_NAME"
  systemctl status "$SERVICE_NAME" --no-pager
else
  printf 'Skipping service restart\n'
fi

printf 'Open WebUI deployment complete.\n'
