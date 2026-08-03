from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_cutover_rejects_secret_state_and_removes_legacy_configurator() -> None:
    script = (
        REPO_ROOT / "hermes-lite" / "scripts" / "cutover_lite_production.sh"
    ).read_text(encoding="utf-8")

    assert "-name 'users_mapping.yaml'" in script
    assert "-name 'model_proxy.yaml'" in script
    assert "configure_hermes_model.py" in script
    assert "protected_legacy_state_paths" in script
    assert "interface/data" in script
    assert "deployed source tree contains protected runtime state" in script
    assert 'rm -f "${repo}/${relative}"' in script
    assert "deployed source tree still contains database runtime state" in script
    assert "-name '*.db-journal'" in script
    assert "-name '*.sqlite-journal'" in script
    assert "-name '*.sqlite3-journal'" in script
    assert script.count("-name '.env'") >= 2
    assert script.count("-name 'users_mapping.yaml'") >= 2
    assert script.count("-name 'model_proxy.yaml'") >= 2
    assert "code source must be root-owned and not writable by group or other" in script
    assert "code source must be readable by the deployment service group" in script
    assert "deployed source tree is not readable by the deployment service group" in script
    assert script.count("! -perm -0050") >= 2
    assert script.count("! -perm -0040") >= 2
    assert "inactive release must be root-owned and immutable to ordinary users" in script
    assert "unsafe mapping ownership or mode" in script
    assert "unsafe Interface data directory ownership or mode" in script
    assert "unsafe Interface authentication database ownership or mode" in script
    assert "unsafe Interface privileged helper ownership, mode, or file type" in script
    assert "staged_privileged_helper=" in script
    assert '"${backup}/privileged-helper"' in script
    assert '"${staged_privileged_helper}" "${privileged_helper}"' in script
    assert "privileged helper does not match the staged wrapper" in script
    assert "privileged helper verification" in script
    assert script.count('find_forbidden_deploy_artifact "${repo}"') >= 2
    assert 'systemd_file_is_safe "${privileged_helper}"' in script
    assert "'root:root:755'" in script


def test_cutover_uses_private_service_boundaries() -> None:
    script = (
        REPO_ROOT / "hermes-lite" / "scripts" / "cutover_lite_production.sh"
    ).read_text(encoding="utf-8")

    assert "model_proxy_user=potato-model-proxy" in script
    assert "model_proxy_usage_db=${model_proxy_state_dir}/usage.db" in script
    assert "Interface secrets remain in the systemd environment" in script
    assert "Interface unit still loads an EnvironmentFile" in script
    assert '"${staged_interface_unit}" "${interface_unit_file}"' in script
    assert '"${staged_model_proxy_unit}" "${model_proxy_unit_file}"' in script
    assert '"${backup}/service-units/${unit_name}"' in script
    assert '"${backup}/interface-dropins/$(basename "${dropin_file}")"' in script
    assert "EnvironmentFile[[:space:]]*=" in script
    assert "ARCHIVE_RETENTION_DAYS|ARCHIVE_STORAGE_RETENTION_DAYS" in script
    assert "Interface chat retention policy is not loaded" in script
    assert "environment_has_exact 'INTERFACE_ARCHIVE_RETENTION_DAYS=7'" in script
    assert "environment_has_exact 'INTERFACE_ARCHIVE_STORAGE_RETENTION_DAYS=30'" in script
    assert "INTERFACE privileged helper entrypoint is not loaded".lower() in script.lower()
    assert '"INTERFACE_PRIVILEGED_HELPER=${privileged_helper}"' in script
    assert "INTERFACE_ALLOW_INSECURE_HTTP=false" in script
    assert "INTERFACE_SESSION_COOKIE_SECURE=true" in script
    assert "INTERFACE_ALLOW_INSECURE_HTTP=true" in script
    assert "INTERFACE_SESSION_COOKIE_SECURE=false" in script
    assert "INTERFACE_BIND_HOST=" in script
    assert "interface_transport_profile=https" in script
    assert "interface_transport_profile=insecure-http" in script
    assert "Interface HTTP transport profile is inconsistent" in script
    assert "Interface unit bypasses the site bind host" in script
    assert "insecure HTTP Interface profile lacks a specific IPv4 listener" in script
    assert "migrate_model_proxy_usage.py" in script
    assert "mapping-for-unit-refresh.yaml" in script
    assert "ensure_unique_user_api_keys(config)" in script
    assert '--mapping "${unit_refresh_mapping}"' in script
    assert 'if [[ -e ${model_proxy_usage_db} || -L ${model_proxy_usage_db} ]]' in script
    assert "model_proxy_usage_db_existed=0" in script
    assert 'if [[ ${model_proxy_usage_db_existed} -eq 0 ]]' in script
    assert "dedicated usage database already existed" in script
    assert '--destination "${model_proxy_usage_db}"' in script
    helper_backup = script.index(
        'cp -a "${privileged_helper}" "${backup}/privileged-helper"'
    )
    cutover_start = script.index("cutover_started=1")
    helper_install = script.index(
        '"${staged_privileged_helper}" "${privileged_helper}"', cutover_start
    )
    assert helper_backup < cutover_start < helper_install
    rollback = script[script.index("rollback()"):cutover_start]
    assert '"${backup}/privileged-helper" "${privileged_helper}"' in rollback
    assert 'cmp -s "${backup}/privileged-helper" "${privileged_helper}"' in rollback
    stop_proxy = script.index('systemctl stop "${model_proxy_unit}"', cutover_start)
    migrate_usage = script.index(
        '"${interface_python}" -B "${usage_migration_script}"', cutover_start
    )
    install_units = script.index(
        '"${staged_interface_unit}" "${interface_unit_file}"', cutover_start
    )
    assert stop_proxy < migrate_usage < install_units
    assert script.index('"${backup}/service-units/${unit_name}"') < cutover_start
    assert script.index('"${backup}/interface-dropins/$(basename "${dropin_file}")"') < cutover_start
    assert (
        "'ExecStart=/opt/interface-env/bin/python -m interface.serve "
        "--port 3000'"
    ) in script
    assert "interface.serve --host" not in script
    assert '"http://${interface_bind_host}:3000/health"' in script


