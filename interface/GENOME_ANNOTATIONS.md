# Domain annotation

Genomes 菜单中的 **Domain annotation** 页面位于 `/functional-annotation`，公开提供五个
马铃薯基因组的结构域注释、TF 家族信息及结果导出。浏览器与 Hermes 智能体共用
`/api/genome-annotations` 只读 API，无需登录。API 根据已发布数据库提供完整材料目录，
支持 154 个基因组的全量库；页面独立限定为 DMv8.2、E4-63、A6-26、Des、C88，
保持原顺序、显示名称及默认选择。页面全选、URL 恢复和导出也只使用这五个材料，
空选择不会发送代表全库的空 assembly 列表。不提供序列 FASTA 导出或在线重新注释。

页面提供基因组选择、All genes / Transcription factors 切换、ID/关键词查询和批量 ID。
选择 Transcription factors 会立即按当前基因组和搜索条件查询（API `tfStatus: selected`），
切换后从第一页开始并清空旧选择；重置恢复 DMv8.2 的全部基因。每页显示 20 条记录。
筛选随页面 URL、翻页和导出保留；Export all results 导出全部匹配记录，
Export selected 导出跨页选中的记录。页面导出选项为基因、转录本和结构域表。
TF families 和 Downloads & Methods 独立标签页已移除，相关 API 仍供智能体和脚本使用。
旧标签页链接回到搜索；除 `tfStatus: selected` 外，旧链接中的高级筛选参数不再应用。
结果和展开详情只显示 TF 家族名称，不显示判定、等级、证据表或注释状态。
点击结果中的基因号后，全部转录本作为同级信息块直接展开，自动加载各自的结构域示意图、
蛋白信息和注释表，无需再点击转录本号。各转录本独立加载，失败时可单独重试。
数据库、API 明细、TSV/CSV 导出及整套注释下载均不再包含 Pathways。
结构域图使用即时悬浮提示，支持键盘聚焦、Escape 关闭；详情不再显示坐标说明段落。
以下高级筛选、TF 判定及证据字段属于 API/智能体接口与原始数据格式，网页不再提供相应控件。

## 数据与解释

五基因组基线输入是 `interproscan-5_genomes-260919`；全量输入是 `interproscan-260916`，
两者均包含 `tf_extraction_planttfdb_rules` 结果。下表是五基因组基线的统计。
使用 InterProScan 5.74-105.0 的 CDD、PANTHER、Pfam、SMART 四个应用；只公布所运行应用的结果，
不能把“无命中”理解为所有数据库均无注释。

| 材料 | 规范 assembly ID | 基因 | 转录本 | TF 基因 |
|---|---|---:|---:|---:|
| DMv8.2 | `monoploid/DMv8.2` | 38,176 | 61,208 | 1,782 |
| E4-63 | `monoploid/E4-63` | 40,172 | 40,172 | 1,807 |
| A6-26 | `monoploid/A6-26` | 44,859 | 45,869 | 1,732 |
| Des | `phased_tetraploid/Des` | 203,828 | 309,453 | 7,199 |
| C88 | `phased_tetraploid/C88` | 150,853 | 217,651 | 7,177 |
| 合计 | | **477,888** | **674,353** | **19,697** |

其中 638,694 个转录本进入有效蛋白注释，35,659 个转录本无 CDS。TF 数量包含 Grade-C
Homeobox 候选，具体统计及当前版本以 `/metadata` 为准。

- 原始 assembly、gene、transcript 和 global unique protein ID 通过发布映射关联。
  蛋白按全局 `UP` ID 保存一份，不能把 subset protein ID 当作全局 ID，也不能删掉 `.1`
  等转录本后缀来推断基因。异常转录本的基因归属来自经校验的 transcript metadata。
