---
name: search-gene-function
description: 根据马铃薯基因号检索其在马铃薯中的已报道功能，并用 DIAMOND 查找拟南芥、水稻和玉米 TOP10 同源候选，再逐个检索模式植物同源基因的功能与 DOI，输出每个马铃薯基因一个功能证据 CSV。
version: 2.0.0
author: Potato Agent
license: MIT
metadata:
  hermes:
    tags: [potato, gene-function, homolog, diamond, Arabidopsis, rice, maize, DOI]
    related_skills: [potato-gene-search, potato-knowledge-search, arabidopsis-gene-search, rice-gene-search, maize-gene-search, literature-review, slurm-for-long-running-tasks]
prerequisites:
  commands: [python3, diamond]
---

# Search Gene Function

## 目标定位

本技能用于根据马铃薯基因号查询基因功能。核心目标不是只做注释拼接，而是生成**可追溯的功能证据表**：

1. 查询该马铃薯基因在马铃薯中的基因名、已报道基因号、结构域和文献功能证据。
2. 从 `/mnt/data/public_data/Genomes` 中提取该马铃薯基因的蛋白序列。
3. 用 DIAMOND blastp 比对拟南芥、水稻和玉米蛋白集，保留每个物种 TOP10 hit。
4. 对 TOP10 hit 逐个查询同源基因功能，并记录 DOI。
5. 每个输入马铃薯基因输出一个 CSV 文件。

最终 CSV/TSV/报告文件默认使用 English/ASCII，避免编码问题；原始 API/RAG/文献检索结果可保留 Unicode。

## 何时使用

- 用户提供一个或多个马铃薯基因号，如 `DM8C01G28550`，希望查询其功能。
- 用户需要结合马铃薯自身文献和拟南芥、水稻、玉米同源基因功能推断候选基因功能。
- 用户需要 TOP10 同源候选的 gene ID、gene name、blastp identity、功能描述和 DOI。

## 不做什么

- 不把 BLAST TOP hit 直接称为严格直系同源基因；表中统一称为 homolog candidate / hit。
- 不用 `domain-containing protein`、`putative protein`、`expressed protein` 这类泛注释充当明确功能描述；如果没有具体功能证据，功能描述和 DOI 写 `NA`。

## 必须加载的相关技能

执行前加载：

- `potato-gene-search`
- `potato-knowledge-search`
- `arabidopsis-gene-search`
- `rice-gene-search`
- `maize-gene-search`
- `literature-review`

如果是多基因批量查询可以开启子agent并行查询。

## 输入和目录

```bash
GENE=${GENE:-DM8C01G28550}
PUBLIC_DATA_DIR=${PUBLIC_DATA_DIR:-/mnt/data/public_data}
WORK_BASE=${WORK_BASE:-${HOME}/work}
WORK=${WORK_BASE}/search_gene_function_${GENE}
mkdir -p "$WORK"/{00_context,01_potato,02_query_pep,03_diamond,04_hits,05_functions,06_final}

# Portable companion-skill paths. Defaults assume the companion skills are
# installed under the current user's Hermes skill root. If skills are loaded
# from another location, override these variables with the skill_dir reported
# by skill_view.
SKILLS_ROOT=${SKILLS_ROOT:-${HOME}/.hermes/skills}
BIOINFO_SKILLS_ROOT=${BIOINFO_SKILLS_ROOT:-${SKILLS_ROOT}/potato-knowledge-bioinformatics}
POTATO_GENE_SKILL_DIR=${POTATO_GENE_SKILL_DIR:-${BIOINFO_SKILLS_ROOT}/potato-gene-search}
POTATO_RAG_SKILL_DIR=${POTATO_RAG_SKILL_DIR:-${BIOINFO_SKILLS_ROOT}/potato-knowledge-search}
AT_SKILL_DIR=${AT_SKILL_DIR:-${BIOINFO_SKILLS_ROOT}/arabidopsis-gene-search}
LIT_SKILL_DIR=${LIT_SKILL_DIR:-${BIOINFO_SKILLS_ROOT}/literature-review}
export GENE PUBLIC_DATA_DIR WORK_BASE WORK SKILLS_ROOT BIOINFO_SKILLS_ROOT POTATO_GENE_SKILL_DIR POTATO_RAG_SKILL_DIR AT_SKILL_DIR LIT_SKILL_DIR
```

