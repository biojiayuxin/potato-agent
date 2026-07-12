from __future__ import annotations

from types import SimpleNamespace

from interface import app as app_mod


def _job() -> dict[str, str]:
    return {
        "job_id": "job-1",
        "username": "alice",
        "email": "alice@example.com",
        "display_name": "Alice",
    }


def test_signup_job_sync_completes_as_one_side_effect_unit(monkeypatch) -> None:
    events: list[tuple] = []
    monkeypatch.setattr(
        app_mod,
        "set_signup_job_status",
        lambda job_id, **kwargs: events.append(("status", job_id, kwargs)),
    )
    monkeypatch.setattr(
        app_mod.privileged_client,
        "provision_user",
        lambda **kwargs: events.append(("provision", kwargs)),
    )
    monkeypatch.setattr(app_mod, "_reset_mapping_store_cache", lambda: None)
    monkeypatch.setattr(
        app_mod.mapping_store,
        "get_target_by_username",
        lambda username: SimpleNamespace(username=username),
    )
    monkeypatch.setattr(
        app_mod,
        "activate_signup_user",
        lambda job_id, **kwargs: events.append(("activate", job_id, kwargs)),
    )

    app_mod._process_signup_job_sync(_job())

    assert events == [
        ("status", "job-1", {"status": "provisioning"}),
        (
            "provision",
            {
                "username": "alice",
                "email": "alice@example.com",
                "display_name": "Alice",
            },
        ),
        ("activate", "job-1", {"mapping_username": "alice"}),
        ("status", "job-1", {"status": "completed"}),
    ]


def test_signup_job_sync_rolls_back_before_marking_failed(monkeypatch) -> None:
    events: list[tuple] = []
    monkeypatch.setattr(
        app_mod,
        "set_signup_job_status",
        lambda job_id, **kwargs: events.append(("status", job_id, kwargs)),
    )
    monkeypatch.setattr(
        app_mod.privileged_client,
        "provision_user",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("provision failed")),
    )
    monkeypatch.setattr(app_mod, "_reset_mapping_store_cache", lambda: None)
    monkeypatch.setattr(
        app_mod.privileged_client,
        "deprovision_user",
        lambda username, **kwargs: events.append(("deprovision", username, kwargs)),
    )
    monkeypatch.setattr(
        app_mod.privileged_client,
        "remove_mapping",
        lambda username: events.append(("remove_mapping", username)),
    )

    app_mod._process_signup_job_sync(_job())

    assert events == [
        ("status", "job-1", {"status": "provisioning"}),
        ("deprovision", "alice", {"delete_home": True}),
        ("remove_mapping", "alice"),
        (
            "status",
            "job-1",
            {"status": "failed", "error_message": "provision failed"},
        ),
    ]
