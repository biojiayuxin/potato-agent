#!/usr/bin/env python3
import argparse
import subprocess


def parse_args():
    p = argparse.ArgumentParser(description="Filter VCF by QUAL and sample-specific depth range from fls.txt")
    p.add_argument("--input-vcf", required=True)
    p.add_argument("--output-vcf", required=True)
    p.add_argument("--sample", required=True)
    p.add_argument("--fls", required=True)
    p.add_argument("--qual", type=float, required=True)
    return p.parse_args()


def load_sample_depth(fls_path, sample_name):
    with open(fls_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            if parts[0] == sample_name:
                return float(parts[1])
    raise ValueError(f"Sample {sample_name} not found in {fls_path}")


def main():
    args = parse_args()
    depth_gb = load_sample_depth(args.fls, args.sample)
    dp_min = depth_gb / 3.0
    dp_max = depth_gb * 3.0
    expr = f"QUAL>{args.qual} && DP>={dp_min:.4f} && DP<={dp_max:.4f}"

    subprocess.run([
        "bcftools", "filter",
        "-i", expr,
        args.input_vcf,
        "-Ov",
        "-o", args.output_vcf
    ], check=True)


if __name__ == "__main__":
    main()
