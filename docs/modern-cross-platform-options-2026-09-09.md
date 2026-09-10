# 现代跨端框架专项补充评估：Electron / Tauri / Flutter Desktop / React Native Windows

> 评估对象：`C:\Users\Bingo\Desktop\buddy2\lawfirm_app`（基线 `main` @ `90f22b1`）
> 评估日期：2026-09-09　评估人：高见远（架构师）
> 性质：决策依据，非开工指令。**未修改任何源码、未执行任何 git 写操作。**
> 上游文档：`docs/tech-stack-rewrite-options-2026-09-09.md`（方案 6 已评过 TS+Electron/Tauri，本篇只复核 + 补齐 Flutter/RN + 横向收口）；`docs/refactor-audit-2026-09-09.md`（审计）；`docs/plan4-dotnet-deep-dive-2026-09-09.md`（.NET 深评）。
>
> **已锁定约束**：目标 Win11 25H2，兼容底线 Win10 22H2，Win7 主线放弃但关注"后补可能性"；**只收免费开源（MIT/Apache/BSD/GPL），有营收门槛的 Syncfusion Community License 同样出局**；.xls/.xlsx/.xlsm 读取 + 33 sheet 导出 11 类 openpyxl 特性为硬需求；单人非全职维护；3 台 PC Seafile 同步。

---

## 0. 结论速览（先读这个）

| 方案 | 一句话判决 |
|---|---|
| **Electron** | ❌ 否决。Excel 生态双重硬伤：唯一能读 .xls 的 SheetJS CE **npm 上冻结在 0.18.5（含两个未修复 CVE）**，修复版只能从自家 CDN 以 tarball 安装（供应链脱离 npm 审计体系）；ExcelJS 不支持 .xls 且打印设置不完整。Electron 23 起弃 Win7，后补票 = 锁死在一个 2023 年已停安全更新的旧版本 |
| **Tauri 2.x** | ❌ 否决。比 Electron 少了 Node 侧 xlsx 生态，Rust 侧 xlsx 写入库（rust_xlsxwriter）只写不读且样式/公式能力需逐项验证；.xls 读取要引 calamine（读可以），但**导出侧 11 类特性无单库覆盖**；WebView2 无 Win7。移动端支持（Tauri 2 新增）对纯桌面财务工具是零价值加分项 |
| **Flutter Desktop** | ❌ 否决，且是四者中 Excel 短板最硬的：**pub.dev 纯 Dart 生态不存在任何 .xls 读取库**——spreadsheet_decoder（MIT）只支持 ODS/XLSX，excel/excel_community 只支持 XLSX；能补齐 Excel 能力的 syncfusion_flutter_xlsio **触发用户"只收免费开源"红线**。.xls 读取这一条就一票否决 |
| **React Native Windows** | ❌ 否决。框架本身健康（微软维护、MIT、0.81.4 活跃），但①完全复用 JS 生态 → 继承 Electron 的 SheetJS/ExcelJS 全部硬伤；②最低 Win10 1903、0.82 起强制 Fabric 架构（无 Win7 路径）；③强依赖 VS2022 + Win10 SDK + C++ 桥接，单人非全职维护成本四者最高 |
| **Compose Multiplatform Desktop** | ❌ 一句话否决。Excel 层可借 Apache POI（JVM，Apache 2.0，能力强），但 Kotlin/JVM 桌面学习曲线 + 打包体积 + 单人维护生态小，无任何相对 .NET 路线的补偿性优势 |
| **.NET MAUI** | ❌ 一句话否决（作为"为什么走 .NET 却选 WPF 而不是 MAUI"的补注）：MAUI 的 Windows 目标**就是 WinUI 3 的包装** → 继承 .NET 深评里 WinUI 3 的全部短板（无第一方 DataGrid），且不支持 Win7 |
| **Neutralinojs 等轻量替代** | ❌ 一句话否决：生态远小于 Electron，Excel 生态问题一样存在，只是体积小，不解决任何本项目痛点 |

