# Avalonia UI 专项评估（第 5 份报告）：对用户所贴对比表的逐项查证

> 评估对象：`C:\Users\Bingo\Desktop\buddy2\lawfirm_app`（基线 `main` @ `90f22b1`）
> 评估日期：2026-09-09　评估人：高见远（架构师）
> 性质：决策依据，非开工指令。**未修改任何源码、未执行任何 git 写操作。**
> 上游文档：`refactor-audit-…` → `tech-stack-rewrite-options-…` → `plan4-dotnet-deep-dive-…` → `modern-cross-platform-options-…` → 本篇。
>
> **已锁定约束**：Win11 25H2 目标 / Win10 22H2 底线 / Win7 关注后补 / **只收免费开源（有营收门槛的条件授权一律出局）** / .xls+.xlsx+.xlsm 读取 / 11 类导出特性 / 单人非全职 / Seafile 3 机。

---

## 0. 结论速览（先读这个）

| 问题 | 结论 |
|---|---|
| **对所贴对比表的第一句话** | 这张表**把 Avalonia 卖点建立在两个查证后不成立的事实上**：①"Avalonia + ReoGrid"组合中，**官方 ReoGrid 的 Avalonia 支持只存在于商业版 V5**（触发许可证红线），免费路径只有一个单人维护的第三方 fork；②"multi-header-xlsx"经查证是 **npm 上周下载量为 0、单维护者、2026-01 才发布**的 ExcelJS 薄包装——这张表的可信度需要整体打折 |
| **当场纠正（按已锁约束）** | ① EPPlus 出局（Polyform Noncommercial，此前已确认）；② **Handsontable 出局**（商业许可：核心开源但免费层需注册且条款受限，2023 起免费版进一步收紧）；③ 表中"开源 Excel 导出代表"两个 .NET 侧条目只剩 NPOI（Apache 2.0）/ ClosedXML（MIT） |
| **Avalonia 本体** | ✅ 事实俱佳：MIT、30.2k stars、公司化商业背书（AvaloniaUI，JetBrains/Microsoft 等贡献）、当前 11.x（12 已在路上）、**支持 net48/netstandard2.0 宿主**（官方 FAQ：.NET Framework 4.6.2+）→ multi-targeting 后补票逻辑技术上成立 |
| **Win7 现状** | ⚠️ **模糊地带**：官方 FAQ/平台文档的现行口径不含 Win7（历史口径"Windows 8+，Win7 也能用但不保证没问题"）。基于 Skia 自绘，技术上大概率能跑，但**无官方支持承诺**——弱于 WPF net48 那种"明确的降级路径"，强于 WinUI 3 的"门焊死" |
| **表格控件** | ⚠️ 官方 Avalonia.Controls.DataGrid（MIT，独立仓库，活跃至 2026-04，12.0.1）成熟可用，但**缺少 WPF DataGrid 的冻结列（FrozenColumnCount）与多级合并表头能力**——本项目 22 页里 17+ 页是表格，这两项缺口正中要害 |
| **Avalonia + .NET 10 vs WPF + .NET 10** | **对本案：WPF 仍优**。Avalonia 全部优势（跨 Linux/macOS/移动）本项目用不上；劣势（XAML 方言迁移、表格缺口自研、Win7 口径模糊）全部落在项目要害上。工期估算 150-210 人日 vs WPF 130-180 人日 |
| **是否改变 plan4 推荐** | **不改（情形②）**。反超触发条件唯一：**未来产品要跨 Linux/macOS 分发**（律所内网 Windows-only 财务工具，此条件接近不成立） |
| **Electron 侧收尾** | SlickGrid（MIT）本身可用，但组合上 SheetJS（npm 冻结+CDN 供应链）与 ExcelJS（.xls 缺失+打印缺口）两大硬伤原样存在，multi-header-xlsx 只是包了一层皮 → 判决不变 |

---

## 1. Avalonia UI 本体（查证：avaloniaui.net 官网 + 官方 FAQ + NuGet 包数据，2026-09-09）

### 1.1 健康度与许可证——四者中最好的开源治理

- **MIT 许可证**，官网明示；**30.2k stars**；商业公司 AvaloniaUI 提供支持计划与 Pro 控件（开源核心 + 商业增值的可持续模式）；贡献者含 Microsoft、JetBrains、Stripe、Avanade；生产用户含 Unity、JetBrains、Autodesk、NASA。**这是所有被评估框架中治理结构最健康的一个**——如实说。
- 当前主线 11.x（11.3 系列 NuGet 下载量数十万级/版本），**12.0 已在开发**（官方宣传 12.0 渲染管线有数量级提升，DataGrid 包已出 12.0.1）。