记录运行环境：

```bash
{
  echo -e "key\tvalue"
  echo -e "gene\t${GENE}"
  echo -e "work\t${WORK}"
  echo -e "public_data_dir\t${PUBLIC_DATA_DIR}"
  echo -e "diamond\t$(command -v diamond || true)"
  command -v diamond >/dev/null 2>&1 && diamond version 2>/dev/null | head -1 | sed 's/^/diamond_version\t/'
  date '+run_time\t%F %T %z'
} > "$WORK/00_context/run_context.tsv"
```

## 1. 查询马铃薯基因基础信息

使用 `potato-gene-search` 查询马铃薯基因名、已报道 ID、结构域和可用参考信息。

使用 `potato-gene-search` 技能提供的查询脚本或等价命令执行 details 查询，并将结果保存为：

```text
$WORK/01_potato/potato_gene_details.json
```

`POTATO_GENE_SKILL_DIR` 使用“输入和目录”部分的通用默认值；若当前环境的技能安装在其它位置，用 `skill_view` 返回的 `skill_dir` 覆盖该变量。

```bash
python3 "$POTATO_GENE_SKILL_DIR/scripts/query_potato_gene.py" details "$GENE" \
  > "$WORK/01_potato/potato_gene_details.json"
```

从 JSON 中提取以下字段用于最终表：

- `potato_reported_name`: `symbols` / `symbol` / `gene_symbol`
- `potato_reported_ids`: `ID_reported`
- `potato_domain`: `domain`

注意：基因功能不能只由 domain 或 UniProt 相似描述生成；必须结合马铃薯文献检索结果。

## 2. 检索马铃薯文献功能

使用 `potato-knowledge-search`。查询优先级：

1. 基因名 / symbol，例如 `GAME9 potato`、`GAME9 Solanum tuberosum`。
2. 已报道基因号 / historical ID，例如 `Soltu.DM...`、`PGSC...`。
3. DMv8 gene ID，例如 `DM8C01G28550`。

生成查询词：

```bash
python3 - <<'PY'
import json, os, re
from pathlib import Path
work = Path(os.environ['WORK'])
gene = os.environ['GENE']
data = json.load(open(work/'01_potato/potato_gene_details.json'))
terms=[]; seen=set()
def add(term, source, priority):
    term=re.sub(r'\s+',' ',str(term or '').strip())
    if not term or term.lower() in seen: return
    seen.add(term.lower()); terms.append((priority, source, term))
for field in ['symbols','symbol','gene_symbol']:
    val=data.get(field)
    if isinstance(val,str):
        for x in re.split(r'[,;]', val):
            x=x.strip()
            if x and x not in ['-','NA']:
                add(f'{x} potato', field, 1)
                add(f'{x} Solanum tuberosum', field, 1)
                add(x, field, 1)
for x in re.split(r'[,;\s]+', str(data.get('ID_reported') or '')):
    x=re.sub(r'\(.*?\)$','',x.strip())
    if x: add(f'{x} potato', 'ID_reported', 2)
add(gene, 'gene_id', 3)
terms.sort()
with open(work/'01_potato/potato_rag_query_terms.tsv','w') as out:
    out.write('priority\tsource\tquery\n')
    for p,s,t in terms:
        out.write(f'{p}\t{s}\t{t}\n')
PY
```

执行 RAG 检索时，使用 `potato-knowledge-search` 技能提供的查询脚本或等价命令；`POTATO_RAG_SKILL_DIR` 使用“输入和目录”部分的通用默认值，必要时用 `skill_view` 返回的 `skill_dir` 覆盖。