**对推荐的影响：零。** 没有任何一个现代跨端方案值得改变"方案 1（Python 重组）+ 方案 4 作为远期备选"的既有推荐。最硬的三条理由见第 7 章。

---

## 1. Electron（复核方案 6，本轮补强 OS 支持与 SheetJS 事实）

### 1.1 OS 支持线（查证：Electron 官方 Breaking Changes 文档，2026-09-09）

- **Electron 23.0.0（2023-02-07 发布）起移除 Windows 7/8/8.1 支持**，官方原文："Windows 10 or later will be required to run Electron v23.0.0 and higher"。当前所有受支持版本要求 **Win10+** → **Win10 22H2 与 Win11 25H2 均在支持范围内** ✅
- **Win7 后补成本**：最后一个支持 Win7 的版本是 Electron 22（Chromium 108），官方安全维护已于 **2023-10-10 截止**。后补 Win7 = 把财务软件钉死在 3 年前无安全更新的 Chromium 上。**对比 WPF 的 multi-targeting：WPF 是"活的后门"，Electron 是"死标本"**。结论与 .NET 深评一致：Electron 的 Win7 路径实质不可用。

### 1.2 Excel 层的供应链事实（查证：SheetJS 官方文档 + 安全分析，2026-09-09）

这是本轮最重要的新证据，比方案 6 里"exceljs 打印设置缺口"更致命：

- **SheetJS CE 在 npm registry 上的最后版本是 0.18.5**，官方文档明言 npm 仓库"out of date"、修复版只从 `cdn.sheetjs.com` 以 tarball URL 安装。
- 两个已修复的 CVE——**CVE-2023-30533（原型污染）、CVE-2024-22363（ReDoS）**——修复版（0.19.3 / 0.20.2+）**从未进入 npm**。`npm i xlsx` 装到的就是含漏洞的 0.18.5。
- 对"只收免费开源 + 供应链安全"的含义：SheetJS CE 本身 Apache-2.0 没问题，但**它的分发模式把项目钉在"绕过 npm 审计体系的自建 CDN"上**——每次 CI/换机都要从第三方 CDN 拉 tarball，Seafile 三机环境 + 单人维护下这是持续性风险。社区有非官方 fork（如 @keep-lts/xlsx，Apache-2.0，回移植两个 CVE 修复），但那是把供应链安全交给一个无名维护者。
- ExcelJS（MIT）：明确不支持 .xls；打印设置（fitToPage/页边距/打印区域）覆盖不完整 → **导出的结算表打印格式会变**，而"打印出来交给律所财务"是这个导出物的真实用途（方案 6 已论证）。

**判决**：❌ 维持否决。Electron 在 Excel 层同时踩"读不了/读不安全 .xls"与"导出打印格式"两颗雷，且 Win7 后门是死的。

---

## 2. Tauri 2.x（复核方案 6）

- **OS 支持**：Tauri 2 依赖 WebView2；WebView2 运行时支持 Win10 1809+，**不支持 Win7**（WebView2 早已停止 Win7 支持）→ Win7 门焊死，与 WinUI 3 同级。
- **Excel 生态**：Rust 侧 calamine（MIT/Apache）可读 .xls/.xlsx/.xlsm（读取能力 ✅）；但**写出侧最强开源是 rust_xlsxwriter（MIT，只能写）**，样式/冻结窗格/公式/多 sheet 基本覆盖，**打印设置（fitToWidth/fitToPage）与数据校验支持薄弱**，且读/写/测试需两套库（.NET 深评对 NPOI 的"单库覆盖"优势在 Tauri 侧完全不存在）。
- **架构成本**：业务逻辑要从前端 TS 或 Rust 二选一；对单人财务背景开发者，Rust 所有权模型学习成本高于 C# 与 Python，前端 UI 层还要再学一套框架。
- **Tauri 2 的移动端支持**：对纯桌面财务工具**零价值**——它解决的问题（一套代码发 iOS/Android）本项目根本不存在，不构成加分项。

**判决**：❌ 维持否决。读取靠 calamine 可解，导出 11 类特性无单库覆盖，Win7 无路径，且 Rust + 前端双技术栈对单人维护是负担最大的一种组合。

