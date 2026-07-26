#!/usr/bin/env bash

set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "error: this installer must run as root" >&2
  exit 2
fi

if [[ $# -ne 3 ]]; then
  echo "usage: $0 RELEASE_SOURCE RELEASE_ID WHEELHOUSE" >&2
  exit 2
fi

release_source=$(realpath "$1")
release_id=$2
wheelhouse=$(realpath "$3")

if [[ ! ${release_id} =~ ^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$ ]]; then
  echo "error: unsafe release id: ${release_id}" >&2
  exit 2
fi
if [[ ! -d ${release_source} || ! -f ${release_source}/manifest.json ]]; then
  echo "error: release source is incomplete: ${release_source}" >&2
  exit 2
fi
if [[ ! -d ${wheelhouse} ]]; then
  echo "error: wheelhouse does not exist: ${wheelhouse}" >&2
  exit 2
fi

base=/opt/potato-hermes-lite
releases=${base}/releases
final=${releases}/${release_id}
staging=${releases}/.${release_id}.staging

if [[ -e ${final} || -L ${final} || -e ${staging} || -L ${staging} ]]; then
  echo "error: release id already exists: ${release_id}" >&2
  exit 2
fi

python3 - "${release_source}" "${wheelhouse}" <<'PY'
import hashlib
import json
import pathlib
import platform
import subprocess
import sys

root = pathlib.Path(sys.argv[1])
wheelhouse = pathlib.Path(sys.argv[2])
if sys.version_info[:2] != (3, 12) or sys.platform != "linux" or platform.machine() != "x86_64":
    raise SystemExit("Lite release installation requires CPython 3.12 on Linux x86_64")
manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
project = manifest.get("project")
if not isinstance(project, dict):
    raise SystemExit("release manifest project must be an object")
if project.get("name") != "potato-hermes-lite":
    raise SystemExit("release manifest project name mismatch")
expected_project_version = project.get("version")
if not isinstance(expected_project_version, str) or not expected_project_version:
    raise SystemExit("release manifest project version is invalid")

def digest(relative: str) -> str:
    value = hashlib.sha256()
    with (root / relative).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()

wheel = "wheel/" + manifest["wheel"]["filename"]
checks = {
    wheel: manifest["wheel"]["sha256"],
    manifest["runtime_profile"]["path"]: manifest["runtime_profile"]["sha256"],
    manifest["browser_assets"]["agent_browser"]["path"]: manifest["browser_assets"]["agent_browser"]["sha256"],
}
for relative, expected in checks.items():
    actual = digest(relative)
    if actual != expected:
        raise SystemExit(f"release hash mismatch for {relative}: {actual} != {expected}")

config = root / "config" / "manifests"
lock_path = config / "requirements-py312-linux-x86_64.lock"
wheelhouse_manifest_path = config / "wheelhouse-py312-linux-x86_64.json"
wheelhouse_manifest = json.loads(wheelhouse_manifest_path.read_text(encoding="utf-8"))
if digest(str(lock_path.relative_to(root))) != wheelhouse_manifest["lock"]["sha256"]:
    raise SystemExit("Lite dependency lock differs from the wheelhouse manifest")
expected_wheels = {}
for item in wheelhouse_manifest.get("files", []):
    filename = item.get("filename")
    expected = item.get("sha256")
    if not isinstance(filename, str) or not isinstance(expected, str):
        raise SystemExit("invalid wheelhouse file manifest")
    expected_wheels[filename] = expected
actual_wheels = {path.name for path in wheelhouse.glob("*.whl")}
if actual_wheels != set(expected_wheels):
    raise SystemExit("wheelhouse contents differ from the file manifest")
for filename, expected in expected_wheels.items():
    value = hashlib.sha256((wheelhouse / filename).read_bytes()).hexdigest()
    if value != expected:
        raise SystemExit(f"wheelhouse hash mismatch for {filename}")

chrome = root / manifest["browser_assets"]["chrome_for_testing"]["path"]
output = subprocess.check_output([chrome, "--version"], text=True).strip()
expected_version = manifest["browser_assets"]["chrome_for_testing"]["version"]
if expected_version not in output:
    raise SystemExit(f"unexpected Chrome version: {output}")
PY

install -d -o root -g root -m 0755 "${base}" "${releases}"
install -d -o root -g root -m 0755 "${staging}"
rsync -a --numeric-ids "${release_source}/" "${staging}/"
chown -R root:root "${staging}"
chmod -R go-w "${staging}"
mv "${staging}" "${final}"

python3 -m venv "${final}/venv"
wheel_name=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["wheel"]["filename"])' "${final}/manifest.json")
PIP_NO_CACHE_DIR=1 "${final}/venv/bin/pip" install \
  --require-hashes \
  --no-index \
  --find-links "${wheelhouse}" \
  -r "${final}/config/manifests/requirements-py312-linux-x86_64.lock"
PIP_NO_CACHE_DIR=1 "${final}/venv/bin/pip" install \
  --no-deps \
  --no-index \
  "${final}/wheel/${wheel_name}"
"${final}/venv/bin/pip" check

"${final}/venv/bin/python" -I - "${final}" <<'PY'
import importlib.metadata
import base64
import hashlib
import json
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1]).resolve()
site_root = (root / "venv").resolve()
manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
project = manifest.get("project")
if not isinstance(project, dict):
    raise SystemExit("release manifest project must be an object")
