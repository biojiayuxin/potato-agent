from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from interface import chat_share_store as store


NOW = 1_800_000_000
MESSAGES = [
    {"role": "user", "content": "How do I inspect this dataset?"},
    {"role": "assistant", "content": "Start by checking its schema."},
]


def _create_share(db_path: Path, **overrides):
    kwargs = {
        "owner_user_id": "owner-1",
        "source_session_id": "source-session-1",
        "title": "Shared analysis",
        "messages": MESSAGES,
        "now": NOW,
        "db_path": db_path,
    }
    kwargs.update(overrides)
    return store.create_chat_share(**kwargs)


def _table_rows(db_path: Path, table: str) -> list[sqlite3.Row]:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        return list(conn.execute(f"select * from {table}"))


def _database_bytes(db_path: Path) -> bytes:
    return b"".join(
        path.read_bytes()
        for path in sorted(db_path.parent.glob(f"{db_path.name}*"))
        if path.is_file()
    )


def test_share_persists_only_token_hash_and_exact_seven_day_expiry(tmp_path) -> None:
    db_path = tmp_path / "chat-shares.db"
    created = _create_share(db_path)

    assert created.expires_at == NOW + 7 * 24 * 60 * 60
    assert created.message_count == len(MESSAGES)

    rows = _table_rows(db_path, "chat_shares")
    assert len(rows) == 1
    row = rows[0]
    expected_hash = hashlib.sha256(created.token.encode("utf-8")).hexdigest()
    assert row["token_sha256"] == expected_hash
    database_bytes = _database_bytes(db_path)
    assert created.token.encode("utf-8") not in database_bytes
    assert expected_hash.encode("ascii") in database_bytes

    with sqlite3.connect(str(db_path)) as conn:
        column_names = {
            str(column[1]) for column in conn.execute("pragma table_info(chat_shares)")
        }
    assert "token" not in column_names
    assert "token_sha256" in column_names


def test_snapshot_strips_non_transcript_fields_and_enforces_size_limits(
    tmp_path,
) -> None:
    db_path = tmp_path / "chat-shares.db"
    created = _create_share(
        db_path,
        messages=[
            {
                "role": "user",
                "content": "visible question",
                "attachments": [{"path": "/private/source.fastq"}],
                "metadata": {"workspace": "/home/owner"},
            },
            {
                "role": "assistant",
                "content": "visible answer",
                "reasoning": "private chain of thought",
                "tool_calls": [{"name": "read_file", "arguments": {"path": "/etc"}}],
            },
        ],
    )

    row = _table_rows(db_path, "chat_shares")[0]
    assert json.loads(row["snapshot_json"]) == {
        "version": 1,
        "messages": [
            {"role": "user", "content": "visible question"},
            {"role": "assistant", "content": "visible answer"},
        ],
    }
    database_bytes = _database_bytes(db_path)
    assert b"private chain of thought" not in database_bytes
    assert b"/private/source.fastq" not in database_bytes
    assert created.message_count == 2

    with pytest.raises(store.ChatShareValidationError):
        _create_share(
            tmp_path / "too-many.db",
            messages=[{"role": "user", "content": "x"}] * 201,
        )
    with pytest.raises(store.ChatShareValidationError):
        _create_share(
            tmp_path / "message-too-large.db",
            messages=[{"role": "user", "content": "x" * (64 * 1024 + 1)}],
        )
    with pytest.raises(store.ChatShareValidationError):
        _create_share(
            tmp_path / "snapshot-too-large.db",
            messages=[{"role": "assistant", "content": "x" * 60_000}] * 9,
        )
    with pytest.raises(store.ChatShareValidationError):
        _create_share(
            tmp_path / "unsupported-role.db",
            messages=[{"role": "tool", "content": "secret tool output"}],
        )


def test_share_title_uses_sanitized_source_title(tmp_path) -> None:
    db_path = tmp_path / "chat-shares.db"
    created = _create_share(
        db_path,
        title="  Original\nanalysis\u202etitle  ",
        messages=[
            {"role": "assistant", "content": "Assistant preface"},
            {"role": "user", "content": "  First\nuser\u202etitle  "},
            {"role": "user", "content": "Second user message"},
        ],
    )

    claim = store.claim_chat_share_import(
        token=created.token,
        recipient_user_id="recipient-title",
        now=NOW + 1,
        db_path=db_path,
    )
    assert claim.title == "Original analysistitle"