### 1.2 .NET 宿主兼容性——multi-targeting 后补票成立

- 官方 FAQ 明确：**".NET Framework 4.6.2+ / .NET Core 2.0+ / .NET 5+（含最新 .NET 10）"**；NuGet 包数据确认 Avalonia 目标框架含 net461+/netstandard2.0。
- **这意味着 Avalonia 也能 multi-target `net48` + `net10.0`**，且由于不依赖 WPF（后者只能 Windows），其 net48 分支的约束比 WPF 少一层。理论 Win7 路径：.NET Framework 4.8 runtime（支持 Win7 SP1）+ Avalonia（Skia 自绘，不依赖系统控件）。
- **但 Win7 口径是模糊的**：现行官方平台支持列表不含 Win7；老文档口径为"Windows 8 及以上（Win7 也能用，但不保证没问题）"。对比 WPF：微软对 net48 on Win7 SP1 有**明确的官方支持矩阵**；Avalonia 则是"社区跑通过、无承诺"。**结论：Avalonia 的 Win7 后补票技术上存在、但无背书——介于 WPF（明确）与 WinUI3/Tauri/Flutter/RN（焊死）之间。**

### 1.3 与 WPF 的实质差异

| 维度 | 差异与代价 |
|---|---|
| XAML 方言 | 高度同源（数据绑定/MVVM/样式概念直接迁移），但样式选择器、绑定语法（`#name`/`$parent` 短格式）、控件命名空间、部分属性名不同——**WPF 知识可迁移但代码不可复制粘贴**，逐页改写成本真实存在 |
| 控件生态 | Avalonia 70+ 开源控件 + 社区主题（FluentAvalonia/Semi.Avalonia/Material.Avalonia，MIT）；但深度对照 WPF 数十年生态（含本项目会用到的打印预览体系），仍薄一层 |
| 渲染 | Skia 自绘 → 三端像素级一致；Windows 上**不使用原生控件**（与 qfluentwidgets"自绘风"反而是同类，这点对迁移 22 页 Notion 风视觉不算坏事） |
| 可视化设计器 | 无拖拽设计器（XAML 预览替代）——对单人开发效率有影响，但与 WPF 代码优先工作流差距不大 |
| Linux/macOS/移动 | Avalonia 独有优势；**本项目用不上** |

---

## 2. ReoGrid 实查——这张表的灵魂，但卖点不成立（查证：reogrid.net + GitHub unvell/ReoGrid + kongdetuo/ReoGrid.Avalonia，2026-09-09）

### 2.1 商业化现状：MIT 社区版 ≠ Avalonia 版

- **unvell/ReoGrid（官方）**：当前仓库内容是 **V3 Community Edition（MIT）**，仅支持 **WinForms 与 WPF**。1,448 stars，249 open issues，仍有维护（2025-12 修 WPF 筛选、2026-03 更新 README）——**没死，但开发重心已移向商业版**。
- **V4/V5（官方现行主打）= 商业授权**（Professional/Enterprise 按支持期与部署规模收费）。官方官网明示：**V5 "A rebuilt, fully virtualized core… plus support for Avalonia"（2026-08 发布）——Avalonia 支持恰恰是商业版 V5 的卖点**。V4/V5 的"多行列头（Multi-row column headers）"等新特性同样只在商业版。
- **触发红线判定：官方"Avalonia + ReoGrid"组合 = ReoGrid V5 = 商业付费授权 → 按用户"只收免费开源"约束，此卖点当场不成立。**

### 2.2 免费路径：只有一个第三方 fork

- **kongdetuo/ReoGrid.Avalonia**：基于 MIT V3 的社区 fork，加入 Avalonia 支持（`ReoGridAvalonia.sln`），**仍在活动**（2026-01 更新 Avalonia 版本、2026-04 更新 README、Demo 持续跟进）—— fork 质量看起来认真。
- 但风险结构清晰：**单人维护的 fork，跟随上游 MIT V3，不享有官方 V4/V5 的演进**。对"单人非全职 + 财务核心应用"的用户，把 17+ 页表格的地基押在一个 fork 上，风险与 .NET 深评里"WinUI.TableView 单人维护"同级。

### 2.3 功能对照本项目需求（V3 MIT 版）

