#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[[ "$(id -u)" -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
command -v useradd >/dev/null 2>&1 || { echo "Missing useradd" >&2; exit 1; }
command -v install >/dev/null 2>&1 || { echo "Missing install" >&2; exit 1; }
command -v systemctl >/dev/null 2>&1 || { echo "Missing systemctl" >&2; exit 1; }

id -u hmx_alice >/dev/null 2>&1 || useradd -m -s /bin/bash hmx_alice
install -d -m 700 -o hmx_alice -g hmx_alice /home/hmx_alice
install -d -m 700 -o hmx_alice -g hmx_alice /home/hmx_alice/work
install -d -m 700 -o hmx_alice -g hmx_alice /home/hmx_alice/.hermes
install -d -m 700 -o hmx_alice -g hmx_alice /home/hmx_alice/.hermes/home
install -m 600 -o hmx_alice -g hmx_alice "$SCRIPT_DIR/users/alice/.hermes/.env" /home/hmx_alice/.hermes/.env
install -m 600 -o hmx_alice -g hmx_alice "$SCRIPT_DIR/users/alice/.hermes/config.yaml" /home/hmx_alice/.hermes/config.yaml
install -m 644 "$SCRIPT_DIR/systemd/hermes-alice.service" /etc/systemd/system/hermes-alice.service

id -u hmx_bob >/dev/null 2>&1 || useradd -m -s /bin/bash hmx_bob
install -d -m 700 -o hmx_bob -g hmx_bob /home/hmx_bob
install -d -m 700 -o hmx_bob -g hmx_bob /home/hmx_bob/work
install -d -m 700 -o hmx_bob -g hmx_bob /home/hmx_bob/.hermes
install -d -m 700 -o hmx_bob -g hmx_bob /home/hmx_bob/.hermes/home
install -m 600 -o hmx_bob -g hmx_bob "$SCRIPT_DIR/users/bob/.hermes/.env" /home/hmx_bob/.hermes/.env
install -m 600 -o hmx_bob -g hmx_bob "$SCRIPT_DIR/users/bob/.hermes/config.yaml" /home/hmx_bob/.hermes/config.yaml
install -m 644 "$SCRIPT_DIR/systemd/hermes-bob.service" /etc/systemd/system/hermes-bob.service

systemctl daemon-reload
systemctl enable --now hermes-alice.service
systemctl enable --now hermes-bob.service
