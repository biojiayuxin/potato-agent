---
name: domain-annotation-query
description: 查询 Domain annotation 页面的已有蛋白结构域、InterPro/GO 和转录因子注释，支持五个马铃薯基因组的精确或批量 ID 查询、结构域组合筛选及表格导出。通过当前部署的公开 API 访问，无需登录；不用于基因结构预测或重新注释。
version: 1.1.1
author: Potato Agent
license: MIT
metadata:
  hermes:
    tags: [potato, genome, interproscan, protein-domain, transcription-factor, annotation, API]
    related_skills: [potato-gene-search, potato-pan-genome-query, search-gene-function, go-enrichment]
prerequisites:
  commands: [python3]
---

# Domain Annotation Query

使用当前部署的公开只读 API 查询已有的蛋白结构域注释和 TF 鉴定。
页面路径为 `/functional-annotation`，API 路径为 `/api/genome-annotations`，均相对于当前站点。
脚本只依赖 Python 标准库。不要抓取网页、直接打开服务器 SQLite、读取用户工作目录或重新运行注释。

页面的 “Ask Potato Agent” 提供简短示例，不附带当前筛选、选择或站点地址。
运行脚本前，按文末“部署地址”配置当前站点；上下文明确提供站点地址时，
用 `--base-url` 指定，确保查询和导出来自同一部署。

## 何时使用

- 查询一个或多个基因、转录本或全局蛋白的结构域、InterPro/GO 关联和 TF 证据。
- 筛选具有某些结构域或 TF 家族的记录，比较五个基因组中的家族计数。
- 导出基因汇总、转录本汇总、全部结构域明细、TF 判定或 TF 证据表。

基因结构预测、输入新 FASTA、运行 InterProScan、重新鉴定 TF、提取序列和表达分析不属于本技能。

## 数据解读规则

1. 先运行 `metadata` 确认数据版本和合法 assembly ID。保留 API 返回的 `datasetVersion`，回答中注明材料和版本；不要把 HTTP 错误或缺库理解为没有匹配结果。
2. ID 必须精确匹配并保留点号和异构体后缀。基因、转录本和全局 `UP` 蛋白 ID 的对应关系来自发布映射，不要通过截断 `.1` 等后缀推断基因。不能把局部 subset protein ID 当作全局蛋白 ID。
3. 同时要求结构域 A+B 或 TF 家族+结构域时，必须由同一个转录本对应的蛋白满足；不能拼接不同异构体的证据。`--domain-match all` 是默认组合规则，`any` 表示任一结构域满足。
4. 区分有效蛋白有命中（`hit`）、有效蛋白在四个应用中无命中（`no_match`）、无 CDS 且未进入蛋白注释（`no_cds`）。无命中不能描述为没有生物学功能。
5. TF 是“基于 PlantTFDB 公开规则的本地鉴定”，不是 PlantTFDB 官方数据库的直接注释。A/B/C 代表规则覆盖方式，不能称为实验验证等级或概率。默认保留 Grade-C Homeobox 候选；如果用户排除候选，明确写出筛选条件。
6. 11 个缺少必要自建 HMM 的官方家族属于“本方法不可判定”，不能写成这些基因组没有该家族。未被本地规则选中的基因也不能称为确定的非 TF。
7. 保留原始基因 TF 判定和异构体冲突。家族计数可以共享存在冲突的基因，逐家族相加不能代替去重后的 TF 总数。
8. 结构域坐标是蛋白氨基酸的 1-based 闭区间；不同数据库的原始 score 不能直接横向排序。保留重叠命中。
9. 查询分页的 `returned` 和 `items` 长度只表示当前页，全部数量读取 `total`。依据 `hasMore` 和 `offset` 继续查询；需要大量结果时用导出，不要把几千行放入对话。
10. 检查 `idReport.unmatchedIds`、`ambiguousIds`、`filteredIds`；分别报告找不到、跨材料歧义和被筛选排除的输入，不能默默丢弃。

当前数据库接口为 schema 3（`metadata.schemaVersion`）。结构域详情、导出和整套下载
均已移除 `pathways`，不再查询或补齐该字段；实际数据发布版本读取 `datasetVersion`。

## 命令

Hermes 将 `${HERMES_SKILL_DIR}` 展开为本技能目录。

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_domain_annotations.py" metadata
python3 "${HERMES_SKILL_DIR}/scripts/query_domain_annotations.py" --help
```

当前五个规范 assembly ID：`monoploid/DMv8.2`、`monoploid/E4-63`、`monoploid/A6-26`、
`phased_tetraploid/Des`、`phased_tetraploid/C88`。默认查询 DMv8.2；指定多个材料时重复
`--assembly`，查询全部材料时使用 `--all-assemblies`。名称以后续 `metadata` 返回值为准。

精确 ID 查询、基因详情和转录本详情：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_domain_annotations.py" query DM8.2_chr01G00010
python3 "${HERMES_SKILL_DIR}/scripts/query_domain_annotations.py" gene DM8.2_chr01G00010 \
  --assembly monoploid/DMv8.2
python3 "${HERMES_SKILL_DIR}/scripts/query_domain_annotations.py" transcript '<exact-transcript-id>' \
  --assembly monoploid/DMv8.2
```

批量 ID 从本地 UTF-8 TXT 读取，允许换行、空格、逗号或分号分隔，最多 5,000 个去重后的精确 ID：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_domain_annotations.py" query \
  --ids-file ./genes.txt --all-assemblies --limit 50 --offset 0