```bash
mkdir -p "$WORK/01_potato/rag"
python3 - <<'PY'
import os, re, subprocess
from pathlib import Path
work=Path(os.environ['WORK'])
rag=Path(os.environ['POTATO_RAG_SKILL_DIR'])/'scripts/query_potato_rag.py'
for line in open(work/'01_potato/potato_rag_query_terms.tsv'):
    if line.startswith('priority\t'): continue
    priority, source, query=line.rstrip('\n').split('\t')
    safe=re.sub(r'[^A-Za-z0-9_.-]+','_',query)[:100]
    out=work/'01_potato'/'rag'/f'{priority}_{safe}.json'
    subprocess.run([
        'python3', str(rag), query,
        '--top-k-retrieve','200','--top-k-rerank','20','--format','json'
    ], stdout=open(out,'w'), stderr=subprocess.DEVNULL, check=False)
PY
```

功能总结规则：

- 优先使用基因名检索到的马铃薯文献片段。
- 只有 gene name 无可用结果时，才使用 reported ID 或 DMv8 gene ID 检索结果。
- `potato_function_description` 写 1-2 句英文高度概括句，说明该基因在马铃薯中的已报道功能或相关生物过程。
- `potato_reference_DOI` 写支持该总结的 DOI；多个 DOI 用 `;` 分隔。
- 如果 gene name 和 gene ID 均无具体功能证据，`potato_function_description=NA`，`potato_reference_DOI=NA`。

## 3. 从 public data 提取马铃薯蛋白序列

优先从共享基因组目录中提取蛋白序列，不要手工改写 ID 后直接假定正确；必须记录实际 FASTA header 和来源文件。

默认候选文件：

```bash
POTATO_PEP_CANDIDATES=(
  "$PUBLIC_DATA_DIR/Genomes/DMv8/DMv8.1.pep.fa"
  "$PUBLIC_DATA_DIR/Genomes/DMv8/DMv8.2.pep.fa"
  "$PUBLIC_DATA_DIR/Genomes/DMv8/raw_8.1/DM8.1_all.pep.fa"
)
```

提取逻辑：

```bash
python3 - <<'PY'
import json, os, re, sys
from pathlib import Path
work=Path(os.environ['WORK'])
gene=os.environ['GENE']
data=json.load(open(work/'01_potato/potato_gene_details.json'))
fa_list=[p for p in os.environ.get('POTATO_PEP_FILES','').split(':') if p]
if not fa_list:
    public=os.environ.get('PUBLIC_DATA_DIR','/mnt/data/public_data')
    fa_list=[
        f'{public}/Genomes/DMv8/DMv8.1.pep.fa',
        f'{public}/Genomes/DMv8/DMv8.2.pep.fa',
        f'{public}/Genomes/DMv8/raw_8.1/DM8.1_all.pep.fa',
    ]

prefixes=[]
def add(x):
    x=str(x or '').strip().lstrip('>')
    if x and x not in prefixes: prefixes.append(x)
add(gene)
for field in ['transID_repre','ID','gene_id']:
    add(data.get(field))
# try DMv8.2 naming only as a candidate; still verify by actual FASTA header
m=re.match(r'DM8C(\d{2})G(\d+)$', gene)
if m:
    add(f'DM8.2_chr{m.group(1)}G{m.group(2)}')

matches=[]
for fa in fa_list:
    p=Path(fa)
    if not p.exists(): continue
    header=None; seq=[]
    def flush():
        if not header: return
        sid=header.split()[0]
        base=sid.split('.')[0]
        ok=False
        for pref in prefixes:
            pb=pref.split()[0].lstrip('>')
            if sid==pb or base==pb or sid.startswith(pb+'.') or sid.startswith(pb+'_'):
                ok=True; break
        if ok:
            matches.append((str(p), header, ''.join(seq)))
    with p.open() as fh:
        for line in fh:
            line=line.rstrip('\n')
            if line.startswith('>'):
                flush(); header=line[1:]; seq=[]
            else:
                seq.append(line.strip())
        flush()

if not matches:
    sys.exit('ERROR: no protein sequence found in public_data Genomes for '+gene)
# If multiple isoforms are found, prefer transID_repre exact prefix, otherwise longest peptide.
rep=str(data.get('transID_repre') or '').strip().lstrip('>')
def score(x):
    fa,h,s=x; sid=h.split()[0]
    return (1 if rep and (sid==rep or sid.startswith(rep+'.')) else 0, len(s))
fa, header, seq=max(matches, key=score)
out=work/'02_query_pep'/'query.pep.fa'
out.write_text(f'>{header}\n'+'\n'.join(seq[i:i+60] for i in range(0,len(seq),60))+'\n')
with open(work/'02_query_pep'/'query_pep_source.tsv','w') as o:
    o.write('field\tvalue\n')
    o.write(f'input_gene\t{gene}\n')
    o.write(f'fasta_file\t{fa}\n')
    o.write(f'fasta_header\t{header}\n')
    o.write(f'peptide_length\t{len(seq)}\n')
    o.write(f'total_candidate_matches\t{len(matches)}\n')
PY
```

