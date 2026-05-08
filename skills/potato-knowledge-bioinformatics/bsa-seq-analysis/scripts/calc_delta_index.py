#!/usr/bin/env python3
import argparse
import glob
import gzip
import os


def parse_args():
    parser = argparse.ArgumentParser(
        description="Calculate SNP-index and delta SNP-index from merged VCF files."
    )
    parser.add_argument(
        "--input-pattern",
        required=True,
        help="Glob pattern for merged VCF files, e.g. 06-merge/*.g.vcf or 06-merge/*.g.vcf.gz",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output delta_index table path",
    )
    parser.add_argument(
        "--sample-a",
        required=True,
        help="Sample name for pool A",
    )
    parser.add_argument(
        "--sample-b",
        required=True,
        help="Sample name for pool B",
    )
    return parser.parse_args()


def open_vcf(vcf_file):
    if vcf_file.endswith('.gz'):
        return gzip.open(vcf_file, 'rt')
    return open(vcf_file, 'r')


def extract_index(sample_field):
    if './.' in sample_field:
        return None
    parts = sample_field.split(':')
    if len(parts) < 2:
        return None
    try:
        return float(parts[-1])
    except ValueError:
        return None


def process_vcf(vcf_file, sample_a, sample_b):
    results = []
    with open_vcf(vcf_file) as handle:
        a_idx = -1
        b_idx = -1
        for line in handle:
            if line.startswith('#CHROM'):
                header = line.rstrip('\n').split('\t')
                if sample_a in header:
                    a_idx = header.index(sample_a)
                if sample_b in header:
                    b_idx = header.index(sample_b)
            elif not line.startswith('#'):
                fields = line.rstrip('\n').split('\t')
                if len(fields) >= max(a_idx, b_idx) + 1 and a_idx > 0 and b_idx > 0:
                    index_a = extract_index(fields[a_idx])
                    index_b = extract_index(fields[b_idx])
                    if index_a is not None and index_b is not None:
                        delta = index_a - index_b
                        if index_a != 0 or index_b != 0:
                            results.append((
                                fields[0],
                                int(fields[1]) - 1,
                                fields[1],
                                fields[3],
                                fields[4],
                                index_a,
                                index_b,
                                delta,
                            ))
    return results


def main():
    args = parse_args()
    vcf_files = sorted(glob.glob(args.input_pattern))
    if not vcf_files:
        raise FileNotFoundError(f"No VCF files matched pattern: {args.input_pattern}")

    outdir = os.path.dirname(args.output)
    if outdir:
        os.makedirs(outdir, exist_ok=True)

    with open(args.output, 'w') as out:
        out.write(f"#CHROM\tp1\tp2\tREF\tALT\t{args.sample_a}\t{args.sample_b}\tdelta\n")
        for vcf in vcf_files:
            results = process_vcf(vcf, args.sample_a, args.sample_b)
            for chrom, p1, p2, ref, alt, index_a, index_b, delta in results:
                out.write(
                    f"{chrom}\t{p1}\t{p2}\t{ref}\t{alt}\t{index_a:.6f}\t{index_b:.6f}\t{delta:.6f}\n"
                )


if __name__ == '__main__':
    main()
