---
name: genome-synteny
description: 使用 Snakemake 控制基因组共线性分析；当前实现基于 Python MCScan/jcvi 的基因/蛋白序列相似性流程，支持 macrosynteny 和指定基因区间 microsynteny，默认 --cscore=0.9。
version: 1.2.0
author: Potato Agent
license: MIT
metadata:
  hermes:
    tags: [mcscan, jcvi, snakemake, macrosynteny, synteny, protein, gff3, potato]
---

# Genome Synteny（MCScan/jcvi + Snakemake）

用于两个基因组之间基于基因/蛋白序列相似性和基因顺序的共线性分析。这个技能既可做 chromosome-level 的 **macrosynteny/karyotype** 图，也可复用同一套 MCScan 输出绘制指定基因区间的 **microsynteny** 图。

## 触发条件

当用户要求：

- 用 Python 版 MCScan / jcvi 做共线性；
- 输入为 GFF3 + 蛋白序列或 CDS 序列；
- 需要比较两个马铃薯或植物基因组的 macrosynteny；
- 需要对某个参考基因区间绘制 microsynteny；
- 需要可重复运行的流程、配置文件或 Snakemake 工作流。

## 核心说明

1. `jcvi` 的 Python MCScan 可以做**基于基因/蛋白序列相似性的共线性分析**：先用蛋白/CDS 相似性搜索得到同源基因对，再结合 BED 中的基因顺序聚类成 synteny blocks。
2. **macrosynteny** 使用 `.anchors.simple` 绘制染色体/大片段层面的 karyotype 图；**microsynteny** 使用 `jcvi.compara.synteny mcscan` 生成的 blocks 文件绘制指定基因区间的局部基因顺序图。
3. 这不是全基因组 assembly alignment；若研究问题是大片段结构变异，应考虑 SyRI/plotsr 等全基因组比对流程。
4. 为保证智能体重复性，优先生成并运行 Snakemake 流程，而不是临时拼接一串 shell 命令。


## 运行前软件环境检查

运行前先检查 `mcscan_jcvi` 环境或 `config.yaml` 中的 `env_prefix` 是否可用。至少确认：

```bash
ENV=/mnt/data/potato_agent/.hermes/home/.micromamba/envs/mcscan_jcvi
export PATH="$ENV/bin:$PATH"

python --version
jcvi --version
python - <<'PY'
import jcvi
from jcvi.compara import catalog, synteny
from jcvi.graphics import karyotype, synteny as graphics_synteny
print('jcvi imports OK')
PY

for x in snakemake diamond lastdb lastal blastp gffread seqkit bedtools samtools; do
  command -v "$x" || echo "MISSING: $x"
done
```

若使用蛋白模式且 `align_soft: diamond_blastp`，必须有 `diamond`。若用 LAST，则需要 `lastdb` 和 `lastal`。绘制 karyotype 时当前 `jcvi` 版本可能需要 `ete4`。

## jcvi / MCScan 安装建议

优先使用 `micromamba` / `mamba` / `conda` 建立独立环境，不要混入通用 RNA-seq 或系统 Python 环境。若本机已有 `/opt/micromamba/bin/micromamba`，优先使用它。

推荐安装命令：

```bash
/opt/micromamba/bin/micromamba create -y -n mcscan_jcvi \
  -c conda-forge -c bioconda \
  python=3.11 \
  jcvi \
  snakemake \
  diamond \
  last \
  blast \
  minimap2 \
  samtools \
  bedtools \
  seqkit \
  gffread \
  ete4
```

若环境已存在但缺少 Snakemake 或绘图依赖，可补装：

```bash
/opt/micromamba/bin/micromamba install -y -n mcscan_jcvi \
  -c conda-forge -c bioconda snakemake ete4 diamond last blast
```

安装后验证：

```bash
/opt/micromamba/bin/micromamba run -n mcscan_jcvi jcvi --version
/opt/micromamba/bin/micromamba run -n mcscan_jcvi snakemake --version
/opt/micromamba/bin/micromamba run -n mcscan_jcvi python -m jcvi.compara.catalog ortholog -h
/opt/micromamba/bin/micromamba run -n mcscan_jcvi python -m jcvi.graphics.karyotype -h
```

## 运行前必须确认 `--cscore`

运行前先告知用户计划使用的 `--cscore` 参数。

默认值：

```text
--cscore=0.9
```

需要向用户说明：

- `0.7` 更宽松，保留更多同源/旁系同源基因对，但近缘基因组中可能产生较多跨染色体噪音；
- `0.9` 是本技能默认值，较适合近缘马铃薯基因组，通常图更干净；
- `0.99` 更接近 RBH/1:1，可能丢失弱但真实的共线性信号。