def test_share_title_falls_back_to_sanitized_user_text(tmp_path) -> None:
    db_path = tmp_path / "chat-shares.db"
    created = _create_share(
        db_path,
        title="\u202e",
        messages=[
            {"role": "assistant", "content": "Assistant preface"},
            {"role": "user", "content": "  First\nuser\u202etitle  "},
        ],
    )

    claim = store.claim_chat_share_import(
        token=created.token,
        recipient_user_id="recipient-fallback-title",
        now=NOW + 1,
        db_path=db_path,
    )
    assert claim.title == "First usertitle"


@pytest.mark.parametrize("content", [[{"type": "image_url", "url": "secret"}], {"text": "secret"}])
def test_snapshot_rejects_non_text_content(tmp_path, content) -> None:
    with pytest.raises(store.ChatShareValidationError):
        _create_share(
            tmp_path / "chat-shares.db",
            messages=[{"role": "user", "content": content}],
        )


def test_claim_is_idempotent_across_lease_retry_and_completion(tmp_path) -> None:
    db_path = tmp_path / "chat-shares.db"
    created = _create_share(db_path)

    first = store.claim_chat_share_import(
        token=created.token,
        recipient_user_id="recipient-1",
        now=NOW + 1,
        db_path=db_path,
    )
    duplicate = store.claim_chat_share_import(
        token=created.token,
        recipient_user_id="recipient-1",
        now=NOW + 2,
        db_path=db_path,
    )

    assert first.status == store.CLAIM_STATUS_CLAIMED
    assert duplicate.status == store.CLAIM_STATUS_IN_PROGRESS
    assert duplicate.imported_session_id == first.imported_session_id
    assert duplicate.claim_id == ""
    assert store.chat_share_import_claim_is_valid(
        share_id=first.share_id,
        recipient_user_id="recipient-1",
        claim_id=first.claim_id,
        now=NOW + 29,
        db_path=db_path,
    )

    reclaimed = store.claim_chat_share_import(
        token=created.token,
        recipient_user_id="recipient-1",
        now=NOW + store.CHAT_SHARE_IMPORT_LEASE_SECONDS + 1,
        db_path=db_path,
    )
    assert reclaimed.status == store.CLAIM_STATUS_CLAIMED
    assert reclaimed.claim_id != first.claim_id
    assert reclaimed.imported_session_id == first.imported_session_id
    assert not store.complete_chat_share_import(
        share_id=first.share_id,
        recipient_user_id="recipient-1",
        claim_id=first.claim_id,
        imported_title="stale writer",
        now=NOW + 32,
        db_path=db_path,
    )
    assert store.complete_chat_share_import(
        share_id=reclaimed.share_id,
        recipient_user_id="recipient-1",
        claim_id=reclaimed.claim_id,
        imported_title="Imported copy",
        now=NOW + 32,
        db_path=db_path,
    )

    completed = store.claim_chat_share_import(
        token=created.token,
        recipient_user_id="recipient-1",
        now=NOW + 33,
        db_path=db_path,
    )
    assert completed.status == store.CLAIM_STATUS_COMPLETED
    assert completed.imported_session_id == first.imported_session_id
    assert completed.imported_title == "Imported copy"
    assert len(_table_rows(db_path, "chat_share_imports")) == 1


def test_concurrent_claims_for_one_recipient_allocate_one_import_session(
    tmp_path,
) -> None:
    db_path = tmp_path / "chat-shares.db"
    created = _create_share(db_path)
    worker_count = 12
    barrier = threading.Barrier(worker_count)

    def claim_once(_index: int):
        barrier.wait(timeout=10)
        return store.claim_chat_share_import(
            token=created.token,
            recipient_user_id="recipient-1",
            now=NOW + 1,
            db_path=db_path,
        )

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        claims = list(executor.map(claim_once, range(worker_count)))

    assert sum(claim.status == store.CLAIM_STATUS_CLAIMED for claim in claims) == 1
    assert sum(
        claim.status == store.CLAIM_STATUS_IN_PROGRESS for claim in claims
    ) == worker_count - 1
    assert len({claim.imported_session_id for claim in claims}) == 1
    assert len(_table_rows(db_path, "chat_share_imports")) == 1


