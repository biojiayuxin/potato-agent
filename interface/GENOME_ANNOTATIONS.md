# Domain annotation

Genomes 菜单中的 **Domain annotation** 页面位于 `/functional-annotation`，公开提供五个
马铃薯基因组的结构域注释、TF 家族信息及结果导出。浏览器与 Hermes 智能体共用
`/api/genome-annotations` 只读 API，无需登录。第一版不提供序列 FASTA 导出或在线重新注释。

页面提供基因组选择、All genes / Transcription factors 切换、ID/关键词查询和批量 ID。
选择 Transcription factors 会立即按当前基因组和搜索条件查询（API `tfStatus: selected`），
切换后从第一页开始并清空旧选择；重置恢复 DMv8.2 的全部基因。每页显示 20 条记录。
筛选随页面 URL、翻页、导出和智能体交接保留；Export all results 导出全部匹配记录，
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

冻结输入是 `interproscan-5_genomes-260919` 项目及其 `tf_extraction_planttfdb_rules` 结果。
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
  等转录本后缀来推断基因。无 CDS 转录本的基因归属来自 source lock 指定的 transcript metadata。
- 注释状态分别是 `hit`（有效蛋白有命中）、`no_match`（有效蛋白在四个应用无命中）、
  `no_cds`（无 CDS，未进入蛋白注释），三者不互换。
- 结构域 A+B 或 TF 家族+结构域的组合，必须在同一个转录本对应的蛋白上满足。
  API 筛选不重写原始基因 TF 判定和冲突，网页展开列出全部异构体。
- TF 是**基于 PlantTFDB 公开规则的本地鉴定**，不是 PlantTFDB 官方直接注释。
  A/B/C 表示规则覆盖方式，不是实验验证等级。缺少必要自建 HMM 的 11 个官方家族
  在 API 中提供规则覆盖信息，不能解释为零个或家族缺失。未被规则选中不等于确定的非 TF。
- 家族间可共享有冲突的基因，家族计数之和不等于去重 TF 总数。所有重叠原始命中均保留；
  坐标为蛋白氨基酸 1-based 闭区间，score 保留原值，不在不同数据库之间直接比较。

## 离线构建与发布

构建器 `python -m interface.build_genome_annotations_db` 读取冻结 TSV/GZIP、release SHA-256
清单与 source lock，验证引用、统计、TF closure、SQLite 完整性和全文索引后，原子替换指定
构建输出。源数据只读，输出必须位于源目录之外。在线服务用只读 SQLite 连接打开已发布版本。

数据库 schema 3 删除了 `pathway_sets` 表与 `matches.pathway_set_id` 列，构建器读取
冻结的 InterProScan 输入时忽略其最后一列通路文本，其他命中与重复记录逐条保留。
API 详情及结构域导出不再返回 `pathways`；整套注释下载改为 `*.domains.tsv.gz`，
从原始表中移除 `pathways` 列并重新计算公开文件的 SHA-256，其他列保持一致。
旧库需要离线重建并与 schema 3 代码配套切换。本次公开字段变化使用新的
`datasetVersion`：`interproscan-5genomes-20260919-tf20260920-no-pathways`，旧版本请求返回 409。

在能读取源结果和锁定 transcript metadata 的开发身份下，构建到独立临时目录：

```bash
python3 -m interface.build_genome_annotations_db \
  --source-root /mnt/data/potato_agent/work/interproscan-5_genomes-260919 \
  --output-db /tmp/potato-genome-annotation-build/genome_annotations.sqlite \
  --dataset-version interproscan-5_genomes-260919-v1 \
  --downloads-dir /tmp/potato-genome-annotation-build/downloads
```

`--downloads-dir` 可选，必须位于数据库父目录下面的独立子目录。使用它会生成去掉通路列的
逐材料结构域表，复制未命中、无 CDS、TF 表和规则表，并记录公开文件大小与 SHA-256；
不复制用户工作目录、source lock、日志或带内部路径的报告。不传该参数时仍可动态查询和导出，
但不提供整套发布表下载。