## 4. DIAMOND 比对其它物种蛋白集

目标蛋白集：

```bash
AT_PEP="$PUBLIC_DATA_DIR/Genomes/Other_species/Arabidopsis/At.repre.pep.fa"
RICE_PEP="$PUBLIC_DATA_DIR/Genomes/Other_species/Rice_Nipponbare/RGAP_MSU7_Nipponbare.repre.pep.fa"
MAIZE_PEP="$PUBLIC_DATA_DIR/Genomes/Other_species/Maize/Zm-B73-REFERENCE-NAM-5.0_Zm00001eb.1.protein.fa"
```

建立数据库并比对。批量分析时数据库应在批量根目录只建一次并复用。

```bash
mkdir -p "$WORK/03_diamond/db" "$WORK/03_diamond/raw"
for item in "arabidopsis:$AT_PEP" "rice:$RICE_PEP" "maize:$MAIZE_PEP"; do
  species=${item%%:*}
  fa=${item#*:}
  [ -s "$fa" ] || { echo "missing $species FASTA: $fa" >&2; continue; }
  diamond makedb --quiet --in "$fa" -d "$WORK/03_diamond/db/$species"
  diamond blastp --quiet --ultra-sensitive \
    -q "$WORK/02_query_pep/query.pep.fa" \
    -d "$WORK/03_diamond/db/$species" \
    -o "$WORK/03_diamond/raw/${species}.top50.tsv" \
    -f 6 qseqid sseqid pident length qlen slen evalue bitscore qcovhsp scovhsp \
    --max-target-seqs 50 --evalue 1e-5 --threads ${THREADS:-4}
done
```

从 raw top50 中提取每个物种 TOP10 unique gene：

```bash
python3 - <<'PY'
from pathlib import Path
import os, re
work=Path(os.environ['WORK'])
outdir=work/'04_hits'; outdir.mkdir(exist_ok=True)

def gene_id(species, sid):
    x=sid.split()[0]
    if species=='arabidopsis':
        m=re.search(r'(AT[1-5CM]G\d{5})', x, re.I)
        return m.group(1).upper() if m else x.split('.')[0]
    if species=='rice':
        m=re.search(r'(LOC_Os\d{2}g\d+)', x, re.I)
        return m.group(1) if m else x.split('.')[0]
    if species=='maize':
        m=re.search(r'(Zm\d{5}eb\d{6})', x)
        return m.group(1) if m else x.split('_')[0]
    return x.split('.')[0]

for species in ['arabidopsis','rice','maize']:
    inp=work/'03_diamond'/'raw'/f'{species}.top50.tsv'
    out=outdir/f'{species}.top10_unique_gene.tsv'
    with open(out,'w') as o:
        o.write('blast_rank\thit_gene_id\thit_protein_or_transcript_id\tqseqid\tpident\talign_length\tqlen\tslen\tevalue\tbitscore\tqcovhsp\tscovhsp\n')
        if not inp.exists(): continue
        seen=set(); rank=0
        for line in inp.open():
            if not line.strip(): continue
            qseqid,sseqid,pident,length,qlen,slen,evalue,bitscore,qcov,scov=line.rstrip('\n').split('\t')
            gid=gene_id(species,sseqid)
            if gid in seen: continue
            seen.add(gid); rank+=1
            o.write('\t'.join([str(rank),gid,sseqid,qseqid,pident,length,qlen,slen,evalue,bitscore,qcov,scov])+'\n')
            if rank>=10: break
PY
```