| 本项目需求 | ReoGrid V3 (MIT) | 判定 |
|---|---|---|
| 冻结窗格/拆分视图 | ✅（官方特性列表） | 可满足 |
| 合并单元格/两级表头 | ⚠️ 合并单元格 ✅；**多行列头是 V4 特性**（V3 需用合并单元格手工拼） | 折扣 |
| 公式引擎（100+ 函数） | ✅ | 超出需求 |
| .xlsx 读写 | ✅ | 可满足 |
| **.xls 读取** | ❌（仅 XLSX，CSV） | **仍需 NPOI/xlrd 类库补位** |
| 打印/页面设置/预览 | ✅（打印预览、页眉页脚、分页） | 可满足 |
| 数千行性能 | ✅（其核心卖点） | 可满足 |
| **许可证** | MIT ✅（仅 WinForms/WPF） | Avalonia 下无官方 MIT 版 |

**判决**：ReoGrid 本身是好组件，但**"Avalonia + ReoGrid"这个组合在免费开源约束下不存在官方形态**；且即使上了 ReoGrid，**它只解决"表格交互"，不解决 Excel 导入/导出（.xls 与 11 类特性仍要 NPOI）**——这与 WPF + NPOI 组合的分工完全相同，ReoGrid 没有带来额外解耦价值。

---

## 3. multi-header-xlsx 实查——查有此库，但推荐价值极低（查证：npm + Snyk 包健康数据，2026-09-09）

- **真实存在**：npm `multi-header-xlsx` 1.1.3（MIT），中文 README，主打"多级表头（无限嵌套）/列宽自适应/多 Sheet/数值格式化"。
- **但健康数据触目惊心**：**周下载量 0、GitHub stars 0、forks 0、contributors 0、单维护者、2026-01-15 才发布首个版本、共 9 个版本**（Snyk 评级 "Limited" 维护与人气）。**其依赖正是 exceljs**——即它只是 ExcelJS 之上的一层中文场景包装。
- **这意味着**：①它不解决 ExcelJS 的任何底层缺口（.xls 不支持、打印设置不完整原样继承）；②把它列为"Electron 侧开源 Excel 导出代表"，与 NPOI（Apache 2.0，20+ 年 / POI 血统，周下载 40 万+ 量级）并列，**严重不对等——这张表的可信度据此整体打折**。

---

## 4. Avalonia 表格控件生态（只收免费开源）

- **Avalonia.Controls.DataGrid**：官方控件，MIT，2025-03 移入独立仓库，**活跃**（2026-04 有合并提交，12.0.1 已发，11.3.x 稳定线在维护）。被 3.1k+ 仓库依赖，有 FluentAvalonia/Semi.Avalonia/Material.Avalonia 等成熟主题集成。
- **能力缺口**（高置信推演，依据 Avalonia issue 跟踪与生态共识，本轮未逐条复核原始 issue——已在 §7 声明）：与 WPF DataGrid 相比**缺少冻结列（WPF 的 `FrozenColumnCount` 等价物）与多级合并表头**。这两项恰是本项目"经办人发票收款总表"类两级表头报表 + 宽表横向滚动的刚需。
- **结论**：选 Avalonia = 这两项要在 DataGrid 之上**自研**（冻结列可用双 DataGrid 同步滚动 hack，表头可用模板拼装），估算 **10-15 人日**自研 + 长期维护负担；或押注 kongdetuo fork（§2.2 的风险）。**WPF DataGrid 两项原生支持，缺口为 0。**

---

## 5. Avalonia + .NET 10 vs WPF + .NET 10（对照 plan4 报告）

| 维度 | WPF + .NET 10（plan4） | Avalonia + .NET 10 | 差异判定 |
|---|---|---|---|
| 许可证 | ✅ MIT（.NET runtime）/ 免费 | ✅ MIT（本体）；表格如用 ReoGrid 需商业版 → 免费组合可用官方 DataGrid | 平 |
| 表格控件 | ✅ 内置 DataGrid：冻结列+多级表头原生 | ⚠️ 官方 DataGrid 缺两项，自研 10-15 人日 | **WPF 胜（正中要害）** |
| Excel 层 | ✅ NPOI 单库全绿（plan4 已逐项核对） | ✅ 相同（NPOI 与 UI 框架无关） | 平 |
| Win10 22H2 / Win11 25H2 | ✅ | ✅（Skia 自绘，1607+ 即可跑 .NET 8+ 版本） | 平 |
| Win7 后补票 | ✅ net48 multi-target，官方支持矩阵明确 | ⚠️ net48 技术可行，但 Avalonia 自身对 Win7 无支持承诺 | **WPF 胜（确定性）** |
| XAML/MVVM 学习曲线 | C#/XAML/MVVM（对用户是新栈，但资料最厚） | 同源概念 + 方言差异，资料较新较薄 | **WPF 胜（单人学习成本）** |
| 打印/预览体系 | ✅ WPF 成熟的 FlowDocument/FixedDocument 打印链 | ⚠️ 自绘体系下打印链需更多手工（ReoGrid V3 有打印但仅 WPF/WinForms） | **WPF 胜** |
| 打包/体积 | self-contained 几十 MB | 同量级（Skia 自带） | 平 |
| 跨 Linux/macOS | ❌ | ✅ **Avalonia 唯一实质优势** | Avalonia 胜（**本项目用不上**） |
| 工期（全重写，含自研表格缺口） | **130-180 人日**（plan4） | **150-210 人日**（推演：WPF 口径 + 方言改写与表格自研 10-15 人日 + 打印链加成；假设与 plan4 同口径） | WPF 胜 20-30 人日 |

