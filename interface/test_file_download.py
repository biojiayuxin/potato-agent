from __future__ import annotations

from fastapi.responses import Response

from interface import app as interface_app_mod


def test_download_content_disposition_encodes_unicode_filename() -> None:
    header = interface_app_mod._attachment_content_disposition("\u62a5\u544a.txt")

    assert header == "attachment; filename*=utf-8''%E6%8A%A5%E5%91%8A.txt"
    assert all(ord(char) < 128 for char in header)
    Response(headers={"Content-Disposition": header})