---

## 3. Flutter Desktop（本轮新评）

### 3.1 OS 支持（查证：Flutter 官方 Supported platforms，2026-09-09）

- 官方支持矩阵：Windows **Supported = Windows 10, 11**；**"8 and earlier" Unsupported** → Win10 22H2 / Win11 25H2 ✅，Win7 ❌ 无路径。
- 附带成本：构建需 **Visual Studio 2022（C++ 桌面开发负载）**，SDK 磁盘占用官方建议 52GB。

### 3.2 Excel 读取：.xls 一票否决（查证：pub.dev，2026-09-09）

逐包核对 pub.dev 生态：

| 包 | 许可证 | 格式支持 | 判定 |
|---|---|---|---|
| spreadsheet_decoder 2.3.0 | MIT | **仅 ODS/XLSX**（官方文档明列，无 .xls） | ❌ 读不了台账 |
| excel / excel_community | MIT | 仅 XLSX | ❌ |
| syncfusion_flutter_xlsio | **Syncfusion Community License** | XLSX 写出为主，能力最强 | ❌ **触发用户"只收免费开源"红线**（社区许可有营收/团队规模门槛，属条件授权，非 OSI 开源） |
| flutter_excel | MIT | XLSX fork，维护状态 Poor | ❌ |

**结论：在"免费开源"约束下，Flutter/Dart 生态不存在任何 .xls 读取方案。** 退路只有两种：让用户每月先把 .xls 手动另存为 .xlsx（**改变业务流程，用户核心诉求就是直接吃台账原格式**），或用 FFI 包一个 C/Rust 的 .xls 解析器（工作量失控）。

### 3.3 Excel 导出 11 类特性

免费侧能写 XLSX 的只有 excel/excel_community：支持公式、边框、合并单元格等基础项，但**打印设置（fitToWidth/fitToPage）、自动筛选、数据校验的覆盖不完整或缺失**——与 ExcelJS 同级别的缺口，而 Syncfusion XlsIO（唯一全覆盖的）已被许可证排除。

### 3.4 其他维度（推演，基于框架公认特性）

- **表格控件**：官方 DataTable 无虚拟化，数千行需第三方（PlutoGrid/TwoDimensionalScrolling 等，开源可用但成熟度一般）；两级表头（合并表头）在开源侧需要自行拼装。
- **Seafile**：Flutter SDK 本体 + build/ 产物（含中间 C++ 构建产物）文件数量级与 Electron 同级，三机同步下 build 目录必须排除。
- **学习曲线**：Dart 对用户是第四门语言（Python/C#…），换来的唯一好处是移动端能力——本项目没有移动端需求。

**判决**：❌ 否决，且是四者中**唯一被"读取侧直接无解"一票否决**的方案。无需再比其他维度。

---

## 4. React Native Windows（本轮新评）

### 4.1 框架健康度（查证：GitHub microsoft/react-native-windows，2026-09-09）

- 微软维护、**MIT**、活跃（最新 0.81.4，2026-02 发布；约 17.3k stars）——**框架本身没有问题**，这点要如实说。
- 但注意定位：RN Windows 是 Meta RN 的 **out-of-tree（树外）平台**，桌面从来不是 RN 的主战场；**0.82 起 Paper 架构完全移除，强制 Fabric**，目标默认为 WinAppSDK Win32 应用。
- **OS 支持：最低 Windows 10 1903**，明确"no support for older Windows versions" → Win10 22H2/Win11 25H2 ✅，Win7 ❌。

### 4.2 为什么它对本案不可行