- 注释状态分别是 `hit`（有效蛋白有命中）、`no_match`（有效蛋白在四个应用无命中）、
  `no_cds`（无 CDS，未进入蛋白注释）、`extraction_error`（重复 GFF 转录本 ID、
  空蛋白或缺失蛋白）。详情的 `extractionStatus` 保留原始提取状态，`reason` 保留原因。
  `tfStatus: unassessable` 包括全部非 valid 记录；提取异常不属于 no_match 或确定的非 TF。
- 结构域 A+B 或 TF 家族+结构域的组合，必须在同一个转录本对应的蛋白上满足。
  API 筛选不重写原始基因 TF 判定和冲突，网页展开列出全部异构体。
- TF 是**基于 PlantTFDB 公开规则的本地鉴定**，不是 PlantTFDB 官方直接注释。
  A/B/C 表示规则覆盖方式，不是实验验证等级。缺少必要自建 HMM 的 11 个官方家族
  在 API 中提供规则覆盖信息，不能解释为零个或家族缺失。未被规则选中不等于确定的非 TF。
- 家族间可共享有冲突的基因，家族计数之和不等于去重 TF 总数。所有重叠原始命中均保留；
  坐标为蛋白氨基酸 1-based 闭区间，score 保留原值，不在不同数据库之间直接比较。

全量源最终验收报告记录 154 个材料（63 monoploid、76 phased diploid、15 phased tetraploid）、
9,524,058 个可归属基因、11,623,230 个转录本、5,160,125 个唯一蛋白及 16,228,693 条蛋白级命中。
其中 10,968,097 个转录本有有效蛋白；655,133 个异常记录分为 148,649 个 no_cds、
505,708 个 duplicate_gff_transcript_id、772 个 empty_protein、4 个 missing_protein。
TF 包括 424,617 个基因、529,008 个转录本、220,188 个唯一蛋白。GFF feature 出现次数与
唯一转录本数量是不同口径，不能将重复 feature 展开成新的转录本。35,757,299 条转录本级命中
通过映射查询和导出，不在数据库中重复保存。

全量库采用最新源结果。与旧五基因组库相比，63 个唯一蛋白的结构域记录存在已核实的来源差异：
53 个涉及 PANTHER、10 个涉及 Pfam，唯一蛋白层为 64 条新增、64 条移除，影响 88 个基因、
106 个转录本，其中包括 `UP002386586` 新增的 PANTHER 命中。原五材料的基因和转录本归属、
TF 判定、家族等级与 TF 证据保持一致。最终验收与精确来源差异保存在
`/srv/genome_annotations/deployment-backups/local-154-20261009T103529Z/release-audit.tar.gz`。

## 离线构建与发布

构建器 `python -m interface.build_genome_annotations_db` 读取冻结 TSV/GZIP、release SHA-256
清单及五基因组 source lock（全量来源使用原生发布清单），验证引用、统计、TF closure、SQLite 完整性和全文索引后，原子替换指定
构建输出。源数据只读，输出必须位于源目录之外。在线服务用只读 SQLite 连接打开已发布版本。

数据库 schema 3 删除了 `pathway_sets` 表与 `matches.pathway_set_id` 列，构建器读取
冻结的 InterProScan 输入时忽略其最后一列通路文本，其他命中与重复记录逐条保留。
API 详情及结构域导出不再返回 `pathways`；整套注释下载改为 `*.domains.tsv.gz`，
从原始表中移除 `pathways` 列并重新计算公开文件的 SHA-256，其他列保持一致。
该历史 schema 3 发布使用 `datasetVersion`：`interproscan-5genomes-20260919-tf20260920-no-pathways`。
当前构建器生成 schema 4，扩展提取状态，读取器同时兼容 schema 3/4，便于分阶段发布与回滚。
每次重新发布使用新 `datasetVersion`；固定旧版本的请求在切库后返回 409。