def test_concurrent_distinct_recipients_cannot_overshoot_the_cap(tmp_path) -> None:
    db_path = tmp_path / "chat-shares.db"
    created = _create_share(db_path)
    max_recipients = 10
    worker_count = 24
    barrier = threading.Barrier(worker_count)

    def claim_once(index: int):
        barrier.wait(timeout=10)
        return store.claim_chat_share_import(
            token=created.token,
            recipient_user_id=f"concurrent-recipient-{index}",
            max_recipients=max_recipients,
            now=NOW + 1,
            db_path=db_path,
        )

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        claims = list(executor.map(claim_once, range(worker_count)))

    assert sum(claim.status == store.CLAIM_STATUS_CLAIMED for claim in claims) == 10
    assert sum(
        claim.status == store.CLAIM_STATUS_RECIPIENT_LIMIT for claim in claims
    ) == worker_count - max_recipients
    assert len(_table_rows(db_path, "chat_share_imports")) == max_recipients
    assert _table_rows(db_path, "chat_shares")[0]["recipient_count"] == max_recipients


def test_create_rate_limit_is_atomic_and_invalidation_does_not_reset_it(
    tmp_path,
) -> None:
    db_path = tmp_path / "chat-shares.db"
    created = [
        _create_share(db_path, source_session_id=f"source-{index}")
        for index in range(store.CHAT_SHARE_CREATE_RATE_LIMIT)
    ]
    for index in range(store.CHAT_SHARE_CREATE_RATE_LIMIT):
        assert store.invalidate_source_session_shares(
            "owner-1", f"source-{index}", now=NOW + 1, db_path=db_path
        ) == 1

    with pytest.raises(store.ChatShareLimitError) as error:
        _create_share(db_path, source_session_id="source-over-rate", now=NOW + 1)
    assert error.value.code == store.LIMIT_CODE_CREATE_RATE
    assert error.value.retry_after == store.CHAT_SHARE_RATE_WINDOW_SECONDS - 1
    assert len(created) == store.CHAT_SHARE_CREATE_RATE_LIMIT


def test_concurrent_creates_cannot_exceed_hourly_limit(tmp_path) -> None:
    db_path = tmp_path / "chat-shares.db"
    worker_count = store.CHAT_SHARE_CREATE_RATE_LIMIT + 6
    barrier = threading.Barrier(worker_count)

    def create_once(index: int) -> str:
        barrier.wait(timeout=10)
        try:
            _create_share(db_path, source_session_id=f"concurrent-{index}")
        except store.ChatShareLimitError as exc:
            return exc.code
        return "created"

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(executor.map(create_once, range(worker_count)))

    assert results.count("created") == store.CHAT_SHARE_CREATE_RATE_LIMIT
    assert results.count(store.LIMIT_CODE_CREATE_RATE) == (
        worker_count - store.CHAT_SHARE_CREATE_RATE_LIMIT
    )
    assert len(_table_rows(db_path, "chat_shares")) == store.CHAT_SHARE_CREATE_RATE_LIMIT


def test_active_share_limit_spans_rate_windows_and_releases_on_invalidation(
    tmp_path,
) -> None:
    db_path = tmp_path / "chat-shares.db"
    for index in range(10):
        _create_share(db_path, source_session_id=f"first-window-{index}", now=NOW)
    for index in range(10):
        _create_share(
            db_path,
            source_session_id=f"second-window-{index}",
            now=NOW + store.CHAT_SHARE_RATE_WINDOW_SECONDS,
        )

    third_window = NOW + 2 * store.CHAT_SHARE_RATE_WINDOW_SECONDS
    with pytest.raises(store.ChatShareLimitError) as error:
        _create_share(db_path, source_session_id="over-active-limit", now=third_window)
    assert error.value.code == store.LIMIT_CODE_ACTIVE_SHARES
    assert error.value.retry_after > 0

    assert store.invalidate_source_session_shares(
        "owner-1", "first-window-0", now=third_window, db_path=db_path
    ) == 1
    replacement = _create_share(
        db_path,
        source_session_id="replacement-after-revocation",
        now=third_window,
    )
    assert replacement.share_id


