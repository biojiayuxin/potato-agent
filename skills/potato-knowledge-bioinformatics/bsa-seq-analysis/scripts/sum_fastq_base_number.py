#!/usr/bin/env python3
import argparse
import csv
import glob
import os
import shutil
import subprocess


def parse_args():
    p = argparse.ArgumentParser(description="Summarize FASTQ sequencing amount (Gb) with seqkit")
    p.add_argument("--fastq-dir", required=True, help="Directory containing *_R1.fq.gz and *_R2.fq.gz")
    p.add_argument("--output", required=True, help="Output fls.txt")
    p.add_argument("--threads", type=int, default=10, help="seqkit threads")
    return p.parse_args()


def normalize_sample_name(name):
    for suffix in ["_R1", "_r1", "_1"]:
        if suffix in name:
            return name.split(suffix)[0]
    return None


def find_seqkit():
    path_seqkit = shutil.which("seqkit")
    if path_seqkit:
        return path_seqkit
    env_root = "/root/micromamba/envs"
    if os.path.isdir(env_root):
        for env_name in sorted(os.listdir(env_root)):
            candidate = os.path.join(env_root, env_name, "bin", "seqkit")
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    raise FileNotFoundError("seqkit not found in PATH or /root/micromamba/envs/*/bin")


def main():
    args = parse_args()
    pattern = os.path.join(args.fastq_dir, "*.fq.gz")
    fastqs = sorted(glob.glob(pattern))
    if not fastqs:
        raise FileNotFoundError(f"No FASTQ files found: {pattern}")

    seqkit = find_seqkit()
    cmd = [seqkit, "stats", *fastqs, "-T", "--threads", str(args.threads), "--basename"]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)

    sample_to_gb = {}
    reader = csv.DictReader(result.stdout.strip().splitlines(), delimiter='\t')
    for row in reader:
        file_name = row.get("file", "")
        sample = normalize_sample_name(file_name)
        if sample is None:
            continue
        num_bases = int(row["sum_len"])
        if sample not in sample_to_gb:
            sample_to_gb[sample] = num_bases * 2 / 1e9

    with open(args.output, "w") as out:
        out.write("# 样本ID\t测序数据量(Gb)\n")
        for sample in sorted(sample_to_gb):
            out.write(f"{sample}\t{sample_to_gb[sample]:.2f}\n")


if __name__ == "__main__":
    main()
