---
name: potato-pan-genome-query
description: 查询 Potato Agent 马铃薯泛基因组 Orthogroups 公开 API。用于按精确 gene ID 查所属 orthogroup、查看 135 个基因组、按 core/soft-core/dispensable/private 分类分页查询、查看跨基因组分布及成员。适用于任何能通过 HTTPS 访问 potato-agent.ynnu.edu.cn 且有 Python 3 的 Hermes Agent 环境；不要求 Potato Agent 账户或本地数据库。
version: 1.1.0
author: Potato Agent
license: MIT
metadata:
  hermes:
    tags: [potato, pan-genome, orthogroup, orthofinder, gene-family, bioinformatics]
    related_skills: [potato-gene-search, find-orthogroups, ortholog-finder]
prerequisites:
  commands: [python3]
---

# Potato Pan-genome Query

通过 Potato Agent 的公开只读 API 查询马铃薯泛基因组 Orthogroups 数据。当前数据集包含 135 个基因组；查询必须使用精确 gene ID、genome 名称或 orthogroup ID。

## 何时使用

- 用户给出某个泛基因组 gene ID，希望知道它属于哪个 orthogroup。
- 用户希望查看一个 orthogroup 覆盖哪些基因组，以及每个基因组有多少成员。
- 用户希望按 core、soft-core、dispensable 或 private 分类查找 orthogroup。
- 用户需要列出某个 orthogroup 的全部或指定基因组成员。
- 用户想确认当前数据版本、基因组清单或数据规模。

不要用本技能进行序列相似性搜索、从新 FASTA 重新推断 orthogroup、基因功能注释或表达分析。新序列的同源搜索使用 `ortholog-finder`，从蛋白集重新推断分组使用 `find-orthogroups`。

## 基本原则

1. 优先运行本技能脚本，不要抓取网页或直接读取服务器 SQLite。
2. `gene` 是精确匹配，不是模糊搜索。若不知道 gene ID，先从其他基因查询技能解析准确 ID。
3. 不知道合法 genome 名称时，先运行 `genomes`；不要猜测名称或擅自改写下划线、点号和连字符。
4. 查询 orthogroup 时先运行 `orthogroup` 获取分布摘要。只有用户需要具体成员时才运行 `members`。
5. 按类别查找时使用 `orthogroups --category`，不要从成员结果自行推断或重新定义分类边界。
6. `orthogroups` 的 `--limit` 只限制当前页返回量，用于避免智能体上下文过度膨胀；它不代表该分类的 orthogroup 总数。分类总数必须读取 `pagination.total`，不能用 `len(orthogroups)`、`pagination.returned` 或 `--limit` 代替。需要继续读取时依据 `pagination.hasMore` 增加 `--offset`。
7. `orthogroups` 和 `members` 默认最多返回 100 条，单次上限 1000 条。优先用分类或 genome 缩小范围。
8. 不要在对话中打印数千个 orthogroup 或成员。大量结果应分批查询并给出数量摘要；只有用户明确要求完整清单时才输出或保存全部结果。

Hermes 加载技能时会将 `${HERMES_SKILL_DIR}` 展开为技能目录。先检查命令：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_pan_genome.py" --help
```

脚本只依赖 Python 标准库，不读取本地 SQLite，也不需要 Potato Agent 登录。默认 API 地址是
`https://potato-agent.ynnu.edu.cn/api/pan-genome`。仅在测试其他部署或镜像时，才用
`POTATO_PAN_GENOME_BASE_URL` 或 `--base-url` 指定站点根地址或 API 根地址；命令行参数优先于环境变量。

## 推荐命令

查看当前数据版本和规模：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_pan_genome.py" metadata
```

列出全部基因组及各自 gene/orthogroup 数量：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_pan_genome.py" genomes
```

按精确 gene ID 查询所属 orthogroup：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_pan_genome.py" gene DM8.2_chr01G00010
```

如果相同 gene ID 可能出现在多个基因组，指定 genome：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_pan_genome.py" gene DM8.2_chr01G00010 \
  --genome DMv8_2
```

按泛基因组类别分页列出 orthogroup：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_pan_genome.py" orthogroups \
  --category private --limit 100 --offset 0
```

`--category` 只能是 `core`、`soft-core`、`dispensable` 或 `private`。省略时分页列出全部 orthogroup。
这里的 `--limit 100` 只是为控制单页响应和智能体上下文大小而设，不表示 private 分类只有 100 个
orthogroup。回答该分类的完整数量时读取响应中的 `pagination.total`；`pagination.returned` 和
`orthogroups` 数组长度只表示当前页实际返回量。

查看 orthogroup 的基因数、覆盖基因组数和各基因组成员数：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_pan_genome.py" orthogroup OG0000001
```

