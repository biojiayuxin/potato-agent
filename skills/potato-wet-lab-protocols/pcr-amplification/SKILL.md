---
name: pcr-amplification
description: PCR 普通与高保真扩增：Taq、Phanta、PrimeSTAR、KOD 选型、菌落筛选、配液和程序。
version: 1.0.4
author: Potato Agent
metadata:
  hermes:
    tags: [pcr, colony-pcr, taq, phanta, primestar, kod, wet-lab, protocol]
---

# PCR 普通扩增与高保真扩增

按实验目的选择普通扩增或高保真扩增，再按具体货号和剂型配制反应。收录 Vazyme、Takara Bio 和 TOYOBO 的产品说明书参数；同一系列的不同型号不共用配方或温度程序。

## 适用范围与输入

用于菌落 PCR、常规终点 PCR 检测、克隆片段与载体高保真扩增，以及相应试剂核算、程序选择和排期。这里的“普通扩增”指常规 Taq 类扩增；热启动、扩增速度和是否含上样染料是另外的产品属性。高保真酶也可以用于筛选，但不同产品的保真性、粗模板耐受性和速度并不相同。

不涵盖 qPCR 定量、逆转录反应、突变专用试剂盒或未收录型号的完整参数。引物设计任务可衔接已有 `primer-design`；ClonExpress、In-Fusion 的同源臂设计与组装分别衔接 `clonexpress-vazyme`、`in-fusion-takara`。扩增 cDNA 前核对反转录产品的适用范围，不能将现有 AU341 反转录技能中仅用于 qPCR 的产物默认用于常规 PCR。

按任务需要确认，已有信息直接采用：

- 目的：判断有无/大小、鉴定连接处或方向，还是制备将用于克隆和测序的片段。
- 模板类型、来源与纯化状态；液体模板确认浓度和投入量。菌落 PCR 默认直接挑取少量菌落，注明微生物种类即可，不要求提供菌落的 DNA 浓度或模板体积。cDNA 的 RNA 当量不等于 cDNA 实测质量。
- 目标长度、GC 特征；引物序列、储液浓度、模板结合区的 Tm 及其计算方法。带同源臂或接头时，初始退火按模板结合区判断。
- 商品全名、货号、单酶/Buffer/完整 Master Mix 剂型，以及是否带上样染料。仅有“Phanta”“PrimeSTAR”“KOD”不能确定配方。
- 每反应体积、样本与重复数、对照数、仪器容量和可用时段。

只有选型需求时直接比较产品。需要精确配液或程序而缺少型号、片段长度等关键输入时，先补齐相关信息；也可明确推荐一个具体产品作为条件方案，但不能假定用户手中试剂就是该型号。

## 分类、选型与按需读取

给出精确体系和程序前，读取所选产品文件；不默认加载全部说明书。菌落筛选另读 [references/colony-pcr.md](references/colony-pcr.md)。优先使用用户已有且适用的产品。

| 分类 | 产品、货号与剂型 | 主要用途与参考文件 |
| --- | --- | --- |
| 普通扩增 | Vazyme 2× Rapid Taq Master Mix，P222，完整预混液 | 常规快速检测；[P222](references/routine/vazyme-rapid-taq-p222.md) |
| 普通扩增 | TOYOBO Quick Taq HS DyeMix，DTM-101，2× 热启动含染料预混液 | 常规检测、大肠杆菌菌落直接筛选；[DTM-101](references/routine/toyobo-quick-taq-dtm-101.md) |
| 高保真 | Vazyme Phanta Max Super-Fidelity DNA Polymerase，P505，酶/Buffer/dNTP 分装 | 克隆用片段和载体扩增；[P505](references/high-fidelity/vazyme-phanta-max-p505.md) |
| 高保真 | Vazyme 2× Phanta Max Master Mix，P515，完整预混液 | 简化高保真配液；[P515](references/high-fidelity/vazyme-phanta-max-p515.md) |
| 高保真 | Takara PrimeSTAR HS DNA Polymerase，R010A，分装组分 | 常规高保真扩增；[R010A](references/high-fidelity/takara-primestar-hs-r010a.md) |
| 高保真 | Takara PrimeSTAR Max DNA Polymerase，R045A，2× 完整预混液 | 快速高保真扩增，速度受模板投入量影响；[R045A](references/high-fidelity/takara-primestar-max-r045a.md) |
| 高保真 | Takara PrimeSTAR GXL DNA Polymerase，R050A，分装组分 | 长片段、GC-rich 模板；高速分支需加倍酶量；[R050A](references/high-fidelity/takara-primestar-gxl-r050a.md) |
| 高保真 | TOYOBO KOD One PCR Master Mix / -Blue-，KMM-101/KMM-201，2× 完整预混液 | 快速扩增、部分粗模板；Blue 含上样染料；[KOD One](references/high-fidelity/toyobo-kod-one.md) |
| 高保真 | TOYOBO KOD -Plus- Neo，KOD-401，酶/10× Buffer/MgSO₄/dNTP 分装 | 高保真片段制备；[KOD-401](references/high-fidelity/toyobo-kod-plus-neo.md) |
| 高保真 | TOYOBO KOD FX Neo，KFX-201，酶/2× Buffer/dNTP 分装 | 粗模板、困难模板与长片段；[KFX-201](references/high-fidelity/toyobo-kod-fx-neo.md) |

