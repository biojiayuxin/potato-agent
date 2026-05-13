---
name: rice-gene-search
description: 水稻基因检索技能：通过 RAP-DB API 查询基因名/符号/基因号对应的 RAP-DB locus ID 与基础功能描述；符号搜索为全文模糊匹配，必要时结合 literature-review 进一步检索功能证据。
version: 1.0.0
metadata:
  hermes:
    tags: [rice, Oryza_sativa, IRGSP-1.0, RAP-DB, gene-search, OsID]
    related_skills: [literature-review]
---

# 水稻基因检索（RAP-DB API）

## 适用场景

当用户需要查询水稻 / rice / *Oryza sativa* IRGSP-1.0 的：

- 基因名、符号（如 `TB1`、`FC1`、`MOC1`、`D14`）对应的 RAP-DB locus ID（`Os03g0706500`）和 transcript ID；
- 完整 locus ID（`Os03g0706500`）或 transcript ID（`Os03t0706500-01`）对应的基因注释；
- 基因的染色体坐标、描述、CGSNL/Oryzabase 同义符号；
- 按染色体坐标范围查询基因。

## 核心原则

1. **水稻基因名/符号/ID 查询优先使用 RAP-DB `/api/search` 端点。**
2. **`q` 参数为全文模糊搜索**，在基因符号、名称、描述等文本字段中搜索。完整 locus ID 可通过 `q` 直接查（因为 ID 出现在索引文本中），但 locus ID **前缀**（如 `Os03g`）不返回结果。
3. **API 默认返回全部可用字段**，调用者应在回答中提取关键信息，避免回显全部原始 JSON。
4. **如需更深入的功能证据、已发表实验结果或跨物种背景，再加载 `literature-review` 技能检索文献。**

## API 端点

```text
GET https://rapdb.dna.naro.go.jp/api/search
```

无需认证，直接返回 JSON。

## 参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `q` | string | 关键词搜索：基因名、符号、描述、完整 locus ID、完整 transcript ID |
| `id` | string | 精确 ID 查询（locus 或 transcript ID） |
| `coord` | string | 坐标范围：`chr03:28428000..28431000` |
| `marker` | string | 分子标记名 |
| `type` | string | `locus`（默认）或 `transcript` |
| `nrow` | int | 每页条数（默认 10） |
| `offset` | int | 分页偏移（默认 0） |
| `sort` | string | 排序字段（如 `locus`） |
| `dir` | string | `asc` 或 `desc` |

## 返回字段

```json
{
  "status": "success",
  "count": 1,
  "nrow": 10,
  "offset": 0,
  "message": "",
  "result": [{
    "locus": "Os03g0706500",
    "transcript": "Os03t0706500-01",
    "seqid": "chr03",
    "start_pos": 28428504,
    "end_pos": 28430438,
    "strand": "+",
    "description": "TCP family transcription factor, ...",
    "CGSNL Gene Name": "FINE CULM 1",
    "CGSNL Gene Symbol": "FC1",
    "RAP-DB Gene Name Synonym(s)": "...",
    "RAP-DB Gene Symbol Synonym(s)": "...",
    "Oryzabase Gene Name Synonym(s)": "...",
    "Oryzabase Gene Symbol Synonym(s)": "..."
  }]
}
```

## 推荐查询命令

### 基因名/符号 → locus ID + 注释

```bash
curl -s --max-time 20 \
  'https://rapdb.dna.naro.go.jp/api/search?q=TB1&nrow=5' \
  -H 'Accept: application/json'
```

### 完整 locus ID → 基因信息

```bash
curl -s --max-time 20 \
  'https://rapdb.dna.naro.go.jp/api/search?q=Os03g0706500' \
  -H 'Accept: application/json'
```

`q` 接受完整 locus ID（大小写不敏感）和完整 transcript ID。

### 精确 ID 查询（备选，行为与 `q=ID` 一致）

```bash
curl -s --max-time 20 \
  'https://rapdb.dna.naro.go.jp/api/search?id=Os03g0706500' \
  -H 'Accept: application/json'
```

### 多结果分页

