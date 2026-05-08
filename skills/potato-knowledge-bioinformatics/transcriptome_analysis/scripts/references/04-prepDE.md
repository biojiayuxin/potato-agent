# prepDE.py3 参数参考

## 功能
从 StringTie 输出结果生成基因/转录本 count 矩阵

## 命令模板

```bash
python3 prepDE.py3 -i {INPUT_DIR} -g {GENE_MATRIX} -t {TRANSCRIPT_MATRIX}
```

## 参数说明

| 参数 | 说明 | 必需 |
|------|------|------|
| `-i` | 输入目录或样本列表文件 | ✅ |
| `-g` | 基因 count 矩阵输出路径 | 推荐 |
| `-t` | 转录本 count 矩阵输出路径 | 推荐 |
| `-l` | read 长度（默认 75） | 可选 |

## 输入格式

### 方式 1：目录输入
直接指定 StringTie 输出目录：
```bash
python3 prepDE.py3 -i ./02-stringtie -g gene_count.csv -t transcript_count.csv
```

目录结构要求：
```
02-stringtie/
├── sample1_stringtie/
│   ├── sample1.gtf
│   └── e2t.ctab, e_data.ctab, ... (Ballgown 文件)
├── sample2_stringtie/
│   └── ...
```

### 方式 2：样本列表文件
创建 `samples.txt`：
```
sample1	./02-stringtie/sample1_stringtie/sample1.gtf
sample2	./02-stringtie/sample2_stringtie/sample2.gtf
```

运行：
```bash
python3 prepDE.py3 -i samples.txt -g gene_count.csv -t transcript_count.csv
```

## 输出格式

### 基因 count 矩阵 (gene_count_matrix.csv)
```
gene_id,sample1,sample2,sample3
gene1,150,120,180
gene2,0,5,2
gene3,500,480,520
```

### 转录本 count 矩阵 (transcript_count_matrix.csv)
```
transcript_id,sample1,sample2,sample3
transcript1.1,150,120,180
transcript2.1,0,5,2
```

## 实际项目示例

```bash
# 使用目录输入
python3 prepDE.py3 \
    -i ./02-stringtie \
    -g ./02-stringtie/gene_count_matrix.csv \
    -t ./02-stringtie/transcript_count_matrix.csv \
    -l 150

# 或使用样本列表文件
echo -e "sample1\t./02-stringtie/sample1_stringtie/sample1.gtf" > samples.txt
echo -e "sample2\t./02-stringtie/sample2_stringtie/sample2.gtf" >> samples.txt

python3 prepDE.py3 -i samples.txt -g gene_count.csv -t transcript_count.csv -l 150
```

## 注意事项

- 需要运行 StringTie 时使用 `-B -e` 参数生成 Ballgown 文件
- `-l` 参数应设置为测序 read 长度（通常是 150）
- 输出格式为 CSV，第一列为基因/转录本 ID
- 后续用于 DESeq2 差异分析

## 脚本来源

原始脚本来自 StringTie 官方：
http://ccb.jhu.edu/software/stringtie/index.shtml?t=manual#deseq

技能目录内置版本：`/skills/transcriptome_analysis/scripts/prepDE.py3`
