#!/usr/bin/env bash

set -Eeuo pipefail
umask 077

if [[ ${EUID} -ne 0 ]]; then
  echo "error: this cutover must run as root" >&2
  exit 2
fi
if [[ $# -ne 3 ]]; then
  echo "usage: $0 CODE_SOURCE RELEASE_ID EXPECTED_USER_COUNT" >&2
  exit 2
fi

code_source=$(realpath "$1")
release_id=$2
expected_count=$3

if [[ ! ${release_id} =~ ^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$ ]]; then
  echo "error: unsafe release id: ${release_id}" >&2
  exit 2
fi
if [[ ! ${expected_count} =~ ^[1-9][0-9]*$ ]]; then
  echo "error: expected user count must be positive" >&2
  exit 2
fi

interface_python=/opt/interface-env/bin/python
mapping=/var/lib/potato-agent/config/users_mapping.yaml
model_proxy_config=/var/lib/potato-agent/config/model_proxy.yaml
model_proxy_state_dir=/var/lib/potato-agent/model-proxy
model_proxy_usage_db=${model_proxy_state_dir}/usage.db
data_dir=/var/lib/potato-agent/data
auth_db=${data_dir}/interface.db
credential_dir=/etc/potato-agent/credentials
session_credential=${credential_dir}/interface-session-secret
resend_credential=${credential_dir}/resend-api-key
privileged_helper=/usr/local/libexec/potato-agent-privileged-helper
staged_privileged_helper=${code_source}/packaging/libexec/potato-agent-privileged-helper
repo=/srv/potato_agent
base=/opt/potato-hermes-lite
release=${base}/releases/${release_id}
current=${base}/current
hermes_link=/usr/local/bin/hermes
interface_unit=potato-interface.service
model_proxy_unit=potato-model-proxy.service
model_proxy_user=potato-model-proxy
interface_unit_file=/etc/systemd/system/potato-interface.service
model_proxy_unit_file=/etc/systemd/system/potato-model-proxy.service
staged_interface_unit=${code_source}/packaging/systemd/potato-interface.service
staged_model_proxy_unit=${code_source}/packaging/systemd/potato-model-proxy.service
interface_dropin_dir=/etc/systemd/system/potato-interface.service.d
interface_exec_dropin=${interface_dropin_dir}/40-security-entrypoint.conf
interface_dropin=${interface_dropin_dir}/50-hermes-lite.conf
fingerprint_script=${code_source}/hermes-lite/scripts/fingerprint_state.py
usage_migration_script=${code_source}/migrate_model_proxy_usage.py
refresh_script=${repo}/refresh_hermes_systemd_units.py
cutover_lock_dir=/run/lock/potato-agent
cutover_lock=${cutover_lock_dir}/lite-cutover.lock
backup_root=/var/backups/potato-agent/hermes-lite-cutover
legacy_code_paths=(
  configure_hermes_model.py
  interface/test_configure_hermes_model.py
)
legacy_deploy_paths=(
  hermes-agent
  packaging/hermes
)
protected_legacy_state_paths=(
  interface/data
  users_mapping.yaml
  model_proxy.yaml
)

find_forbidden_deploy_artifact() {
  local root=$1
  find "${root}" -xdev \( \
    -type d \( \
      -name '.codex-tmp' -o -name '.deploy-backups' -o \
      -name '.git' -o -name '.mypy_cache' -o -name '.pytest_cache' -o \
      -name '.ruff_cache' -o -name '.tox' -o -name '__pycache__' -o \
      -name 'build' -o -name 'dist' -o -name 'htmlcov' -o \
      -name 'node_modules' -o -name '*.egg-info' \
    \) -o \
    -type f \( \
      -name '.coverage' -o -name '.DS_Store' -o -name '*.bak' -o \
      -name '*.bak-*' -o -name '*.orig' -o -name '*.pyc' -o \
      -name '*.pyo' -o -name '*~' \
    \) \
  \) -print -quit
}

systemd_file_is_safe() {
  local path=$1
  local mode
  [[ ! -L ${path} && -f ${path} ]] || return 1
  [[ $(stat -c '%u:%g:%h' "${path}") == '0:0:1' ]] || return 1
  mode=$(stat -c '%a' "${path}") || return 1
  (( (8#${mode} & 8#022) == 0 ))
}

read_stable_unit_state() {
  local unit=$1
  local state
  state=$(systemctl show "${unit}" --property=ActiveState --value)
  case ${state} in
    active|inactive)
      printf '%s\n' "${state}"
      ;;
    *)
      echo "error: unit is not in a stable active/inactive state: ${unit} (${state})" >&2
      return 1
      ;;
  esac
}

assert_runtime_idle() {
  "${interface_python}" -B - "${auth_db}" <<'PY'
import sqlite3
import sys
import time

path = sys.argv[1]
now = int(time.time())
with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
    tables = {
        str(row[0])
        for row in conn.execute(
            "select name from sqlite_master where type = 'table'"
        )
    }
    required = {
        "session_live_state",
        "runtime_leases",
        "signup_jobs",
        "turn_submission_receipts",
    }
    missing = sorted(required - tables)
    if missing:
        raise SystemExit("runtime-idle gate lacks tables: " + ", ".join(missing))
    counts = {
        "live": conn.execute(
            "select count(*) from session_live_state "
            "where status in ('queued','starting','running','awaiting_approval')"
        ).fetchone()[0],
        "leases": conn.execute(
            "select count(*) from runtime_leases where expires_at > ?", (now,)
        ).fetchone()[0],
        "receipts": conn.execute(
            "select count(*) from turn_submission_receipts "
            "where status = 'pending' and (expires_at = 0 or expires_at > ?)",
            (now,),
        ).fetchone()[0],
        "signup": conn.execute(
            "select count(*) from signup_jobs "
            "where status in ('pending','provisioning')"
        ).fetchone()[0],
    }
if any(counts.values()):
    detail = ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
    raise SystemExit("runtime is not idle: " + detail)
PY
}

if [[ -L ${cutover_lock_dir} || \
      ( -e ${cutover_lock_dir} && ! -d ${cutover_lock_dir} ) ]]; then
  echo "error: unsafe cutover lock directory" >&2
  exit 2
fi
install -d -o root -g root -m 0700 "${cutover_lock_dir}"
if [[ -L ${cutover_lock} || \
      ( -e ${cutover_lock} && ! -f ${cutover_lock} ) ]]; then
  echo "error: unsafe cutover lock file" >&2
  exit 2
fi
exec 9>"${cutover_lock}"
chown root:root "${cutover_lock}"
chmod 0600 "${cutover_lock}"
if ! flock -n 9; then
  echo "error: another Lite cutover is already running" >&2
  exit 2
fi

for path in \
  "${interface_python}" \
  "${mapping}" \
  "${model_proxy_config}" \
  "${auth_db}" \
  "${session_credential}" \
  "${resend_credential}" \
  "${staged_privileged_helper}" \
  "${staged_interface_unit}" \
  "${staged_model_proxy_unit}" \
  "${release}/manifest.json" \
  "${release}/venv/bin/python3" \
  "${release}/venv/bin/hermes" \
  "${release}/config/runtime-profile.yaml" \
  "${release}/browser/bin/agent-browser" \
  "${release}/browser/chrome/chrome-linux64/chrome" \
  "${release}/browser/chrome/chrome-linux64/chrome-sandbox" \
  "${fingerprint_script}" \
  "${usage_migration_script}"; do
  if [[ ! -e ${path} ]]; then
    echo "error: required cutover path is missing: ${path}" >&2
    exit 2
  fi
done
if ! systemd_file_is_safe "${privileged_helper}" || \
   [[ $(stat -c '%U:%G:%a' "${privileged_helper}") != 'root:root:755' ]]; then
  echo "error: unsafe Interface privileged helper ownership, mode, or file type" >&2
  exit 2
fi
if [[ ! -d ${code_source}/interface || ! -f ${code_source}/interface/app.py ]]; then
  echo "error: code source is incomplete: ${code_source}" >&2
  exit 2
fi
while IFS= read -r mount_target; do
  if [[ ${mount_target} == "${code_source}/"* ]]; then
    echo "error: code source contains a nested mount: ${mount_target}" >&2
    exit 2
  fi
done < <(findmnt --raw --noheadings --output TARGET)
if find "${code_source}" ! -uid 0 -print -quit | grep -q . ||
   find "${code_source}" -perm /022 -print -quit | grep -q .; then
  echo "error: code source must be root-owned and not writable by group or other" >&2
  exit 2
fi
if find "${code_source}" -type d ! -perm -0050 -print -quit | grep -q . ||
   find "${code_source}" -type f ! -perm -0040 -print -quit | grep -q .; then
  echo "error: code source must be readable by the deployment service group" >&2
  exit 2
fi
if find "${code_source}" -type l -print -quit | grep -q .; then
  echo "error: code source must not contain symlinks" >&2
  exit 2
fi
if find "${code_source}" ! \( -type d -o -type f \) -print -quit | grep -q .; then
  echo "error: code source contains a special file" >&2
  exit 2
fi
if find "${code_source}" -type f \( \
  -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' -o \
  -name '*.db-wal' -o -name '*.db-shm' -o -name '*.db-journal' -o \
  -name '*.sqlite-wal' -o -name '*.sqlite-shm' -o -name '*.sqlite-journal' -o \
  -name '*.sqlite3-wal' -o -name '*.sqlite3-shm' -o -name '*.sqlite3-journal' -o \
  -name '.env' -o -name '*.pyc' -o -name '*.pyo' -o \
  -name 'users_mapping.yaml' -o -name 'model_proxy.yaml' \
\) -print -quit | grep -q .; then
  echo "error: code source contains runtime state, secret YAML, or generated bytecode" >&2
  exit 2
fi
forbidden_source_artifact=$(find_forbidden_deploy_artifact "${code_source}")
if [[ -n ${forbidden_source_artifact} ]]; then
  echo "error: code source contains a cache, backup, or build artifact: ${forbidden_source_artifact}" >&2
  exit 2
fi
for relative in "${protected_legacy_state_paths[@]}"; do
  if [[ -e ${code_source}/${relative} || -L ${code_source}/${relative} ]]; then
    echo "error: code source contains excluded runtime state: ${relative}" >&2
    exit 2
  fi
done
for relative in "${legacy_deploy_paths[@]}"; do
  if [[ -e ${code_source}/${relative} || -L ${code_source}/${relative} ]]; then
    echo "error: code source contains excluded legacy path: ${relative}" >&2
    exit 2
  fi
done
for relative in "${legacy_code_paths[@]}"; do
  if [[ -e ${code_source}/${relative} || -L ${code_source}/${relative} ]]; then
    echo "error: code source contains removed legacy path: ${relative}" >&2
    exit 2
  fi
done
if [[ ! -L ${hermes_link} ]]; then
  echo "error: existing Hermes compatibility entry is not a symlink" >&2
  exit 2
fi
if [[ -e ${current} && ! -L ${current} ]]; then
  echo "error: Lite current path exists but is not a symlink" >&2
  exit 2
fi
if find "${release}" ! -uid 0 -print -quit | grep -q . ||
   find "${release}" \( -type d -o -type f \) -perm /022 -print -quit | grep -q .; then
  echo "error: inactive release must be root-owned and immutable to ordinary users" >&2
  exit 2
fi
if find "${release}" ! \( -type d -o -type f -o -type l \) -print -quit | grep -q .; then
  echo "error: inactive release contains an unsupported special file" >&2
  exit 2
fi
if [[ -L ${repo} || ! -d ${repo} ]]; then
  echo "error: deployed source tree must be a real directory: ${repo}" >&2
  exit 2
fi
repo_resolved=$(realpath "${repo}")
if [[ ${code_source} == "${repo_resolved}" ||
      ${code_source} == "${repo_resolved}/"* ||
      ${repo_resolved} == "${code_source}/"* ]]; then
  echo "error: code source and deployed source tree must not overlap" >&2
  exit 2
fi
while IFS= read -r mount_target; do
  if [[ ${mount_target} == "${repo}/"* ]]; then
    echo "error: deployed source tree contains a nested mount: ${mount_target}" >&2
    exit 2
  fi
done < <(findmnt --raw --noheadings --output TARGET)
if find "${repo}" -xdev ! \( -type d -o -type f -o -type l \) \
  -print -quit | grep -q .; then
  echo "error: deployed source tree contains a special file" >&2
  exit 2
fi
if find "${repo}" -xdev \( \
  -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' -o \
  -name '*.db-wal' -o -name '*.db-shm' -o \
  -name '*.db-journal' -o \
  -name '*.sqlite-wal' -o -name '*.sqlite-shm' -o \
  -name '*.sqlite-journal' -o \
  -name '*.sqlite3-wal' -o -name '*.sqlite3-shm' -o \
  -name '*.sqlite3-journal' -o \
  -name '.env' -o -name 'users_mapping.yaml' -o -name 'model_proxy.yaml' \
\) -print -quit | grep -q .; then
  echo "error: deployed source tree still contains database runtime state or secret state" >&2
  echo "migrate, back up, and explicitly remove it before cutover" >&2
  exit 2
fi
for relative in "${protected_legacy_state_paths[@]}"; do
  if [[ -e ${repo}/${relative} || -L ${repo}/${relative} ]]; then
    echo "error: deployed source tree contains protected runtime state: ${relative}" >&2
    echo "migrate and independently back it up before removing it from the source tree" >&2
    exit 2
  fi
done

if [[ -L ${mapping} ]] || [[ ! -f ${mapping} ]] || \
   [[ $(stat -c '%U:%G:%a' "${mapping}") != 'root:potato-interface:640' ]]; then
  echo "error: unsafe mapping ownership or mode" >&2
  exit 2
fi
if [[ -L ${data_dir} ]] || [[ ! -d ${data_dir} ]] || \
   [[ $(stat -c '%U:%G:%a' "${data_dir}") != \
      'potato-interface:potato-interface:700' ]]; then
  echo "error: unsafe Interface data directory ownership or mode" >&2
  exit 2
fi
if [[ -L ${auth_db} ]] || [[ ! -f ${auth_db} ]] || \
   [[ $(stat -c '%U:%G:%a' "${auth_db}") != \
      'potato-interface:potato-interface:600' ]]; then
  echo "error: unsafe Interface authentication database ownership or mode" >&2
  exit 2
fi

if ! getent passwd "${model_proxy_user}" >/dev/null; then
  echo "error: required model proxy service account is missing: ${model_proxy_user}" >&2
  exit 2
fi
if ! id -nG "${model_proxy_user}" | tr ' ' '\n' | grep -Fxq potato-interface; then
  echo "error: ${model_proxy_user} must have supplementary group potato-interface" >&2
  exit 2
fi
if [[ -L ${model_proxy_state_dir} ]] || \
   [[ $(stat -c '%U:%G:%a' "${model_proxy_state_dir}") != \
      "${model_proxy_user}:${model_proxy_user}:700" ]]; then
  echo "error: unsafe model proxy state directory ownership or mode" >&2
  exit 2
fi
model_proxy_usage_db_existed=0
if [[ -e ${model_proxy_usage_db} || -L ${model_proxy_usage_db} ]]; then
  if [[ -L ${model_proxy_usage_db} ]] || \
     [[ ! -f ${model_proxy_usage_db} ]] || \
     [[ $(stat -c '%U:%G:%a' "${model_proxy_usage_db}") != \
        "${model_proxy_user}:${model_proxy_user}:600" ]]; then
    echo "error: unsafe existing model proxy usage database" >&2
    exit 2
  fi
  model_proxy_usage_db_existed=1
fi
if [[ -L ${model_proxy_config} ]] || [[ ! -f ${model_proxy_config} ]] || \
   [[ $(stat -c '%U:%G:%a' "${model_proxy_config}") != \
      "root:${model_proxy_user}:640" ]]; then
  echo "error: unsafe model proxy key configuration ownership or mode" >&2
  exit 2
fi
if [[ -L ${credential_dir} ]] || \
   [[ $(stat -c '%U:%G:%a' "${credential_dir}") != 'root:root:700' ]]; then
  echo "error: unsafe Interface credential directory ownership or mode" >&2
  exit 2
fi
for credential in "${session_credential}" "${resend_credential}"; do
  if [[ -L ${credential} ]] || [[ ! -f ${credential} ]] || \
     [[ $(stat -c '%U:%G:%a' "${credential}") != 'root:root:600' ]]; then
    echo "error: unsafe Interface credential ownership or mode: ${credential}" >&2
    exit 2
  fi
  if [[ ! -s ${credential} ]]; then
    echo "error: Interface credential is empty: ${credential}" >&2
    exit 2
  fi
done
if [[ $(stat -c '%s' "${session_credential}") -lt 32 ]]; then
  echo "error: Interface session credential is shorter than 32 bytes" >&2
  exit 2
fi
for unit in "${interface_unit}" "${model_proxy_unit}"; do
  if [[ $(systemctl show "${unit}" --property=LoadState --value) == not-found ]]; then
    echo "error: required production unit is not currently loaded: ${unit}" >&2
    exit 2
  fi
done

"${release}/venv/bin/pip" check >/dev/null
"${release}/venv/bin/hermes" --help >/dev/null
"${release}/venv/bin/python3" -I - "${release}" <<'PY'
import hashlib
import importlib.metadata
import json
import pathlib
import stat
import sys

root = pathlib.Path(sys.argv[1]).resolve()
site_root = (root / "venv").resolve()
manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
project = manifest.get("project")
if not isinstance(project, dict):
    raise SystemExit("release manifest project must be an object")
if project.get("name") != "potato-hermes-lite":
    raise SystemExit("release manifest project name mismatch")
expected_version = project.get("version")
if not isinstance(expected_version, str) or not expected_version:
    raise SystemExit("release manifest project version is invalid")
installed = {
    distribution.metadata["Name"].lower(): distribution.version
    for distribution in importlib.metadata.distributions()
    if distribution.metadata.get("Name")
}
if installed.get("potato-hermes-lite") != expected_version:
    raise SystemExit(
        "potato-hermes-lite version mismatch: "
        f"expected {expected_version}, got {installed.get('potato-hermes-lite')}"
    )
if "hermes-agent" in installed:
    raise SystemExit("legacy hermes-agent leaked into Lite venv")

import agent.codex_runtime
import potato_hermes_lite
import tui_gateway.entry

if potato_hermes_lite.__version__ != expected_version:
    raise SystemExit(
        "potato_hermes_lite.__version__ mismatch: "
        f"expected {expected_version}, got {potato_hermes_lite.__version__}"
    )
for module in (agent.codex_runtime, potato_hermes_lite, tui_gateway.entry):
    origin = pathlib.Path(module.__file__).resolve()
    if site_root not in origin.parents:
        raise SystemExit(f"module loaded outside Lite venv: {origin}")

wheel = root / "wheel" / manifest["wheel"]["filename"]
profile = root / manifest["runtime_profile"]["path"]
for path, expected in (
    (wheel, manifest["wheel"]["sha256"]),
    (profile, manifest["runtime_profile"]["sha256"]),
):
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(f"release hash mismatch: {path}")
sandbox = root / "browser/chrome/chrome-linux64/chrome-sandbox"
info = sandbox.stat()
if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o4755:
    raise SystemExit("Chrome sandbox must be root-owned mode 04755")
PY

mapfile -t mapped_services < <(
  "${interface_python}" - "${mapping}" "${repo}" <<'PY'
import os
import pathlib
import re
import sys
import yaml

value = yaml.safe_load(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")) or {}
repo_lexical = pathlib.Path(os.path.abspath(sys.argv[2]))
repo_resolved = repo_lexical.resolve(strict=True)


def overlaps(left: pathlib.Path, right: pathlib.Path) -> bool:
    return left == right or left in right.parents or right in left.parents


services = []
for index, user in enumerate(value.get("users") or []):
    if not isinstance(user, dict):
        raise SystemExit(f"invalid mapping user at index {index}")
    linux_user = str(user.get("linux_user") or f"hmx_{user.get('username') or index}").strip()
    home_dir = str(user.get("home_dir") or f"/home/{linux_user}").strip()
    runtime_paths = {
        "home_dir": home_dir,
        "hermes_home": str(user.get("hermes_home") or (pathlib.Path(home_dir) / ".hermes")).strip(),
        "workdir": str(user.get("workdir") or home_dir).strip(),
    }
    for label, raw_path in runtime_paths.items():
        candidate = pathlib.Path(raw_path)
        if not candidate.is_absolute():
            raise SystemExit(f"mapping {label} must be absolute at index {index}")
        lexical = pathlib.Path(os.path.abspath(candidate))
        resolved = candidate.resolve(strict=False)
        if overlaps(lexical, repo_lexical) or overlaps(resolved, repo_resolved):
            raise SystemExit(
                f"mapping {label} overlaps deployed source tree at index {index}"
            )
    service = str(user.get("systemd_service") or "").strip()
    if re.fullmatch(r"hermes-[A-Za-z0-9_.@-]+\.service", service) is None:
        raise SystemExit(f"missing or unsafe mapped service at index {index}")
    services.append(service)
if len(services) != len(set(services)):
    raise SystemExit("mapping contains duplicate Hermes services")
print("\n".join(sorted(services)))
PY
)
if [[ ${#mapped_services[@]} -ne ${expected_count} ]]; then
  echo "error: expected ${expected_count} mapped services, found ${#mapped_services[@]}" >&2
  exit 2
fi

old_hermes_target=$(readlink "${hermes_link}")
old_current_target=
if [[ -L ${current} ]]; then
  old_current_target=$(readlink "${current}")
fi
interface_state=$(read_stable_unit_state "${interface_unit}") || exit 2
model_proxy_state=$(read_stable_unit_state "${model_proxy_unit}") || exit 2
interface_was_active=0
if [[ ${interface_state} == active ]]; then
  interface_was_active=1
fi
model_proxy_was_active=0
if [[ ${model_proxy_state} == active ]]; then
  model_proxy_was_active=1
fi
if [[ ${interface_was_active} -eq 1 && ${model_proxy_was_active} -ne 1 ]]; then
  echo "error: active Interface requires an active model proxy before cutover" >&2
  exit 2
fi
active_services=()
for service in "${mapped_services[@]}"; do
  service_state=$(read_stable_unit_state "${service}") || exit 2
  if [[ ${service_state} == active ]]; then
    active_services+=("${service}")
  fi
done

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
backup=${backup_root}/${timestamp}-${release_id}
install -d -o root -g root -m 0700 "${backup_root}"
if ! mkdir -m 0700 "${backup}"; then
  echo "error: cutover backup path already exists: ${backup}" >&2
  exit 2
fi
install -d -o root -g root -m 0700 \
  "${backup}/units" \
  "${backup}/service-units" \
  "${backup}/interface-dropins" \
  "${backup}/code-before" \
  "${backup}/code-overwritten" \
  "${backup}/legacy-code"
cp -a "${mapping}" "${backup}/users_mapping.yaml"
cp -a "${privileged_helper}" "${backup}/privileged-helper"
for service in "${mapped_services[@]}"; do
  if ! systemd_file_is_safe "/etc/systemd/system/${service}"; then
    echo "error: unsafe mapped systemd unit: ${service}" >&2
    exit 2
  fi
  cp -a "/etc/systemd/system/${service}" "${backup}/units/${service}"
done
printf '%s\n' "${old_hermes_target}" >"${backup}/old-hermes-target.txt"
printf '%s\n' "${old_current_target}" >"${backup}/old-current-target.txt"
printf '%s\n' "${active_services[@]}" >"${backup}/active-hermes-services.txt"
printf '%s\n' "${interface_was_active}" >"${backup}/interface-was-active.txt"
printf '%s\n' "${model_proxy_was_active}" >"${backup}/model-proxy-was-active.txt"
for unit_file in "${interface_unit_file}" "${model_proxy_unit_file}"; do
  unit_name=$(basename "${unit_file}")
  if [[ -e ${unit_file} || -L ${unit_file} ]]; then
    if ! systemd_file_is_safe "${unit_file}"; then
      echo "error: unsafe production systemd unit: ${unit_file}" >&2
      exit 2
    fi
    cp -a "${unit_file}" "${backup}/service-units/${unit_name}"
  else
    : >"${backup}/service-units/${unit_name}.absent"
  fi
done
if [[ -L ${interface_dropin_dir} || \
      ( -e ${interface_dropin_dir} && ! -d ${interface_dropin_dir} ) ]]; then
  echo "error: Interface drop-in path is not a regular directory" >&2
  exit 2
fi
if [[ -d ${interface_dropin_dir} ]]; then
  while IFS= read -r -d '' dropin_file; do
    if ! systemd_file_is_safe "${dropin_file}"; then
      echo "error: unsafe Interface drop-in: ${dropin_file}" >&2
      exit 2
    fi
    cp -a "${dropin_file}" "${backup}/interface-dropins/$(basename "${dropin_file}")"
  done < <(find "${interface_dropin_dir}" -mindepth 1 -maxdepth 1 -print0)
fi
for relative in "${legacy_code_paths[@]}"; do
  deployed_path=${repo}/${relative}
  if [[ -L ${deployed_path} || ( -e ${deployed_path} && ! -f ${deployed_path} ) ]]; then
    echo "error: legacy code path is not a regular file: ${deployed_path}" >&2
    exit 2
  fi
  if [[ -f ${deployed_path} ]]; then
    install -d -o root -g root -m 0700 "${backup}/legacy-code/$(dirname "${relative}")"
    cp -a "${deployed_path}" "${backup}/legacy-code/${relative}"
  fi
done
if [[ -f ${interface_dropin} && ! -L ${interface_dropin} ]]; then
  cp -a "${interface_dropin}" "${backup}/interface-dropin.conf"
elif [[ -e ${interface_dropin} || -L ${interface_dropin} ]]; then
  echo "error: interface Lite drop-in is not a regular file" >&2
  exit 2
fi
if [[ -f ${interface_exec_dropin} && ! -L ${interface_exec_dropin} ]]; then
  cp -a "${interface_exec_dropin}" "${backup}/interface-exec-dropin.conf"
elif [[ -e ${interface_exec_dropin} || -L ${interface_exec_dropin} ]]; then
  echo "error: Interface entrypoint drop-in is not a regular file" >&2
  exit 2
fi

atomic_symlink() {
  local target=$1
  local destination=$2
  local temporary
  temporary=$(dirname "${destination}")/.$(basename "${destination}").cutover.$$
  rm -f "${temporary}"
  ln -s "${target}" "${temporary}"
  mv -Tf "${temporary}" "${destination}"
}

cutover_started=0
rollback() {
  local incoming=$?
  local rc=${1:-${incoming}}
  local rollback_failed=0
  trap - ERR INT TERM
  if [[ ${cutover_started} -eq 0 ]]; then
    exit "${rc}"
  fi
  set +e
  rollback_error() {
    printf 'rollback step failed:' >&2
    printf ' %q' "$@" >&2
    printf '\n' >&2
    rollback_failed=1
  }
  rollback_step() {
    if ! "$@"; then
      rollback_error "$@"
    fi
  }
  service_was_active_before() {
    local candidate=$1
    local active
    for active in "${active_services[@]}"; do
      [[ ${candidate} == "${active}" ]] && return 0
    done
    return 1
  }
  echo "cutover failed; restoring legacy runtime from ${backup}" >&2
  rollback_step systemctl stop "${interface_unit}"
  rollback_step systemctl stop "${model_proxy_unit}"
  for service in "${mapped_services[@]}"; do
    rollback_step systemctl stop "${service}"
  done
  if [[ ${model_proxy_usage_db_existed} -eq 0 ]]; then
    rollback_step rm -f \
      "${model_proxy_usage_db}" \
      "${model_proxy_usage_db}-wal" \
      "${model_proxy_usage_db}-shm"
  fi
  for service in "${mapped_services[@]}"; do
    rollback_step cp -a \
      "${backup}/units/${service}" "/etc/systemd/system/${service}"
  done
  for unit_file in "${interface_unit_file}" "${model_proxy_unit_file}"; do
    unit_name=$(basename "${unit_file}")
    if [[ -f ${backup}/service-units/${unit_name} ]]; then
      rollback_step cp -a \
        "${backup}/service-units/${unit_name}" "${unit_file}"
    else
      rollback_step rm -f "${unit_file}"
    fi
  done
  rollback_step install -o root -g root -m 0755 \
    "${backup}/privileged-helper" "${privileged_helper}"
  if [[ -f ${backup}/interface-exec-dropin.conf ]]; then
    rollback_step cp -a \
      "${backup}/interface-exec-dropin.conf" "${interface_exec_dropin}"
  else
    rollback_step rm -f "${interface_exec_dropin}"
  fi
  if [[ -f ${backup}/interface-dropin.conf ]]; then
    rollback_step cp -a \
      "${backup}/interface-dropin.conf" "${interface_dropin}"
  else
    rollback_step rm -f "${interface_dropin}"
  fi
  if [[ -d ${backup}/interface-dropins ]]; then
    for dropin_backup in "${backup}/interface-dropins"/*; do
      [[ -e ${dropin_backup} ]] || continue
      rollback_step cp -a \
        "${dropin_backup}" \
        "${interface_dropin_dir}/$(basename "${dropin_backup}")"
    done
  fi
  rollback_step atomic_symlink "${old_hermes_target}" "${hermes_link}"
  if [[ -n ${old_current_target} ]]; then
    rollback_step atomic_symlink "${old_current_target}" "${current}"
  else
    rollback_step rm -f "${current}"
  fi
  if [[ -f ${backup}/code-before.complete ]]; then
    rollback_step rsync -aHAX \
      --numeric-ids \
      --one-file-system \
      --delete-delay \
      --checksum \
      "${backup}/code-before/" "${repo}/"
  else
    if find "${backup}/code-overwritten" -type f -print -quit | grep -q .; then
      rollback_step rsync -a --chown=root:potato-interface \
        "${backup}/code-overwritten/" "${repo}/"
    fi
    for relative in "${legacy_code_paths[@]}"; do
      if [[ -f ${backup}/legacy-code/${relative} ]]; then
        rollback_step install -d -o root -g potato-interface -m 0750 \
          "${repo}/$(dirname "${relative}")"
        rollback_step cp -a \
          "${backup}/legacy-code/${relative}" "${repo}/${relative}"
      fi
    done
    rollback_step chown root:potato-interface "${repo}"
    rollback_step chmod 0750 "${repo}"
  fi
  if [[ -f ${backup}/code-before.complete ]]; then
    if ! rsync -aHAX \
      --numeric-ids \
      --one-file-system \
      --delete-delay \
      --checksum \
      --dry-run \
      --itemize-changes \
      "${backup}/code-before/" "${repo}/" \
      >"${backup}/rollback-code-verify.txt"; then
      rollback_error "rollback code verification command"
    elif [[ -s ${backup}/rollback-code-verify.txt ]]; then
      rollback_error "rollback code verification drift"
    fi
  fi
  if [[ $(readlink "${hermes_link}") != "${old_hermes_target}" ]]; then
    rollback_error "Hermes symlink verification"
  fi
  if [[ -n ${old_current_target} ]]; then
    if [[ $(readlink "${current}") != "${old_current_target}" ]]; then
      rollback_error "Lite current symlink verification"
    fi
  elif [[ -e ${current} || -L ${current} ]]; then
    rollback_error "Lite current absence verification"
  fi
  for service in "${mapped_services[@]}"; do
    if ! cmp -s \
      "${backup}/units/${service}" "/etc/systemd/system/${service}"; then
      rollback_error "mapped unit verification" "${service}"
    fi
  done
  for unit_file in "${interface_unit_file}" "${model_proxy_unit_file}"; do
    unit_name=$(basename "${unit_file}")
    if [[ -f ${backup}/service-units/${unit_name} ]]; then
      if ! cmp -s "${backup}/service-units/${unit_name}" "${unit_file}"; then
        rollback_error "service unit verification" "${unit_name}"
      fi
    elif [[ -e ${unit_file} || -L ${unit_file} ]]; then
      rollback_error "service unit absence verification" "${unit_name}"
    fi
  done
  if ! systemd_file_is_safe "${privileged_helper}" || \
     ! cmp -s "${backup}/privileged-helper" "${privileged_helper}"; then
    rollback_error "privileged helper verification"
  fi
  for dropin_backup in "${backup}/interface-dropins"/*; do
    [[ -e ${dropin_backup} ]] || continue
    dropin_name=$(basename "${dropin_backup}")
    if ! cmp -s \
      "${dropin_backup}" "${interface_dropin_dir}/${dropin_name}"; then
      rollback_error "Interface drop-in verification" "${dropin_name}"
    fi
  done
  if [[ ${rollback_failed} -ne 0 ]]; then
    printf 'rollback_failed\n' >"${backup}/result.txt" || true
    echo "cutover rollback was incomplete; services remain stopped" >&2
    exit 125
  fi
  if ! systemctl daemon-reload; then
    rollback_error "systemctl daemon-reload"
  fi
  if [[ ${rollback_failed} -ne 0 ]]; then
    printf 'rollback_failed\n' >"${backup}/result.txt" || true
    echo "cutover rollback could not reload systemd; services remain stopped" >&2
    exit 125
  fi
  if [[ ${model_proxy_was_active} -eq 1 ]]; then
    rollback_step systemctl start "${model_proxy_unit}"
  fi
  for service in "${active_services[@]}"; do
    rollback_step systemctl start "${service}"
  done
  if [[ ${interface_was_active} -eq 1 ]]; then
    rollback_step systemctl start "${interface_unit}"
  fi
  if [[ ${model_proxy_was_active} -eq 1 ]]; then
    systemctl is-active --quiet "${model_proxy_unit}" || \
      rollback_error "model proxy active-state verification"
  elif systemctl is-active --quiet "${model_proxy_unit}"; then
    rollback_error "model proxy inactive-state verification"
  fi
  if [[ ${interface_was_active} -eq 1 ]]; then
    systemctl is-active --quiet "${interface_unit}" || \
      rollback_error "Interface active-state verification"
  elif systemctl is-active --quiet "${interface_unit}"; then
    rollback_error "Interface inactive-state verification"
  fi
  for service in "${mapped_services[@]}"; do
    if service_was_active_before "${service}"; then
      systemctl is-active --quiet "${service}" || \
        rollback_error "mapped service active-state verification" "${service}"
    elif systemctl is-active --quiet "${service}"; then
      rollback_error "mapped service inactive-state verification" "${service}"
    fi
  done
  if [[ ${rollback_failed} -ne 0 ]]; then
    printf 'rollback_failed\n' >"${backup}/result.txt" || true
    echo "cutover rollback was incomplete; manual recovery is required" >&2
    exit 125
  fi
  if ! printf 'rolled_back\n' >"${backup}/result.txt"; then
    echo "cutover rollback completed but result marker could not be written" >&2
    exit 125
  fi
  exit "${rc}"
}
trap rollback ERR
trap 'rollback 130' INT TERM

assert_runtime_idle
cutover_started=1
echo "captured ${#active_services[@]} active Hermes service(s) before cutover"
if [[ ${interface_was_active} -eq 1 ]]; then
  systemctl stop "${interface_unit}"
fi
if [[ ${model_proxy_was_active} -eq 1 ]]; then
  systemctl stop "${model_proxy_unit}"
fi
for service in "${active_services[@]}"; do
  systemctl stop "${service}"
done
for service in "${mapped_services[@]}"; do
  if systemctl is-active --quiet "${service}"; then
    echo "error: Hermes service did not stop: ${service}" >&2
    false
  fi
done
if systemctl is-active --quiet "${interface_unit}"; then
  echo "error: interface service did not stop" >&2
  false
fi
if systemctl is-active --quiet "${model_proxy_unit}"; then
  echo "error: model proxy service did not stop" >&2
  false
fi
if pgrep -f '[t]ui_gateway.entry|[s]lash_worker' >"${backup}/unexpected-gateway-processes.txt"; then
  echo "error: a TUI gateway or slash worker remained after stop" >&2
  false
fi
assert_runtime_idle

if [[ ${model_proxy_usage_db_existed} -eq 0 ]]; then
  PYTHONPATH="${code_source}" \
  "${interface_python}" -B "${usage_migration_script}" \
    --source "${auth_db}" \
    --destination "${model_proxy_usage_db}" \
    >"${backup}/model-proxy-usage-migration.log" 2>&1
else
  printf 'skipped: dedicated usage database already existed\n' \
    >"${backup}/model-proxy-usage-migration.log"
fi
if [[ -L ${model_proxy_usage_db} ]] || [[ ! -f ${model_proxy_usage_db} ]] || \
   [[ $(stat -c '%U:%G:%a' "${model_proxy_usage_db}") != \
      "${model_proxy_user}:${model_proxy_user}:600" ]]; then
  echo "error: model proxy usage migration produced an unsafe database" >&2
  false
fi

if ! "${interface_python}" -B "${fingerprint_script}" capture \
  --mapping "${mapping}" \
  --data-dir "${data_dir}" \
  --output "${backup}/state-before.json"; then
  echo "error: pre-cutover state fingerprint failed" >&2
  rollback 2
fi

rsync -aHAX \
  --numeric-ids \
  --one-file-system \
  "${repo}/" "${backup}/code-before/"
: >"${backup}/code-before.complete"

rsync -aHAX \
  --one-file-system \
  --delete-delay \
  --checksum \
  --chown=root:potato-interface \
  --chmod=Dgo-w,Do-rwx,Fgo-w \
  --backup \
  --backup-dir="${backup}/code-overwritten" \
  --itemize-changes \
  "${code_source}/" "${repo}/" \
  >"${backup}/code-sync.log"
for relative in "${legacy_code_paths[@]}"; do
  rm -f "${repo}/${relative}"
done
chown root:potato-interface "${repo}"
chmod 0750 "${repo}"

rsync -aHAX \
  --one-file-system \
  --delete-delay \
  --checksum \
  --chown=root:potato-interface \
  --chmod=Dgo-w,Do-rwx,Fgo-w \
  --dry-run \
  --itemize-changes \
  "${code_source}/" "${repo}/" \
  >"${backup}/code-sync-verify.txt"
if [[ -s ${backup}/code-sync-verify.txt ]]; then
  echo "error: deployed source tree does not exactly match staged code" >&2
  false
fi
forbidden_deployed_artifact=$(find_forbidden_deploy_artifact "${repo}")
if [[ -n ${forbidden_deployed_artifact} ]]; then
  echo "error: deployed source tree contains a cache, backup, or build artifact: ${forbidden_deployed_artifact}" >&2
  false
fi
for relative in "${legacy_deploy_paths[@]}" "${legacy_code_paths[@]}"; do
  if [[ -e ${repo}/${relative} || -L ${repo}/${relative} ]]; then
    echo "error: deployed source tree contains a legacy path: ${relative}" >&2
    false
  fi
done
if find "${repo}" -xdev ! \( -type d -o -type f \) \
  -print -quit | grep -q .; then
  echo "error: deployed source tree contains a symlink or special file" >&2
  false
fi
if find "${repo}" -xdev ! -uid 0 -print -quit | grep -q . ||
   find "${repo}" -xdev \( -type d -o -type f \) -perm /022 \
     -print -quit | grep -q .; then
  echo "error: deployed source tree is not root-owned and immutable to ordinary users" >&2
  false
fi
if find "${repo}" -xdev -type d ! -perm -0050 -print -quit | grep -q . ||
   find "${repo}" -xdev -type f ! -perm -0040 -print -quit | grep -q .; then
  echo "error: deployed source tree is not readable by the deployment service group" >&2
  false
fi

install -o root -g root -m 0755 \
  "${staged_privileged_helper}" "${privileged_helper}"
if ! systemd_file_is_safe "${privileged_helper}" || \
   ! cmp -s "${staged_privileged_helper}" "${privileged_helper}"; then
  echo "error: privileged helper does not match the staged wrapper" >&2
  false
fi

atomic_symlink "${release}" "${current}"
atomic_symlink "${current}/venv/bin/hermes" "${hermes_link}"

install -o root -g root -m 0644 \
  "${staged_interface_unit}" "${interface_unit_file}"
install -o root -g root -m 0644 \
  "${staged_model_proxy_unit}" "${model_proxy_unit_file}"

install -d -o root -g root -m 0755 "${interface_dropin_dir}"
for existing_dropin in "${interface_dropin_dir}"/*.conf; do
  [[ -e ${existing_dropin} ]] || continue
  sanitized_dropin=${existing_dropin}.cutover.$$
  sed -E \
    -e '/^[[:space:]]*Environment[[:space:]]*=[[:space:]]*"?INTERFACE_(SESSION_SECRET|RESEND_API_KEY|ARCHIVE_RETENTION_DAYS|ARCHIVE_STORAGE_RETENTION_DAYS)=/d' \
    -e '/^[[:space:]]*EnvironmentFile[[:space:]]*=/d' \
    "${existing_dropin}" >"${sanitized_dropin}"
  chown --reference="${existing_dropin}" "${sanitized_dropin}"
  chmod --reference="${existing_dropin}" "${sanitized_dropin}"
  mv -Tf "${sanitized_dropin}" "${existing_dropin}"
done
exec_dropin_temp=${interface_exec_dropin}.cutover.$$
printf '%s\n' \
  '[Service]' \
  'ExecStart=' \
  'ExecStart=/opt/interface-env/bin/python -m interface.serve --port 3000' \
  >"${exec_dropin_temp}"
chown root:root "${exec_dropin_temp}"
chmod 0644 "${exec_dropin_temp}"
mv -Tf "${exec_dropin_temp}" "${interface_exec_dropin}"
dropin_temp=${interface_dropin}.cutover.$$
printf '%s\n' \
  '[Service]' \
  'Environment=INTERFACE_TUI_GATEWAY_PYTHON=/opt/potato-hermes-lite/current/venv/bin/python3' \
  >"${dropin_temp}"
chown root:root "${dropin_temp}"
chmod 0644 "${dropin_temp}"
mv -Tf "${dropin_temp}" "${interface_dropin}"
systemctl daemon-reload

if [[ $(systemctl show "${model_proxy_unit}" --property=User --value) != \
      "${model_proxy_user}" ]] || \
   [[ $(systemctl show "${model_proxy_unit}" --property=Group --value) != \
      "${model_proxy_user}" ]]; then
  echo "error: model proxy unit is not loaded with its dedicated identity" >&2
  false
fi
model_proxy_environment=$(
  systemctl show "${model_proxy_unit}" --property=Environment --value
)
if [[ ${model_proxy_environment} != \
      *POTATO_MODEL_PROXY_USAGE_DB=${model_proxy_usage_db}* ]]; then
  echo "error: model proxy unit is not loaded with its dedicated usage database" >&2
  false
fi
interface_environment=$(
  systemctl show "${interface_unit}" --property=Environment --value
)
interface_environment_files=$(
  systemctl show "${interface_unit}" --property=EnvironmentFiles --value
)
environment_has_exact() {
  local expected=$1
  local values=$2
  printf '%s\n' "${values}" | tr ' ' '\n' | grep -Fxq "${expected}"
}
if [[ -n ${interface_environment_files//[[:space:]]/} ]]; then
  echo "error: Interface unit still loads an EnvironmentFile" >&2
  false
fi
if [[ ${interface_environment} == *INTERFACE_SESSION_SECRET=* ||
      ${interface_environment} == *INTERFACE_RESEND_API_KEY=* ]]; then
  echo "error: Interface secrets remain in the systemd environment" >&2
  false
fi
if ! environment_has_exact 'INTERFACE_ENVIRONMENT=production' "${interface_environment}"; then
  echo "error: Interface production guard is not loaded" >&2
  false
fi
interface_transport_profile=
if environment_has_exact 'INTERFACE_ALLOW_INSECURE_HTTP=false' "${interface_environment}" &&
   environment_has_exact 'INTERFACE_SESSION_COOKIE_SECURE=true' "${interface_environment}"; then
  interface_transport_profile=https
elif environment_has_exact 'INTERFACE_ALLOW_INSECURE_HTTP=true' "${interface_environment}" &&
     environment_has_exact 'INTERFACE_SESSION_COOKIE_SECURE=false' "${interface_environment}"; then
  interface_transport_profile=insecure-http
else
  echo "error: Interface HTTP transport profile is inconsistent" >&2
  false
fi
interface_bind_host=$(
  printf '%s\n' "${interface_environment}" |
    tr ' ' '\n' |
    sed -n 's/^INTERFACE_BIND_HOST=//p'
)
if [[ -z ${interface_bind_host} ]] || [[ ${interface_bind_host} == *$'\n'* ]]; then
  echo "error: Interface bind host is missing or ambiguous" >&2
  false
fi
if ! environment_has_exact 'INTERFACE_ARCHIVE_RETENTION_DAYS=7' "${interface_environment}" ||
   ! environment_has_exact 'INTERFACE_ARCHIVE_STORAGE_RETENTION_DAYS=30' "${interface_environment}"; then
  echo "error: Interface chat retention policy is not loaded" >&2
  false
fi
if ! environment_has_exact \
  "INTERFACE_PRIVILEGED_HELPER=${privileged_helper}" \
  "${interface_environment}"; then
  echo "error: Interface privileged helper entrypoint is not loaded" >&2
  false
fi
interface_exec_start=$(
  systemctl show "${interface_unit}" --property=ExecStart --value
)
if [[ ${interface_exec_start} != *'-m interface.serve --port 3000'* ]] || \
   [[ ${interface_exec_start} == *'--host'* ]]; then
  echo "error: Interface unit bypasses the site bind host" >&2
  false
fi
if [[ ${interface_transport_profile} == https ]]; then
  if [[ ${interface_bind_host} != 127.0.0.1 ]]; then
    echo "error: HTTPS Interface profile is not restricted to loopback" >&2
    false
  fi
elif ! "${interface_python}" -B - "${interface_bind_host}" <<'PY'
import ipaddress
import sys

try:
    address = ipaddress.ip_address(sys.argv[1])
except ValueError:
    raise SystemExit(1)
valid = (
    address.version == 4
    and not address.is_loopback
    and not address.is_unspecified
    and not address.is_multicast
)
raise SystemExit(0 if valid else 1)
PY
then
  echo "error: insecure HTTP Interface profile lacks a specific IPv4 listener" >&2
  false
fi

unit_refresh_mapping=${backup}/mapping-for-unit-refresh.yaml
PYTHONPATH="${repo}" \
"${interface_python}" -B - "${mapping}" "${unit_refresh_mapping}" "${expected_count}" <<'PY'
import os
import pathlib
import sys

import yaml

from interface.mapping import (
    build_targets_from_config,
    ensure_model_proxy_tokens,
    ensure_unique_user_api_keys,
    load_mapping,
)

source = pathlib.Path(sys.argv[1])
destination = pathlib.Path(sys.argv[2])
expected = int(sys.argv[3])
config = load_mapping(source, resolve_env=False)
ensure_model_proxy_tokens(config)
ensure_unique_user_api_keys(config)
targets = build_targets_from_config(config, resolve_env=False)
if len(targets) != expected:
    raise SystemExit(
        f"unit refresh mapping expected {expected} targets, found {len(targets)}"
    )
body = yaml.safe_dump(config, sort_keys=False, allow_unicode=False).encode("utf-8")
fd = os.open(
    destination,
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
    0o600,
)
try:
    os.write(fd, body)
    os.fsync(fd)
finally:
    os.close(fd)
PY

if PYTHONPATH="${repo}" \
  "${interface_python}" -B "${refresh_script}" \
    --mapping "${unit_refresh_mapping}" \
    --all \
    --expect-count "${expected_count}" \
    --require-existing-set \
    >"${backup}/unit-refresh-check.log" 2>&1; then
  refresh_check_rc=0
else
  refresh_check_rc=$?
fi
if [[ ${refresh_check_rc} -ne 0 && ${refresh_check_rc} -ne 1 ]]; then
  echo "error: Hermes unit preflight failed" >&2
  false
fi

PYTHONPATH="${repo}" \
"${interface_python}" -B "${refresh_script}" \
  --mapping "${unit_refresh_mapping}" \
  --apply \
  --all \
  --expect-count "${expected_count}" \
  --require-existing-set \
  >"${backup}/unit-refresh-apply.log" 2>&1
rm -f "${unit_refresh_mapping}"

if ! "${interface_python}" -B "${fingerprint_script}" capture \
  --mapping "${mapping}" \
  --data-dir "${data_dir}" \
  --output "${backup}/state-after.json"; then
  echo "error: post-cutover state fingerprint failed" >&2
  rollback 2
fi
if ! "${interface_python}" -B "${fingerprint_script}" compare \
  --before "${backup}/state-before.json" \
  --after "${backup}/state-after.json" \
  >"${backup}/state-compare.json"; then
  echo "error: protected state changed during cutover" >&2
  rollback 2
fi

if [[ ${model_proxy_was_active} -eq 1 ]]; then
  systemctl start "${model_proxy_unit}"
  systemctl is-active --quiet "${model_proxy_unit}"
  proxy_health_ok=0
  for _attempt in $(seq 1 40); do
    if curl -fsS --connect-timeout 0.5 --max-time 1 \
      http://127.0.0.1:8765/healthz \
      >"${backup}/model-proxy-health.json"; then
      proxy_health_ok=1
      break
    fi
    sleep 0.25
  done
  if [[ ${proxy_health_ok} -ne 1 ]]; then
    echo "error: model proxy health check failed" >&2
    false
  fi
fi
for service in "${active_services[@]}"; do
  systemctl start "${service}"
  systemctl is-active --quiet "${service}"
done
if [[ ${interface_was_active} -eq 1 ]]; then
  systemctl start "${interface_unit}"
  systemctl is-active --quiet "${interface_unit}"
  health_ok=0
  for _attempt in $(seq 1 40); do
    if curl -fsS --connect-timeout 0.5 --max-time 1 \
      "http://${interface_bind_host}:3000/health" \
      >"${backup}/interface-health.json"; then
      health_ok=1
      break
    fi
    sleep 0.25
  done
  if [[ ${health_ok} -ne 1 ]]; then
    echo "error: interface health check failed" >&2
    false
  fi
fi

interface_pid=$(systemctl show "${interface_unit}" --property=MainPID --value)
if [[ ${interface_was_active} -eq 1 ]]; then
  interface_python_env=$(tr '\0' '\n' <"/proc/${interface_pid}/environ" | sed -n 's/^INTERFACE_TUI_GATEWAY_PYTHON=//p')
  if [[ ${interface_python_env} != "${current}/venv/bin/python3" ]]; then
    echo "error: interface did not activate the Lite gateway Python" >&2
    false
  fi
fi
for service in "${active_services[@]}"; do
  service_pid=$(systemctl show "${service}" --property=MainPID --value)
  service_command=$(tr '\0' ' ' <"/proc/${service_pid}/cmdline")
  if [[ ${service_command} != *"${release}/venv/bin/python3"* ]]; then
    echo "error: service did not start from Lite venv: ${service}" >&2
    false
  fi
done
for service in "${mapped_services[@]}"; do
  expected_active=0
  for active_service in "${active_services[@]}"; do
    if [[ ${service} == "${active_service}" ]]; then
      expected_active=1
      break
    fi
  done
  if [[ ${expected_active} -eq 0 ]] && systemctl is-active --quiet "${service}"; then
    echo "error: inactive mapped service unexpectedly became active: ${service}" >&2
    false
  fi
done
if pgrep -f '[s]lash_worker' >"${backup}/unexpected-slash-workers.txt"; then
  echo "error: legacy slash worker appeared after cutover" >&2
  false
fi
forbidden_deployed_artifact=$(find_forbidden_deploy_artifact "${repo}")
if [[ -n ${forbidden_deployed_artifact} ]]; then
  echo "error: deployed source tree gained a cache, backup, or build artifact after service startup: ${forbidden_deployed_artifact}" >&2
  false
fi

if [[ ${model_proxy_was_active} -eq 0 ]] && \
   systemctl is-active --quiet "${model_proxy_unit}"; then
  echo "error: model proxy unexpectedly became active" >&2
  false
fi
if [[ ${interface_was_active} -eq 0 ]] && \
   systemctl is-active --quiet "${interface_unit}"; then
  echo "error: Interface unexpectedly became active" >&2
  false
fi

"${current}/venv/bin/pip" check >"${backup}/lite-pip-check.txt"
"${hermes_link}" --help >"${backup}/lite-hermes-help.txt"
printf 'complete\n' >"${backup}/result.txt"
trap - ERR INT TERM

echo "Lite cutover complete"
echo "backup: ${backup}"
echo "restored Hermes services: ${#active_services[@]}"
echo "restored model proxy: ${model_proxy_was_active}"
