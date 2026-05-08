#!/usr/bin/env python3
import argparse
import re
import sys


def parse_args():
    parser = argparse.ArgumentParser(
        description="Calculate per-sample SNP index from a filtered VCF and emit VCF to stdout."
    )
    parser.add_argument("input_vcf", help="Input uncompressed VCF")
    return parser.parse_args()


def extract_index(info_field):
    match = re.search(r"DP4=(\d+),(\d+),(\d+),(\d+);", info_field)
    if not match:
        return None

    dp_ref1 = int(match.group(1))
    dp_ref2 = int(match.group(2))
    dp_alt1 = int(match.group(3))
    dp_alt2 = int(match.group(4))
    total = dp_ref1 + dp_ref2 + dp_alt1 + dp_alt2
    if total <= 0:
        return None

    return (dp_alt1 + dp_alt2) / total


def main():
    args = parse_args()

    with open(args.input_vcf, "r") as f:
        for line in f:
            if line.startswith("##FORMAT=<ID=GT"):
                sys.stdout.write(line)
                sys.stdout.write('##FORMAT=<ID=INDEX,Number=1,Type=Float,Description="Depth_index">\n')
            elif line.startswith("#"):
                sys.stdout.write(line)
            else:
                fields = line.rstrip("\n").split("\t")
                if len(fields) < 10:
                    continue

                index = extract_index(fields[7])
                if index is None:
                    continue

                gt = fields[-1].split(":")[0]
                prefix = "\t".join(fields[:-2])
                sys.stdout.write(f"{prefix}\tGT:INDEX\t{gt}:{index:.6f}\n")


if __name__ == "__main__":
    main()
