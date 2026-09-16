---
name: plant-genomic-dna-extraction
description: 植物基因组 DNA 的 CTAB 湿实验提取：三种规格选型、操作流程、试剂配制和周期估算。
version: 1.0.0
author: Potato Agent
metadata:
  hermes:
    tags: [plant, potato, genomic-dna, dna-extraction, ctab, wet-lab, protocol]
---

# 植物基因组 DNA 提取

使用 CTAB 法提取植物叶片基因组 DNA，涵盖三种提取规格、配套试剂及周期估算。

## 适用范围与输入

用户咨询植物叶片基因组 DNA 提取、选择提取规格、配制相应缓冲液或评估提取周期时使用。本技能是湿实验方法，不用于从 FASTA 文件提取序列；不涵盖 RNA、质粒或其他组织专用提取方法。

按任务需要确认植物及材料状态、样本数、可用材料量、下游用途、容器与设备、试剂是否已备齐，以及是否需要第二次抽提。起始材料为新鲜叶片，96 孔规格使用嫩叶。

## 规格选择与按需读取

先用下表选择规格。给出完整操作步骤或精确核算用量、时间前，加载对应参考文件；仅咨询试剂配制时只需加载配方文件。不要默认读取所有规格。

| 规格 | 起始材料 | CTAB 用量 | DNA 复溶体积 | 参考文件与选择依据 |
| --- | --- | --- | --- | --- |
| 2 mL 离心管 | 管盖大小的新鲜叶片 2 片，液氮研磨 | 900 μL/样本 | 100 μL | [references/ctab-2ml.md](references/ctab-2ml.md)：采用单管小规格，需匹配 12,000 rpm 的转子和耗材 |
| 50 mL 离心管 | 液氮研磨的叶片粉末，刚覆盖管圆底部 | 20 mL/样本 | 约 300 μL | [references/ctab-50ml.md](references/ctab-50ml.md)：采用大管规格，需匹配 50 mL 管和 4,500 rpm 离心设备 |
| 八连管/96 孔 | 约 1 cm² 新鲜嫩叶 1 片/管，钢珠研磨 | 500 μL/样本 | 约 100 μL | [references/ctab-96well.md](references/ctab-96well.md)：多样本批量处理，需匹配振荡器、管架、深孔板及离心设备 |

需要配制 2% CTAB、0.5 M EDTA 或 1 M Tris·HCl，或核对其他试剂时，读取 [references/reagents.md](references/reagents.md)。Hermes 可通过 `skill_view(name="plant-genomic-dna-extraction", file_path="references/reagents.md")` 按文件加载，其余参考文件同理。

不同规格采用各自的操作参数，不按容器体积简单倍增或互换全部参数。第二次抽提按实验需要选择；未确定时可分别给出两种分支。

所有离心均在室温进行，转速保持以 rpm 表示。复溶液统一使用含 1% RNase 的纯水。50 mL 和 96 孔规格的第二次抽提与首次抽提均使用酚/氯仿/异戊醇（25:24:1）。

## 周期估算

三种规格均包括 65 °C 水浴 60 min、冷却至低于 15 °C、异丙醇沉淀、离心洗涤、风干和 37 °C 消化 RNA 约 60 min。风干为 4 h 以上或过夜；2 mL 和 50 mL 的低温沉淀为至少 30 min，96 孔为 30 min。

下表将串行定时步骤取最短值相加，沉淀按 30 min、风干按 240 min、RNA 消化按 60 min 计算，作为排期基准。

| 规格与分支 | 已知定时步骤的基准小计 |
| --- | --- |
| 2 mL | 442 min（7 h 22 min） |
| 50 mL，不做第二次抽提 | 502 min（8 h 22 min） |
| 50 mL，做第二次抽提 | 544 min（9 h 4 min） |
| 96 孔，不做第二次抽提 | 520 min（8 h 40 min） |
| 96 孔，做第二次抽提 | 562 min（9 h 22 min） |

小计未含试剂准备、取样、未定时的研磨/冷却/转移/洗涤操作、设备等待、复溶及检测；96 孔研磨的两次 4 min 已计入。按所选参考文件的时间区间、实际批次数、设备容量和人员安排补充排期；不要将整批水浴或离心时间简单乘以样本数，也不要假定所有批次都能并行。

若选过夜风干，按实际起止时间安排跨日流程。新增的操作时间或缓冲时间标为排期假设，不将上述小计直接作为完整实验周期。

## 检测、保存与输出

取 2 μL DNA，用 NanoDrop 检测 DNA 纯度。DNA 在 4 °C 短期保存，-20 °C 长期保存。

按用户所问输出所选规格、材料与设备要求、相关操作或配方；涉及周期时区分定时步骤、人工操作、等待和跨日安排，并标明估算假设。

## 方法来源文献

本方法参考以下文献，并结合实验流程作调整：

Murray, M. G., & Thompson, W. F. (1980). Rapid isolation of high molecular weight plant DNA. *Nucleic Acids Research*, 8(19), 4321-4326. DOI: [10.1093/nar/8.19.4321](https://doi.org/10.1093/nar/8.19.4321).