分页查看成员：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_pan_genome.py" members OG0000001 \
  --limit 100 --offset 0
```

只查看一个基因组的成员：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_pan_genome.py" members OG0000001 \
  --genome DMv8_2 --limit 100
```

仅用于部署测试的地址覆盖示例：

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_pan_genome.py" metadata \
  --base-url http://10.186.0.25:3000
```

不要把测试地址写入技能或持久化环境配置；正常使用始终保留公开 HTTPS 默认值。

## API 参考

| 命令 | API |
|---|---|
| `metadata` | `GET /api/pan-genome/metadata` |
| `genomes` | `GET /api/pan-genome/genomes` |
| `gene` | `GET /api/pan-genome/genes/lookup?gene_id=...&genome=...` |
| `orthogroups` | `GET /api/pan-genome/orthogroups?category=...&limit=...&offset=...` |
| `orthogroup` | `GET /api/pan-genome/orthogroups/{orthogroup}` |
| `members` | `GET /api/pan-genome/orthogroups/{orthogroup}/members?genome=...&limit=...&offset=...` |

每次脚本输出包含：

```json
{
  "api_url": "实际请求 URL",
  "data": "API 返回对象"
}
```

保留 `api_url` 便于核对数据来源。HTTP 404 表示精确 gene、genome 或 orthogroup 不存在；HTTP 400 表示参数无效；HTTP 503 表示查询数据库尚未部署或暂不可用。遇到错误时报告真实错误，不要推测或回退到未经确认的 TSV。

## 结果解读

- `orthogroup` 是 OrthoFinder 根据输入蛋白数据推断的基因家族分组。
- 同一 genome 在一个 orthogroup 中有多个 gene，通常提示该基因组内存在旁系同源、复制或注释冗余；不能自动选择其中某一个作为“一对一 ortholog”。
- 某 genome 没有成员可能是真实缺失，也可能来自组装、注释、代表蛋白选择或聚类阈值；不要仅凭空 cell 宣称基因丢失。
- 两个 gene 位于同一 orthogroup 支持家族层面的同源关系，但不证明功能完全相同，也不提供调控方向、进化方向或表达证据。
- `geneCount` 是成员 gene 数；`genomeCount` 是至少含一个成员的 genome 数，两者含义不同。
- `category` 由 135 个 accession 中的存在数定义：core 为 135，soft-core 为 122–134，dispensable 为 2–121，private 为 1。
- `matchCount > 1` 表示精确 gene ID 在多个 genome 中重复出现。回答时必须同时报告 genome 和 orthogroup，不能静默选择第一条。

## 常见错误

1. **把 orthogroup 当成一对一 ortholog。** 必须检查每个 genome 的成员数；多成员组不是一对一关系。
2. **对 gene ID 做模糊或子串搜索。** API 只接受精确 gene ID；先用其他技能解析准确 ID。
3. **猜测 genome 名称。** 先调用 `genomes`，按返回值原样提交。
4. **一次请求全部成员并塞进上下文。** 使用 genome 过滤和分页，先提供数量与分布摘要。
5. **把缺失成员直接解释成生物学缺失。** 必须保留组装和注释不完整等替代解释。
6. **绕过 API 直接读 `/srv`。** Hermes 用户不应依赖服务内部路径或数据库结构。
7. **API 失败后编造分组。** 返回真实 HTTP/连接错误，必要时说明数据库尚未部署。
8. **默认查询 localhost。** 本技能面向外部安装，正常请求必须使用公开 HTTPS 默认值；本机或内网地址只能显式用于部署测试。

## 验证清单

- [ ] 命令路径使用 Hermes 展开的 `${HERMES_SKILL_DIR}`。
- [ ] gene ID、genome 和 orthogroup 均为精确值。
- [ ] 先查看 orthogroup 摘要，再按需要分页读取成员。
- [ ] 分类查询使用 API 返回的规范 category，不自行改变分类边界。
- [ ] 分类总数读取 `pagination.total`，不把 `--limit`、`pagination.returned` 或当前页数组长度当作总数。
- [ ] 多匹配 gene 同时报告 genome 和 orthogroup。
- [ ] 回答区分 gene 数和 genome 数。
- [ ] 不把 orthogroup 直接表述为一对一 ortholog 或功能等价。
- [ ] 不把空成员直接表述为确定的基因丢失。
- [ ] 保留真实 API 错误，不读取服务内部 SQLite 作为降级。
- [ ] 正常查询使用 `https://potato-agent.ynnu.edu.cn`，测试覆盖地址不被持久化。
