---
name: "aliyun-oss-download"
description: "Use when downloading a user-provided Aliyun OSS prefix or delivery directory via AccessKey authentication, with safe credential handling, resumable transfer, manifest generation, and optional checksum verification."
---

# Aliyun OSS Download

Use this skill when a user asks to download data from Alibaba Cloud OSS using AccessKey authentication. The user should provide, or you should ask for, the OSS endpoint, AccessKeyId, AccessKeySecret, source OSS URI, and local destination directory.

This skill is intentionally generic. Do **not** hard-code or preserve any user's AccessKey, secret, bucket, prefix, project ID, or local data path in the skill or in memory.

## Inputs to Collect

Required:

- `endpoint`: OSS endpoint URL, for example `https://oss-<region>.aliyuncs.com`.
- `access_key_id`: AccessKeyId provided by the user.
- `access_key_secret`: AccessKeySecret provided by the user.
- `oss_uri`: source URI in the form `oss://<bucket>/<prefix>/` or `oss://<bucket>/<object>`.
- `dest_dir`: local destination directory.

Optional:

- `wanted_subdir` or delivery-content hint, such as `raw`, `Rawdata`, `result`, etc.
- Whether to preserve the full prefix tree or strip the source prefix when writing local files.
- Whether to run checksum verification if a checksum file such as `md5.txt`, `MD5.txt`, or `*.md5` is included.
- Transfer concurrency and part size. Good defaults: 8 threads, 64 MiB parts.

## Security Rules

1. Never write credentials into the skill, memory, final summary, shell history, persistent config, or reusable scripts.
2. Prefer passing credentials through a temporary `0600` JSON file or environment variables, then delete them immediately after the download script reads them.
3. Do not echo AccessKeySecret in terminal output. Avoid `set -x` around credential handling.
4. Remove temporary download scripts or credential files after completion unless the user explicitly asks to keep them.
5. Report credential-related failures generically, for example "authentication failed" or "permission denied"; do not print secrets.

## Tool Choice

Prefer one of these approaches:

1. **Python `oss2` SDK** — robust when `ossutil` is absent or when you need custom progress, manifest, and checksum handling.
2. **`ossutil` / `ossutil64`** — acceptable if already installed and configured for one-shot copy, but avoid saving credentials to global config unless the user explicitly approves.

If Python `oss2` is missing, first look for an existing conda/mamba/micromamba environment before using pip. In managed Python environments, create/use an isolated environment rather than installing into the system interpreter.

## Workflow

1. **Parse and validate the OSS URI**

   `oss://<bucket>/<prefix>` should be split into:

   - `bucket_name`
   - `prefix` or object key

   Normalize the source:

   - Remove only the leading `oss://`.
   - Keep case exactly as provided; OSS keys are case-sensitive.
   - If the user asks for a directory/prefix download, ensure the prefix ends with `/`.

2. **Check destination and capacity**

   ```bash
   mkdir -p <dest_dir>
   test -w <dest_dir>
   df -h <dest_dir>
   ```

3. **List remote objects before downloading**

   Use OSS listing to count objects and total bytes. If the user requested a delivery subdirectory such as `raw`, first check the literal `<prefix>/raw/`; if no objects exist, list the parent prefix and inspect object names for likely delivery folders such as `Rawdata/`. Do not assume names or case.

4. **Confirm target set when ambiguous**

   If several plausible subdirectories exist, ask the user to choose. If there is only one obvious data subdirectory, proceed and state the assumption in the final summary.

5. **Download with resumable transfer**

   With `oss2`, use `oss2.resumable_download` for large files. Preserve relative paths under the selected prefix, and protect against unsafe keys that would escape the destination directory.

6. **Generate a manifest**

   Write a TSV manifest in the destination directory containing at least:

   ```text
   object_key\tsize\tetag\tlast_modified\tlocal_path
   ```

7. **Verify sizes and checksums**

   For every downloaded object, compare local file size to remote object size. If a checksum file is present and references local relative paths, run the appropriate checker, for example:

   ```bash
   cd <download_root>
   md5sum -c md5.txt > md5check.linux.log 2>&1
   ```

   Treat nonzero checksum exit status as failure and report the log path.

8. **Clean up sensitive and temporary files**

   Delete temporary credentials and transient scripts after successful completion. It is OK to keep non-sensitive logs, manifests, progress JSON, and checksum logs.

9. **Final report**

   Include:

   - Source OSS URI, without credentials.
   - Destination directory.
   - Number of remote objects downloaded.
   - Total bytes / GiB.
   - File counts by important type if relevant, for example FASTQ count.
   - Manifest path.
   - Checksum result and checksum log path, if run.
   - Note any assumptions, such as using a parent prefix because a requested literal subdirectory was empty.

## Python `oss2` Template

Use this as a starting point. Replace placeholders at runtime only; do not store real credentials in the skill.

