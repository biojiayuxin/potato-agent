from __future__ import annotations

import re
import string


COMMON_PASSWORD_SYMBOLS = string.punctuation


PASSWORD_COMPLEXITY_DETAIL = (
    "Password must be at least 8 characters and include uppercase letters, "
    "lowercase letters, numbers, and common symbols."
)


def password_complexity_error(password: str) -> str | None:
    if (
        len(password) < 8
        or re.search(r"[a-z]", password) is None
        or re.search(r"[A-Z]", password) is None
        or re.search(r"[0-9]", password) is None
        or not any(char in COMMON_PASSWORD_SYMBOLS for char in password)
    ):
        return PASSWORD_COMPLEXITY_DETAIL
    return None


def validate_password_complexity(password: str) -> None:
    error = password_complexity_error(password)
    if error is not None:
        raise ValueError(error)
