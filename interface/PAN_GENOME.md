# Pan-genome Orthogroups

Pan-genome Orthogroups 是公开只读 API 数据集。Interface 默认从
`/srv/pan_genome/current/pan_genome.sqlite` 读取一个 SQLite 文件；浏览器和 Hermes 技能只调用 API，不能直接读取数据库。

## 当前数据

构建输入：

```text
/mnt/data/potato_agent/work/pan-genome-diploid-260831/02_orthofinder_run/
  orthofinder_orthogroups_only/Results_Aug31/Orthogroups/Orthogroups.tsv
```

经过完整构建验证的来源及规模：

```text
dataset version:   pan-genome-diploid-260831
source bytes:      241428019
source SHA-256:    fa74c2102cfafb946774977a6b5624e1210671e137bd2e5740fad59d20294b2a
genomes:           135
orthogroups:       40236
gene memberships: 7423576
occupied cells:    2446201
core groups:       6596      (135 accessions; 2759097 gene memberships)
soft-core groups:  8763      (122-134 accessions; 3363272 gene memberships)
dispensable groups:21301     (2-121 accessions; 1289575 gene memberships)
private groups:    3576      (1 accession; 11632 gene memberships)
SQLite bytes:      943120384
```

四类按 orthogroup 中至少含一个成员基因的 accession 数量划分，合计 40236。135 个输入列包括
60 个 monoploid 和 75 个 phased-diploid 蛋白组；分类边界与旧版保持一致。Builder 将分类写入
`orthogroups.category`，并严格校验全部计数。命令行的 `--expected-* 0` 可以关闭单项预期值，但生产发布
不应关闭当前数据集的校验。

参考统计位于 `/mnt/data/potato_agent/work/pan-genome-diploid-260831/06-potato_pangenome_summary`。
逐组分类、覆盖基因组数和成员数与 `orthogroup_categories.tsv` 一致；分类汇总与
`orthogroup_category_summary.tsv` 一致。继续只收录 `Orthogroups.tsv` 中的已分组成员，
不将另外 56190 个 unassigned singleton 纳入 private。

## 构建

构建器先在输出目录创建临时数据库，完成 schema、索引、外键和 `quick_check` 后才替换指定输出。它不会修改源 TSV。
构建器默认参数仍对应旧版数据，因此构建本版本时必须显式传入下列来源、版本和全部预期计数。

```bash
cd /srv/potato_agent
PAN_BUILD_ROOT=/var/tmp/potato-pan-genome-diploid-260831
PAN_BUILD_DB=$PAN_BUILD_ROOT/pan_genome.sqlite
test ! -e "$PAN_BUILD_ROOT"
install -d -o root -g root -m 0700 "$PAN_BUILD_ROOT"

/opt/interface-env/bin/python -m interface.build_pan_genome_db \
  --source-tsv /mnt/data/potato_agent/work/pan-genome-diploid-260831/02_orthofinder_run/orthofinder_orthogroups_only/Results_Aug31/Orthogroups/Orthogroups.tsv \
  --dataset-version pan-genome-diploid-260831 \
  --expected-genomes 135 \
  --expected-orthogroups 40236 \
  --expected-gene-memberships 7423576 \
  --expected-core-orthogroups 6596 \
  --expected-soft-core-orthogroups 8763 \
  --expected-dispensable-orthogroups 21301 \
  --expected-private-orthogroups 3576 \
  --output-db "$PAN_BUILD_DB"
```

源码目录权限要求 builder 以有权读取该 TSV 的受控身份运行。不要放宽整个 `/mnt/data/potato_agent` 目录权限。

构建输出 JSON 必须包含上述计数和 SHA-256。发布前再进行只读检查：

```bash
/opt/interface-env/bin/python - "$PAN_BUILD_DB" <<'PY'
import json
import pathlib
import sqlite3
import sys

path = pathlib.Path(sys.argv[1]).resolve()
with sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True) as conn:
    assert conn.execute("pragma quick_check").fetchone()[0] == "ok"
    assert not conn.execute("pragma foreign_key_check").fetchall()
    row = conn.execute(
        "select schema_version,dataset_version,source_sha256,counts_json "
        "from pan_genome_metadata where singleton=1"
    ).fetchone()
    assert row is not None and row[0] == 2
    assert row[1] == "pan-genome-diploid-260831"
    assert row[2] == "fa74c2102cfafb946774977a6b5624e1210671e137bd2e5740fad59d20294b2a"
    counts = json.loads(row[3])
    assert counts == {
        "genomes": 135,
        "orthogroups": 40236,
        "gene_memberships": 7423576,
        "occupied_cells": 2446201,
        "orthogroup_categories": {
            "core": 6596,
            "soft-core": 8763,
            "dispensable": 21301,
            "private": 3576,
        },
    }
    print(row[1], row[2], json.dumps(counts, sort_keys=True))
PY
test ! -e "$PAN_BUILD_DB-wal"
test ! -e "$PAN_BUILD_DB-shm"
test ! -e "$PAN_BUILD_DB-journal"
```

## 发布