if project.get("name") != "potato-hermes-lite":
    raise SystemExit("release manifest project name mismatch")
expected_version = project.get("version")
if not isinstance(expected_version, str) or not expected_version:
    raise SystemExit("release manifest project version is invalid")
def canonical_name(value):
    return re.sub(r"[-_.]+", "-", value).lower()

installed = {
    canonical_name(distribution.metadata["Name"]): distribution.version
    for distribution in importlib.metadata.distributions()
    if distribution.metadata.get("Name")
}
expected_dependency_data = json.loads(
    (root / "config" / "manifests" / "distributions-py312-linux-x86_64.json").read_text(
        encoding="utf-8"
    )
)
expected_dependencies = expected_dependency_data.get("distributions")
if not isinstance(expected_dependencies, dict):
    raise SystemExit("invalid dependency distribution fingerprint")
expected_installed = {
    canonical_name(str(name)): str(version)
    for name, version in expected_dependencies.items()
}
expected_installed["potato-hermes-lite"] = expected_version
for name, version in expected_installed.items():
    if installed.get(name) != version:
        raise SystemExit(
            f"distribution version mismatch for {name}: expected {version}, got {installed.get(name)}"
        )
unexpected = set(installed) - set(expected_installed) - {"pip", "setuptools"}
if unexpected:
    raise SystemExit(f"unexpected distributions in Lite venv: {sorted(unexpected)}")
if installed.get("potato-hermes-lite") != expected_version:
    raise SystemExit(
        "potato-hermes-lite version mismatch: "
        f"expected {expected_version}, got {installed.get('potato-hermes-lite')}"
    )
if "hermes-agent" in installed:
    raise SystemExit("legacy hermes-agent leaked into Lite venv")

import agent.codex_runtime
import potato_hermes_lite
import tui_gateway.entry

if potato_hermes_lite.__version__ != expected_version:
    raise SystemExit(
        "potato_hermes_lite.__version__ mismatch: "
        f"expected {expected_version}, got {potato_hermes_lite.__version__}"
    )
for module in (agent.codex_runtime, potato_hermes_lite, tui_gateway.entry):
    origin = pathlib.Path(module.__file__).resolve()
    if site_root not in origin.parents:
        raise SystemExit(f"module loaded outside Lite venv: {origin}")

file_fingerprints = {}
for distribution in importlib.metadata.distributions():
    raw_name = distribution.metadata.get("Name")
    if not raw_name or canonical_name(raw_name) not in expected_installed:
        continue
    records = []
    for entry in distribution.files or ():
        path = pathlib.Path(distribution.locate_file(entry)).resolve()
        if site_root != path and site_root not in path.parents:
            raise SystemExit(f"distribution file escaped Lite venv: {path}")
        if not path.is_file():
            raise SystemExit(f"installed distribution file is missing: {path}")
        digest = hashlib.sha256(path.read_bytes()).digest()
        encoded = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        if entry.hash is not None:
            if entry.hash.mode != "sha256" or entry.hash.value != encoded:
                raise SystemExit(f"installed distribution file hash mismatch: {path}")
        records.append((str(entry), path.stat().st_size, encoded))
    rendered = json.dumps(sorted(records), separators=(",", ":")).encode("utf-8")
    file_fingerprints[canonical_name(raw_name)] = hashlib.sha256(rendered).hexdigest()

snapshot = {
    "python": sys.version.split()[0],
    "distributions": dict(sorted(installed.items())),
    "distribution_file_fingerprints": dict(sorted(file_fingerprints.items())),
}
(root / "config" / "installed-distributions.json").write_text(
    json.dumps(snapshot, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

"${final}/venv/bin/hermes" --help >/dev/null
chown -R root:root "${final}"
chmod -R a+rX "${final}/venv"
chmod -R go-w "${final}"
chrome_dir=${final}/browser/chrome/chrome-linux64
chrome_sandbox_source=${chrome_dir}/chrome_sandbox
chrome_sandbox=${chrome_dir}/chrome-sandbox
if [[ ! -f ${chrome_sandbox_source} || -L ${chrome_sandbox_source} ]]; then
  echo "error: Chrome SUID sandbox is missing or unsafe: ${chrome_sandbox_source}" >&2
  exit 2
fi
if [[ -e ${chrome_sandbox} || -L ${chrome_sandbox} ]]; then
  echo "error: normalized Chrome sandbox path already exists: ${chrome_sandbox}" >&2
  exit 2
fi
ln "${chrome_sandbox_source}" "${chrome_sandbox}"
chmod 04755 "${chrome_sandbox}"

echo "installed inactive Lite release: ${final}"