```

结构域和 TF 筛选：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_domain_annotations.py" query \
  --all-assemblies --signature PF03106 --database Pfam --view transcripts
python3 "${HERMES_SKILL_DIR}/scripts/query_domain_annotations.py" query \
  --tf-family WRKY --tf-status selected --tf-grade A --tf-grade B
python3 "${HERMES_SKILL_DIR}/scripts/query_domain_annotations.py" query \
  --annotation-status no_cds --limit 10
python3 "${HERMES_SKILL_DIR}/scripts/query_domain_annotations.py" families
```

其他筛选包括 `--text`、`--interpro`、`--go`、`--domain-match all|any`、
`--tf-status all|selected|not_selected|ambiguous|unassessable`、
`--annotation-status all|hit|no_match|no_cds` 和 `--conflict all|presence|family|any`。
结构域、InterPro、GO、TF 家族和等级参数可重复。
`--tf-grade` 接受当前 API 的 `A`、`B`、`C`、`U`；`U` 表示规则覆盖不可判定，
该等级查询无记录不能解释为对应家族不存在，应结合 `families` 的覆盖信息。
使用 `--database` 指定应用时，结构域、InterPro 和 GO 筛选证据必须来自这些应用。

## 完整导出

`export` 导出全部匹配结果，不受查询的 `limit` 或 `offset` 限制。默认导出基因汇总；
通过重复 `--table` 选择 `genes`、`transcripts`、`domains`、`tf_decisions`、`tf_evidence`。
ZIP 同时包含 `metadata.json`，记录版本、查询条件和统计；批量查询附 ID 匹配报告。
`domains` 包含匹配转录本的全部原始命中，并标记哪些命中满足结构域筛选。

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_domain_annotations.py" export \
  --all-assemblies --tf-family WRKY --tf-status selected \
  --version '<datasetVersion-from-query>' \
  --table genes --table transcripts --table domains --table tf_decisions --table tf_evidence \
  --format tsv --output ./wrky_annotations.zip
```

用当前工作目录中的实际路径替换 `--output`。文件必须以 `.zip` 结尾，已有文件不会被覆盖。
脚本流式下载并校验 ZIP 元数据，失败时删除临时下载文件。成功后返回绝对路径、大小、SHA-256 和
导出元数据；必须把该绝对路径交给用户。API 只接收筛选和选择信息，不接收本地输出路径。

从网页或先前查询保存的**查询对象**重现导出：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_domain_annotations.py" query --query-json ./query.json
python3 "${HERMES_SKILL_DIR}/scripts/query_domain_annotations.py" export \
  --query-json ./query.json --version '<datasetVersion-from-query>' --output ./annotations.zip
```

`query.json` 是 API 的 `query` 对象，不是整份脚本响应。命令行显式筛选覆盖对应字段。
如果省略 `--version`，优先使用查询对象中的 `datasetVersion`，否则先读取当前元数据，再请求该版本的导出。
为保证与先前查询一致，始终优先传入查询响应的版本。版本切换返回 HTTP 409 时，重新查询并确认新结果。
确认使用新版数据后，显式 `--version` 会同时更新导出请求和查询对象中的版本，避免旧 JSON 版本冲突。

只导出勾选记录时加 `--selected-json ./selection.json`，文件内容示例：

```json
[{"assemblyId":"monoploid/DMv8.2","geneId":"DM8.2_chr01G00010"}]
```

转录本视图使用 `transcriptId` 代替 `geneId`，并保持与原查询的 `view` 一致。选择的记录仍受查询条件约束。

运行 `downloads` 可查看当前发布文件清单、大小及 SHA-256。结构域文件为 `*.domains.tsv.gz`，
已移除 Pathways；只使用 API 返回的下载地址，不拼接旧版 `*.interproscan.tsv.gz` 文件地址。

## API 与错误

| 命令 | 接口 |
|---|---|
| `metadata` | `GET /metadata` |
| `query` | `POST /query` |
| `gene` / `transcript` | `GET /genes/{id}?assembly=…` / `GET /transcripts/{id}?assembly=…` |
| `families` | `GET /tf-families` |
| `export` | `POST /export` |
| `downloads` | `GET /downloads` |

网页已移除 TF families 和 Downloads & Methods 独立标签页，但 `/tf-families`、`/downloads`
及 API 的 TF 判定、证据导出仍有效。脚本的 JSON 导出使用 `/export`；`/export-download` 是网页表单适配器。

普通查询输出 `{"api_url":"实际 URL","data":{…API 响应…}}`；导出输出路径和元数据。
HTTP 400/422 表示参数不正确，404 表示指定记录不存在，409 表示版本不一致，503 表示数据暂不可用。
遇到错误报告真实状态；不要猜测注释，也不要回退读取未经发布的本地表格。

## 部署地址

脚本将 `/api/genome-annotations` 拼接到当前部署的站点地址，不内置域名或服务器 IP。
Python HTTP 客户端没有浏览器的当前站点上下文，地址按以下优先级读取：

1. 命令行 `--base-url`（显式提供目标部署地址）。
2. `POTATO_DOMAIN_ANNOTATIONS_BASE_URL`；兼容旧变量 `POTATO_GENOME_ANNOTATIONS_BASE_URL`。
3. 部署环境变量 `INTERFACE_PUBLIC_BASE_URL`。
4. 技能根目录的 `api-base-url.txt`，只写实际站点根地址；文件位置相对于脚本解析，不依赖工作目录。

支持站点根地址、`/functional-annotation` 页面地址或 `/api/genome-annotations` API 根地址。
部署配置文件不提交到代码仓库。迁移部署时由目标环境提供地址；未配置时脚本报错，
不要猜测地址或使用开发服务器地址回退。不需要 API key 或 Potato Agent 登录凭证。