1. **Excel 层 = Electron 同款问题**。RN 的 JS 生态与 Node 生态是同一个 npm：读 .xls 仍然只有 SheetJS（npm 停更 + CDN 分发问题原样继承），导出仍然 ExcelJS。RN 不带来任何 Excel 库的增量。
2. **表格控件**：RN 生态没有面向财务报表的桌面级 DataGrid；开源侧（FlashList 等）面向移动长列表，冻结列/合并表头/打印预览需要基于原生 WinUI 控件自研桥接。
3. **工具链最重**：VS2022（C++/UWP/.NET/Node 四个工作负载）+ Win10 SDK + MSVC v143 + 本机 Node；**C++ 原生模块桥接是绕不开的技能项**（SQLite/文件系统/打印都要 C# 或 C++ 模块）。对单人非全职开发者，这是四者中学习曲线最陡、构建故障排除成本最高的栈（可类比：从"改 Python 脚本"跳到"维护一个 VS 大型解决方案 + 自写原生桥接层"）。
4. **业务匹配度**：RN Windows 的价值主张是"移动 RN 团队复用代码到 Windows"——用户没有移动端 RN 代码，此价值为 0，只剩全部成本。

**判决**：❌ 否决。框架健康但价值主张与本案错位，Excel 硬伤全额继承，工具链负担最重。

---

## 5. 一句话带过

- **Compose Multiplatform Desktop**：Excel 可走 Apache POI（Apache 2.0，能力确实强，是 JVM 生态真优势），但 Kotlin + JVM 桌面打包（数百 MB 自带 JRE）+ 桌面控件生态尚年轻，单人维护无补偿优势 → 否决。
- **.NET MAUI**：Windows 目标 = WinUI 3 包装 → .NET 深评已论证 WinUI 3 无第一方 DataGrid、无 Win7 路径；"走 .NET 该选谁"的答案是 WPF 而非 MAUI（详见 plan4 深评第 2 章）。
- **Neutralinojs / NW.js 等**：Neutralino 生态极小（Excel 需全部手写或嵌 SheetJS，问题原样继承）；NW.js 与 Electron 同生态同问题，仅分发形态不同 → 一句话否决。

---

## 6. 硬约束对照总表（横向收口）

| 硬约束 | Electron | Tauri 2 | Flutter Desktop | RN Windows | （参照）WPF+.NET 10 | （参照）Python 现状 |
|---|---|---|---|---|---|---|
| .xls 读取（免费开源） | ⚠️ 仅 SheetJS，npm 停更+CDN 分发 | ✅ calamine | ❌ **无任何开源库** | ⚠️ 同 Electron（SheetJS） | ✅ NPOI 单库 | ✅ xlrd（现状已跑通） |
| 导出 11 类特性 | ❌ ExcelJS 打印设置缺口 | ⚠️ rust_xlsxwriter 覆盖不全、双库 | ❌ excel 包缺口大，Syncfusion 被许可证排除 | ❌ 同 Electron | ✅ NPOI 全覆盖（plan4 已逐项核对） | ✅ openpyxl 全覆盖 |
| Win10 22H2 | ✅ | ✅ | ✅ | ✅（1903+） | ✅ | ✅ |
| Win11 25H2 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Win7 后补可能性 | ⚠️ 死标本（Electron 22，2023-10 EOL） | ❌ 无 | ❌ 无 | ❌ 无 | ✅ multi-targeting 活后门 | ⚠️ 现状本就支持（Python） |
| 许可证红线（纯开源） | ✅（但供应链扣分） | ✅ | ⚠️ 唯一全覆盖 Excel 库被排除 | ✅ MIT | ✅ | ✅ |
| 打包体积/启动 | ❌ 200-400MB 解包、Chromium 内存 | ✅ 小（共享 WebView2） | ✅ 中（10-30MB 级） | ✅ 中（无 Chromium） | ✅ 小（几十 MB self-contained 稍大） | ⚠️ PyInstaller 100-300MB（现状） |
| Seafile 兼容 | ❌ node_modules 万级文件 | ⚠️ src/modules 目标目录 | ⚠️ SDK+build 产物 | ❌ node_modules + VS 产物最重 | ✅ bin/obj 可 ignore | ✅ 现状（仅 .venv 需排除） |
| 单人非全职学习曲线 | 中（JS/TS+Node） | **高**（Rust+TS 双栈） | 高（Dart，第四门语言） | **最高**（RN+C++/C# 桥接+VS 工具链） | 中（C#/XAML/MVVM） | 零（现状） |
| 工期量级（全重写） | 131-208 人日（方案 6） | 140-220 人日（推演，同量级+Rust） | 140-200 人日（推演，Excel 缺口需自研补） | 150-230 人日（推演，桥接成本最高） | 130-180 人日（plan4） | 20-28 人日（方案 1） |