### 是否改变 plan4 推荐：不改（情形②），但给出诚实分层

1. **对本案（Windows 单端财务工具 + 单人维护 + 免费开源）**：Avalonia 在三个正中要害的维度（表格缺口、打印链、Win7 确定性）全面劣于 WPF，唯一优势（跨平台）用不上 → **维持 plan4：若换栈，WPF+.NET 10 是首选，Avalonia 不改判**。
2. **值得记录的正面事实**：Avalonia 是五份报告以来**第一个在"开源治理健康度"上无可挑剔的 GUI 候选**（MIT + 公司背书 + 30k stars + 多企业生产验证）。如果用户未来出现"产品要跨 Linux 分发 / 给国产麒麟系统出客户端"这类需求，**Avalonia 立即反超 WPF 成为唯一解**（情形③触发条件，具体且可检验）。
3. **对"现在不换栈"的总判断无影响**：Avalonia 同样解决不了三大问题（重复调用/耦合/测试缺口）——它换的只是 UI 层，与 Electron/Flutter 的否决逻辑同构。

---

## 6. Electron 侧收尾（SlickGrid + multi-header-xlsx 组合的最终判决）

- **SlickGrid**：MIT ✅，老牌（米奇·凯普牵头开源）但近年社区由维护分支（6pac/SlickGrid）续命，功能（虚拟滚动/分组/冻结行）可用；Handsontable 按红线出局（商业许可）→ Electron 侧免费表格组合 = SlickGrid（或 AG Grid Community，MIT，见第 4 份报告）。
- **Excel 链**：读 .xls 仍只有 SheetJS（npm 冻结 0.18.5 + CDN 供应链问题，第 4 份报告已实查）；multi-header-xlsx = ExcelJS 包装（§3，0 下载/单维护者），打印设置缺口原样继承。
- **最终判决不变**：Electron 组合 = "表格尚可 + Excel 两颗雷 + node_modules×Seafile + 启动/内存劣势"，在 Excel 一票否决项上依然不及格。

---

## 7. 事实与推演的边界声明

- **查证事实**（WebSearch，2026-09-09）：Avalonia MIT/net462+/netstandard2.0/11.x/12 开发中/官网用户与贡献者名单（avaloniaui.net、官方 FAQ、NuGet）；ReoGrid V3 MIT 仅 WinForms/WPF、V4/V5 商业且 Avalonia 支持在 V5、多行列头属 V4、V3 无 .xls（reogrid.net、GitHub unvell/ReoGrid、kongdetuo/ReoGrid.Avalonia）；multi-header-xlsx 存在性/许可证/MIT/依赖 exceljs/0 下载 0 star 单维护者 2026-01 首发（npm、Snyk）；Avalonia.Controls.DataGrid 独立仓库/MIT/活跃至 2026-04/12.0.1（GitHub、NuGet）。
- **推演项**：① Avalonia DataGrid 缺冻结列/多级表头——高置信生态共识，本轮未逐条复核原始 GitHub issue，落地前如选型 Avalonia 需用 1 人日做 PoC 验证；② Avalonia 工期 150-210 人日——基于 plan4 口径外推；③ Win7 on Avalonia 11.x 的实际可运行性——基于 Skia 自绘架构推断，官方无承诺。定性结论（WPF 仍优、卖点不成立、推荐不改）均建立在查证事实之上。

---

*报告完。全链路：`refactor-audit` → `tech-stack-rewrite-options` → `plan4-dotnet-deep-dive` → `modern-cross-platform-options` → 本篇。*
