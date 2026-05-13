---
name: ortholog-finder
description: 基于 genome-synteny 前置 MCScan/jcvi 比对步骤寻找两个基因组间直系同源候选基因；复用 GFF3 转 BED、FASTA 链接、ID 检查与 catalog ortholog，并将 .last.filtered 按最佳匹配过滤为 .one_to_one.best.tsv。
version: 0.1.0
author: Potato Agent
license: MIT
metadata:
  hermes:
    tags: [ortholog, jcvi, mcscan, diamond, synteny, protein, gff3, potato]
---

# Ortholog Finder（复用 genome-synteny 前置比对）

## 触发条件

当用户要求：

- 在两个基因组之间寻找共线性基因；
- 在两个马铃薯或植物基因组之间寻找直系同源/同源候选基因对；
- 已有 GFF3 + 蛋白 FASTA 或 CDS FASTA，需要产出可复用的一对一直系同源候选基因表；
- 需要 MCScan/jcvi 的前置序列相似性过滤结果，并进一步去除一对多和多对一冲突；

## 核心原则

1. 本技能复用 `genome-synteny` 的前置步骤：
   - GFF3 转 BED；
   - FASTA 标准化链接为 `<prefix>.pep` 或 `<prefix>.cds`；
   - 检查 BED 第 4 列基因 ID 是否存在于 FASTA header；
   - 运行 `python -m jcvi.compara.catalog ortholog` 生成 `results/<speciesA>.<speciesB>.last.filtered`；
   - 将 `.last.filtered` 按最佳匹配优先级过滤为最终结果 `results/<speciesA>.<speciesB>.last.filtered.one_to_one.best.tsv`。
2. 默认不继续做 `screen --simple`、`.anchors.simple` 和绘图。若用户明确说“用 ortholog-finder 这个技能做就行”或目标只是得到共线性/直系同源候选基因表，不要自行扩展到 `genome-synteny` 的 macrosynteny/karyotype 绘图流程；只产出 `last`、`last.filtered`、`one_to_one.best.tsv`、ID 检查和摘要日志。
3. 一对一最佳过滤采用贪心策略：先按 `bitscore` 高、`identity` 高、`alignment_length` 长、`evalue` 小、`mismatches + gaps` 少、原始行号靠前排序；从高到低保留基因对，若 query 或 subject 已经在已保留结果中出现，则剔除该冲突行。

## 推荐项目结构

```text
config.yaml
Snakefile
logs/
results/
work/
```

从模板初始化：

```bash
SKILL_DIR=/mnt/data/potato_agent/.hermes/skills/potato-knowledge-bioinformatics/ortholog-finder
WORK=/mnt/data/potato_agent/work/ortholog_<speciesA>_<speciesB>
mkdir -p "$WORK"
cp "$SKILL_DIR/templates/config.yaml" "$WORK/config.yaml"
cp "$SKILL_DIR/templates/Snakefile" "$WORK/Snakefile"
```

然后编辑 `config.yaml` 中两个物种的 `prefix`、`gff3`、`fasta`、`gff_type`、`gff_key`、`mcscan.dbtype` 和 `mcscan.cscore`。

## 参数建议

- `mcscan.dbtype: prot`：默认推荐，输入蛋白 FASTA，使用 `diamond_blastp`；
- `mcscan.dbtype: nucl`：输入 CDS FASTA，可按需要调整比对软件；
- `mcscan.cscore: 0.9`：近缘马铃薯基因组默认值；
- `mcscan.cscore: 0.99`：更接近严格一对一，但可能丢掉弱同源；
- `mcscan.cpus`：按机器资源设置。

执行前应告知用户计划使用的 `--cscore`。

## 运行

本技能使用模板化 Snakemake 流程。运行前先确认模板文件存在，并确保 `config.yaml` 中的 `env_prefix` 环境包含 `jcvi`；`snakemake` 使用系统环境中已安装的命令，蛋白模式下还需要 `diamond`。

```bash
test -s "$SKILL_DIR/templates/config.yaml"
test -s "$SKILL_DIR/templates/Snakefile"
```

若当前技能目录缺少 `templates/Snakefile`（某些本地安装只带 `templates/config.yaml`），不要盲目复制不存在的模板；应在项目目录生成一个自包含 Snakefile，明确实现 GFF3→BED、FASTA 链接、ID 检查、`jcvi.compara.catalog ortholog`、`.last.filtered` 解析，以及一对一最佳匹配过滤后再运行。