所有表、JSON 和下载列使用与精简后的五基因组库一致的字段清单。原始 source lock、完整 QC 报告、
运行路径、日志、FASTA 和分片副本仅用于必要的验证或不读取，不作为数据库内容。
Pathways 在解析命中时被丢弃，包括超过 CSV 默认字段大小的通路文本。
全量 TF 以最终 `qc/final_validation.json` 的 PASS 放行，`run_summary.json` 的
COMPUTE_COMPLETE 不代表最终验收通过。实际消费的源文件均须通过原发布 SHA-256。

在能读取源结果和锁定 transcript metadata 的开发身份下，构建到独立临时目录：

```bash
python3 -m interface.build_genome_annotations_db \
  --source-root /mnt/data/potato_agent/work/interproscan-5_genomes-260919 \
  --output-db /tmp/potato-genome-annotation-build/genome_annotations.sqlite \
  --dataset-version interproscan-5genomes-20260919-tf20260920-no-pathways-schema4 \
  --downloads-dir /tmp/potato-genome-annotation-build/downloads
```

`--downloads-dir` 可选，必须位于数据库父目录下面的独立子目录。使用它会生成去掉通路列的
逐材料结构域表、未命中、提取异常、TF 表和规则表，并记录公开文件大小与 SHA-256；
不复制用户工作目录、source lock、日志或带内部路径的报告。不传该参数时仍可动态查询和导出，
但不提供整套发布表下载。

如果原始 metadata 位于其他只读 staging 目录，可额外传
`--source-project-root /path/to/staged/source-project`。该目录保留
`metadata/transcripts/<assembly_id>.transcripts.tsv.gz` 相对结构，仍须逐文件通过 source lock
中的原始 SHA-256；该选项不绕过来源验证。

全量构建使用显式格式和独立候选 release 目录，例如：

```bash
python3 -m interface.build_genome_annotations_db \
  --source-format full \
  --source-root /mnt/data/potato_agent/work/interproscan-260916 \
  --output-db /tmp/potato-genome-annotation-full/genome_annotations.sqlite \
  --dataset-version interproscan-154genomes-20261007-tf20261008-no-pathways-v4 \
  --downloads-dir /tmp/potato-genome-annotation-full/downloads
```

全量下载从已验证的数据库记录投影为旧发布字段，生成缺少的逐材料异常表；
不为下载再次读取巨大的转录本展开原始命中表。异常下载类别为 `extraction_exceptions`。

本地启动示例：

```bash
GENOME_ANNOTATIONS_DB_PATH=/tmp/potato-genome-annotation-build/genome_annotations.sqlite \
  python3 -m uvicorn interface.app:app --host 127.0.0.1 --port 3000
```

默认数据库路径是 `/srv/genome_annotations/current/genome_annotations.sqlite`，可通过
`GENOME_ANNOTATIONS_DB_PATH` 覆盖。当前本地已发布 schema 4，版本为
`interproscan-154genomes-20261007-tf20261008-no-pathways-v4`，同一 release 的 `downloads/`
包含 772 个精简下载文件。数据库和下载由 root 持有，`potato-interface` 服务组只读。

YNNU 使用从本地 `/srv` 同步的已构建 release 和兼容代码，无需重新构建数据库或运行升级脚本。
同步时保留 `/srv/genome_annotations/releases/`、`current` 符号链接及 release 内的相对下载路径。
后续新增数据仍使用独立不可变 release 和原子切换，保留上一个 release 供回滚。
构建命令本身不切换服务、修改服务单元或分发到用户 Hermes home。

## 查询和导出 API

| 接口 | 用途 |
|---|---|
| `GET /api/genome-annotations/metadata` | 当前版本、材料、统计、方法与限制 |
| `POST /api/genome-annotations/query` | 组合筛选、批量 ID 与分页 |
| `GET /api/genome-annotations/genes/{id}?assembly=…` | 原始基因判定、全部异构体和查询关联 |
| `GET /api/genome-annotations/transcripts/{id}?assembly=…` | 蛋白长度、全部命中与 TF 证据 |
| `GET /api/genome-annotations/tf-families` | 家族规则覆盖与各材料基因/转录本/蛋白计数 |
| `POST /api/genome-annotations/export` | 全部匹配或所选记录的流式 ZIP 导出 |
| `GET /api/genome-annotations/downloads` | 允许公开的发布文件及 SHA-256 |
| `GET /api/genome-annotations/downloads/{file_id}` | 按不透明 file ID 下载发布文件 |

