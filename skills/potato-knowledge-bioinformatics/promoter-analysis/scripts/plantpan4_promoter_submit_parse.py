#!/usr/bin/env python3
"""Submit promoter FASTA to PlantPAN4 and parse promoter-analysis results.

This script uses only Python standard library modules. It submits the same form
as https://plantpan.itps.ncku.edu.tw/plantpan4/promoter_analysis.php to
promoter_results.php, saves the raw HTML/downloaded text, and creates practical
TSV summaries.
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import html as html_lib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib import parse, request
from urllib.parse import urljoin

BASE_URL = "https://plantpan.itps.ncku.edu.tw/plantpan4/"
FORM_URL = urljoin(BASE_URL, "promoter_analysis.php")
RESULT_URL = urljoin(BASE_URL, "promoter_results.php")
DEFAULT_PRIORITY_PATTERN = (
    r"MADS|WRKY|bZIP|NAC|NAM|MYB|Myb/SANT|Dof|AP2|ERF|B3|ARF|"
    r"bHLH|HD-ZIP|TCP|GATA|Trihelix|Homeodomain|SBP|GRAS|G2-like"
)


def read_fasta(path: Path, query_name: str | None = None) -> tuple[str, str, str]:
    header = None
    seq_parts: list[str] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(">"):
            if header is None:
                header = line[1:].strip()
            continue
        seq_parts.append(line)
    seq = "".join(seq_parts).upper().replace(" ", "")
    if not seq:
        raise SystemExit(f"No sequence found in FASTA: {path}")
    bad = sorted(set(seq) - set("ACGTNRYMKSWHBVD-"))
    if bad:
        raise SystemExit(f"Unexpected sequence characters in {path}: {''.join(bad)}")
    name = query_name or ((header or "promoter_sequence").split()[0] or "promoter_sequence")
    fasta = f">{name}\n" + "\n".join(seq[i : i + 80] for i in range(0, len(seq), 80)) + "\n"
    return name, seq, fasta


def fetch_page(url: str, timeout: int = 60) -> str:
    req = request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def submit_plantpan(
    fasta_text: str,
    choose: str,
    species: list[str],
    modes: list[str],
    timeout: int,
) -> str:
    fields: list[tuple[str, str]] = [
        ("sequence", fasta_text),
        ("motif", "database"),
        ("choose", choose),
        ("motif_seq", ""),
    ]
    if choose == "others":
        if not species:
            species = ["Arabidopsis_thaliana"]
        for sp in species:
            fields.append(("TFBSspecies[]", sp))
    for mode in modes:
        fields.append(("mode[]", mode))
    data = parse.urlencode(fields).encode()
    req = request.Request(
        RESULT_URL,
        data=data,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)",
            "Referer": FORM_URL,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def download_result_text(result_html: str, timeout: int) -> tuple[str, str | None]:
    m = re.search(r'href=["\']([^"\']*download_promoter\.php[^"\']+)["\']', result_html)
    if not m:
        return "", None
    href = html_lib.unescape(m.group(1))
    url = urljoin(BASE_URL, href)
    req = request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": RESULT_URL})
    with request.urlopen(req, timeout=timeout) as r:
        text = r.read().decode("utf-8", "replace")
    return text, url


def clean_html_text(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s)
    s = html_lib.unescape(s).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def parse_downloaded_tsv(text: str, seq_len: int) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if not text.strip():
        return rows
    lines = text.splitlines()
    reader = csv.DictReader(lines, delimiter="\t")
    needed = {"Matrix ID", "TF Family", "TF ID or Motif Name", "Position", "Hit Sequence", "Strand", "Similar Score"}
    if not reader.fieldnames or not needed.issubset(set(reader.fieldnames)):
        return rows
    for r in reader:
        pos_s = (r.get("Position") or "").strip()
        if not pos_s.isdigit():
            continue
        pos = int(pos_s)
        hit_seq = r.get("Hit Sequence", "")
        upper = [i for i, c in enumerate(hit_seq) if c.isupper() and c.upper() in "ACGTN"]
        if upper:
            core_start = pos + min(upper)
            core_end = pos + max(upper)
            core_seq = hit_seq[min(upper) : max(upper) + 1]
        else:
            core_start = pos
            core_end = pos + len(hit_seq) - 1
            core_seq = hit_seq
        try:
            score: float | str = float(r.get("Similar Score", ""))
        except Exception:
            score = ""
        rows.append(
            {
                "matrix_id": r.get("Matrix ID", ""),
                "tf_family": r.get("TF Family", ""),
                "tf_id_or_motif_name": r.get("TF ID or Motif Name", ""),
                "position_1based": pos,
                "hit_sequence": hit_seq,
                "strand": r.get("Strand", ""),
                "similar_score": score,
                "full_start_1based": pos,
                "full_end_1based": pos + len(hit_seq) - 1,
                "relative_full_start_to_ATG": pos - (seq_len + 1),
                "relative_full_end_to_ATG": (pos + len(hit_seq) - 1) - (seq_len + 1),
                "core_start_1based": core_start,
                "core_end_1based": core_end,
                "relative_core_start_to_ATG": core_start - (seq_len + 1),
                "relative_core_end_to_ATG": core_end - (seq_len + 1),
                "core_sequence": core_seq,
            }
        )
    return rows


def parse_html_fallback(result_html: str, seq_len: int) -> list[dict[str, object]]:
    """Parse hidden PlantPAN result tables from HTML if download text is absent."""
    rows: list[dict[str, object]] = []
    block_pat = re.compile(
        r"<input[^>]*name=['\"]Promoter['\"][^>]*value=['\"]([^'\"]+)['\"][^>]*>.*?"
        r"<a[^>]*>(TFmatrixID_\d+)</a>&nbsp;&nbsp;/&nbsp;&nbsp;(.*?)</strong>.*?"
        r"<table width=['\"]100%['\"] border=['\"]1['\"] align=['\"]center['\"] style=['\"]display: none['\"]>(.*?)</table>",
        re.S | re.I,
    )
    row_pat = re.compile(
        r"<tr>\s*<td>\s*(\d+)\s*</td>\s*<td>\s*(.*?)\s*</td>\s*<td>\s*([+-])\s*</td>\s*<td>\s*([0-9.]+)\s*</td>\s*</tr>",
        re.S | re.I,
    )
    for bm in block_pat.finditer(result_html):
        value, _matrix_id_link, fam_html, inner = bm.groups()
        matrix_id = value.split(":", 1)[0]
        family = clean_html_text(fam_html)
        for pos_s, hit_html, strand, score_s in row_pat.findall(inner):
            pos = int(pos_s)
            hit_seq = clean_html_text(hit_html)
            upper = [i for i, c in enumerate(hit_seq) if c.isupper() and c.upper() in "ACGTN"]
            if upper:
                core_start = pos + min(upper)
                core_end = pos + max(upper)
                core_seq = hit_seq[min(upper) : max(upper) + 1]
            else:
                core_start = pos
                core_end = pos + len(hit_seq) - 1
                core_seq = hit_seq
            rows.append(
                {
                    "matrix_id": matrix_id,
                    "tf_family": family,
                    "tf_id_or_motif_name": "",
                    "position_1based": pos,
                    "hit_sequence": hit_seq,
                    "strand": strand,
                    "similar_score": float(score_s),
                    "full_start_1based": pos,
                    "full_end_1based": pos + len(hit_seq) - 1,
                    "relative_full_start_to_ATG": pos - (seq_len + 1),
                    "relative_full_end_to_ATG": (pos + len(hit_seq) - 1) - (seq_len + 1),
                    "core_start_1based": core_start,
                    "core_end_1based": core_end,
                    "relative_core_start_to_ATG": core_start - (seq_len + 1),
                    "relative_core_end_to_ATG": core_end - (seq_len + 1),
                    "core_sequence": core_seq,
                }
            )
    return rows


def family_terms(tf_family: str) -> list[str]:
    return [t.strip() for t in re.split(r"\s*;\s*", tf_family or "") if t.strip()]


def write_tsv(path: Path, rows: list[dict[str, object]], fields: list[str] | None = None) -> None:
    if fields is None:
        fields = list(rows[0].keys()) if rows else []
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        w.writeheader()
        w.writerows(rows)


def make_summaries(
    rows: list[dict[str, object]],
    priority_pattern: str,
    score_cutoff: float,
    outdir: Path,
    seq_len: int,
) -> dict[str, int]:
    priority_re = re.compile(priority_pattern, re.I)
    # family summary
    fam_count: Counter[str] = Counter()
    fam_matrices: defaultdict[str, set[str]] = defaultdict(set)
    fam_scores: defaultdict[str, list[float]] = defaultdict(list)
    for r in rows:
        for term in family_terms(str(r.get("tf_family", ""))):
            fam_count[term] += 1
            fam_matrices[term].add(str(r.get("matrix_id", "")))
            score = r.get("similar_score", "")
            if isinstance(score, (int, float)):
                fam_scores[term].append(float(score))
    fam_rows = []
    for term, n in fam_count.most_common():
        scores = fam_scores[term]
        fam_rows.append(
            {
                "family_term": term,
                "hit_count": n,
                "unique_matrix_count": len(fam_matrices[term]),
                "mean_score": round(sum(scores) / len(scores), 4) if scores else "",
                "max_score": max(scores) if scores else "",
            }
        )
    write_tsv(outdir / "plantpan4_tfbs_family_summary.tsv", fam_rows)

    priority_rows = [r for r in rows if priority_re.search(str(r.get("tf_family", "")))]
    write_tsv(outdir / "plantpan4_priority_tfbs_hits.tsv", priority_rows)

    # collapsed priority loci
    collapsed: dict[tuple[str, int, int, str], dict[str, object]] = {}
    for r in priority_rows:
        score = r.get("similar_score", "")
        if not isinstance(score, (int, float)) or float(score) < score_cutoff:
            continue
        for term in family_terms(str(r.get("tf_family", ""))):
            if not priority_re.search(term):
                continue
            key = (
                term,
                int(r["core_start_1based"]),
                int(r["core_end_1based"]),
                str(r.get("core_sequence", "")).upper(),
            )
            cur = collapsed.get(key)
            if cur is None:
                collapsed[key] = {
                    "family_term": term,
                    "core_start_1based": key[1],
                    "core_end_1based": key[2],
                    "relative_core_start_to_ATG": int(r["relative_core_start_to_ATG"]),
                    "relative_core_end_to_ATG": int(r["relative_core_end_to_ATG"]),
                    "core_sequence": key[3],
                    "max_score": float(score),
                    "n_raw_hits": 1,
                    "matrix_ids": {str(r.get("matrix_id", ""))},
                    "tf_ids": {str(r.get("tf_id_or_motif_name", ""))},
                }
            else:
                cur["max_score"] = max(float(cur["max_score"]), float(score))
                cur["n_raw_hits"] = int(cur["n_raw_hits"]) + 1
                cur["matrix_ids"].add(str(r.get("matrix_id", "")))  # type: ignore[union-attr]
                cur["tf_ids"].add(str(r.get("tf_id_or_motif_name", "")))  # type: ignore[union-attr]
    collapsed_rows = []
    for v in collapsed.values():
        row = dict(v)
        row["matrix_ids"] = ";".join(sorted(row["matrix_ids"]))  # type: ignore[arg-type]
        row["tf_ids"] = ";".join(x for x in sorted(row["tf_ids"]) if x)  # type: ignore[arg-type]
        collapsed_rows.append(row)
    collapsed_rows.sort(key=lambda x: (int(x["core_start_1based"]), str(x["family_term"])))
    write_tsv(outdir / "plantpan4_priority_tfbs_collapsed_loci.tsv", collapsed_rows)

    # 300 bp windows
    window_rows = []
    for st in range(1, seq_len + 1, 100):
        en = min(seq_len, st + 299)
        hs = [
            r
            for r in priority_rows
            if not (int(r["core_end_1based"]) < st or int(r["core_start_1based"]) > en)
        ]
        if not hs:
            continue
        counter: Counter[str] = Counter()
        for r in hs:
            for term in family_terms(str(r.get("tf_family", ""))):
                if priority_re.search(term):
                    counter[term] += 1
        window_rows.append(
            {
                "window_start_1based": st,
                "window_end_1based": en,
                "relative_window": f"{st - (seq_len + 1)}..{en - (seq_len + 1)}",
                "priority_tfbs_hits": len(hs),
                "families": ";".join(f"{k}:{v}" for k, v in counter.most_common(15)),
            }
        )
    window_rows.sort(key=lambda x: int(x["priority_tfbs_hits"]), reverse=True)
    write_tsv(outdir / "plantpan4_priority_300bp_windows.tsv", window_rows)

    return {
        "raw_hits": len(rows),
        "priority_hits": len(priority_rows),
        "collapsed_priority_loci": len(collapsed_rows),
        "family_terms": len(fam_rows),
        "priority_windows": len(window_rows),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fasta", required=True, type=Path, help="Promoter FASTA to submit/analyze")
    ap.add_argument("--outdir", required=True, type=Path, help="Output directory")
    ap.add_argument("--query-name", help="Override FASTA header/query name submitted to PlantPAN")
    ap.add_argument("--choose", choices=["allspecies", "others"], default="allspecies")
    ap.add_argument("--species", action="append", default=[], help="TFBSspecies[] value; repeatable; used with --choose others")
    ap.add_argument("--mode", action="append", default=[], help="Optional mode[] value; repeatable; default Tandem and CpNpG")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--parse-only-html", type=Path, help="Skip web submission and parse an existing PlantPAN result HTML")
    ap.add_argument("--parse-only-download", type=Path, help="Skip web submission and parse an existing PlantPAN downloaded TSV")
    ap.add_argument("--priority-pattern", default=DEFAULT_PRIORITY_PATTERN)
    ap.add_argument(
        "--score-cutoff",
        type=float,
        default=0.85,
        help="Minimum Similar Score retained in collapsed priority loci; default 0.85",
    )
    args = ap.parse_args(argv)

    args.outdir.mkdir(parents=True, exist_ok=True)
    name, seq, fasta_text = read_fasta(args.fasta, args.query_name)
    seq_len = len(seq)
    (args.outdir / "submitted_promoter.fa").write_text(fasta_text)

    form_html = ""
    result_html = ""
    download_text = ""
    download_url = None
    modes = args.mode or ["Tandem", "CpNpG"]

    if args.parse_only_download:
        download_text = args.parse_only_download.read_text(errors="replace")
    elif args.parse_only_html:
        result_html = args.parse_only_html.read_text(errors="replace")
        download_text, download_url = download_result_text(result_html, args.timeout)
    else:
        try:
            form_html = fetch_page(FORM_URL, timeout=args.timeout)
            (args.outdir / "plantpan4_promoter_analysis_page.html").write_text(form_html)
        except Exception as e:
            print(f"WARNING: failed to fetch form page: {type(e).__name__}: {e}", file=sys.stderr)
        result_html = submit_plantpan(fasta_text, args.choose, args.species, modes, args.timeout)
        (args.outdir / "plantpan4_results.html").write_text(result_html)
        download_text, download_url = download_result_text(result_html, args.timeout)

    if result_html and not (args.outdir / "plantpan4_results.html").exists():
        (args.outdir / "plantpan4_results.html").write_text(result_html)
    if download_text:
        (args.outdir / "plantpan4_download.txt").write_text(download_text)

    rows = parse_downloaded_tsv(download_text, seq_len)
    parsed_source = "download_tsv"
    if not rows and result_html:
        rows = parse_html_fallback(result_html, seq_len)
        parsed_source = "html_fallback"
    if not rows:
        raise SystemExit("No TFBS rows parsed from PlantPAN result; inspect saved HTML/text files.")

    hit_fields = [
        "matrix_id",
        "tf_family",
        "tf_id_or_motif_name",
        "position_1based",
        "hit_sequence",
        "strand",
        "similar_score",
        "full_start_1based",
        "full_end_1based",
        "relative_full_start_to_ATG",
        "relative_full_end_to_ATG",
        "core_start_1based",
        "core_end_1based",
        "relative_core_start_to_ATG",
        "relative_core_end_to_ATG",
        "core_sequence",
    ]
    write_tsv(args.outdir / "plantpan4_tfbs_hits.tsv", rows, hit_fields)
    summary = make_summaries(rows, args.priority_pattern, args.score_cutoff, args.outdir, seq_len)

    manifest = {
        "run_time": _dt.datetime.now().isoformat(timespec="seconds"),
        "form_url": FORM_URL,
        "result_url": RESULT_URL,
        "download_url": download_url,
        "query_name": name,
        "input_fasta": str(args.fasta),
        "sequence_length": seq_len,
        "choose": args.choose,
        "species": args.species,
        "modes": modes,
        "score_cutoff": args.score_cutoff,
        "parsed_source": parsed_source,
        "outputs": {
            "submitted_fasta": "submitted_promoter.fa",
            "results_html": "plantpan4_results.html",
            "download_text": "plantpan4_download.txt" if download_text else None,
            "tfbs_hits": "plantpan4_tfbs_hits.tsv",
            "family_summary": "plantpan4_tfbs_family_summary.tsv",
            "priority_hits": "plantpan4_priority_tfbs_hits.tsv",
            "collapsed_loci": "plantpan4_priority_tfbs_collapsed_loci.tsv",
            "priority_windows": "plantpan4_priority_300bp_windows.tsv",
        },
        "summary": summary,
    }
    (args.outdir / "plantpan4_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))

    print(json.dumps({"outdir": str(args.outdir), **summary}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
