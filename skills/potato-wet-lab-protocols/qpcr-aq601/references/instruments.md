# AQ601：仪器适配与荧光采集

## 参比染料选择

下表按 AQ601 Version 2.0 说明书列示机型整理。配方使用试剂盒随附的 **Universal Passive Reference Dye（50×）**，不据仪器品牌自行改用其他产品的高浓度或低浓度 ROX 配方。

| 参比染料分组 | 说明书列示机型 |
| --- | --- |
| 添加配套参比染料 | ABI Prism 7000、7300、7700、7900；ABI Step One、Step One Plus；ABI 7900HT、7900HT Fast |
| 添加配套参比染料 | ABI Prism 7500、7500 Fast；ABI QuantStudio Dx、3、5；ABI QuantStudio 6、7、12K Flex；ABI ViiA 7 |
| 添加配套参比染料 | Stratagene Mx3000P、Mx3005P、Mx4000 |
| 不添加参比染料 | Roche LightCycler 480、Light Cycler 96；MJ Research Chromo4、Opticon 2；Takara TP-800 |
| 不添加参比染料 | Bio-Rad iCycler iQ、iCycler iQ5、CFX96、C1000 Thermal Cycler |
| 不添加参比染料 | Thermo Scientific Pikoreal 96；Qiagen Corbett Rotor-Gene 6000、Rotor-Gene G、Rotor-Gene Q、Rotor-Gene 3000；Mastercycler ep realplex |

添加时，每 20 μL 反应加入 0.4 μL 50× 参比染料，终浓度为 1×；省略时按 [reagents.md](reagents.md) 补水至 20 μL。软件中的参比校正选项应与实际加入的试剂及仪器要求一致。

使用前核对机型铭牌、荧光检测模块及软件版本。表中保留说明书的型号表述；例如 C1000 Thermal Cycler 名称本身不能确认已配置实时荧光检测模块。未列示型号或实际设备名称与表中不一致时，需查对应仪器和当前试剂说明书，不能仅凭品牌或相近名称推断兼容性。

## ABI 仪器的采集时间

说明书对以下机型单列了荧光信号采集步骤的时间。**这是循环内采集所在温度步骤的保温设置，不是在该步骤后再增加一段同样时长。**

| 仪器 | 说明书给出的采集时间 |
| --- | --- |
| ABI Prism 7700、7900 | 30 s |
| ABI Prism 7000、7300 | 31 s |
| ABI Prism 7500 | 34 s |
| ABI Prism ViiA 7 | 至少 19 s |

- **两步法：** 在 60 °C 退火/延伸阶段采集。以原程序的 30 s 为基础，满足对应机型采集要求；7000/7300 设为 31 s，7500 设为 34 s。ViiA 7 的“至少 19 s”不构成将反应阶段从 30 s 缩短至 19 s 的依据。
- **三步法：** 可在 50–60 °C 退火阶段或 72 °C 延伸阶段采集。选定其中一个采集位置，将该阶段保温时长调整至满足机型要求，同时保留未选阶段的原时长。例如 ABI Prism 7500 在延伸阶段采集时，每循环为 94 °C 5 s、所选退火温度 15 s、72 °C 34 s。
- **未单列采集时间的机型：** 按该仪器当前说明及实际软件要求设置；参比染料表列入某机型，不代表其采集时间已在本说明书中给出。不将 7500 的 34 s 自动套用到 7500 Fast 等其他型号。

## 上机前检查

确认 SYBR Green I 适用的检测通道、实际反应体积、光学耗材、热盖和模块配置，以及仪器要求的校准状态。核对参比染料与软件参比设置，确认每个循环的荧光采集位置及实际保温时长。热盖温度、升降温速率和软件参数名称按具体仪器设置，不补写为 AQ601 的统一参数。

两种程序均需在循环结束后设置熔解曲线。AQ601 说明书仅标示 Dissociation Stage，未给出起止温度、升温步长、速率或各步停留时间。完整上机方案须从相应仪器说明或已验证方法补齐这些参数，并将实际运行时长计入排期。