def test_import_rate_limit_counts_distinct_shares_but_not_idempotent_retries(
    tmp_path,
) -> None:
    db_path = tmp_path / "chat-shares.db"
    shares = [
        _create_share(
            db_path,
            owner_user_id=f"owner-{index}",
            source_session_id=f"source-{index}",
        )
        for index in range(store.CHAT_SHARE_IMPORT_RATE_LIMIT + 1)
    ]
    claims = [
        store.claim_chat_share_import(
            token=share.token,
            recipient_user_id="rate-limited-recipient",
            now=NOW + 1,
            db_path=db_path,
        )
        for share in shares[: store.CHAT_SHARE_IMPORT_RATE_LIMIT]
    ]
    assert {claim.status for claim in claims} == {store.CLAIM_STATUS_CLAIMED}

    duplicate = store.claim_chat_share_import(
        token=shares[0].token,
        recipient_user_id="rate-limited-recipient",
        now=NOW + 2,
        db_path=db_path,
    )
    assert duplicate.status == store.CLAIM_STATUS_IN_PROGRESS
    assert duplicate.imported_session_id == claims[0].imported_session_id

    limited = store.claim_chat_share_import(
        token=shares[-1].token,
        recipient_user_id="rate-limited-recipient",
        now=NOW + 2,
        db_path=db_path,
    )
    assert limited.status == store.CLAIM_STATUS_RATE_LIMITED
    assert limited.retry_after == store.CHAT_SHARE_RATE_WINDOW_SECONDS - 1

    after_window = store.claim_chat_share_import(
        token=shares[-1].token,
        recipient_user_id="rate-limited-recipient",
        now=NOW + store.CHAT_SHARE_RATE_WINDOW_SECONDS + 1,
        db_path=db_path,
    )
    assert after_window.status == store.CLAIM_STATUS_CLAIMED


def test_cleanup_prunes_rate_events_after_the_hourly_window(tmp_path) -> None:
    db_path = tmp_path / "chat-shares.db"
    created = _create_share(db_path, owner_user_id="owner-rate-retention")
    claim = store.claim_chat_share_import(
        token=created.token,
        recipient_user_id="recipient-rate-retention",
        now=NOW + 1,
        db_path=db_path,
    )
    assert claim.status == store.CLAIM_STATUS_CLAIMED
    assert len(_table_rows(db_path, "chat_share_rate_events")) == 2

    cleanup_at = NOW + store.CHAT_SHARE_RATE_WINDOW_SECONDS + 2
    assert store.cleanup_expired_chat_shares(now=cleanup_at, db_path=db_path) == 0
    assert len(_table_rows(db_path, "chat_shares")) == 1
    assert _table_rows(db_path, "chat_share_rate_events") == []


def test_recipient_cap_is_monotonic_and_existing_recipient_can_retry(tmp_path) -> None:
    db_path = tmp_path / "chat-shares.db"
    created = _create_share(db_path)
    claims = [
        store.claim_chat_share_import(
            token=created.token,
            recipient_user_id=f"recipient-{index}",
            now=NOW + 1,
            db_path=db_path,
        )
        for index in range(store.CHAT_SHARE_MAX_RECIPIENTS)
    ]
    assert {claim.status for claim in claims} == {store.CLAIM_STATUS_CLAIMED}

    first = claims[0]
    assert store.fail_chat_share_import(
        share_id=first.share_id,
        recipient_user_id="recipient-0",
        claim_id=first.claim_id,
        error_code="transient_failure",
        now=NOW + 2,
        db_path=db_path,
    )
    retried = store.claim_chat_share_import(
        token=created.token,
        recipient_user_id="recipient-0",
        now=NOW + 3,
        db_path=db_path,
    )
    assert retried.status == store.CLAIM_STATUS_CLAIMED
    assert retried.imported_session_id == first.imported_session_id

    assert store.mark_chat_share_import_target_deleted(
        share_id=claims[1].share_id,
        recipient_user_id="recipient-1",
        now=NOW + 3,
        db_path=db_path,
    )
    deleted_target = store.claim_chat_share_import(
        token=created.token,
        recipient_user_id="recipient-1",
        now=NOW + 4,
        db_path=db_path,
    )
    assert deleted_target.status == store.CLAIM_STATUS_TARGET_DELETED

    cleanup = store.invalidate_user_chat_share_data(
        "recipient-2", now=NOW + 4, db_path=db_path
    )
    assert cleanup["recipient_receipts_invalidated"] == 1

    over_cap = store.claim_chat_share_import(
        token=created.token,
        recipient_user_id="recipient-100",
        now=NOW + 5,
        db_path=db_path,
    )
    assert over_cap.status == store.CLAIM_STATUS_RECIPIENT_LIMIT
    assert len(_table_rows(db_path, "chat_share_imports")) == 99
    share_row = next(
        row
        for row in _table_rows(db_path, "chat_shares")
        if row["share_id"] == created.share_id
    )
    assert share_row["recipient_count"] == 100


