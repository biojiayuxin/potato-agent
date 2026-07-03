from __future__ import annotations

from interface import app as interface_app_mod
from interface.test_file_upload import _build_client_and_user


def _allow_direct_file_reads(monkeypatch) -> None:
    monkeypatch.setattr(
        interface_app_mod,
        "_assert_user_can_read_file",
        lambda path, *, linux_user: None,
    )


def test_text_file_preview_returns_metadata_and_content(monkeypatch) -> None:
    client, home_dir = _build_client_and_user(monkeypatch)
    _allow_direct_file_reads(monkeypatch)
    target = home_dir / "notes.md"
    target.write_text("# Potato\nhello\n", encoding="utf-8")
    try:
        meta_response = client.get("/api/files/preview/meta", params={"path": "notes.md"})
        assert meta_response.status_code == 200, meta_response.text
        meta = meta_response.json()
        assert meta["filename"] == "notes.md"
        assert meta["preview_type"] == "text"
        assert meta["mime_type"] == "text/markdown"
        assert meta["too_large"] is False

        text_response = client.get("/api/files/preview/text", params={"path": "notes.md"})
        assert text_response.status_code == 200, text_response.text
        payload = text_response.json()
        assert payload["preview_type"] == "text"
        assert payload["content"] == "# Potato\nhello\n"
    finally:
        client.close()


def test_log_style_files_preview_as_text(monkeypatch) -> None:
    client, home_dir = _build_client_and_user(monkeypatch)
    _allow_direct_file_reads(monkeypatch)
    filenames = [
        "worker.err",
        "nohup.out",
        "service.log.1",
        "events.jsonl",
        "stderr",
        "access_log",
    ]
    for filename in filenames:
        (home_dir / filename).write_text(f"{filename}: ok\n", encoding="utf-8")
    try:
        for filename in filenames:
            meta_response = client.get("/api/files/preview/meta", params={"path": filename})
            assert meta_response.status_code == 200, meta_response.text
            assert meta_response.json()["preview_type"] == "text"

            text_response = client.get("/api/files/preview/text", params={"path": filename})
            assert text_response.status_code == 200, text_response.text
            assert text_response.json()["content"] == f"{filename}: ok\n"
    finally:
        client.close()


def test_bioinformatics_sequence_and_annotation_files_preview_as_text(monkeypatch) -> None:
    client, home_dir = _build_client_and_user(monkeypatch)
    _allow_direct_file_reads(monkeypatch)
    files = {
        "sample.fa": ">seq1\nATGC\n",
        "sample.fasta": ">seq1\nATGC\n",
        "sample.fna": ">seq1\nATGC\n",
        "sample.fastq": "@seq1\nATGC\n+\n!!!!\n",
        "sample.fq": "@seq1\nATGC\n+\n!!!!\n",
        "genes.gff3": "##gff-version 3\nchr1\t.\tgene\t1\t4\t.\t+\t.\tID=gene1\n",
        "genes.gff": "chr1\t.\tgene\t1\t4\t.\t+\t.\tID=gene1\n",
        "genes.gtf": "chr1\t.\tgene\t1\t4\t.\t+\t.\tgene_id \"gene1\";\n",
    }
    for filename, content in files.items():
        (home_dir / filename).write_text(content, encoding="utf-8")
    try:
        for filename, content in files.items():
            meta_response = client.get("/api/files/preview/meta", params={"path": filename})
            assert meta_response.status_code == 200, meta_response.text
            assert meta_response.json()["preview_type"] == "text"

            text_response = client.get("/api/files/preview/text", params={"path": filename})
            assert text_response.status_code == 200, text_response.text
            assert text_response.json()["content"] == content
    finally:
        client.close()


def test_svg_preview_streams_inline_content(monkeypatch) -> None:
    client, home_dir = _build_client_and_user(monkeypatch)
    _allow_direct_file_reads(monkeypatch)
    (home_dir / "plot.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"></svg>',
        encoding="utf-8",
    )
    try:
        meta_response = client.get("/api/files/preview/meta", params={"path": "plot.svg"})
        assert meta_response.status_code == 200, meta_response.text
        meta = meta_response.json()
        assert meta["preview_type"] == "image"
        assert meta["content_url"].startswith("/api/files/preview/content?")

        content_response = client.get("/api/files/preview/content", params={"path": "plot.svg"})
        assert content_response.status_code == 200, content_response.text
        assert content_response.headers["content-type"].startswith("image/svg+xml")
        assert content_response.headers["content-disposition"] == 'inline; filename="plot.svg"'
        assert content_response.headers["x-content-type-options"] == "nosniff"
        assert "default-src 'none'" in content_response.headers["content-security-policy"]
    finally:
        client.close()


def test_pdf_preview_streams_as_pdf(monkeypatch) -> None:
    client, home_dir = _build_client_and_user(monkeypatch)
    _allow_direct_file_reads(monkeypatch)
    (home_dir / "paper.pdf").write_bytes(b"%PDF-1.4\n% test\n")
    try:
        meta_response = client.get("/api/files/preview/meta", params={"path": "paper.pdf"})
        assert meta_response.status_code == 200, meta_response.text
        assert meta_response.json()["preview_type"] == "pdf"

        content_response = client.get("/api/files/preview/content", params={"path": "paper.pdf"})
        assert content_response.status_code == 200, content_response.text
        assert content_response.headers["content-type"].startswith("application/pdf")
        assert content_response.headers["content-disposition"] == 'inline; filename="paper.pdf"'
    finally:
        client.close()


def test_preview_rejects_file_over_limit(monkeypatch) -> None:
    client, home_dir = _build_client_and_user(monkeypatch)
    _allow_direct_file_reads(monkeypatch)
    monkeypatch.setattr(interface_app_mod, "MAX_PREVIEW_SIZE_BYTES", 4)
    (home_dir / "large.txt").write_text("too large", encoding="utf-8")
    try:
        meta_response = client.get("/api/files/preview/meta", params={"path": "large.txt"})
        assert meta_response.status_code == 200, meta_response.text
        meta = meta_response.json()
        assert meta["preview_type"] == "too_large"
        assert meta["too_large"] is True

        text_response = client.get("/api/files/preview/text", params={"path": "large.txt"})
        assert text_response.status_code == 413, text_response.text
    finally:
        client.close()


def test_preview_rejects_invalid_relative_path(monkeypatch) -> None:
    client, _ = _build_client_and_user(monkeypatch)
    _allow_direct_file_reads(monkeypatch)
    try:
        response = client.get("/api/files/preview/meta", params={"path": "../secret.txt"})
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "Invalid path"
    finally:
        client.close()
