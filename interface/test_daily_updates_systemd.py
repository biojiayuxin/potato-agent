from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SYSTEMD_ROOT = REPO_ROOT / "packaging" / "systemd"
RUNBOOK_PATH = REPO_ROOT / "interface" / "DAILY_UPDATES.md"
HPC_DEPLOYMENT_PATH = REPO_ROOT / "HPC_DEPLOYMENT.md"


def test_daily_updates_service_uses_dedicated_identity_and_credentials() -> None:
    unit = (SYSTEMD_ROOT / "potato-daily-updates.service").read_text(encoding="utf-8")
    credential = (
        "LoadCredential=daily-updates-model-proxy-token:"
        "/etc/potato-agent/credentials/daily-updates-model-proxy-token"
    )

    assert "Type=oneshot" in unit
    assert "User=potato-daily-updates" in unit
    assert "Group=potato-daily-updates" in unit
    assert "SupplementaryGroups=potato-interface" in unit
    assert "Environment=DAILY_UPDATES_PRODUCTION=1" in unit
    assert "Environment=DAILY_UPDATES_DB_PATH=/srv/daily_updates/data/daily_updates.sqlite" in unit
    assert credential in unit
    assert "POTATO_DAILY_UPDATES_MODEL_PROXY_TOKEN=" not in unit
    assert "DAILY_UPDATES_PUBMED_API_KEY=" not in unit
    assert "ReadWritePaths=/srv/daily_updates/data" in unit
    assert "ReadOnlyPaths=/srv/potato_agent" in unit
    assert "InaccessiblePaths=-/var/lib/potato-agent -/etc/potato-agent" in unit
    assert "ProtectSystem=strict" in unit
    assert "ProtectHome=true" in unit
    assert "NoNewPrivileges=true" in unit
    assert "TimeoutStartSec=7200" in unit
    assert "-m interface.daily_updates_job run" in unit


def test_daily_updates_timer_runs_persistently_at_shanghai_0800() -> None:
    unit = (SYSTEMD_ROOT / "potato-daily-updates.timer").read_text(encoding="utf-8")

    assert "OnCalendar=*-*-* 08:00:00 Asia/Shanghai" in unit
    assert "AccuracySec=1min" in unit
    assert "RandomizedDelaySec=0" in unit
    assert "Persistent=true" in unit
    assert "Unit=potato-daily-updates.service" in unit
    assert "WantedBy=timers.target" in unit


def test_interface_and_proxy_receive_only_required_daily_updates_access() -> None:
    interface_unit = (SYSTEMD_ROOT / "potato-interface.service").read_text(encoding="utf-8")
    proxy_unit = (SYSTEMD_ROOT / "potato-model-proxy.service").read_text(encoding="utf-8")
    interface_dropin = (SYSTEMD_ROOT / "potato-interface-daily-updates.conf").read_text(
        encoding="utf-8"
    )
    proxy_dropin = (SYSTEMD_ROOT / "potato-model-proxy-daily-updates.conf").read_text(
        encoding="utf-8"
    )
    credential = (
        "LoadCredential=daily-updates-model-proxy-token:"
        "/etc/potato-agent/credentials/daily-updates-model-proxy-token"
    )

    assert "SupplementaryGroups=potato-daily-updates" not in interface_unit
    assert "LoadCredential=daily-updates-model-proxy-token:" not in interface_unit
    assert "SupplementaryGroups=potato-daily-updates" in interface_dropin
    assert "Environment=DAILY_UPDATES_DB_PATH=/srv/daily_updates/data/daily_updates.sqlite" in interface_dropin
    assert "Environment=INTERFACE_ENVIRONMENT=production" in proxy_unit
    assert "LoadCredential=daily-updates-model-proxy-token:" not in proxy_unit
    assert credential in proxy_dropin
    assert "SupplementaryGroups=potato-daily-updates" not in proxy_unit
    assert "ReadWritePaths=/srv/daily_updates" not in proxy_unit


def test_daily_updates_runbook_preserves_operational_boundaries() -> None:
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")

    assert "--groups potato-interface" not in runbook
    assert "usermod -a -G potato-interface potato-daily-updates" not in runbook
    assert "gpasswd --delete potato-daily-updates potato-interface" in runbook
    assert "/etc/systemd/system/potato-daily-updates.service.d" in runbook
    assert "systemctl show potato-daily-updates.service --property=Environment" in runbook
    assert "replace the example PubMed contact email before continuing" in runbook
    assert "os.O_EXCL | os.O_NOFOLLOW" in runbook
    assert "openssl rand" not in runbook
    assert "source.backup(destination)" in runbook
    assert 'source.execute("PRAGMA integrity_check")' in runbook
    assert 'destination.execute("PRAGMA integrity_check")' in runbook
    assert "root:root:600" in runbook
    assert "## Unified database transfer" in runbook
    assert "PRAGMA foreign_key_check" in runbook
    assert "Do not run the legacy three-database migration afterward" in runbook


def test_hpc_deployment_marks_daily_updates_as_a_separate_deployment() -> None:
    deployment_guide = HPC_DEPLOYMENT_PATH.read_text(encoding="utf-8")

    assert "#### Daily Updates（需单独部署）" in deployment_guide
    assert "只复制数据库只能让前端读取已有内容" in deployment_guide
    assert "/srv/daily_updates/data/daily_updates.sqlite" in deployment_guide
    assert "potato-daily-updates:potato-daily-updates 0640" in deployment_guide
    assert "同步统一数据库时跳过旧 Knowledge" in deployment_guide
