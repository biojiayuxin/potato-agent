---
name: genome-compare-dotplot
description: 用于两套基因组的比较并绘制点阵图；如果已知染色体编号并绘制同线性图，可以使用 `syri-plotsr-workflow` 技能。
version: 1.0.0
author: Potato Agent
license: MIT
metadata:
  hermes:
    tags: [genome-comparison, dotplot, mummer4, nucmer, mummerplot, snakemake, comparative-genomics]
---

# Genome Compare Dotplot

基于 **MUMmer4 `nucmer` + `mummerplot`** 的全基因组比较与点阵图绘制，由 Snakemake 控制。如需已知染色体编号的同线性图 / 结构变异分析，使用 `syri-plotsr-workflow` 技能。

## 适用场景

- 两套基因组 FASTA 的全基因组比较，不要求已知染色体对应关系。
- 需要 MUMmer dot plot，不做 SyRI 结构变异分类。
- 适用于 scaffold/contig 级别的粗糙组装。

## 依赖要求

- `nucmer` / `show-coords` / `mummerplot`（来自 MUMmer4）
- `gnuplot`（mummerplot 的绘图后端，需单独安装）
- `python3`（Snakemake 运行）
- `snakemake`

本服务器上 `syri_env` 环境已包含以上所有工具，可直接 `micromamba activate syri_env` 使用。若新建环境，注意 MUMmer4 包未必自动带 gnuplot，需手动安装。

## 模板文件

```text
templates/config.yaml
templates/Snakefile
templates/fasta_to_order.py    ← 独立脚本，可单独调用
```

复制模板到工作目录：

```bash
SKILL_DIR=/mnt/data/potato_agent/.hermes/skills/potato-knowledge-bioinformatics/genome-compare-dotplot
WORK=/mnt/data/potato_agent/Work/genome_compare_dotplot_<ref>_vs_<qry>
mkdir -p "$WORK/config" "$WORK/scripts"
cp "$SKILL_DIR/templates/config.yaml"    "$WORK/config/config.yaml"
cp "$SKILL_DIR/templates/Snakefile"      "$WORK/Snakefile"
cp "$SKILL_DIR/templates/fasta_to_order.py" "$WORK/scripts/fasta_to_order.py"
```

然后编辑 `$WORK/config/config.yaml` 修改输入路径和参数即可。

### fasta_to_order.py（独立工具）

`scripts/fasta_to_order.py` 从 FASTA 生成 MUMmer 所需的 `-R/-Q` order 文件（`seq_id<TAB>length<TAB>+`），可脱离 Snakemake 单独使用：

```bash
# 单文件
python3 fasta_to_order.py genome.fa genome.order.tsv

# 双文件（一次运行同时生成 ref 和 qry 的 order）
python3 fasta_to_order.py ref.fa qry.fa ref.order.tsv qry.order.tsv
```

输出写入 stderr（序列数/总 bp），order 文件写入目标路径。

## config.yaml 关键参数

```yaml
project:
  name: "ref_vs_qry"

inputs:
  ref: "/path/to/reference.fa"  # X-axis
  qry: "/path/to/query.fa"      # Y-axis

resources:
  threads: 20

nucmer:
  match_mode: "--maxmatch"
  minmatch: 100
  mincluster: 500
  maxgap: 1000
  breaklen: 500

mummerplot:
  terminal: "png"
  size: "large"
  color: true
  title: "ref vs qry MUMmer4 dot plot"
```

## 参数说明

- `nucmer.match_mode`
  - `--maxmatch`：默认；保留所有 maximal exact matches，适合观察重复、多拷贝和异位同源信号。
  - `--mum`：图更干净，但会丢失重复区信号。
  - `--mumreference`：只使用 reference 中唯一匹配。
- `nucmer.minmatch` (`-l`)：最小 exact match 长度。大基因组建议从 `100` 开始。
- `nucmer.mincluster` (`-c`)：最小 cluster 长度。默认模板使用 `500` 减少碎片信号。
- `nucmer.maxgap` (`-g`)：cluster 内相邻 match 的最大间距。默认 `1000`。
- `nucmer.breaklen` (`-b`)：延伸遇到低分区域时的容忍长度。默认 `500`。
- `mummerplot.color: true`：使用 identity 颜色梯度；设为 `false` 时按方向着色。

## 运行

先 dry-run：

```bash
cd "$WORK"
snakemake -n all --cores 1 --printshellcmds
```

确认无误后运行：

```bash
snakemake all --cores 20 --printshellcmds --rerun-incomplete
```

大基因组建议用 Slurm 提交 Snakemake 命令；提交前先加载 `slurm-for-long-running-tasks` 技能。

## 输出

```text
results/orders/<project>.ref.order.tsv
results/orders/<project>.qry.order.tsv
results/nucmer/<project>.delta
results/qc/<project>.coords.tsv
results/plots/<project>.dotplot.png
results/plots/<project>.dotplot.gp
logs/
```

## 注意事项

- 默认不使用 `delta-filter`。
- 默认不使用 `mummerplot --filter` 或 `--layout`。
- `-R/-Q` order 文件由 Snakefile 从 FASTA 自动生成，保留所有 scaffold/contig 和原始顺序。
- 若图太密，可提高 `minmatch` 或 `mincluster`；不要擅自过滤输入 contig，除非用户明确要求。
