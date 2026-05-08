#!/usr/bin/env python3
import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path


def build_argparser():
    p = argparse.ArgumentParser(
        description='Verify ENA FASTQ download completeness and leftover partial/checkpoint files.'
    )
    p.add_argument('manifest', help='Manifest TSV built by build_ena_fastq_manifest.py')
    p.add_argument('out_dir', help='FASTQ output directory')
    p.add_argument('--json', action='store_true', help='Print JSON instead of text summary')
    p.add_argument('--strict', action='store_true', help='Exit non-zero if missing files or leftovers exist')
    return p


def main():
    args = build_argparser().parse_args()
    manifest = Path(args.manifest)
    out_dir = Path(args.out_dir)

    if not manifest.exists():
        raise SystemExit(f'manifest not found: {manifest}')
    if not out_dir.exists():
        raise SystemExit(f'out_dir not found: {out_dir}')

    expected = []
    with manifest.open() as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            expected.append(out_dir / row['run_accession'] / row['filename'])

    existing = [p for p in expected if p.is_file() and p.stat().st_size > 0]
    missing = [str(p) for p in expected if not (p.is_file() and p.stat().st_size > 0)]
    partials = sorted(str(p) for p in out_dir.rglob('*.partial'))
    ckpts = sorted(str(p) for p in out_dir.rglob('*.aspera-ckpt'))
    final_bytes = sum(p.stat().st_size for p in expected if p.is_file())

    try:
        du_sh = subprocess.check_output(['du', '-sh', str(out_dir)], text=True).strip()
    except Exception as e:
        du_sh = f'ERROR: {e}'

    result = {
        'expected_count': len(expected),
        'existing_count': len(existing),
        'missing_count': len(missing),
        'missing': missing,
        'final_fastq_bytes': final_bytes,
        'final_fastq_gb': round(final_bytes / 1e9, 2),
        'final_fastq_gib': round(final_bytes / (1024 ** 3), 2),
        'du_sh_out_dir': du_sh,
        'partial_count': len(partials),
        'partials': partials,
        'ckpt_count': len(ckpts),
        'ckpts': ckpts,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"expected_count\t{result['expected_count']}")
        print(f"existing_count\t{result['existing_count']}")
        print(f"missing_count\t{result['missing_count']}")
        print(f"final_fastq_gb\t{result['final_fastq_gb']}")
        print(f"final_fastq_gib\t{result['final_fastq_gib']}")
        print(f"du_sh_out_dir\t{result['du_sh_out_dir']}")
        print(f"partial_count\t{result['partial_count']}")
        print(f"ckpt_count\t{result['ckpt_count']}")
        if result['missing_count']:
            print('missing_files')
            for item in result['missing']:
                print(item)

    if args.strict and (result['missing_count'] or result['partial_count'] or result['ckpt_count']):
        sys.exit(1)


if __name__ == '__main__':
    main()