如果原始 metadata 位于其他只读 staging 目录，可额外传
`--source-project-root /path/to/staged/source-project`。该目录保留
`metadata/transcripts/<assembly_id>.transcripts.tsv.gz` 相对结构，仍须逐文件通过 source lock
中的原始 SHA-256；该选项不绕过来源验证。

本地启动示例：

```bash
GENOME_ANNOTATIONS_DB_PATH=/tmp/potato-genome-annotation-build/genome_annotations.sqlite \
  python3 -m uvicorn interface.app:app --host 127.0.0.1 --port 3000
```

默认数据库路径是 `/srv/genome_annotations/current/genome_annotations.sqlite`，可通过
`GENOME_ANNOTATIONS_DB_PATH` 覆盖。生产部署需 owner 批准：将数据库及相对下载目录作为完整的
不可变 release 发布，然后原子切换 `current` symlink；保留上一个 release 以便回滚。
不要原地修改活动 SQLite，也不要开放用户数据目录权限。构建命令本身不切换生产服务、修改服务单元
或分发到用户 Hermes home。

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
单次最多 5,000 个输入 ID，每页默认 50 条、最多 500 条。`analyses`、`tfFamilies`、`grades`、`goIds`
同字段选项取 OR；不同筛选条件取 AND。`domainMode` 为 `all` 或 `any`，控制多个结构域/InterPro
条件的全部满足或任意满足，默认 `all`。
指定 `analyses` 后，描述关键词、结构域、InterPro 和 GO 证据必须来自所选应用；其他应用的命中不能满足这些筛选条件。

`tfStatus` 可取 `all`、`selected`、`not_selected`、`ambiguous`、`unassessable`；
`annotationStatus` 可取 `all`、`hit`、`no_match`、`no_cds`；
`conflict` 可取 `all`、`presence`、`family`、`any`。
`datasetVersion` 可放在查询对象中，以固定版本查询。

查询响应包含 `datasetVersion`、规范化 `query`、`items`、`total`、`returned`、`limit`、
`offset`、`hasMore` 和 `idReport`。`idReport` 分别报告 `unmatchedIds`、`ambiguousIds`、
`filteredIds`，不能将当前页行数当作完整匹配数量。

导出对象：

```json
{
  "query": {"assemblyIds": ["monoploid/DMv8.2"], "view": "genes", "tfFamilies": ["WRKY"]},
  "datasetVersion": "interproscan-5genomes-20260919-tf20260920",
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
结构域明细还保留蛋白长度、MD5 与注释来源；`tf_decisions` 表同时保留对应基因的原始判定、
家族并集/交集与异构体冲突，不能将过滤后的异构体集合重新解释为新的基因级结论。
浏览器使用 `/export-download` 表单适配器（`payload` 字段为同一导出 JSON），让下载直接流向浏览器文件系统；
HTTP 客户端仍使用 JSON `/export`。两者的筛选、校验和 ZIP 格式一致。

API 不接受服务器文件路径。版本已变化返回 409，客户端须刷新结果再导出。参数问题返回 400/422，
记录不存在返回 404，缺库或 schema 不兼容返回 503；公开错误不暴露内部路径。
公共注释请求不会触发用户智能体 runtime 保活。

## Hermes 客户端与验证

Hermes 托管技能源码位于 `skills/potato-knowledge-bioinformatics/genome-annotation-query/`。
它只通过 HTTP 访问 API，默认公开 HTTPS 地址；无需凭据、SQLite 访问或专属 Python 包。

```bash
python3 skills/potato-knowledge-bioinformatics/genome-annotation-query/scripts/query_genome_annotations.py metadata
python3 skills/potato-knowledge-bioinformatics/genome-annotation-query/scripts/query_genome_annotations.py \
  query --all-assemblies --tf-family WRKY --tf-status selected --limit 10