> 推演项说明：Flutter/RN/Tauri 的工期因 Excel 缺口存在"自研补齐"的不确定下界，只能给区间推演；定性结论（被硬约束否决）不依赖这些数字的精度。

---

## 7. 与方案 1 / 方案 4 的定位关系 + 最终判断

### 7.1 这批方案是替代谁的？解决什么、解决不了什么

现代跨端家族的**全部价值主张是"一套代码发多端"**，而本项目是**单端（Windows 桌面）财务工具**——多端能力从一开始就与本项目的需求正交。对照审计报告的三大问题：

| 三大问题 | 现代跨端方案能否解决 |
|---|---|
| ① person_settlement 被重复调用（24 次全量结算） | ❌ **不能**。这是调用结构问题，换任何语言/框架都原样存在——重写时只会把同样的调用链搬进新栈 |
| ② db.py 循环依赖 + 12 个 UI 文件直连 get_conn() | ❌ 不能。重写确实会"重新分层"，但分层在 Python 里 3-5 人日就能做（方案 1 的 B 部分）；为此付 130+ 人日重写是买椟还珠 |
| ③ 测试缺口（结算引擎/导入器/5 导出器零单测） | ❌ 不能，甚至**恶化**——golden-file 回归与现有 pytest 资产在换栈后全部作废重建，而测试缺口恰恰是重写最大的风险敞口 |

### 7.2 最终判断：不改推荐

**推荐维持不变：方案 1（Python 重组，20-28 人日）为主线；若未来确需换栈，方案 4（WPF + .NET 10，130-180 人日）是唯一值得保留的选项；现代跨端家族全部出局。**

最硬的三条理由：

1. **Excel 是本项目的一票否决项，而现代跨端家族在这一项上集体不及格**：Flutter 无任何开源 .xls 读取库；Electron/RN 唯一的 .xls 读取方案（SheetJS）npm 停更、修复版绕行自建 CDN（含两个未修复 CVE 暴露在默认安装里）；Tauri 导出侧无单库覆盖。对照 NPOI 单库全绿的核对结果，这个差距不是"努力一下能补齐"，是生态结构性缺口。
2. **价值主张与需求正交**：所有四个方案卖的是多端复用，本项目只有 Windows 单端、单人、非全职。付 130-230 人日买回的是用不上的移动端能力和一套全新工具链——这与方案 4 至少"类型安全 + 原生表格控件 + multi-targeting"的实收益形成鲜明对比。
3. **Win7 后补维度全面落败**：用户明确关心"以后能不能再补 Win7"。四个方案里三个（Tauri/Flutter/RN）根本没有 Win7 路径，Electron 的路径是钉死在 2023 年已停安全更新的 Electron 22——只有 WPF 的 multi-targeting 是"活的后门"，只有现状 Python 本来就支持。

### 7.3 事实与推演的边界声明

- **查证事实**（WebSearch，2026-09-09，来源见正文）：Electron 23 弃 Win7/8/8.1；SheetJS npm 冻结 0.18.5 + CVE 清单 + CDN 分发模式；Flutter 官方平台矩阵（Windows 10/11）；spreadsheet_decoder 仅 ODS/XLSX；Syncfusion Community License 条件授权性质；RN Windows MIT/微软维护/0.81.4/最低 Win10 1903/0.82 强制 Fabric。
- **推演项**：各方案工期区间、Seafile 文件数量级、学习曲线类比——基于框架公认特性与既有报告口径推演，已在表中标注。定性判决（否决）均建立在查证事实之上，不依赖推演数字。

---

*报告完。相关文档：`docs/refactor-audit-2026-09-09.md` → `docs/tech-stack-rewrite-options-2026-09-09.md` → `docs/plan4-dotnet-deep-dive-2026-09-09.md` → 本篇。*