若用户未指定，使用 `0.9`；若用户认为不合适，可调整 `config.yaml` 中的 `mcscan.cscore`。

## 项目结构

在工作目录中生成：

```text
config.yaml
Snakefile
logs/
results/
work/
```

推荐从模板复制：

```bash
SKILL_DIR=/mnt/data/potato_agent/.hermes/skills/potato-knowledge-bioinformatics/genome-synteny
WORK=/mnt/data/potato_agent/work/mcscan_<speciesA>_<speciesB>
mkdir -p "$WORK"
cp "$SKILL_DIR/templates/config.yaml" "$WORK/config.yaml"
cp "$SKILL_DIR/templates/Snakefile" "$WORK/Snakefile"
```

然后编辑 `config.yaml`。

## 配置文件要点

`config.yaml` 控制所有关键参数：

- `env_prefix`：`mcscan_jcvi` 环境路径；
- `species.a` / `species.b`：前缀、标签、GFF3、蛋白/CDS FASTA；
- `mcscan.dbtype`：`prot` 或 `nucl`；
- `mcscan.align_soft`：蛋白模式推荐 `diamond_blastp`；
- `mcscan.cscore`：默认 `0.9`；
- `mcscan.minspan`：默认 `30`；
- `plot.seqids_a` / `plot.seqids_b`：展示的染色体；
- `plot.formats`：输出图格式。

## 运行流程

本技能使用模板化 Snakemake 流程。运行前必须确认模板文件存在，并确保 `config.yaml` 中的 `env_prefix` 环境包含 `snakemake`。

```bash
test -s "$SKILL_DIR/templates/config.yaml"
test -s "$SKILL_DIR/templates/Snakefile"
```

若 `config.yaml` 中的 `env_prefix` 未设置，默认使用：

```bash
ENV=/mnt/data/potato_agent/.hermes/home/.micromamba/envs/mcscan_jcvi
```

检查 Snakemake；若环境中找不到则自动安装到该环境：

```bash
ENV="$(python3 - <<'PY'
import yaml
from pathlib import Path
cfg = Path('config.yaml')
if cfg.exists():
    data = yaml.safe_load(cfg.read_text()) or {}
    print(data.get('env_prefix') or '/mnt/data/potato_agent/.hermes/home/.micromamba/envs/mcscan_jcvi')
else:
    print('/mnt/data/potato_agent/.hermes/home/.micromamba/envs/mcscan_jcvi')
PY
)"
export PATH="$ENV/bin:$PATH"

if ! command -v snakemake >/dev/null 2>&1; then
  /opt/micromamba/bin/micromamba install -y -p "$ENV" \
    -c conda-forge -c bioconda snakemake
fi
snakemake --version
```

Snakemake 路径：

```bash
cd "$WORK"
snakemake -n --cores 1
snakemake --cores 16 --printshellcmds --rerun-incomplete
```

大基因组任务优先提交 Slurm，加载 `slurm-for-long-running-tasks`，用其 `scripts/submit-job.sh` 提交 Snakemake 运行脚本。

## Macrosynteny Snakemake 流程步骤

1. GFF3 转 BED。
2. FASTA 标准化或软链接为 `prefix.pep` / `prefix.cds`。
3. 检查 BED 第 4 列 ID 是否存在于 FASTA header。
4. 运行 `jcvi.compara.catalog ortholog`，使用 `config.yaml` 中的 `mcscan.cscore`。
5. 生成 `.anchors.simple`。
6. 写出 `seqids` 与 `layout`，绘制 PDF/PNG/line-style 图。

## 特定区间 microsynteny 绘图

当用户要求参照 MCScan Python 官方文档绘制某个基因区间的 microsynteny 时，优先复用已经完成的 pairwise MCScan 结果，尤其是 `*.lifted.anchors`、两个物种的 `.bed` 文件。若 pairwise MCScan 结果不存在，先按本技能的 macrosynteny/Snakemake 流程生成 `*.lifted.anchors`。

智能体执行时应收集/确认：

- 参考物种 prefix 与 BED，例如 `dmv82.bed`；
- 查询物种 prefix 与 BED，例如 `e463.bed`；
- pairwise lifted anchors，例如 `dmv82.e463.lifted.anchors`；
- 参考基因区间起止 ID，例如 `START_GENE` 到 `END_GENE`；
- `--iter`，默认 `1`。

官方流程要点：

1. 先基于参考 BED 和 lifted anchors 生成局部匹配 block 表：

```bash
python -m jcvi.compara.synteny mcscan ref.bed ref.query.lifted.anchors \
  --iter=1 \
  -o ref.query.i1.blocks
```

`--iter=1` 表示为每个参考区域选取一个最佳匹配区域；如果要展示多倍化或多个匹配区域，可提高 `--iter`。