python3 skills/potato-knowledge-bioinformatics/genome-annotation-query/scripts/query_genome_annotations.py \
  export --all-assemblies --tf-family WRKY --table genes --table domains \
  --version '<datasetVersion-from-query>' --output ./wrky_annotations.zip
```

导出在用户自己的工作目录创建 ZIP，不覆盖既有文件；传输失败或收到非 ZIP 响应时清理临时文件。
成功后输出完整绝对路径、SHA-256、大小和元数据。省略 `--version` 时先读取当前元数据并固定该版本；
重现先前查询应显式传版本或使用包含 `datasetVersion` 的 `--query-json` 文件。

开发测试可在命令末尾加 `--base-url http://127.0.0.1:3000`。正常默认保留
`https://potato-agent.ynnu.edu.cn`；也可临时使用 `POTATO_GENOME_ANNOTATIONS_BASE_URL`，不要持久化测试地址。
页面的 “Ask Potato Agent” 请求会携带当前站点地址，要求客户端用 `--base-url` 访问相同部署。
使用内网或 ZeroTier 入口时，可显式传入该入口地址；不同入口可能运行不同数据版本。

客户端回归命令：

```bash
python -m pytest interface/test_genome_annotation_skill.py -q
```

测试使用临时本机 HTTP 服务，覆盖精确 ID/筛选传递、版本固定、带空格路径、原子下载、并发文件冲突、
非 ZIP/截断文件、版本错误和输入限制，不需要真实 API keys、systemd 或特权用户。
完整数据验收还应核对上述计数、source checksums、异构体组合查询、TF 冲突和跨页导出的结果一致性。

## 开发验收记录

数据来源是 `interproscan-5genomes-20260919-tf20260920` 冻结结果。schema 3 发布版本为
`interproscan-5genomes-20260919-tf20260920-no-pathways`，保留 477,888 个基因、674,353 个
转录本、381,286 个全局唯一蛋白及 1,193,989 条结构域/家族命中；只删除通路字段。
TF 数据包括 19,697 个基因、29,142 个转录本，家族计数含 354 个统计单元格。
27 个公开下载文件中，五个结构域表去掉通路列，其他表保留源文件内容。
当前数据库为 1,924,857,856 字节（约 1.92 GB），全部下载文件合计 74,496,627 字节（约 74.5 MB）。

schema 2 的 1,941,606,400 字节数据库仅用于此次迁移校验和部署回退，不兼容 schema 3 读取器。
完整发布验证需要逐条比较剩余字段、全部下载表和科学计数，检查外键、SQLite/全文索引完整性，
并对比查询、TSV/CSV 导出与异构体详情。API/构建器回归覆盖通路字段移除、重复命中保留、
版本冲突和下载校验；浏览器回归覆盖即时悬浮提示、键盘操作、单次展开、移动端与原生 ZIP 下载。
截图及构建、验证报告保存在本次开发的临时 review 目录。

```bash
python -m pytest -q interface/test_*.py
POTATO_ANNOTATION_BROWSER_TESTS=1 python -m pytest -q interface/test_functional_annotation_browser.py
```

浏览器测试依赖 Playwright/Chromium，`POTATO_ANNOTATION_SCREENSHOTS` 可指定截图输出目录。
本次生产库位于 `/srv/genome_annotations/current/genome_annotations.sqlite`，`current` 指向独立
schema 3 release，27 个公开下载文件位于同一 release 的 `downloads/`。生产读取身份为
`potato-interface`；库和下载文件由 root 持有，服务组只读。页面、API 和 12 个账号的查询技能已部署，
部署前代码备份保存在 `/var/backups/potato-agent/genome-annotations/`。
本机内网与 ZeroTier 入口均已确认新版本；公网域名在此次检查时仍提供旧版站点，需由对应入口运维另行同步。
后续生产发布继续遵守本文件的发布边界，并取得 owner 授权。