```python
#!/usr/bin/env python3
import json
import os
from pathlib import Path

import oss2

ENDPOINT = os.environ["OSS_ENDPOINT"]
ACCESS_KEY_ID = os.environ["OSS_ACCESS_KEY_ID"]
ACCESS_KEY_SECRET = os.environ["OSS_ACCESS_KEY_SECRET"]
OSS_URI = os.environ["OSS_URI"]
DEST_DIR = Path(os.environ["OSS_DEST_DIR"])
THREADS = int(os.environ.get("OSS_THREADS", "8"))
PART_SIZE = int(os.environ.get("OSS_PART_SIZE", str(64 * 1024 * 1024)))


def parse_oss_uri(uri: str):
    if not uri.startswith("oss://"):
        raise ValueError("OSS_URI must start with oss://")
    rest = uri[len("oss://"):]
    bucket, sep, key = rest.partition("/")
    if not bucket:
        raise ValueError("missing bucket in OSS_URI")
    return bucket, key


def safe_relative_path(key: str, prefix: str) -> Path:
    rel = key[len(prefix):] if prefix and key.startswith(prefix) else Path(key).name
    rel_path = Path(rel)
    if rel_path.is_absolute() or ".." in rel_path.parts:
        raise ValueError(f"unsafe object key: {key}")
    return rel_path


def list_objects(bucket, prefix):
    rows = []
    for obj in oss2.ObjectIterator(bucket, prefix=prefix):
        if obj.key.endswith("/"):
            continue
        rows.append({
            "key": obj.key,
            "size": int(obj.size or 0),
            "etag": getattr(obj, "etag", "") or "",
            "last_modified": int(getattr(obj, "last_modified", 0) or 0),
        })
    rows.sort(key=lambda x: x["key"])
    return rows


def main():
    bucket_name, prefix = parse_oss_uri(OSS_URI)
    if prefix and not prefix.endswith("/"):
        # If the URI is meant as a directory, callers should include a trailing slash.
        # Keeping this strict avoids accidentally stripping the wrong local path prefix.
        pass

    DEST_DIR.mkdir(parents=True, exist_ok=True)
    auth = oss2.Auth(ACCESS_KEY_ID, ACCESS_KEY_SECRET)
    bucket = oss2.Bucket(auth, ENDPOINT, bucket_name)

    rows = list_objects(bucket, prefix)
    if not rows:
        raise RuntimeError(f"no objects found under prefix: oss://{bucket_name}/{prefix}")

    checkpoint_dir = DEST_DIR / ".oss_checkpoint"
    checkpoint_dir.mkdir(exist_ok=True)
    manifest_path = DEST_DIR / "oss_download_manifest.tsv"

    total = sum(r["size"] for r in rows)
    downloaded = []
    for i, r in enumerate(rows, 1):
        key = r["key"]
        local = DEST_DIR / safe_relative_path(key, prefix)
        local.parent.mkdir(parents=True, exist_ok=True)
        print(f"[{i}/{len(rows)}] {key} -> {local}", flush=True)
        if r["size"] == 0:
            local.write_bytes(b"")
        else:
            oss2.resumable_download(
                bucket,
                key,
                str(local),
                store=oss2.ResumableDownloadStore(root=str(checkpoint_dir)),
                multiget_threshold=16 * 1024 * 1024,
                part_size=PART_SIZE,
                num_threads=THREADS,
            )
        if local.stat().st_size != r["size"]:
            raise RuntimeError(f"size mismatch: {local}")
        downloaded.append((r, local))

    with manifest_path.open("w", encoding="utf-8") as out:
        out.write("object_key\tsize\tetag\tlast_modified\tlocal_path\n")
        for r, local in downloaded:
            out.write(f'{r["key"]}\t{r["size"]}\t{r["etag"]}\t{r["last_modified"]}\t{local}\n')

    print(json.dumps({
        "status": "completed",
        "objects": len(rows),
        "total_bytes": total,
        "manifest": str(manifest_path),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
```

Run it with environment variables, keeping secrets out of command text where possible:

```bash
export OSS_ENDPOINT='<endpoint>'
export OSS_ACCESS_KEY_ID='<access-key-id>'
export OSS_ACCESS_KEY_SECRET='<access-key-secret>'
export OSS_URI='oss://<bucket>/<prefix>/'
export OSS_DEST_DIR='<dest-dir>'
python3 oss_download.py
unset OSS_ACCESS_KEY_ID OSS_ACCESS_KEY_SECRET
```

For very large transfers, run in a tracked background process or submit to Slurm according to the user's resource preference and the local environment.

## Common Pitfalls

- OSS keys are case-sensitive. `raw/` and `Rawdata/` are different prefixes.
- Some delivery portals label content as "raw" while the actual object path uses a vendor-specific directory such as `Rawdata/`; list before assuming.
- Multi-part OSS ETags often contain a dash and are not the plain MD5 of the whole file. Do not use ETag as a checksum unless you know how the object was uploaded.
- Checksum files may contain Windows line endings or Chinese localized output from `md5sum`; rely on the command exit code rather than only matching English `OK` text.
- Avoid global `ossutil config` with user-provided secrets unless the user explicitly asks for persistent configuration.
- If the download is interrupted, rerun the resumable script with the same checkpoint directory and destination.
- Do not include user-provided AccessKey values in final summaries, logs, manifests, or memory.

## Verification Checklist

- [ ] Destination directory exists and is writable.
- [ ] Remote object count and total bytes were listed before transfer.
- [ ] Every local file size matches the corresponding OSS object size.
- [ ] Manifest TSV was written.
- [ ] Included checksum file was executed when present and exited with code 0.
- [ ] Temporary credentials and transient scripts were deleted or secured.
- [ ] Final report contains source/destination/counts/checksum status but no secrets.
