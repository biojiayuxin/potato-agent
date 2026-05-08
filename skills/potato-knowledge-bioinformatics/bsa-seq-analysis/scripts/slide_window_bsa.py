#!/usr/bin/env python3
import argparse
from collections import defaultdict


def parse_args():
    p = argparse.ArgumentParser(description="Sliding window analysis for BSA-Seq delta index")
    p.add_argument("--input", required=True, help="delta_index.txt path")
    p.add_argument("--output", required=True, help="window_result.txt path")
    p.add_argument("--genome-fasta", required=True, help="reference genome fasta used to derive chromosome lengths")
    p.add_argument("--window-size", type=int, required=True, help="window size in bp")
    p.add_argument("--step-size", type=int, required=True, help="step size in bp")
    return p.parse_args()


def load_fasta_lengths(path):
    lengths = {}
    chrom = None
    length = 0
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            if line.startswith(">"):
                if chrom is not None:
                    lengths[chrom] = length
                chrom = line[1:].split()[0]
                length = 0
            else:
                length += len(line.strip())
    if chrom is not None:
        lengths[chrom] = length
    return lengths


def load_delta_index(path):
    data = defaultdict(list)
    with open(path) as f:
        header = f.readline().strip().split("\t")
        expected = ["#CHROM", "p1", "p2", "REF", "ALT"]
        if header[:5] != expected:
            raise ValueError(f"Unexpected header in {path}: {header}")
        for line in f:
            line = line.strip()
            if not line:
                continue
            fields = line.split("\t")
            if len(fields) < 8:
                continue
            chrom = fields[0]
            try:
                p1 = int(fields[1])
                p2 = int(fields[2])
                index_a = float(fields[5])
                index_b = float(fields[6])
                delta = float(fields[7])
            except ValueError:
                continue
            data[chrom].append((p1, p2, index_a, index_b, delta))
    for chrom in data:
        data[chrom].sort(key=lambda x: x[0])
    return data


def mean(values):
    return sum(values) / len(values) if values else 0.0


def compute_windows(records, chrom_len, window_size, step_size):
    result = []
    n = len(records)
    left = 0
    right = 0
    for start in range(0, chrom_len, step_size):
        end = min(start + window_size, chrom_len)
        while left < n and records[left][1] <= start:
            left += 1
        if right < left:
            right = left
        while right < n and records[right][0] < end:
            right += 1
        window = [rec for rec in records[left:right] if rec[1] > start and rec[0] < end]
        a_vals = [x[2] for x in window]
        b_vals = [x[3] for x in window]
        d_vals = [x[4] for x in window]
        result.append((start, end, mean(a_vals), mean(b_vals), abs(mean(d_vals))))
        if end >= chrom_len:
            break
    return result


def main():
    args = parse_args()
    lengths = load_fasta_lengths(args.genome_fasta)
    data = load_delta_index(args.input)

    with open(args.output, "w") as out:
        out.write("Chrom\tStart\tEnd\tSNP_index_A_window\tSNP_index_B_window\tDelta_index_window\n")
        for chrom in sorted(lengths.keys()):
            windows = compute_windows(data.get(chrom, []), lengths[chrom], args.window_size, args.step_size)
            for start, end, avg_a, avg_b, avg_d in windows:
                out.write(f"{chrom}\t{start}\t{end}\t{avg_a:.6f}\t{avg_b:.6f}\t{avg_d:.6f}\n")


if __name__ == "__main__":
    main()