若 `config.yaml` 中的 `env_prefix` 未设置，默认使用：

```bash
ENV=/mnt/data/potato_agent/.hermes/home/.micromamba/envs/mcscan_jcvi
```

检查运行环境；Snakemake 使用系统命令，若缺少 diamond 则优先补装到该环境：

```bash
ENV="$(python3 - <<'PY'
from pathlib import Path
import yaml
fallback = '/mnt/data/potato_agent/.hermes/home/.micromamba/envs/mcscan_jcvi'
cfg = Path('config.yaml')
if cfg.exists():
    data = yaml.safe_load(cfg.read_text()) or {}
    print(data.get('env_prefix') or fallback)
else:
    print(fallback)
PY
)"
SNAKEMAKE_BIN="$(command -v snakemake)"
export PATH="$ENV/bin:$PATH"

if ! command -v diamond >/dev/null 2>&1; then
  /opt/micromamba/bin/micromamba install -y -p "$ENV" \
    -c conda-forge -c bioconda diamond
fi

python -m jcvi.compara.catalog ortholog -h >/dev/null
"$SNAKEMAKE_BIN" --version
```

Snakemake 路径：

```bash
cd "$WORK"
"$SNAKEMAKE_BIN" -n --cores 1
"$SNAKEMAKE_BIN" --cores 16 --printshellcmds --rerun-incomplete
```

长任务优先结合 `slurm-for-long-running-tasks` 提交 Slurm 后台运行。

## 输出

主要输出：

```text
results/<speciesA>.<speciesB>.last.filtered.one_to_one.best.tsv
results/id_check.tsv
```

### 单基因 ID 映射查询

当用户要求“从已有 ortholog 结果目录查询某个基因在另一个版本/物种中的对应基因号”时，应优先查询 `results/<speciesA>.<speciesB>.last.filtered.one_to_one.best.tsv`，而不是仅做 ID 字符串风格转换。常用步骤：

1. 在指定 `results/` 目录列出实际结果文件，确认是否存在 `.one_to_one.best.tsv`、`.last.filtered` 和 `.last`。
2. 对用户输入的基因 ID 同时尝试基因级和转录本级匹配，例如 `DM8C03G24900` 与 `DM8C03G24900.1`。
3. 先查 `.one_to_one.best.tsv`；若无命中，再查 `.last.filtered` / `.last` 作为候选证据，并明确说明不是最终一对一结果。
4. 报告时去掉转录本后缀得到基因号：如 `DM8C03G24900.1 -> DM8.2_chr03G22620.1` 对应基因号 `DM8.2_chr03G22620`。
5. 若发现直接 ID 风格转换结果（如 `DM8C03G24900 -> DM8.2_chr03G24900`）与 ortholog 表不一致，以用户指定的 ortholog 结果为准，并说明该直接转换 ID 可能对应另一个源基因。

辅助输出：

```text
results/<speciesA>.<speciesB>.last
results/<speciesA>.<speciesB>.last.filtered
logs/mcscan_ortholog.log
logs/one_to_one_best.log
```

`.one_to_one.best.tsv` 是最终结果，每个 query 和每个 subject 在该文件中最多出现一次。

## 结果验证

完成后至少检查：

```bash
wc -l results/<speciesA>.<speciesB>.last.filtered results/<speciesA>.<speciesB>.last.filtered.one_to_one.best.tsv
head -n 5 results/id_check.tsv
cat logs/one_to_one_best.log
```

确认最终结果一对一唯一性：

```bash
python - <<'PY'
from collections import Counter
path = 'results/<speciesA>.<speciesB>.last.filtered.one_to_one.best.tsv'
qs, ss = [], []
with open(path) as f:
    for line in f:
        if line.strip():
            a = line.rstrip('\n').split('\t')
            qs.append(a[0]); ss.append(a[1])
print('pairs', len(qs))
print('duplicated_query_ids', sum(v > 1 for v in Counter(qs).values()))
print('duplicated_subject_ids', sum(v > 1 for v in Counter(ss).values()))
PY
```

报告给用户：

- `id_check.tsv` 中两个物种 BED/FASTA ID 是否完全匹配；
- `.last.filtered` 原始候选行数；
- `.one_to_one.best.tsv` 最终保留行数与删除的冲突行数；
- 使用的 `dbtype`、`align_soft`、`cscore`、线程数；
- 若结果为空或极少，优先检查 ID 是否一致、FASTA 类型是否与 `dbtype` 匹配、`cscore` 是否过严。
