#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[[ "$(id -u)" -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
command -v useradd >/dev/null 2>&1 || { echo "Missing useradd" >&2; exit 1; }
command -v install >/dev/null 2>&1 || { echo "Missing install" >&2; exit 1; }
command -v systemctl >/dev/null 2>&1 || { echo "Missing systemctl" >&2; exit 1; }

id -u hmx_user_test >/dev/null 2>&1 || useradd -m -s /bin/bash hmx_user_test
install -d -m 700 -o hmx_user_test -g hmx_user_test /home/hmx_user_test
install -d -m 700 -o hmx_user_test -g hmx_user_test /home/hmx_user_test/work
install -d -m 700 -o hmx_user_test -g hmx_user_test /home/hmx_user_test/.hermes
install -d -m 700 -o hmx_user_test -g hmx_user_test /home/hmx_user_test/.hermes/home
install -m 600 -o hmx_user_test -g hmx_user_test "$SCRIPT_DIR/users/user_test/.hermes/.env" /home/hmx_user_test/.hermes/.env
install -m 600 -o hmx_user_test -g hmx_user_test "$SCRIPT_DIR/users/user_test/.hermes/config.yaml" /home/hmx_user_test/.hermes/config.yaml
install -m 644 "$SCRIPT_DIR/systemd/hermes-user-test.service" /etc/systemd/system/hermes-user-test.service

systemctl daemon-reload
systemctl enable --now hermes-user-test.service
