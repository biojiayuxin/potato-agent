from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_model_proxy_service_uses_dedicated_identity_and_state() -> None:
    unit = (
        REPO_ROOT / "packaging" / "systemd" / "potato-model-proxy.service"
    ).read_text(encoding="utf-8")

    assert "User=potato-model-proxy" in unit
    assert "Group=potato-model-proxy" in unit
    assert "SupplementaryGroups=potato-interface" in unit
    assert (
        "Environment=POTATO_MODEL_PROXY_USAGE_DB="
        "/var/lib/potato-agent/model-proxy/usage.db"
    ) in unit
    assert "Environment=INTERFACE_AUTH_DB=" not in unit
    assert "ReadWritePaths=/var/lib/potato-agent/model-proxy" in unit
    assert "ReadWritePaths=/var/lib/potato-agent/data" not in unit
    assert "ProtectSystem=strict" in unit
    assert "ProtectProc=invisible" in unit
    assert "ProcSubset=pid" in unit
    assert "NoNewPrivileges=true" in unit
    assert "LimitCORE=0" in unit
    assert "--host 127.0.0.1 --port 8765" in unit