def test_packaged_interface_unit_pins_chat_retention_policy() -> None:
    service = (
        REPO_ROOT / "packaging" / "systemd" / "potato-interface.service"
    ).read_text(encoding="utf-8")

    assert "Environment=INTERFACE_ARCHIVE_RETENTION_DAYS=7" in service
    assert "Environment=INTERFACE_ARCHIVE_STORAGE_RETENTION_DAYS=30" in service
    assert "Environment=INTERFACE_ALLOW_INSECURE_HTTP=false" in service
    assert "Environment=INTERFACE_BIND_HOST=127.0.0.1" in service
    assert "Environment=INTERFACE_ALLOW_INSECURE_HTTP=true" not in service
    assert "--host" not in service
    assert (
        "Environment=INTERFACE_PRIVILEGED_HELPER="
        "/usr/local/libexec/potato-agent-privileged-helper"
    ) in service


def test_packaged_privileged_helper_disables_bytecode_and_pins_source() -> None:
    helper = (
        REPO_ROOT / "packaging" / "libexec" / "potato-agent-privileged-helper"
    ).read_text(encoding="utf-8")

    assert "export PYTHONPATH=/srv/potato_agent" in helper
    assert "${PYTHONPATH" not in helper
    assert (
        'exec /opt/interface-env/bin/python -B -m '
        'interface.privileged_helper "$@"'
    ) in helper


def test_cutover_preserves_site_transport_dropins_and_restores_them() -> None:
    script = (
        REPO_ROOT / "hermes-lite" / "scripts" / "cutover_lite_production.sh"
    ).read_text(encoding="utf-8")

    sanitize_start = script.index(
        'for existing_dropin in "${interface_dropin_dir}"/*.conf'
    )
    sanitize_end = script.index("systemctl daemon-reload", sanitize_start)
    sanitizer = script[sanitize_start:sanitize_end]
    assert "INTERFACE_ALLOW_INSECURE_HTTP" not in sanitizer
    assert "INTERFACE_BIND_HOST" not in sanitizer
    assert "INTERFACE_SESSION_COOKIE_SECURE" not in sanitizer

    rollback_start = script.index("rollback()")
    cutover_start = script.index("cutover_started=1")
    rollback = script[rollback_start:cutover_start]
    assert 'if [[ -d ${backup}/interface-dropins ]]' in rollback
    assert '"${interface_dropin_dir}/$(basename "${dropin_backup}")"' in rollback
    assert "systemd_file_is_safe" in script
    assert "stat -c '%u:%g:%h'" in script
    assert "unsafe Interface drop-in" in script


