---
name: figshare-data-download
description: Download public Figshare dataset/article files, including file-list resolution, resumable downloads, checksum validation, and browser-cookie fallback when terminal access is blocked.
version: 1.0.0
author: Potato Agent
license: MIT
metadata:
  hermes:
    tags: [figshare, download, dataset, wget, checksum]
---

# Figshare Data Download

## 何时使用

- 用户给出 Figshare DOI、article ID 或 landing page，需要下载公开数据文件。
- `api.figshare.com/v2/...` 返回 403，或 `figshare.com/ndownloader/...` 在命令行返回 202/空内容。
- 需要下载较大的 Figshare 文件并校验大小、MD5 或其他 checksum。

## 输入信息

尽量先确定：

```text
Figshare landing page: https://figshare.com/articles/.../<article_id>/<version>
article_id: <article_id>
version:    <version>
```

如果只有 DOI，可先用 DataCite 解析：

```bash
python3 - <<'PY'
import json
import urllib.parse
import urllib.request

query = '<figshare DOI or title>'
url = 'https://api.datacite.org/dois?query=' + urllib.parse.quote(query)
req = urllib.request.Request(url, headers={'User-Agent':'PotatoAgent/1.0'})
with urllib.request.urlopen(req, timeout=30) as resp:
    data = json.load(resp)
for item in data.get('data', [])[:5]:
    attrs = item.get('attributes', {})
    print(item.get('id'), attrs.get('url'), attrs.get('sizes'), attrs.get('titles'))
PY
```

## 获取文件列表

### 方案 A：页面源码中已有文件信息

先渲染 Figshare 页面，查看是否能从页面源码或 `window.__APOLLO_STATE__` 中拿到文件 ID、文件名、大小和 MD5。

如果普通 `requests` 被 WAF 拦截，使用 headless Firefox。Snap 版 Firefox 的 wrapper 可能因 HOME 不在 `/home` 下报错，优先用真实二进制：

```text
/snap/firefox/current/usr/lib/firefox/geckodriver
/snap/firefox/current/usr/lib/firefox/firefox
```

### 方案 B：浏览器上下文 GraphQL 查询

在已打开 landing page 的浏览器上下文中查询 `/api/graphql`。匿名公共数据应查询 `itemVersion`，不要查询 `article`，否则可能报 `INSUFFICIENT_PERMISSIONS`。

GraphQL 查询：

```graphql
query itemFiles($id: Int!, $version: Int, $cursor: String!, $pageSize: Int!) {
  publicItem: itemVersion(id: $id, version: $version) {
    id version filesCount size downloadUrl doi title
    files(pageSize: $pageSize, cursor: $cursor) {
      cursor
      elements {
        id name status extension size mimeType md5 suppliedMd5 downloadUrl isLinkOnly
      }
    }
  }
}
```

注意：`pageSize` 必须小于 40，推荐用 `20`。

可在浏览器会话中执行的 JavaScript：

```javascript
const articleId = 123456; // replace with Figshare article_id
const version = 1;        // replace with Figshare version
const query = `query itemFiles($id: Int!, $version: Int, $cursor: String!, $pageSize: Int!) {
  publicItem: itemVersion(id: $id, version: $version) {
    id version filesCount size downloadUrl doi title
    files(pageSize: $pageSize, cursor: $cursor) {
      cursor
      elements { id name status extension size mimeType md5 suppliedMd5 downloadUrl isLinkOnly }
    }
  }
}`;
const token = document.querySelector('meta[name="csrf_token"]')?.content || '';
const r = await fetch('/api/graphql', {
  method: 'POST', credentials: 'include',
  headers: {'content-type':'application/json', 'accept':'application/json', 'x-csrf-token': token},
  body: JSON.stringify({operationName:'itemFiles', variables:{id: articleId, version, cursor:'', pageSize:20}, query})
});
console.log(await r.text());
```

## 下载方法

### 1. 直接尝试 ndownloader

如果没有被 WAF 拦截，单文件下载格式：

```bash
wget -c -O <filename> "https://figshare.com/ndownloader/files/<file_id>"
```

整包下载格式：

```bash
wget -c -O figshare_<article_id>_v<version>.zip \
  "https://figshare.com/ndownloader/articles/<article_id>/versions/<version>"
```

### 2. 如果命令行被 WAF 拦截：先用浏览器拿 cookie

生成 Netscape 格式 cookie 文件，供 `wget --load-cookies` 使用。把 `ARTICLE` 改成实际 landing page。