def test_expiry_and_invalidation_make_tokens_uniformly_unavailable(tmp_path) -> None:
    db_path = tmp_path / "chat-shares.db"
    expired = _create_share(db_path, source_session_id="expires")
    before_expiry = store.claim_chat_share_import(
        token=expired.token,
        recipient_user_id="recipient-before-expiry",
        now=expired.expires_at - 1,
        db_path=db_path,
    )
    assert before_expiry.status == store.CLAIM_STATUS_CLAIMED
    at_expiry = store.claim_chat_share_import(
        token=expired.token,
        recipient_user_id="recipient-at-expiry",
        now=expired.expires_at,
        db_path=db_path,
    )
    assert at_expiry.status == store.CLAIM_STATUS_UNAVAILABLE
    assert store.cleanup_expired_chat_shares(now=expired.expires_at, db_path=db_path) == 1
    assert _table_rows(db_path, "chat_share_imports") == []

    revoked = _create_share(db_path, source_session_id="revoked", now=NOW + 10)
    active_claim = store.claim_chat_share_import(
        token=revoked.token,
        recipient_user_id="recipient-revoked",
        now=NOW + 11,
        db_path=db_path,
    )
    assert active_claim.status == store.CLAIM_STATUS_CLAIMED
    assert store.invalidate_source_session_shares(
        "owner-1", "revoked", now=NOW + 12, db_path=db_path
    ) == 1
    assert not store.chat_share_import_claim_is_valid(
        share_id=active_claim.share_id,
        recipient_user_id="recipient-revoked",
        claim_id=active_claim.claim_id,
        now=NOW + 12,
        db_path=db_path,
    )
    assert store.complete_chat_share_import(
        share_id=active_claim.share_id,
        recipient_user_id="recipient-revoked",
        claim_id=active_claim.claim_id,
        imported_title="Already-created destination",
        now=NOW + 12,
        db_path=db_path,
    )
    assert store.claim_chat_share_import(
        token=revoked.token,
        recipient_user_id="another-recipient",
        now=NOW + 13,
        db_path=db_path,
    ).status == store.CLAIM_STATUS_UNAVAILABLE

    revoked_row = next(
        row for row in _table_rows(db_path, "chat_shares") if row["share_id"] == revoked.share_id
    )
    assert revoked_row["invalidated_at"] == NOW + 12
    assert revoked_row["snapshot_json"] == "{}"
    assert _table_rows(db_path, "chat_share_source_tombstones")[0][
        "source_session_id"
    ] == "revoked"
    with pytest.raises(store.ChatShareValidationError, match="no longer shareable"):
        _create_share(
            db_path,
            source_session_id="revoked",
            now=NOW + 13,
        )


