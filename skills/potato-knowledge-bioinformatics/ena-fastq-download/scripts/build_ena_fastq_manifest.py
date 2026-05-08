#!/usr/bin/env python3
import argparse
import csv
import json
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

RUN_KEYS = {
    'run', 'run_accession', 'accession', 'srr', 'err', 'drr', 'runid', 'run_id'
}
SAMPLE_KEYS = {
    'samplename', 'sample_name', 'sample', 'sampleid', 'sample_id'
}
ENA_FIELDS = 'run_accession,library_layout,fastq_ftp,fastq_aspera,fastq_bytes'


def build_argparser():
    p = argparse.ArgumentParser(
        description='Build an ENA FASTQ manifest (one row per actual FASTQ file) from a run list.'
    )
    p.add_argument('input', help='Input TXT/TSV/CSV containing SRR/ERR/DRR runs')
    p.add_argument('output', help='Output TSV manifest path')
    p.add_argument('--max-workers', type=int, default=4, help='Parallel ENA metadata requests (default: 4)')
    p.add_argument('--max-retries', type=int, default=4, help='Retries per run on ENA API failure (default: 4)')
    p.add_argument('--timeout', type=int, default=60, help='HTTP timeout seconds (default: 60)')
    return p


def normalize_key(text: str) -> str:
    return ''.join(ch.lower() for ch in text.strip() if ch.isalnum() or ch == '_')


def parse_input(path: Path):
    text = path.read_text().splitlines()
    rows = []
    seen = set()

    if not text:
        raise SystemExit(f'empty input file: {path}')

    first = text[0].strip()
    has_delim = ('\t' in first) or (',' in first)

    if has_delim:
        with path.open(newline='') as fh:
            sample = fh.read()
        dialect = csv.excel_tab if '\t' in first else csv.excel
        reader = csv.DictReader(sample.splitlines(), dialect=dialect)
        if reader.fieldnames:
            norm = {name: normalize_key(name) for name in reader.fieldnames}
            run_col = next((orig for orig, key in norm.items() if key in RUN_KEYS), None)
            sample_col = next((orig for orig, key in norm.items() if key in SAMPLE_KEYS), None)
            if run_col:
                for row in reader:
                    run = (row.get(run_col) or '').strip()
                    if not run or run in seen:
                        continue
                    seen.add(run)
                    rows.append((run, (row.get(sample_col) or '').strip() if sample_col else ''))

    if rows:
        return rows

    for line in text:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = [x for x in line.replace(',', '\t').split('\t') if x != '']
        if not parts:
            continue
        run = parts[0].strip()
        sample = parts[1].strip() if len(parts) > 1 else ''
        if normalize_key(run) in RUN_KEYS:
            continue
        if run in seen:
            continue
        seen.add(run)
        rows.append((run, sample))

    if not rows:
        raise SystemExit(
            'no runs found. Provide either: (1) a table with a Run/run_accession column, or '
            '(2) a plain text file with one run per line, optionally followed by a sample name.'
        )
    return rows


def fetch_one(run: str, timeout: int, max_retries: int):
    url = (
        'https://www.ebi.ac.uk/ena/portal/api/filereport?'
        + urllib.parse.urlencode({
            'accession': run,
            'result': 'read_run',
            'fields': ENA_FIELDS,
            'format': 'json',
        })
    )
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
            if not data:
                raise RuntimeError(f'ENA returned no metadata for {run}')
            return run, data[0]
        except Exception as e:
            last_err = e
            if attempt < max_retries:
                time.sleep(min(5 * attempt, 20))
    raise RuntimeError(str(last_err))


def main():
    args = build_argparser().parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        raise SystemExit(f'input file not found: {input_path}')

    runs = parse_input(input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    run_order = [run for run, _sample in runs]
    run2sample = {run: sample for run, sample in runs}
    api_map = {}
    errors = []

    with ThreadPoolExecutor(max_workers=args.max_workers) as ex:
        futures = {
            ex.submit(fetch_one, run, args.timeout, args.max_retries): run
            for run in run_order
        }
        for fut in as_completed(futures):
            run = futures[fut]
            try:
                key, value = fut.result()
                api_map[key] = value
            except Exception as e:
                errors.append(f'{run}: {e}')

    if errors:
        raise SystemExit('ENA metadata fetch errors: ' + ' | '.join(errors[:10]) + (' ...' if len(errors) > 10 else ''))

    missing = [run for run in run_order if run not in api_map]
    if missing:
        raise SystemExit('ENA metadata missing for runs: ' + ', '.join(missing[:10]) + (' ...' if len(missing) > 10 else ''))

    with output_path.open('w', newline='') as f:
        writer = csv.writer(f, delimiter='\t')
        writer.writerow(['run_accession', 'sample_name', 'mate', 'filename', 'aspera_source', 'ftp_url', 'bytes', 'layout'])
        file_count = 0
        for run in run_order:
            row = api_map[run]
            ftp_items = [x for x in (row.get('fastq_ftp') or '').split(';') if x]
            aspera_items = [x for x in (row.get('fastq_aspera') or '').split(';') if x]
            byte_items = [x for x in (row.get('fastq_bytes') or '').split(';') if x]
            layout = row.get('library_layout') or ''
            if not ftp_items or not aspera_items:
                raise SystemExit(f'no fastq links for {run}')
            if len(ftp_items) != len(aspera_items):
                raise SystemExit(f'ftp/aspera item count mismatch for {run}')
            if byte_items and len(byte_items) != len(ftp_items):
                raise SystemExit(f'byte/item count mismatch for {run}')

            for idx, (ftp, asp) in enumerate(zip(ftp_items, aspera_items), start=1):
                filename = Path(ftp).name
                if '@' not in asp:
                    asp = 'era-fasp@' + asp
                ftp_url = ftp if ftp.startswith(('http://', 'https://')) else ('https://' + ftp)
                size = byte_items[idx - 1] if idx - 1 < len(byte_items) else ''
                writer.writerow([
                    run,
                    run2sample.get(run, ''),
                    idx,
                    filename,
                    asp,
                    ftp_url,
                    size,
                    layout,
                ])
                file_count += 1

    print(f'wrote manifest: {output_path} ({file_count} files, {len(run_order)} runs)')


if __name__ == '__main__':
    main()