查询对象示例：

```json
{
  "assemblyIds": ["monoploid/DMv8.2"],
  "view": "genes",
  "ids": [],
  "q": "",
  "analyses": [],
  "signatures": ["PF03106"],
  "interproIds": [],
  "goIds": [],
  "domainMode": "all",
  "tfFamilies": [],
  "grades": [],
  "tfStatus": "all",
  "annotationStatus": "all",
  "conflict": "all",
  "limit": 50,
  "offset": 0
}
```

`assemblyIds: []` 表示全部材料，页面和 CLI 默认 DMv8.2。`view` 为 `genes` 或 `transcripts`。
单次最多 256 个 assembly ID、5,000 个输入 ID，每页默认 50 条、最多 500 条；offset 上限为
100,000,000。`metadata.limits` 公布这些限制。`analyses`、`tfFamilies`、`grades`、`goIds`
同字段选项取 OR；不同筛选条件取 AND。`domainMode` 为 `all` 或 `any`，控制多个结构域/InterPro
条件的全部满足或任意满足，默认 `all`。
目录和跨材料分页优先保持原五材料的顺序，新增材料随后沿用源目录顺序。
全目录且仅筛选 selected TF 时从已选蛋白集合查找转录本，避免逐一查询全部转录本；
限定材料和复合筛选保留原查询方案。
指定 `analyses` 后，描述关键词、结构域、InterPro 和 GO 证据必须来自所选应用；其他应用的命中不能满足这些筛选条件。

`tfStatus` 可取 `all`、`selected`、`not_selected`、`ambiguous`、`unassessable`；
`annotationStatus` 可取 `all`、`hit`、`no_match`、`no_cds`、`extraction_error`；
`conflict` 可取 `all`、`presence`、`family`、`any`。
`datasetVersion` 可放在查询对象中，以固定版本查询。

查询响应包含 `datasetVersion`、规范化 `query`、`items`、`total`、`returned`、`limit`、
`offset`、`hasMore` 和 `idReport`。`idReport` 分别报告 `unmatchedIds`、`ambiguousIds`、
`filteredIds`，不能将当前页行数当作完整匹配数量。

导出对象：

```json
{
  "query": {"assemblyIds": ["monoploid/DMv8.2"], "view": "genes", "tfFamilies": ["WRKY"]},
  "datasetVersion": "<datasetVersion-from-query>",
  "tables": ["genes", "transcripts", "domains", "tf_decisions", "tf_evidence"],
  "format": "tsv",
  "selection": []
}
```

从实际查询响应复制 `datasetVersion`。默认表为 `genes`，格式为 `tsv`，也支持 `csv`。
空 `selection` 导出完整匹配集，忽略分页；非空时传入
`{"assemblyId":"monoploid/DMv8.2","geneId":"…"}` 或对应转录本视图的 `transcriptId`。
选择仍受原查询条件约束。ZIP 包含所选表格、记录版本/查询/计数的 `metadata.json`，批量查询附 ID
匹配报告。结构域表包含匹配转录本的全部命中，并标记哪些命中满足本次结构域条件。
转录本及 TF 判定导出保留原列顺序，并在末尾追加 `extraction_status`。
结构域明细还保留蛋白长度、MD5 与注释来源；`tf_decisions` 表同时保留对应基因的原始判定、
家族并集/交集与异构体冲突，不能将过滤后的异构体集合重新解释为新的基因级结论。
浏览器使用 `/export-download` 表单适配器（`payload` 字段为同一导出 JSON），让下载直接流向浏览器文件系统；
HTTP 客户端仍使用 JSON `/export`。两者的筛选、校验和 ZIP 格式一致。