def test_user_invalidation_revokes_owned_shares_and_deletes_import_receipts(
    tmp_path,
) -> None:
    db_path = tmp_path / "chat-shares.db"
    owned = _create_share(db_path, owner_user_id="deleted-owner")
    other = _create_share(
        db_path,
        owner_user_id="other-owner",
        source_session_id="other-source",
    )
    imported = store.claim_chat_share_import(
        token=other.token,
        recipient_user_id="deleted-owner",
        now=NOW + 1,
        db_path=db_path,
    )
    assert imported.status == store.CLAIM_STATUS_CLAIMED

    result = store.invalidate_user_chat_share_data(
        "deleted-owner", now=NOW + 2, db_path=db_path
    )
    assert result == {
        "owned_shares_invalidated": 1,
        "recipient_receipts_invalidated": 1,
    }
    assert store.claim_chat_share_import(
        token=owned.token,
        recipient_user_id="new-recipient",
        now=NOW + 3,
        db_path=db_path,
    ).status == store.CLAIM_STATUS_UNAVAILABLE
    assert all(
        row["owner_user_id"] != "deleted-owner"
        for row in _table_rows(db_path, "chat_shares")
    )
    assert all(
        row["recipient_user_id"] != "deleted-owner"
        for row in _table_rows(db_path, "chat_share_imports")
    )
    assert store.claim_chat_share_import(
        token=other.token,
        recipient_user_id="deleted-owner",
        now=NOW + 3,
        db_path=db_path,
    ).status == store.CLAIM_STATUS_UNAVAILABLE
    assert _table_rows(db_path, "chat_share_owner_tombstones")[0][
        "owner_user_id"
    ] == "deleted-owner"
    with pytest.raises(store.ChatShareValidationError, match="no longer shareable"):
        _create_share(
            db_path,
            owner_user_id="deleted-owner",
            source_session_id="created-after-account-deletion",
            now=NOW + 3,
        )
    store.cleanup_expired_chat_shares(
        now=NOW + store.CHAT_SHARE_LIFECYCLE_TOMBSTONE_TTL_SECONDS + 3,
        db_path=db_path,
    )
    assert _table_rows(db_path, "chat_share_owner_tombstones") == []
    assert _table_rows(db_path, "chat_share_recipient_tombstones") == []


def test_source_lifecycle_claim_can_only_be_cleared_by_its_owner(tmp_path) -> None:
    db_path = tmp_path / "chat-shares.db"
    store.invalidate_source_session_shares(
        "owner-1",
        "restored-source",
        lifecycle_claim_id="delete-claim",
        now=NOW,
        db_path=db_path,
    )
    store.invalidate_source_session_shares(
        "owner-1",
        "restored-source",
        lifecycle_claim_id="concurrent-claim",
        now=NOW,
        db_path=db_path,
    )

    assert not store.clear_source_session_invalidation(
        "owner-1",
        "restored-source",
        lifecycle_claim_id="different-claim",
        db_path=db_path,
    )
    assert store.clear_source_session_invalidation(
        "owner-1",
        "restored-source",
        lifecycle_claim_id="concurrent-claim",
        db_path=db_path,
    )
    assert store.heartbeat_source_session_invalidation(
        "owner-1",
        "restored-source",
        lifecycle_claim_id="delete-claim",
        now=NOW + store.CHAT_SHARE_LIFECYCLE_TOMBSTONE_TTL_SECONDS,
        db_path=db_path,
    )
    with pytest.raises(store.ChatShareValidationError, match="no longer shareable"):
        _create_share(
            db_path,
            source_session_id="restored-source",
            now=NOW + store.CHAT_SHARE_LIFECYCLE_TOMBSTONE_TTL_SECONDS + 1,
        )

    assert store.finalize_source_session_invalidation(
        "owner-1",
        "restored-source",
        lifecycle_claim_id="delete-claim",
        now=NOW + store.CHAT_SHARE_LIFECYCLE_TOMBSTONE_TTL_SECONDS + 1,
        db_path=db_path,
    )
    assert _table_rows(db_path, "chat_share_source_lifecycle_claims") == []
    with pytest.raises(store.ChatShareValidationError, match="no longer shareable"):
        _create_share(
            db_path,
            source_session_id="restored-source",
            now=NOW + store.CHAT_SHARE_LIFECYCLE_TOMBSTONE_TTL_SECONDS + 2,
        )
    assert store.clear_source_session_invalidation(
        "owner-1",
        "restored-source",
        db_path=db_path,
    )
    restored = _create_share(
        db_path,
        source_session_id="restored-source",
        now=NOW + store.CHAT_SHARE_LIFECYCLE_TOMBSTONE_TTL_SECONDS + 3,
    )
    assert restored.token

    store.invalidate_source_session_shares(
        "owner-1",
        "stale-claim-source",
        lifecycle_claim_id="crashed-claim",
        now=NOW,
        db_path=db_path,
    )
    recovered = _create_share(
        db_path,
        source_session_id="stale-claim-source",
        now=NOW + 2 * store.CHAT_SHARE_LIFECYCLE_TOMBSTONE_TTL_SECONDS + 1,
    )
    assert recovered.token