普通 Taq 筛选阳性不代表序列无错误；克隆构建需测序确认。KOD FX Neo 具有校对活性，但不能将 KOD One 或 KOD -Plus- Neo 的保真性数据移用于它，也不按不同厂商的保真性倍数直接排名。Phanta Flash、PrimeSTAR Max Premix 新版本、KOD FX、KOD -Plus- 等其他型号需另查对应说明书，不自动套用本技能。

Hermes 调用示例：

```text
skill_view(name="pcr-amplification")
skill_view(name="pcr-amplification", file_path="references/routine/toyobo-quick-taq-dtm-101.md")
skill_view(name="pcr-amplification", file_path="references/high-fidelity/vazyme-phanta-max-p515.md")
```

## 延伸时间与循环数的默认规则

**估算 DNA 聚合酶速度及所需延伸时间时，统一按低丰度目标模板保守选择参数。** 这项原则同时用于推荐上机延伸时间和排期基准，避免延伸时间过短；不能只增加预约余量，却仍向用户推荐说明书的最快延伸条件。已有经验证的程序按用户指定保留，并单列保守排期。

1. 优先采用所选产品的低拷贝/低丰度延伸条件。没有专门低丰度分支时，选适用的常规或低产量调整分支中的较长延伸时间；时间范围默认取上限，并标明这是本技能的保守起始建议。没有依据的参数不编造，也不将其他酶的速度移用。
2. 同时满足目标长度、粗模板、GC 特征及总核酸投入量对应的要求，不能因低丰度假设缩短其他分支已经要求的延伸。低目标丰度、粗模板和高总核酸量是不同属性，不据此改写用户的样本类型或投入量。纯化模板、短片段或质粒模板也不自动获得最快分支。
3. 先确定上述每循环延伸时间 `E`，再按[周期估算](#周期估算)的半速规则，以 `2E` 预算延伸阶段。低丰度条件的选择和半速预算各应用一次；低丰度条件本身不是已经做过半速折减。上机推荐值和排期预算分别列出，不能用增加循环数替代足够的延伸时间。

菌落 PCR 的循环数与延伸时间分别选择。对常见质粒克隆筛选，本技能采用以下默认建议；它们是实验设计建议，不是所有产品说明书的统一规定：

| 菌落宿主 | 默认循环数 | 选择依据 |
| --- | --- | --- |
| 大肠杆菌 | **25 个循环** | 常见克隆质粒拷贝数较高，减少过度扩增造成的非特异条带及假阳性判读风险 |
| 农杆菌 | **35 个循环** | 常见载体在农杆菌中拷贝数较少，为低拷贝目标保留更多扩增循环 |

这两项建议不把所有载体的拷贝数视为相同；若已知为低拷贝大肠杆菌载体、基因组靶点或用户已有验证程序，按实际条件说明调整。**大肠杆菌选 25 个循环时，延伸时间仍按低丰度原则估算。** 农杆菌的 35 个循环建议不替代其取样/裂解依据。按[菌落 PCR](references/colony-pcr.md)核对产品适用性、对照及循环总数；改变循环数后重算周期，不沿用 30 循环示例。

## 实验前准备

给出完整操作或排期时，先列出所选方案的准备清单和就绪条件；单独问配液或某一步时，仅列相关准备。

| 类别 | 准备要求 |
| --- | --- |
| 材料 | 已编号的模板、目标长度、引物及浓度；菌落筛选需提前培养出可挑取的独立菌落，并安排保留对应克隆。直接挑菌无需先提取、定量或稀释模板；液体模板所需的提取、定量及稀释提前完成 |
| 仪器 | 能执行所选温度和短时保温程序的 PCR 仪，匹配热盖、管型及板架；合适量程的移液器、微量离心机、冰盒。检测另备电泳槽、电源及成像设备 |
| 耗材 | 薄壁 PCR 管/八连管/反应板及盖或封膜，滤芯吸头、配液管、管架；菌落取样另备无菌吸头或牙签及保种耗材 |
| 试剂 | 对应货号的酶与配套组分、ddH2O（无核酸酶）、引物；凝胶、DNA Marker、上样缓冲液按检测需要准备。完整 Mix 不再常规补加 Mg²⁺、dNTP 或酶；2× Buffer 本身不是完整 Mix |
| 就绪条件 | 核对瓶签和所用说明书版本，按产品要求解冻、混匀、短暂离心并放置；标记孔位与对照，复核程序、体积和仪器容量。PCR 前配液区与扩增产物处理区分开 |

**菌落 PCR 默认直接以少量菌落为模板，菌体不计入反应液体积。** 液体组分用 ddH2O 配足反应终体积后分装，再分别挑菌；配方中的模板写“少量菌落（不计体积）”，不预留 1–2 μL 模板体积。只有明确使用菌悬液、裂解液或提取的 DNA 溶液时，才按实际加入体积扣减 ddH2O。产品公式中的液体模板体积 `x`，在直接挑菌计算时取 0；这表示不扣液体体积，样本管仍要加入菌落。

一般设置无模板对照（NTC）：直接挑菌时，NTC 使用同样配足终体积的液体体系但不挑菌，无需额外补一份“模板替代水”；液体模板方案才用等体积 ddH2O 替代模板。按任务需要设置已知可扩增的阳性对照，菌落鉴定还可设置空载体对照。阳性对照若用 DNA 溶液，须单独按其加入体积扣水。这些是实验设计建议，不宣称所有厂家均规定了同一套对照。

批量配液设实验反应（含重复）为 `n`、NTC 为 `k`、其他对照为 `p`，总孔数 `R = n + k + p`。仅将组成相同的反应合并配制公共混合液；不同引物或不同程序分别分组。公共组分净需求为单反应用量乘相应反应数，移液余量另列比例或额外份数，不默认为模板也加同样余量。每管单独核算模板与补水量：

```text
液体模板体积（μL）= 投入质量（ng）/ 浓度（ng/μL）
引物体积（μL）= 目标终浓度（μM）× 反应体积（μL）/ 储液浓度（μM）
ddH2O 体积 = 反应终体积 − 其他全部液体组分体积之和（直接挑取的菌落不计）
```

补水不得为负；过小加样量先制备适当工作液再重算。参考文件主要保留原说明书的 50 μL 体系；DTM-101 另有原文 20 μL 体系。其他体积若按浓度等比例换算，要标明“换算体系”，同步核对模板限量、仪器及产品适用范围，不把循环数或温度按体积缩放。

## 周期估算

以材料、试剂和仪器已就绪后开始配液为起点，以 PCR 结束、取出产物为终点；单批为相同程序下不超过仪器有效容量的 `R` 个反应。**默认按说明书扩增速度的 50% 估计排期，即每循环延伸时间按原值的 2 倍预算。** 这是本技能采用的保守排期假设，考虑模板质量等带来的调整需要，不是厂商实测性能或成功保证。

先按[低丰度原则](#延伸时间与循环数的默认规则)确定适用的延伸分支和循环数，再进行折减。保留产品文件中的原始配方与程序；给出实验方案时，分别标注“按说明书参数选定的上机建议及保温小计”和“保守排期预算”，默认以后一项安排时间。

```text
设 E 为按低丰度原则选定的说明书延伸秒数，C 为实际选定的循环数：
排期延伸时间 E预算 = E / 0.5 = 2E
说明书程序保温小计 T原 = 预变性 + Σ每循环(变性 + 退火 + E) + 终延伸
保守保温预算 T预算 = 预变性 + Σ每循环(变性 + 退火 + 2E) + 终延伸
同一延伸时间的程序：T预算 = T原 + C × E
完整排期 = T预算 + 配液/取样 + 仪器升降温与等待 + 所需检测/纯化/保存处理
```

- 延伸以 s/kb 或 min/kb 表示时，先按目标长度求 `E` 再加倍；速度减半不是时长增加 50%，也不是整个实验周期翻倍。
- 按长度档固定的循环延伸也加倍预算，例如 GXL 长片段 10/15 min → 20/30 min。KOD One 默认按低拷贝的 10 s/kb 选延伸，再按 20 s/kb 预算；不默认采用短片段 1 s → 2 s 的快速示例。阶梯降温逐段核算。
- 预变性、循环变性、退火、终延伸、循环数及温度保持所选程序的值；这些阶段不因扩增速度折减而统一翻倍。可选预变性和终延伸仅在采用该分支时另计。
- 原说明书给出时间范围时，可列原范围和对应预算范围，但完整方案默认按低丰度原则选其适用上限，并据此给出明确的延伸时间和预约时长。粗模板/高核酸量先满足其较慢分支，不能用最快模式折减后替代。
- PCR 仪按录入程序计时，不会因 DNA 质量差而自动延长运行。这里的 `T预算` 是预留时间；若将延长后的时间用于上机程序，要明确这是调整方案，结合产物特异性及产品适用条件决定，不能标成说明书原值。
- 用户已有验证程序或仪器显示时长时，按已设程序报告实际运行时间；保守预约余量另列。只有已明确按本技能的 50% 速度排期规则加倍延伸时，才不再重复折减；厂家的较慢分支仍须先选定，再应用本规则。缺少与原始程序的对应关系时，不声称已按某一倍率调整。仪器显示时间若包含升降温，不重复加这一项。

下表以 **1 kb、30 个循环、按低丰度原则选定的分支**比较，保守保温预算约 **0.29–1.53 h**。30 循环仅用于横向计时示例；菌落 PCR 按大肠杆菌 25、农杆菌 35 个循环等实际方案重算。主体扩增无需规定性的过夜孵育，但长片段预算可能超过工作日，需按实际起止时间安排；菌落培养可能跨日且应提前另计。

| 产品与原始程序分支 | 说明书程序保温小计 | 默认保守保温预算 |
| --- | --- | --- |
| P222，1 kb 标准三步法，15 s/kb；>1 kb 时按参考文件选较长延伸 | 30.5 min | 38 min |
| DTM-101，三步法，1 min/kb | 62 min | 92 min |
| P505 / P515，三步法，取 60 s/kb | 53 min | 83 min |
| PrimeSTAR HS，三步法，退火 5 或 15 s | 37.5 或 42.5 min | 67.5 或 72.5 min |
| PrimeSTAR Max，三步法，按低产量调整范围取 60 s/kb，退火 5 或 15 s | 37.5 或 42.5 min | 67.5 或 72.5 min |
| PrimeSTAR GXL，标准三步法，1 min/kb | 42.5 min | 72.5 min |
| KOD One，低拷贝三步法，10 s/kb | 12.5 min | 17.5 min；未加可选预变性和终延伸 |
| KOD -Plus- Neo，低拷贝两步法/三步法，1 min/kb | 37 / 52 min | 67 / 82 min |
| KOD FX Neo，两步法，按低产量调整范围取 1 min/kb；粗模板同值 | 37 min | 67 min |

两列都**不等于完整实验周期**：配液、挑菌、短暂离心、仪器升降温及等待、凝胶制备、电泳、成像、纯化和保存处理均另计。短秒级程序的升降温占比尤其明显。单批时间不乘管数；不同程序或超出容量时按设备数量和批次排期。新增人工/设备耗时要列为估算假设或采用用户给定数据，不设未经确认的中途过夜暂停点。

## 检测、保存与下游衔接

凝胶检测目标大小、杂带及 NTC/阳性对照；凝胶浓度、上样量和运行条件按目标长度及实验室既定电泳方案选择。本技能不将通用电泳条件冒充为各酶说明书规定。含上样染料的产品按瓶签及对应文件直接上样；不含染料的产物需另加适用上样缓冲液。PrimeSTAR 系列按所录说明书推荐使用 TAE。

菌落条带只支持与引物布局相对应的判断，不能代替连接处或全插入片段测序。高保真产物多为平末端，TA 克隆按相应方案先去除校对酶再加 A；需要限制酶处理或测序时，按产品文件完成必要纯化。PCR 线性化载体涉及环状模板残留时，模板去除与后续组装按对应克隆技能及所用处理酶说明书安排，不擅自套用统一 Dpn I 条件。

反应后及时衔接检测或纯化。所录说明书未统一给出 PCR 产物的保存时限；暂存采用已确认的下游试剂说明或实验室 SOP。产品文件中的 −20 °C、4 °C 及其保存期限均指相应试剂，不自动用于 PCR 产物。

## 方法来源文献

以下为厂商官方站点提供的说明书，检索日期 **2026-10-01**；版本取文件内标识，不把检索日期当作出版日期。Vazyme PDF 链接来自官方产品页的 Product Manual 下载项。下列原文链接用于查证，日常配液按需读取本技能的中文产品文件即可。

1. Vazyme Biotech Co., Ltd. *2 × Rapid Taq Master Mix*，P222，**Version 22.2**，第 1–2 页。[官方产品页](https://www.vazymeglobal.com/product-center/rapid-pcr/2-rapid-taq-master-mix)；[说明书 PDF](https://vazyme-singapore-website-prod.s3.ap-southeast-1.amazonaws.com/175db66af1ad42e2bb8c6dcb9f101111)。
2. TOYOBO Co., Ltd. *Quick Taq HS DyeMix*，DTM-101，文件标识 **2004 / F1138K**，§1–7。[说明书 PDF](https://www.toyobo-global.com/sites/default/static_root/products/lifescience/support/manual/DTM-101.pdf)。
3. Vazyme Biotech Co., Ltd. *Phanta Max Super-Fidelity DNA Polymerase*，P505，**Version 23.1**，第 1–2 页。[官方产品页](https://www.vazymeglobal.com/product-center/high-fidelity-pcr/phanta-max-super-fidelity-dna-polymerase)；[说明书 PDF](https://vazyme-singapore-website-prod.s3.ap-southeast-1.amazonaws.com/57ae99c31eeb4607acd6e6791300aa28)。
4. Vazyme Biotech Co., Ltd. *2 × Phanta Max Master Mix*，P515，**Version 23.1**，第 1–2 页。[官方产品页](https://www.vazymeglobal.com/product-center/high-fidelity-pcr/2-phanta-max-master-mix)；[说明书 PDF](https://vazyme-singapore-website-prod.s3.ap-southeast-1.amazonaws.com/36a5cbeada6647e0afc093ac43ea6eea)。
5. Takara Bio.《PrimeSTAR HS DNA Polymerase 说明书》，R010A，**v201908Da**，正文第 4–6 页。[说明书 PDF](https://www.takarabiomed.com.cn/profile/Manual/R010A.pdf)。
6. Takara Bio.《PrimeSTAR Max DNA Polymerase 说明书》，R045A，**v202011Da**，正文第 1–3、7–8 页。[说明书 PDF](https://www.takarabiomed.com.cn/profile/Manual/R045A.pdf)。
7. Takara Bio.《PrimeSTAR GXL DNA Polymerase 说明书》，R050A，**v201908Da**，正文第 1–5 页。[说明书 PDF](https://www.takarabiomed.com.cn/profile/Manual/R050A.pdf)。
8. TOYOBO Co., Ltd. *KOD One PCR Master Mix / KOD One PCR Master Mix -Blue-*，KMM-101/KMM-201，文件标识 **2502 / F2154K**，§4–8。[说明书 PDF](https://www.toyobo-global.com/sites/default/static_root/products/lifescience/support/manual/KMM-101_201.pdf)。
9. TOYOBO Co., Ltd. *KOD -Plus- Neo*，KOD-401，文件标识 **2004 / F1066K**，§4–6、8。[说明书 PDF](https://www.toyobo-global.com/sites/default/static_root/products/lifescience/support/manual/KOD-401.pdf)。
10. TOYOBO Co., Ltd. *KOD FX Neo*，KFX-201，文件标识 **2004 / F1100K**，§4–7、9。[说明书 PDF](https://www.toyobo-global.com/sites/default/static_root/products/lifescience/support/manual/KFX-201.pdf)。