## 5. 查询其它物种 hit 的基因功能

### 5.1 拟南芥 hit

对每个 `AT...` gene ID 使用 `arabidopsis-gene-search`：

```bash
mkdir -p "$WORK/05_functions/arabidopsis"
while IFS=$'\t' read -r rank gid sid rest; do
  [ "$rank" = "blast_rank" ] && continue
  python3 "$AT_SKILL_DIR/scripts/query_arabidopsis_gene_search.py" full "$gid" \
    --max-entities 1 --max-edges 50 --snippets 3 --format json \
    > "$WORK/05_functions/arabidopsis/${gid}.json" || true
done < "$WORK/04_hits/arabidopsis.top10_unique_gene.tsv"
```

总结规则：

- `hit_reported_name`: TAIR gene symbol / other names / full name。
- `hit_function_description`: 根据 TAIR 描述和 PlantConnectome/PMID 证据写 1-2 句英文功能总结。
- `hit_reference_DOI`: 优先 DOI；如果只有 PMID 且无法解析 DOI，则 DOI 写 `NA`，PMID 可放入 `function_evidence_source`。
- 若没有具体功能，只得到泛注释，则 `hit_function_description=NA`。

### 5.2 水稻 hit

对每个水稻 TOP10 hit，直接使用 `rice-gene-search` 技能查询。该技能已负责根据水稻基因号或基因名查询 RiceData 基础信息，并结合 `literature-review` 检索功能文献。

执行规则：

1. 将 `hit_gene_id` 直接作为 `rice-gene-search` 的输入；通常为 `LOC_Os...`，也可为其它水稻 ID。
2. 记录该技能返回的 gene symbol / gene name / RAP 或 MSU ID。
3. 将文献证据总结为 1-2 句英文，写入 `hit_function_description`。
4. 将检索到的 DOI 写入 `hit_reference_DOI`；若只有 PMID 或没有 DOI，则 DOI 写 `NA`，PMID 或检索文件路径写入 `function_evidence_source`。
5. 如果 `rice-gene-search` 对 gene name 和 gene ID 均未检索到具体功能证据，则 `hit_function_description=NA`，`hit_reference_DOI=NA`。

原始结果保存到：

```text
$WORK/05_functions/rice/<hit_gene_id>.*
```

### 5.3 玉米 hit

使用 `maize-gene-search` 对 `Zm00001eb...` 查询基因名和本地功能注释，再用 `literature-review` 按 gene name + maize 检索文献。

```bash
mkdir -p "$WORK/05_functions/maize"
python3 "$LIT_SKILL_DIR/scripts/lit_search.py" search "<gene_name> maize" --limit 8 --source all \
  > "$WORK/05_functions/maize/<hit_gene_id>.literature.json"
python3 "$LIT_SKILL_DIR/scripts/lit_search.py" search "<hit_gene_id> maize" --limit 8 --source all \
  > "$WORK/05_functions/maize/<hit_gene_id>.id_literature.json"
```

## 6. 功能总结标准

每个功能描述必须满足：