API 不接受服务器文件路径。版本已变化返回 409，客户端须刷新结果再导出。参数问题返回 400/422，
记录不存在返回 404，缺库或 schema 不兼容返回 503；公开错误不暴露内部路径。
公共注释请求不会触发用户智能体 runtime 保活。

## Hermes 客户端与验证

Hermes 托管技能源码位于 `skills/potato-knowledge-bioinformatics/domain-annotation-query/`。
它只通过 HTTP 访问当前部署的 `/api/genome-annotations`，无需凭据、SQLite 访问或专属 Python 包。
独立运行以下命令前，先设置 `POTATO_DOMAIN_ANNOTATIONS_BASE_URL` 或 `INTERFACE_PUBLIC_BASE_URL`
为目标部署的站点根地址；也可在技能根目录提供不纳入版本管理的 `api-base-url.txt`。

```bash
python3 skills/potato-knowledge-bioinformatics/domain-annotation-query/scripts/query_domain_annotations.py metadata
python3 skills/potato-knowledge-bioinformatics/domain-annotation-query/scripts/query_domain_annotations.py \
  query --all-assemblies --tf-family WRKY --tf-status selected --limit 10
python3 skills/potato-knowledge-bioinformatics/domain-annotation-query/scripts/query_domain_annotations.py \
  export --all-assemblies --tf-family WRKY --table genes --table domains \
  --version '<datasetVersion-from-query>' --output ./wrky_annotations.zip
```

导出在用户自己的工作目录创建 ZIP，不覆盖既有文件；传输失败或收到非 ZIP 响应时清理临时文件。
成功后输出完整绝对路径、SHA-256、大小和元数据。省略 `--version` 时先读取当前元数据并固定该版本；
重现先前查询应显式传版本或使用包含 `datasetVersion` 的 `--query-json` 文件。
确认使用新版数据后，显式 `--version` 同步更新导出请求及其查询对象中的版本；
未显式改版时保留原查询版本，数据库已更新则返回 409。

页面的 “Ask Potato Agent” 提供简短示例：统计 C88 基因组中被注释为 ERF 转录因子的基因数量。
提示词不附带页面筛选、所选记录或版本信息。智能体使用部署配置的 `domain-annotation-query` 技能查询。
脚本不内置域名或 IP，API 路径相对于提供的站点解析。
地址优先级为 `--base-url`、`POTATO_DOMAIN_ANNOTATIONS_BASE_URL`（兼容旧变量
`POTATO_GENOME_ANNOTATIONS_BASE_URL`）、`INTERFACE_PUBLIC_BASE_URL`、技能目录下的 `api-base-url.txt`。
配置文件按脚本位置解析，切换工作目录不影响读取；完全未配置时明确报错。
测试或迁移时提供目标部署地址，不将开发服务器地址写入源码；不同入口可能运行不同数据版本。

客户端回归命令：

```bash
python -m pytest interface/test_domain_annotation_skill.py -q
```

测试使用临时本机 HTTP 服务，覆盖精确 ID/筛选传递、版本固定、带空格路径、原子下载、并发文件冲突、
非 ZIP/截断文件、版本错误和输入限制，不需要真实 API keys、systemd 或特权用户。
完整数据验收还应核对上述计数、source checksums、异构体组合查询、TF 冲突和跨页导出的结果一致性。

API/构建器回归覆盖字段精简、重复命中、提取异常、版本冲突和下载校验；浏览器回归覆盖
五材料展示限制、即时悬浮提示、键盘操作、单次展开、移动端与原生 ZIP 下载。

```bash
python -m pytest -q interface/test_*.py
POTATO_ANNOTATION_BROWSER_TESTS=1 python -m pytest -q interface/test_functional_annotation_browser.py
```

浏览器测试依赖 Playwright/Chromium，`POTATO_ANNOTATION_SCREENSHOTS` 可指定截图输出目录。
