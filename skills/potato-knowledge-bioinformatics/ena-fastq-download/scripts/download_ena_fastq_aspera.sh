#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  download_ena_fastq_aspera.sh <run-list> <out-dir> <log-dir> [manifest.tsv]

Environment variables:
  BUILDER                  Manifest builder script path
  ASCP_BIN                 Path to ascp
  ASCP_KEY                 Path to aspera key
  PARALLEL                 Concurrent files (default: 6)
  MAX_RETRIES              Retries per file (default: 3)
  RETRY_SLEEP              Seconds between retries (default: 30)
  POLL_SLEEP               Slot polling interval (default: 2)
  ASCP_RATE                Target rate, e.g. 500m (default: 500m)
  ASCP_PORT                Aspera port (default: 33001)
  ASCP_DISABLE_ENCRYPTION  yes/no (default: yes)
EOF
}

if [[ ${1:-} == "-h" || ${1:-} == "--help" ]]; then
  usage
  exit 0
fi

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
LIST_FILE=${1:-}
OUT_DIR=${2:-}
LOG_DIR=${3:-}
MANIFEST_FILE=${4:-}
BUILDER=${BUILDER:-$SCRIPT_DIR/build_ena_fastq_manifest.py}
PARALLEL=${PARALLEL:-6}
MAX_RETRIES=${MAX_RETRIES:-3}
RETRY_SLEEP=${RETRY_SLEEP:-30}
POLL_SLEEP=${POLL_SLEEP:-2}
ASCP_RATE=${ASCP_RATE:-500m}
ASCP_PORT=${ASCP_PORT:-33001}
ASCP_DISABLE_ENCRYPTION=${ASCP_DISABLE_ENCRYPTION:-yes}

log() {
  printf '[%s] %s\n' "$(date '+%F %T %Z')" "$*"
}

die() {
  log "[ERROR] $*" >&2
  exit 1
}

detect_ascp_bin() {
  local candidates=()
  [[ -n ${ASCP_BIN:-} ]] && candidates+=("$ASCP_BIN")
  if command -v ascp >/dev/null 2>&1; then
    candidates+=("$(command -v ascp)")
  fi
  candidates+=(
    "$HOME/.aspera/sdk/ascp"
    "$HOME/.aspera/ascli/sdk/ascp"
    "$PWD/.aspera-sdk/ascp"
    "$PWD/.home-ruby/.aspera/sdk/ascp"
    "$HOME/.aspera/connect/bin/ascp"
    "$HOME/.aspera/connect/bin/ascp4"
    "$PWD/.aspera-connect-unpacked/bin/ascp"
    "$PWD/.aspera-connect-unpacked/bin/ascp4"
  )
  local path
  for path in "${candidates[@]}"; do
    [[ -n $path && -x $path ]] && { echo "$path"; return 0; }
  done
  return 1
}

detect_ascp_key() {
  local candidates=()
  [[ -n ${ASCP_KEY:-} ]] && candidates+=("$ASCP_KEY")
  candidates+=(
    "$HOME/.aspera/sdk/aspera_bypass_rsa.pem"
    "$HOME/.aspera/sdk/aspera_bypass_dsa.pem"
    "$HOME/.aspera/ascli/sdk/aspera_bypass_rsa.pem"
    "$HOME/.aspera/ascli/sdk/aspera_bypass_dsa.pem"
    "$PWD/.aspera-sdk/aspera_bypass_rsa.pem"
    "$PWD/.aspera-sdk/aspera_bypass_dsa.pem"
    "$PWD/.home-ruby/.aspera/sdk/aspera_bypass_rsa.pem"
    "$PWD/.home-ruby/.aspera/sdk/aspera_bypass_dsa.pem"
    "$HOME/.aspera/connect/etc/asperaweb_id_dsa.openssh"
    "$HOME/.aspera/connect/etc/aspera_id_dsa.putty"
    "$PWD/.aspera-connect-unpacked/etc/asperaweb_id_dsa.openssh"
  )
  local path
  for path in "${candidates[@]}"; do
    [[ -n $path && -f $path ]] && { echo "$path"; return 0; }
  done
  return 1
}

[[ -n $LIST_FILE && -n $OUT_DIR && -n $LOG_DIR ]] || { usage; exit 1; }
mkdir -p "$OUT_DIR" "$LOG_DIR" "$LOG_DIR/status"
MANIFEST_FILE=${MANIFEST_FILE:-$LOG_DIR/ena_fastq_manifest.tsv}
ASCP_BIN=$(detect_ascp_bin || true)
ASCP_KEY=$(detect_ascp_key || true)

[[ -n $ASCP_BIN ]] || die "ascp not found. Set ASCP_BIN explicitly."
[[ -n $ASCP_KEY ]] || die "Aspera key not found. Set ASCP_KEY explicitly."
[[ -x $BUILDER ]] || die "manifest builder not found/executable: $BUILDER"

