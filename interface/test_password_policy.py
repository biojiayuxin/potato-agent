from __future__ import annotations

from interface.password_policy import password_complexity_error


def test_password_complexity_requires_common_symbol() -> None:
    assert password_complexity_error("Password123") is not None
    assert password_complexity_error("Password123!") is None
    assert password_complexity_error("Password123?") is None