生产发布会修改 `/srv`，必须先取得 owner 批准。每个版本安装到 immutable release 目录，再原子切换 `current` symlink；不要原地覆盖活动 SQLite。

```bash
PAN_ROOT=/srv/pan_genome
PAN_RELEASE=pan-genome-diploid-260831-schema2
PAN_RELEASE_DIR=$PAN_ROOT/releases/$PAN_RELEASE
PAN_NEXT_LINK=$PAN_ROOT/.current-$PAN_RELEASE
test ! -e "$PAN_RELEASE_DIR"
test ! -e "$PAN_NEXT_LINK"

install -d -o root -g potato-interface -m 0750 \
  "$PAN_ROOT" "$PAN_ROOT/releases" "$PAN_RELEASE_DIR"
install -o root -g potato-interface -m 0640 \
  "$PAN_BUILD_DB" "$PAN_RELEASE_DIR/pan_genome.sqlite"
ln -s "releases/$PAN_RELEASE" "$PAN_NEXT_LINK"
mv -T "$PAN_NEXT_LINK" "$PAN_ROOT/current"

test "$(stat -c '%U:%G:%a' "$PAN_RELEASE_DIR")" = root:potato-interface:750
test "$(stat -c '%U:%G:%a' "$PAN_RELEASE_DIR/pan_genome.sqlite")" = \
  root:potato-interface:640
sudo -u potato-interface test -r "$PAN_ROOT/current/pan_genome.sqlite"
```

2026-09-25 本机发布的活动目录为 `/srv/pan_genome/releases/pan-genome-diploid-260831-schema2`。
目录中的 `build-report.json`、`validation-report.json` 和 `deployment-report.json` 记录来源校验值、
逐组核对结果、本机 API 验收及原活动版本。旧版 `pan-genome-260709-a20-schema2` 保留用于回滚。
本次继续使用 schema 2，通过原子切换 `current` 生效，无需重启 Interface。

默认路径无需设置环境变量。使用其他位置时可配置：

```ini
Environment=PAN_GENOME_DB_PATH=/srv/pan_genome/current/pan_genome.sqlite
```

普通 Hermes Linux 用户不应获得 `/srv/pan_genome` 的直接读取权限。数据访问由公开 API 提供。

## API

API 无需登录，当前不做请求频率限制。orthogroup 分类列表和成员列表始终分页，单次最多 1000 条。

```text
GET /api/pan-genome/metadata
GET /api/pan-genome/genomes
GET /api/pan-genome/genes/lookup?gene_id=<exact-id>&genome=<optional-genome>
GET /api/pan-genome/orthogroups?category=<optional-category>&limit=100&offset=0
GET /api/pan-genome/orthogroups/<orthogroup>
GET /api/pan-genome/orthogroups/<orthogroup>/members?genome=<optional>&limit=100&offset=0
```

`category` 的规范值为 `core`、`soft-core`、`dispensable` 和 `private`。元数据、基因命中、orthogroup
详情及成员结果都会返回对应分类；列表接口也接受 `softcore` 和 `soft_core` 作为 `soft-core` 的输入别名。

验收示例：

```bash
ORIGIN=http://127.0.0.1:3000
curl -fsS "$ORIGIN/api/pan-genome/metadata" | python3 -m json.tool
curl -fsS "$ORIGIN/api/pan-genome/genomes" | python3 -m json.tool
curl -fsS "$ORIGIN/api/pan-genome/orthogroups?category=private&limit=2" | \
  python3 -m json.tool
curl -fsS "$ORIGIN/api/pan-genome/orthogroups/OG0000000" | python3 -m json.tool
curl -fsS "$ORIGIN/api/pan-genome/orthogroups/OG0000000/members?limit=2" | \
  python3 -m json.tool
```

缺失数据库或不兼容 schema 返回通用 `503`，数据库查询失败返回通用 `500`，响应不会暴露内部路径。

## Hermes 技能

源码位于：

```text
skills/potato-knowledge-bioinformatics/potato-pan-genome-query/
```

这是待测试的 Hermes Agent 兼容托管技能源码，使用 `${HERMES_SKILL_DIR}` 和标准库查询脚本。当前生产部署
不向用户目录分发该技能；完成独立测试并获得 owner 确认后，才能通过 managed-skills 流程同步到每用户
`HERMES_HOME`。不要把它改写成 Codex skill 或安装到 Codex skills 目录。

```bash
python3 "${HERMES_SKILL_DIR}/scripts/query_pan_genome.py" metadata
python3 "${HERMES_SKILL_DIR}/scripts/query_pan_genome.py" gene <gene-id> --genome <genome>
python3 "${HERMES_SKILL_DIR}/scripts/query_pan_genome.py" orthogroups --category private
python3 "${HERMES_SKILL_DIR}/scripts/query_pan_genome.py" orthogroup OG0000000
```

默认查询 `https://potato-agent.ynnu.edu.cn`，因此非 Potato Agent 用户安装后也可直接调用公开 API，不依赖
本地数据库或登录态。仅在部署测试时用 `POTATO_PAN_GENOME_BASE_URL` 或 `--base-url` 显式覆盖地址。