: > "$LOG_DIR/downloaded_files.txt"
: > "$LOG_DIR/failed_files.txt"
rm -f "$LOG_DIR"/status/*.done "$LOG_DIR"/status/*.fail 2>/dev/null || true

if [[ ! -s $MANIFEST_FILE ]]; then
  log "[INFO] building manifest: $MANIFEST_FILE"
  python3 "$BUILDER" "$LIST_FILE" "$MANIFEST_FILE"
else
  log "[INFO] reusing existing manifest: $MANIFEST_FILE"
fi

run_ascp() {
  local src=$1
  local dest_dir=$2
  local -a cmd
  cmd=("$ASCP_BIN" -Q -P "$ASCP_PORT" -i "$ASCP_KEY" -k1 -l "$ASCP_RATE")
  [[ $ASCP_DISABLE_ENCRYPTION == yes ]] && cmd+=(-T)
  cmd+=("$src" "$dest_dir/")
  "${cmd[@]}"
}

download_one() {
  local run=$1
  local sample=$2
  local mate=$3
  local filename=$4
  local src=$5
  local ftp_url=$6
  local bytes=$7
  local layout=$8
  local run_dir="$OUT_DIR/$run"
  local final_file="$run_dir/$filename"
  local partial_file="$run_dir/$filename.partial"
  local ckpt_file="$run_dir/$filename.aspera-ckpt"
  local safe_name
  safe_name=$(echo "$filename" | tr '/ ' '__')
  local log_file="$LOG_DIR/${safe_name}.log"
  local status_done="$LOG_DIR/status/${safe_name}.done"
  local status_fail="$LOG_DIR/status/${safe_name}.fail"
  local attempt=1

  mkdir -p "$run_dir"

  if [[ -s $final_file ]]; then
    log "[SKIP] $filename already downloaded" | tee -a "$log_file"
    printf '%s\t%s\t%s\t%s\n' "$run" "$sample" "$mate" "$final_file" > "$status_done"
    return 0
  fi

  : > "$log_file"
  while (( attempt <= MAX_RETRIES )); do
    log "[INFO] run=$run mate=$mate sample=${sample:-NA} file=$filename attempt=${attempt}/${MAX_RETRIES}" | tee -a "$log_file"
    if run_ascp "$src" "$run_dir" >> "$log_file" 2>&1; then
      if [[ -s $final_file ]]; then
        log "[OK] $filename downloaded" | tee -a "$log_file"
        printf '%s\t%s\t%s\t%s\n' "$run" "$sample" "$mate" "$final_file" > "$status_done"
        return 0
      fi
      log "[WARN] $filename command finished but final file missing" | tee -a "$log_file"
    else
      log "[WARN] $filename attempt ${attempt} failed" | tee -a "$log_file"
    fi

    if [[ -f $partial_file ]]; then
      log "[INFO] partial remains: $(du -h "$partial_file" | awk '{print $1}')" | tee -a "$log_file"
    fi
    if [[ -f $ckpt_file ]]; then
      log "[INFO] checkpoint present: $ckpt_file" | tee -a "$log_file"
    fi
    if (( attempt < MAX_RETRIES )); then
      log "[INFO] retrying after ${RETRY_SLEEP}s ; ftp_url=$ftp_url" | tee -a "$log_file"
      sleep "$RETRY_SLEEP"
    fi
    attempt=$((attempt + 1))
  done

  log "[ERROR] $filename failed after ${MAX_RETRIES} attempts ; ftp_url=$ftp_url ; expected_bytes=${bytes:-NA} ; layout=${layout:-NA}" | tee -a "$log_file"
  printf '%s\t%s\t%s\t%s\n' "$run" "$sample" "$mate" "$filename" > "$status_fail"
  return 1
}

wait_for_slot() {
  while (( $(jobs -pr | wc -l) >= PARALLEL )); do
    sleep "$POLL_SLEEP"
  done
}

emit_manifest_records() {
  python3 - "$MANIFEST_FILE" <<'PY'
import csv
import sys

manifest_path = sys.argv[1]
with open(manifest_path, newline='') as handle:
    reader = csv.DictReader(handle, delimiter='\t')
    for row in reader:
        values = [
            row.get('run_accession', ''),
            row.get('sample_name', ''),
            row.get('mate', ''),
            row.get('filename', ''),
            row.get('aspera_source', ''),
            row.get('ftp_url', ''),
            row.get('bytes', ''),
            row.get('layout', ''),
        ]
        for value in values:
            sys.stdout.buffer.write(value.encode('utf-8'))
            sys.stdout.buffer.write(b'\0')
PY
}

while IFS= read -r -d '' run \
  && IFS= read -r -d '' sample \
  && IFS= read -r -d '' mate \
  && IFS= read -r -d '' filename \
  && IFS= read -r -d '' src \
  && IFS= read -r -d '' ftp_url \
  && IFS= read -r -d '' bytes \
  && IFS= read -r -d '' layout; do
  [[ -z ${run:-} ]] && continue
  wait_for_slot
  download_one "$run" "$sample" "$mate" "$filename" "$src" "$ftp_url" "$bytes" "$layout" &
done < <(emit_manifest_records)

wait
cat "$LOG_DIR"/status/*.done > "$LOG_DIR/downloaded_files.txt" 2>/dev/null || true
cat "$LOG_DIR"/status/*.fail > "$LOG_DIR/failed_files.txt" 2>/dev/null || true
log "[DONE] ENA Aspera FASTQ download finished. success=$LOG_DIR/downloaded_files.txt failed=$LOG_DIR/failed_files.txt manifest=$MANIFEST_FILE"