def test_cutover_mirrors_staged_source_with_complete_code_rollback() -> None:
    script = (
        REPO_ROOT / "hermes-lite" / "scripts" / "cutover_lite_production.sh"
    ).read_text(encoding="utf-8")

    for artifact in (
        "'.codex-tmp'",
        "'.deploy-backups'",
        "'.pytest_cache'",
        "'.ruff_cache'",
        "'__pycache__'",
        "'build'",
        "'dist'",
        "'node_modules'",
        "'*.egg-info'",
        "'*.bak-*'",
        "'*.pyc'",
    ):
        assert f"-name {artifact}" in script

    assert "mapping {label} overlaps deployed source tree" in script
    assert "code source contains a nested mount" in script
    assert "deployed source tree contains a nested mount" in script
    assert "code source and deployed source tree must not overlap" in script
    assert '"${backup}/code-before"' in script
    assert '"${backup}/code-before/" "${repo}/"' in script
    assert '[[ -f ${backup}/code-before.complete ]]' in script
    assert script.count("--checksum") >= 3

    cutover_start = script.index("cutover_started=1")
    fingerprint = script.index('"${backup}/state-before.json"', cutover_start)
    snapshot = script.index('"${repo}/" "${backup}/code-before/"', fingerprint)
    snapshot_complete = script.index(
        ': >"${backup}/code-before.complete"', snapshot
    )
    forward_sync = script.index(
        '"${code_source}/" "${repo}/"', snapshot_complete
    )
    assert fingerprint < snapshot < snapshot_complete < forward_sync

    sync_block = script[snapshot_complete:forward_sync]
    assert "rsync -aHAX" in sync_block
    assert "--one-file-system" in sync_block
    assert "--delete-delay" in sync_block
    assert '--backup-dir="${backup}/code-overwritten"' in sync_block

    verification = script[forward_sync : script.index("atomic_symlink", forward_sync)]
    assert "--dry-run" in verification
    assert '[[ -s ${backup}/code-sync-verify.txt ]]' in verification
    assert 'find_forbidden_deploy_artifact "${repo}"' in verification
    assert "-perm /022" in verification
    assert "! -uid 0" in verification
    assert "deployed source tree contains a symlink or special file" in verification


def test_cutover_fails_closed_when_rollback_is_incomplete() -> None:
    script = (
        REPO_ROOT / "hermes-lite" / "scripts" / "cutover_lite_production.sh"
    ).read_text(encoding="utf-8")
    rollback_start = script.index("rollback()")
    cutover_start = script.index("trap rollback ERR", rollback_start)
    rollback = script[rollback_start:cutover_start]

    assert "rollback_failed=0" in rollback
    assert "rollback_error()" in rollback
    assert "rollback-code-verify.txt" in rollback
    assert "rollback code verification drift" in rollback
    assert "services remain stopped" in rollback
    assert "rollback_failed\\n" in rollback
    assert "exit 125" in rollback
    verify_index = rollback.index("rollback-code-verify.txt")
    restart_index = rollback.index('systemctl start "${model_proxy_unit}"')
    assert verify_index < restart_index


def test_cutover_serializes_runs_and_drains_only_signup_jobs() -> None:
    script = (
        REPO_ROOT / "hermes-lite" / "scripts" / "cutover_lite_production.sh"
    ).read_text(encoding="utf-8")

    assert 'cutover_lock=${cutover_lock_dir}/maintenance.lock' in script
    assert "flock -n 9" in script
    assert "another maintenance transition or Lite cutover is already running" in script
    assert 'if ! mkdir -m 0700 "${backup}"' in script
    assert "wait_for_signup_jobs 60" in script
    assert '"${signup_drain_script}"' in script
    assert '--db "${auth_db}" wait --timeout "${timeout_seconds}"' in script
    assert "assert_no_provisioning_signup_jobs" in script
    assert '--db "${auth_db}" check-provisioning' in script
    assert "assert_runtime_idle" not in script
    assert "session_live_state" not in script
    assert "runtime_leases" not in script
    assert "turn_submission_receipts" not in script
    assert "read_stable_unit_state" in script
    assert "stable active/inactive state" in script
    assert "active Interface requires an active model proxy" in script
    assert script.count("--connect-timeout 0.5 --max-time 1") == 2


