# hifiasm 0.19.8-r603 到 0.25.0-r726 版本差异要点

来源：官方 `chhylp123/hifiasm` GitHub releases 与 `CommandLines.cpp::init_opt()` 默认值对比（0.19.8 vs 0.25.0）。适用于用户询问 hifiasm 版本升级、默认参数是否变化、升级是否会影响 HiFi/Hi-C 组装时快速参考。

## Release 更新摘要

- 0.19.9-r616：修复若干 segfault；新增端粒相关参数 `--telo-m/-p/-d/-s`；新增 `--ctg-n` 丢弃过小 contig。
- 0.20.0-r639：较大更新，引入新的 error correction 组件；官方称通常提高连续性、QV，并缩短运行时间。
- 0.21.0-r686：新增 ONT simplex R10 reads beta 模块，使用 `--ont` 启用。
- 0.22.0-r689：修复可能导致 segfault 的问题，适用于所有 assembly modules；当时提示极高覆盖度输入可能结果不佳。
- 0.23.0-r691：修复高覆盖度输入问题，适用于所有模块，尤其 ONT。
- 0.24.0-r702：改进 coverage dropouts 情况下的组装质量。
- 0.25.0-r726：ONT 模块新增 `--rl-cut`、`--sc-cut` 内部过滤低质量 reads；修复小尺度误组装；改进高度相似重复拷贝处理，减少 repeat collapse。

## 默认参数结论

对比 0.19.8 与 0.25.0 的 `init_opt()`：未发现已有 HiFi/Hi-C 核心默认参数被改动。保持一致的典型参数包括：

- `-k 51`, `-w 51`, `-f 37`, `-D 5.0`, `-r 3`, `-a 4`, `-p 0`, `-m 10000000`, `-n 3`, `-x 0.8`, `-y 0.2`
- Hi-C：`--seed 11`, `--n-perturb 10000`, `--f-perturb 0.1`, `--n-weight 3`, `--l-msjoin 500000`
- purge-dups 相关默认值未见明显调整。

新增但通常不影响标准 HiFi+Hi-C 默认流程的参数/默认值：

- `--ctg-n`：默认 `max_contig_tip=3`，移除由 `<=3` 条 reads 支持的 tip contigs；可能影响很小 contig 的保留，通常不改变主骨架。
- `--telo-m/-p/-d/-s`：端粒识别；`--telo-m` 默认为 `NULL`，不手动指定 motif 时不启用。
- ONT：`--ont`, `--rl-cut 1000`, `--sc-cut 10`, `--chem-c 1`, `--chem-f 256` 等，主要在 ONT 模式下使用。

## 对组装影响的判断

- 对 HiFi + Hi-C：无需特别担心核心默认参数在 0.19.8 到 0.25.0 间突然大改。
- 结果仍可能不同，主要来自算法更新而非参数默认值变化：
  - 0.20.0 新 error correction 组件；
  - 0.24.0 coverage dropout 改进；
  - 0.25.0 小尺度误组装和高度相似重复拷贝 collapse 修复。
- 对 ONT：0.21.0 以后新增 ONT beta 模块，0.25.0 又加入默认 read 过滤；若比较 ONT 结果，建议从头重跑，不复用旧版 bin。

## 复查方法

1. 用 GitHub API 查 releases：
   ```bash
   python3 - <<'PY'
   import urllib.request, json
   data=json.load(urllib.request.urlopen('https://api.github.com/repos/chhylp123/hifiasm/releases?per_page=100'))
   for r in data[:10]:
       print(r['tag_name'], r['name'], r['published_at'])
   PY
   ```
2. 下载指定 release tarball，比较 `CommandLines.cpp::init_opt()` 中 `asm_opt->... = ...;` 赋值。
3. 回答用户时区分“默认参数变化”和“算法/实现变化”。

## 升级比较建议

- 新旧版本比较不要复用旧中间文件；可加 `-i` 或清理旧 `.bin`/相关缓存，并使用新输出前缀。
- 重点比较：contig N50/总长度、BUSCO、yak QV/k-mer completeness、Hi-C contact map、hap1/hap2 长度平衡、是否有 repeat collapse 或异常小 contig 差异。