- 1-2 句英文。
- 明确描述 biological process / molecular function / phenotype / pathway / stress response 等具体功能。
- 有 DOI 或 PMID/数据库证据支持；DOI 字段只写 DOI，PMID 放入 `function_evidence_source`。
- 如果证据只说明蛋白家族、结构域、GO 泛注释，不足以说明具体功能，则功能描述写 `NA`。
- 不编造 DOI；没有 DOI 写 `NA`。

优先级：

| 对象 | 查询优先级 |
|---|---|
| 马铃薯 | gene name > reported ID > DMv8 gene ID |
| 拟南芥 | AGI gene ID with `arabidopsis-gene-search` |
| 水稻 | gene name + rice > LOC_Os ID + rice |
| 玉米 | gene name + maize > Zm00001eb ID + maize |

## 7. 最终输出

每个马铃薯基因输出一个 CSV：

```text
$WORK/06_final/${GENE}_gene_function.csv
```

推荐同时输出 TSV 便于命令行检查：

```text
$WORK/06_final/${GENE}_gene_function.tsv
```

最终字段固定为：

```text
potato_gene_id
potato_reported_name
potato_reported_ids
potato_domain
potato_function_description
potato_reference_DOI
hit_species
hit_species_latin
blast_rank
hit_gene_id
hit_protein_or_transcript_id
hit_reported_name
hit_function_description
hit_reference_DOI
blast_identity_percent
blast_align_length
blast_query_coverage_percent
blast_subject_coverage_percent
blast_evalue
blast_bitscore
function_evidence_source
analysis_source_note
```

字段说明：

- `hit_species`: `arabidopsis` / `rice` / `maize`。
- `hit_species_latin`: `Arabidopsis thaliana` / `Oryza sativa` / `Zea mays`。
- `blast_rank`: 每个物种内部 TOP10 unique gene 排名。
- `function_evidence_source`: 记录证据来源，如 `potato_knowledge_search:<json_path>`、`arabidopsis_gene_search:<json_path>`、`literature_review:<json_path>`、PMID 或本地注释文件路径。
- `analysis_source_note`: 记录蛋白序列来源、DIAMOND 输出路径，以及 BLAST 是 newly_generated 还是 reused_existing。


## 8. 批量运行建议

多基因时：

1. 每个基因单独子目录。
2. DIAMOND 数据库在批量根目录只建一次。
3. 每个基因输出独立 CSV/TSV。
4. 最后合并为：

```text
all_genes_gene_function.csv
all_genes_gene_function.tsv
```

批量任务耗时较长时使用 Slurm。不要在用户只要求“写计划/讨论流程”时提交任务。

## 9. QC 检查

最终交付前必须检查：

```bash
python3 - <<'PY'
import csv, os, sys
from pathlib import Path
p=Path(os.environ['WORK'])/'06_final'/(os.environ['GENE']+'_gene_function.csv')
assert p.exists(), f'missing {p}'
rows=list(csv.DictReader(open(p, newline='')))
assert rows, 'empty final CSV'
required=['potato_gene_id','potato_function_description','hit_species','blast_rank','hit_gene_id','hit_function_description','hit_reference_DOI','blast_identity_percent']
missing=[c for c in required if c not in rows[0]]
assert not missing, 'missing columns: '+','.join(missing)
for bad in ['rbh_target','orthology_confidence']:
    assert bad not in rows[0], f'forbidden column present: {bad}'
for r in rows:
    assert r['potato_gene_id'], 'empty potato_gene_id'
    assert r['hit_species'] in ['arabidopsis','rice','maize'], 'unexpected species '+r['hit_species']
print('QC passed:', p, 'rows=', len(rows))
PY
```

内容 QC：

- 每个物种最多 10 个 unique gene hit。
- 功能描述没有证据时写 `NA`。
- DOI 不可编造；检索不到写 `NA`。
- 最终 CSV/TSV 尽量保持 ASCII；中文或原始片段保存在中间文件。
