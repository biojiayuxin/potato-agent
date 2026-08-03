#!/usr/bin/env bash

set -Eeuo pipefail
umask 022

if [[ ${EUID} -ne 0 ]]; then
  echo "error: maintenance mode installation must run as root" >&2
  exit 2
fi
if [[ $# -gt 1 ]]; then
  echo "usage: $0 [SOURCE_ROOT]" >&2
  exit 2
fi

source_root=$(realpath "${1:-$(dirname "$0")/..}")
if [[ -L ${source_root} || ! -d ${source_root} ]] || \
   find "${source_root}" ! -uid 0 -print -quit | grep -q . || \
   find "${source_root}" -perm /022 -print -quit | grep -q .; then
  echo "error: maintenance source must be root-owned and immutable" >&2
  exit 2
fi
server_source=${source_root}/packaging/libexec/potato-maintenance-server
control_source=${source_root}/packaging/libexec/potato-maintenancectl
unit_source=${source_root}/packaging/systemd/potato-maintenance.service
html_source=${source_root}/packaging/maintenance/index.html
config_source=${source_root}/packaging/maintenance/maintenance.conf
logo_source=${source_root}/interface/static/LOGO.png
config_target=/etc/potato-maintenance.conf

for source_path in \
  "${server_source}" \
  "${control_source}" \
  "${unit_source}" \
  "${html_source}" \
  "${config_source}" \
  "${logo_source}"; do
  if [[ -L ${source_path} || ! -f ${source_path} ]]; then
    echo "error: missing or unsafe maintenance source: ${source_path}" >&2
    exit 2
  fi
done

install -D -o root -g root -m 0755 \
  "${server_source}" /usr/local/libexec/potato-maintenance-server
install -D -o root -g root -m 0750 \
  "${control_source}" /usr/local/sbin/potato-maintenancectl
install -D -o root -g root -m 0644 \
  "${html_source}" /usr/local/share/potato-agent/maintenance/index.html
install -D -o root -g root -m 0644 \
  "${logo_source}" /usr/local/share/potato-agent/maintenance/logo.png
if [[ ! -e ${config_target} && ! -L ${config_target} ]]; then
  install -D -o root -g root -m 0644 "${config_source}" "${config_target}"
fi
if [[ -L ${config_target} || ! -f ${config_target} ]] || \
   [[ $(stat -c '%U:%G:%a' "${config_target}") != root:root:644 ]]; then
  echo "error: unsafe maintenance site config: ${config_target}" >&2
  exit 2
fi
install -D -o root -g root -m 0644 \
  "${unit_source}" /etc/systemd/system/potato-maintenance.service
systemctl daemon-reload

/usr/local/libexec/potato-maintenance-server \
  --config "${config_target}" --print-address
systemctl cat potato-maintenance.service >/dev/null
echo "maintenance mode installed but not started"