```bash
python3 - <<'PY'
import subprocess, time, requests, tempfile
from pathlib import Path

ARTICLE='https://figshare.com/articles/.../<article_id>/<version>'
COOKIE_OUT='figshare_cookies.txt'
GECKO='/snap/firefox/current/usr/lib/firefox/geckodriver'
FIREFOX='/snap/firefox/current/usr/lib/firefox/firefox'
PORT=4463

log=tempfile.NamedTemporaryFile(delete=False)
p=subprocess.Popen([GECKO, '--port', str(PORT), '--host', '127.0.0.1'], stdout=log, stderr=log)
sid=None
try:
    base=f'http://127.0.0.1:{PORT}'
    for _ in range(60):
        try:
            if requests.get(base+'/status', timeout=1).status_code == 200:
                break
        except Exception:
            time.sleep(0.5)
    caps={'capabilities':{'alwaysMatch':{'browserName':'firefox','acceptInsecureCerts':True,
          'moz:firefoxOptions':{'binary':FIREFOX,'args':['-headless']}}}}
    sid=requests.post(base+'/session', json=caps, timeout=60).json()['value']['sessionId']
    sbase=f'{base}/session/{sid}'
    requests.post(sbase+'/url', json={'url':ARTICLE}, timeout=120)
    time.sleep(8)
    cookies=requests.get(sbase+'/cookie', timeout=20).json()['value']
    netscape='# Netscape HTTP Cookie File\n'
    for c in cookies:
        domain=c.get('domain','.figshare.com')
        flag='TRUE' if domain.startswith('.') else 'FALSE'
        path=c.get('path','/')
        secure='TRUE' if c.get('secure') else 'FALSE'
        expiry=str(c.get('expiry',0) or 0)
        netscape += f'{domain}\t{flag}\t{path}\t{secure}\t{expiry}\t{c["name"]}\t{c["value"]}\n'
    Path(COOKIE_OUT).write_text(netscape)
    print(f'cookie saved: {COOKIE_OUT}, entries={len(cookies)}')
finally:
    if sid:
        try: requests.delete(f'{base}/session/{sid}', timeout=10)
        except Exception: pass
    p.terminate()
    try: p.wait(timeout=5)
    except Exception: p.kill()
    log.close()
PY
```

然后带 cookie 下载：

```bash
wget --load-cookies figshare_cookies.txt \
  --user-agent="Mozilla/5.0 (X11; Linux x86_64; rv:150.0) Gecko/20100101 Firefox/150.0" \
  --referer="https://figshare.com/articles/.../<article_id>/<version>" \
  -c -O <filename> \
  "https://figshare.com/ndownloader/files/<file_id>"
```

## 批量下载模板

把 `FILES` 改成 GraphQL 得到的 `file_id filename expected_size md5`。

```bash
#!/bin/bash
set -euo pipefail

DEST="/path/to/download_dir"
COOKIE="$DEST/figshare_cookies.txt"
ARTICLE="https://figshare.com/articles/dataset/.../<article_id>/<version>"
USER_AGENT="Mozilla/5.0 (X11; Linux x86_64; rv:150.0) Gecko/20100101 Firefox/150.0"
mkdir -p "$DEST"
cd "$DEST"

cat > md5sums.txt <<'EOF'
<md5_1>  <file_1>
<md5_2>  <file_2>
EOF

# 格式：file_id filename expected_size md5
FILES=(
  "<file_id_1> <file_1> <expected_size_1> <md5_1>"
  "<file_id_2> <file_2> <expected_size_2> <md5_2>"
)

for row in "${FILES[@]}"; do
    read -r fid name exp_size exp_md5 <<< "$row"
    url="https://figshare.com/ndownloader/files/$fid"
    echo "START $name"
    wget --load-cookies "$COOKIE" \
         --user-agent="$USER_AGENT" \
         --referer="$ARTICLE" \
         -c --tries=5 --timeout=120 --read-timeout=120 --waitretry=30 \
         -O "$name" "$url"

    test "$(stat -c%s "$name")" = "$exp_size"
    echo "$exp_md5  $name" | md5sum -c -
    echo "DONE $name"
done
```

## 长任务建议

总量较大或网络不稳定时，写成脚本后提交 Slurm：

```bash
SLURM_SKILL_DIR="${SLURM_SKILL_DIR:?set SLURM_SKILL_DIR to the slurm-for-long-running-tasks skill directory}"
bash "$SLURM_SKILL_DIR/scripts/submit-job.sh" \
  --job-name figshare_download \
  --time 8:00:00 \
  --mem-gb 2 \
  --script /path/to/download_figshare.sh
```

## 校验

下载后至少检查：

```bash
md5sum -c md5sums.txt
ls -lh
```

如果下载的是 gzip/tar.gz：

```bash
gzip -t *.gz
```

## 常见注意点

- Figshare `api.figshare.com/v2` 和 `ndownloader` 在命令行可能被 AWS WAF 拦截；浏览器 cookie 通常可解决。
- `ndownloader/files/<file_id>` 会 302 到一个短时效 S3 签名 URL，不要把 S3 URL 当长期下载链接保存。
- `wget -c` 可以断点续传；中断后重新跑同一脚本即可。
- 如果 cookie 过期，重新打开 landing page 生成新的 `figshare_cookies.txt`。
- 公共文件列表用 `itemVersion(id, version)` 查询；不要用 `article(id)` 查询匿名公共文件。