def test_cutover_enters_maintenance_after_online_checks_and_before_mutation() -> None:
    script = (
        REPO_ROOT / "hermes-lite" / "scripts" / "cutover_lite_production.sh"
    ).read_text(encoding="utf-8")

    signup_wait = script.index("wait_for_signup_jobs 60")
    enter = script.index('POTATO_MAINTENANCE_LOCK_FD=9 "${maintenance_ctl}" enter')
    stop_proxy = script.index('systemctl stop "${model_proxy_unit}"', enter)
    provisioning_check = script.index("assert_no_provisioning_signup_jobs", enter)
    fingerprint = script.index('"${backup}/state-before.json"', enter)
    source_sync = script.index('"${code_source}/" "${repo}/"', fingerprint)

    assert script.index('"${release}/venv/bin/pip" check') < signup_wait
    assert script.index('cp -a "${mapping}" "${backup}/users_mapping.yaml"') < signup_wait
    assert signup_wait < enter < stop_proxy < provisioning_check < fingerprint < source_sync


def test_cutover_recovers_interface_only_after_runtime_services() -> None:
    script = (
        REPO_ROOT / "hermes-lite" / "scripts" / "cutover_lite_production.sh"
    ).read_text(encoding="utf-8")
    cutover = script[script.index("wait_for_signup_jobs 60") :]

    start_proxy = cutover.index('systemctl start "${model_proxy_unit}"')
    start_hermes = cutover.index('systemctl start "${service}"')
    leave = cutover.index('POTATO_MAINTENANCE_LOCK_FD=9 "${maintenance_ctl}" leave')
    assert start_proxy < start_hermes < leave
    assert "maintenance service remained active after Interface recovery" in cutover

    rollback = script[script.index("rollback()") : script.index("trap rollback ERR")]
    assert "maintenance remains active" in rollback
    assert 'POTATO_MAINTENANCE_LOCK_FD=9 "${maintenance_ctl}" leave' in rollback
    stop_interface = rollback.index('systemctl stop "${interface_unit}"')
    restart_maintenance = rollback.index('systemctl start "${maintenance_unit}"')
    restore_code = rollback.index('"${backup}/code-before/" "${repo}/"')
    assert stop_interface < restart_maintenance < restore_code


def test_cutover_rejects_runtime_paths_that_overlap_the_deploy_tree() -> None:
    script = (
        REPO_ROOT / "hermes-lite" / "scripts" / "cutover_lite_production.sh"
    ).read_text(encoding="utf-8")

    mapping_start = script.index("mapfile -t mapped_services")
    mapping_end = script.index("if [[ ${#mapped_services[@]}", mapping_start)
    mapping_validation = script[mapping_start:mapping_end]
    assert '"home_dir": home_dir' in mapping_validation
    assert '"hermes_home":' in mapping_validation
    assert '"workdir":' in mapping_validation
    assert "candidate.is_absolute()" in mapping_validation
    assert "overlaps(lexical, repo_lexical)" in mapping_validation
    assert "overlaps(resolved, repo_resolved)" in mapping_validation


def test_readme_staging_excludes_generated_artifacts_and_uses_dynamic_count() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert '-m 0700 "$BUILD_ROOT"' in readme
    cutover_start = readme.index("CODE_SOURCE=$BUILD_ROOT/code-source")
    cutover_end = readme.index(
        "readlink -f /opt/potato-hermes-lite/current", cutover_start
    )
    cutover = readme[cutover_start:cutover_end]

    for exclusion in (
        "--exclude '*.egg-info/'",
        "--exclude 'build/'",
        "--exclude 'dist/'",
        "--exclude 'node_modules/'",
        "--exclude '__pycache__/'",
        "--exclude '*.pyc'",
    ):
        assert exclusion in cutover

    assert 'EXPECTED_USER_COUNT=$(' in cutover
    assert '"$CODE_SOURCE" "$RELEASE_ID" "$EXPECTED_USER_COUNT"' in cutover
    assert 'chmod -R a+rX,go-w "$CODE_SOURCE"' in cutover
    assert 'find "$CODE_SOURCE" -type f ! -perm -0040' in cutover
    assert re.search(r"EXPECTED_USER_COUNT\s*=\s*[0-9]+", readme) is None
    assert re.search(r"--expect-count\s+[\"']?[0-9]+", readme) is None