2. 按用户给定的参考基因起止区间，从 `ref.query.i1.blocks` 中截取目标 rows。例如：

```python
from pathlib import Path
start = 'DM8.2_chr08G04370.1'
end = 'DM8.2_chr08G06000.1'
order = [line.rstrip('\n').split('\t')[3] for line in open('dmv82.bed') if line.strip()]
pos = {g: i for i, g in enumerate(order)}
lo, hi = sorted([pos[start], pos[end]])
allowed = set(order[lo:hi + 1])
selected = []
with open('dmv82.e463.i1.blocks') as f:
    for line in f:
        if line.strip() and line.rstrip('\n').split('\t')[0] in allowed:
            selected.append(line)
Path('blocks').write_text(''.join(selected))
```

3. 准备 `blocks.layout`，列顺序要和 `blocks` 文件列顺序一致：

```text
# x,   y, rotation,     ha,     va,   color, ratio, label
0.5, 0.6,        0, center,    top, #4C78A8,     1, Reference region
0.5, 0.4,        0, center, bottom, #F58518,     1, Matched region
# edges
e, 0, 1
```

4. 为 microsynteny 绘图创建带样品名前缀的 BED 副本，然后合并 BED 并绘图。只修改 BED 第 1 列染色体名，保持第 4 列基因 ID 不变，避免 anchors/blocks 中的基因 ID 失配：

```bash
python - <<'PY'
from pathlib import Path
pairs = [
    ('ref.bed', 'ref.plot.bed', 'REF_PREFIX'),
    ('query.bed', 'query.plot.bed', 'QUERY_PREFIX'),
]
for src, dst, prefix in pairs:
    out = []
    with open(src) as f:
        for line in f:
            if not line.strip() or line.startswith('#'):
                out.append(line)
                continue
            p = line.rstrip('\n').split('\t')
            p[0] = f'{prefix}__{p[0]}'
            out.append('\t'.join(p) + '\n')
    Path(dst).write_text(''.join(out))
PY

python -m jcvi.formats.bed merge ref.plot.bed query.plot.bed -o ref_query.plot.bed
python -m jcvi.graphics.synteny blocks ref_query.plot.bed blocks.layout \
  --notex \
  --glyphstyle=arrow \
  --glyphcolor=orthogroup \
  --outputprefix target_region.microsynteny
```

推荐同时输出 PNG：

```bash
python -m jcvi.graphics.synteny blocks ref_query.plot.bed blocks.layout \
  --notex \
  --glyphstyle=arrow \
  --glyphcolor=orthogroup \
  --format=png \
  --outputprefix target_region.microsynteny
```

若需要标记起止基因，可加：

```bash
--genelabelsize=5 --genelabels=START_GENE,END_GENE
```

5. 验证输出：

```bash
file target_region.microsynteny.pdf target_region.microsynteny.png
wc -l blocks
```

并报告：

- 目标区间参考基因数，即 `blocks` 行数；
- 匹配 rows 数与未匹配 rows 数，`blocks` 第二列为 `.` 的行视为未匹配；
- 匹配物种的首尾基因；
- 图中是否有两条基因轨道和同源连接线。

注意：`jcvi.formats.bed merge` 会给合并 BED 的 seqid 加上前缀；这是正常现象，`jcvi.graphics.synteny` 会据此显示轨道范围。

## Macrosynteny 输出验证

完成 macrosynteny 后检查：

```text
results/<pair>.last.filtered
results/<pair>.anchors
results/<pair>.lifted.anchors
results/<pair>.anchors.simple
results/<pair>.macrosynteny.pdf
results/<pair>.macrosynteny.png
```

必须用 `file` 或等价方式确认 PNG/PDF 不是空文件，并建议用视觉检查确认图中有两条染色体轨道、染色体编号和共线性连线。

同时统计并报告：

- BED 基因数与蛋白/CDS FASTA 条数；
- `last.filtered` 同源基因对数量；
- `.anchors` 与 `.lifted.anchors` 行数；
- `.anchors.simple` block 数；
- 同编号/跨编号染色体 block 是否符合预期；
- 若生成了 `seqpairs` 过滤版，同时报告过滤前后 block 数。

## 近缘马铃薯基因组的解释注意事项

若 `--cscore` 太低，蛋白模式可能保留旁系同源/重复基因家族信号，导致 macrosynteny 图中出现很多小的跨染色体连接。不要直接解释为大规模染色体重排。

建议处理顺序：

1. 先确认 `--cscore` 是否为默认 `0.9`；
2. 必要时提高到 `0.99`；
3. 或提高 `mcscan.minspan`；
4. 若染色体编号已知一一对应，可用 `mcscan.seqpairs` 只展示期望染色体对；
5. 真正研究结构变异时另用全基因组比对流程。
