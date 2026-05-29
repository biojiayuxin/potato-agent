# Assembly directory cleanup audit notes

Use when a user asks which intermediate files can be deleted from a HiFi/Hi-C hifiasm + Juicer/3D-DNA assembly workspace to reduce disk usage.

## Audit commands

Prefer read-only inspection first; do not delete without explicit confirmation.

```bash
ROOT=/path/to/assembly_workdir
python3 - <<'PY'
from pathlib import Path
import os, collections
root=Path(os.environ.get('ROOT','/path/to/assembly_workdir'))

def human(n):
    n=float(n); units=['B','K','M','G','T']
    for u in units:
        if n<1024 or u==units[-1]:
            return f'{n:.1f}{u}' if u!='B' else f'{int(n)}B'
        n/=1024

dir_sizes=collections.defaultdict(int); dir_counts=collections.defaultdict(int)
for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
    dp=Path(dirpath)
    for name in list(dirnames)+filenames:
        p=dp/name
        try: st=p.lstat()
        except Exception: continue
        if p.is_dir() and not p.is_symlink():
            continue
        disk=st.st_blocks*512 if hasattr(st,'st_blocks') else st.st_size
        for anc in [p.parent]+list(p.parent.parents):
            if anc==root or str(anc).startswith(str(root)):
                try: r=anc.relative_to(root)
                except Exception: continue
                key=str(r) if str(r)!='.' else '.'
                dir_sizes[key]+=disk; dir_counts[key]+=1
            if anc==root: break
print('TOTAL_DISK', human(dir_sizes['.']), 'files', dir_counts['.'])
for d,s in sorted(dir_sizes.items(), key=lambda x:(x[0].count('/'), x[0])):
    if d=='.' or d.count('/')<=1:
        print(f'{human(s):>8}\t{dir_counts[d]:>5}\t{d}')
PY
```

For largest files:

```bash
find "$ROOT" -xdev -type f -printf '%s\t%p\n' | sort -nr | head -n 80 | awk '{printf "%.2fG\t%s\n",$1/1024/1024/1024,$2}'
```

## Common deletion candidates after hifiasm + Juicer/3D-DNA

Classify as recommendations, not automatic deletion:

### Usually safe if final assemblies/QC are already archived

- `failed_runs/` and incomplete rerun prefixes such as `hifiasm_out/SAMPLE.hifiasm.l0.*` when logs show the run failed or was killed.
- `hifiasm_out/*.bin` (`ec.bin`, `ovlp.*.bin`, `hic.*.bin`): hifiasm cache files; only useful for reusing caches during parameter reruns.
- `hifiasm_out/*.{p_utg,r_utg}.gfa`: unitig graph files; keep only if graph-level debugging is still needed.
- Juicer heavy intermediates after 3D-DNA is finalized:
  - `*/juicer/aligned/` (`merged_nodups.txt`, `merged*.txt`, dedup BAMs)
  - `*/juicer/splits/` BAM/split files
- Non-final 3D-DNA contact maps: `*.0.hic`, `*.polished.hic`, `*.split.hic` when a chosen final `.hic` is retained or no contact map is needed.
- `.snakemake/` metadata and regenerated dotplot intermediates (`raw.delta`, `filter.delta`, `fplot/rplot`) if plots/tables are already saved.

### Delete only after confirming final files are real, not dangling symlinks

3D-DNA pipelines often place final-looking files in `results/` as symlinks into `work/`, for example `results/*.draft_HiC.fasta -> work/*/3ddna/*.draft_HiC.fasta` or `*.final.fasta -> *.rawchrom.fasta`. Before deleting `work/`, check:

```bash
find "$ROOT" -type l -ls
readlink -f path/to/final.fasta
```

If needed, copy final FASTA/HIC/assembly files to a stable `results/final/` directory with `cp -L` before cleaning work directories.

### Usually keep

- Input `data/` soft links; they are small and preserve provenance.
- `scripts/`, `logs/`, `qc/`, execution plans/README files.
- Final hifiasm FASTA: `*.hic.hap1.p_ctg.fa`, `*.hic.hap2.p_ctg.fa`, `*.hic.p_ctg.fa`.
- Chosen 3D-DNA final FASTA/assembly/contact map, after resolving symlinks.

## Reporting style

Report a compact table with path/pattern, estimated size, and rationale. Separate into: `优先可删除`, `谨慎删除`, `建议保留`. Include a short warning for symlinked final files before recommending removal of `work/` directories.