```bash
# 第 1 页
curl -s --max-time 20 \
  'https://rapdb.dna.naro.go.jp/api/search?q=D14&nrow=5&offset=0' \
  -H 'Accept: application/json'
# 第 2 页
curl -s --max-time 20 \
  'https://rapdb.dna.naro.go.jp/api/search?q=D14&nrow=5&offset=5' \
  -H 'Accept: application/json'
```

### 坐标范围查询

```bash
curl -s --max-time 20 \
  'https://rapdb.dna.naro.go.jp/api/search?coord=chr03:28428000..28431000' \
  -H 'Accept: application/json'
```

## 用 Python 处理结果

```python
import json, subprocess, sys

query = sys.argv[1] if len(sys.argv) > 1 else "TB1"
url = f"https://rapdb.dna.naro.go.jp/api/search?q={query}&nrow=5"
result = subprocess.run(
    ["curl", "-s", "--max-time", "20", url, "-H", "Accept: application/json"],
    capture_output=True, text=True
)
data = json.loads(result.stdout)

if data["status"] != "success":
    print(f"API error: {data.get('message', 'unknown')}")
    sys.exit(1)

print(f"查询: {query} → 共 {data['count']} 条结果")
for r in data["result"]:
    print(f"  {r['locus']} | {r.get('CGSNL Gene Symbol', '')} | chr: {r['seqid']}:{r['start_pos']}-{r['end_pos']} | {r['description'][:80]}")
```

## 输出规则

### 面向用户的 search 结果

默认展示：

- `locus`（RAP-DB locus ID）
- `CGSNL Gene Symbol`（基因符号）
- 染色体坐标（`seqid:start_pos-end_pos`，strand）
- `description` 摘要（截断至 120 字符以内）

多结果时展示 top 5，除非用户要求完整列表。`count` 总结果数始终报告。

### 面向用户的详情

当用户需要更多信息时，可补充：

- `transcript`（代表转录本 ID）
- `CGSNL Gene Name`（基因全名）
- `RAP-DB Gene Symbol Synonym(s)` / `Oryzabase Gene Symbol Synonym(s)`（同义符号）
- `RAP-DB Gene Name Synonym(s)` / `Oryzabase Gene Name Synonym(s)`（同义名称）

### 空结果

无结果时明确报告 `count=0`，不要编造基因信息。

## 已知行为

| 查询 | 结果数 | 说明 |
|------|--------|------|
| `q=TB1` | 1 | 符号精确匹配 |
| `q=FC1` | 3 | 多个基因共享同一符号 |
| `q=D14` | 7 | 模糊匹配含 D14/D14L3/DLK1 |
| `q=Os03g0706500` | 1 | 完整 locus ID 被索引 |
| `q=os03g0706500` | 1 | 大小写不敏感 |
| `q=Os03t0706500-01` | 1 | 完整 transcript ID 被索引 |
| `q=Os03g` | 0 | 前缀不匹配（非 ID 字段索引搜索） |
| `q=LOC_Os03g55240` | 0 | MSU ID 不索引 |
| `q=MOC1` | 1 | gene name 精确匹配 |

## 何时结合 literature-review

如果用户的问题超出 RAP-DB 基础注释范围，例如：

- 某基因的已发表实验功能与调控网络；
- 突变体表型、表达证据、蛋白互作；
- 跨物种同源基因功能推断与比较；
- 需要写综述或引用文献；

则在 RAP-DB 确认基因号（locus ID）和基础描述后，再加载 `literature-review` 技能继续检索。

## 注意事项

- API 响应可能较慢（偶尔 10-20 秒），`curl` 应使用 `--max-time` 防止挂起。
- `results` 和 `fields` 参数会触发 500 错误（前端参数，API 不兼容），不要使用。
- 无查询参数时 API 正常返回空结果，不会报错。
- 搜索以英文为主：`q=分枝`（日文/中文汉字）返回 0 结果。
- RAP-DB API 可能在未来变更端点路径，如果 `/api/search` 不可用，尝试通过浏览器访问 `https://rapdb.dna.naro.go.jp/search/` 页面检查最新 JS bundle 中的端点。
