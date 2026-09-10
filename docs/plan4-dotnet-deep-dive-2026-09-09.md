# 方案 4 专项深评：.NET 桌面栈（WPF / WinUI 3）迁移可行性

> 评估对象：`C:\Users\Bingo\Desktop\buddy2\lawfirm_app`（基线 `main` @ `90f22b1`）
> 评估日期：2026-09-09　评估人：高见远（架构师）
> 性质：决策依据，非开工指令。**未修改任何源码、未执行任何 git 写操作。**
> 上游文档：`docs/refactor-audit-2026-09-09.md`（审计）、`docs/tech-stack-rewrite-options-2026-09-09.md`（8 方案总评）
>
> **已锁定约束**：目标平台 Windows 11 25H2；兼容底线 Windows 10 22H2；Win7 主线放弃但要求保留"后补"可能性（作为新增对比维度）；**只收免费开源（MIT/Apache/BSD/GPL），EPPlus/Xceed/Syncfusion/Telerik 等商业授权全部出局**。

---

## 0. 结论速览（先读这个）

| 问题 | 结论 |
|---|---|
| **WPF vs WinUI 3** | **推荐 WPF（.NET 10）**。决定性理由：① WinUI 3 **没有第一方 DataGrid**（微软官方文档明说，CommunityToolkit 的 DataGrid 只有 UWP 版没移植），免费开源条件下只剩社区项目 `WinUI.TableView`（单人维护风险）；② WPF 保留 **Win7 后补票**（multi-targeting），WinUI 3 的门焊死（Windows App SDK 最低 Win10 1809）；③ 本项目是数据密集财务应用，22 页里 17+ 页是表格 |
| **.NET 版本** | **必须 .NET 10（LTS，支持到 2028-11-14）**。⚠️ **.NET 8 与 .NET 9 都将于 2026-11-10 结束支持——距今仅 2 个月**。这直接否决任何"先用 .NET 8 LTS"的想法 |
| **Win7 后补** | 技术上可行且**只有 WPF 能做到**（同一份代码 multi-target `net48` + `net10.0-windows`）。代价：C# 语言版本锁在 7.3（默认）、EF Core 出局（netstandard2.1 不兼容 net48）、每版发布需 Win7 VM 验证、以及**在 2020 年起无安全更新的系统上跑财务软件本身的风险** |
| **Excel 库** | **NPOI（Apache 2.0）单库覆盖** `.xls/.xlsx/.xlsm` 读写 + 11 类导出特性全支持 —— 与 openpyxl+xlrd 的角色完全对应。备选：ExcelDataReader（读，MIT）+ ClosedXML（写 xlsx，MIT），API 更现代但引入双库且无 .xls 写（本项目不需要写 .xls） |
| **数据访问层** | **Dapper + Microsoft.Data.Sqlite**（两者都兼容 net48 与 modern .NET，恰好把 multi-targeting 的约束也解了）。**EF Core 8 出局**（目标 netstandard2.1，不兼容 net48）。**N+1 不会天然消失**——换栈不改变查询模式，必须照样手动批量化 |
| **共享 SQLite 双轨灰度** | ✅ **可行，是本次深评最有价值的发现**。SQLite 是跨语言文件格式，C# 可直接打开现有 `data/lawfirm.db`（WAL 是库文件的持久属性，C# 侧无需重设）。配合 golden-file 跨语言回归，可做"**C# 只读报表先行 → Python 保留写权限 → 逐页交接 → 最后切写**"的低风险路径 |
| **代码量** | 21,523 行 Python → 估算 **25,000~33,000 行 C#/XAML**（1.2~1.5×）。XAML+code-behind 双份与 NPOI 冗长是主要膨胀源；LINQ/switch 表达式是主要收缩源 |
| **工期** | **130~180 人日**（原估 145-224，因确认 multi-targeting/开源控件约束修正）→ 单人每周 1.5 天 ≈ **1.7~2.3 个日历年** |
| **最终判断** | **现在不值得**（见第 9 章）。触发反转的条件也列了——其中"共享 SQLite 灰度"方案把反转成本降到了可控水平，如果用户真想走，20 人日就能拿到 go/no-go 信号 |

---

## 1. 操作系统支持矩阵（A 项查证，全部来自官方来源）

> **查证日期：2026-09-09**。来源：Microsoft Learn（《在 Windows 上安装 .NET》《.NET Framework system requirements》《WinUI 入门》《.NET Support Policy》），NuGet Gallery 包页面。

### 1.1 各运行时 × 各 Windows 版本

| 运行时 | Win7 SP1 | Win8.1 | **Win10 22H2 (19045)** | **Win11 25H2** | 备注 |
|---|:-:|:-:|:-:|:-:|---|
| **.NET Framework 4.8 / 4.8.1** | ✔ 安装支持† | ✔† | ✔（4.8 预装；4.8.1 可另装） | ✔（24H2+ 预装 **4.8.1**） | †官方页面明示 Win7/8.1 **操作系统本身已停止支持**；.NET Framework 4.8 支持遵循**父 OS 生命周期** |
| .NET 6 (LTS) | ✔ | ✔ | ✔ | ✔ | **最后一个支持 Win7/8.1 的 .NET**；支持已于 **2024-11-12 结束** ❌ |
| .NET 7 | ❌ | ❌ | ✔ | ✔ | 已 EOL（2024-05-14） |
| **.NET 8 (LTS)** | ❌ | ❌ | ✔* | ✔ | **支持至 2026-11-10——仅剩 2 个月** |
| .NET 9 (STS) | ❌ | ❌ | ✔* | ✔ | 支持至 2026-11-10 |
| **.NET 10 (LTS)** | ❌ | ❌ | ✔* | ✔ | **2025-11-11 发布，支持至 2028-11-14** ← 唯一合理选择 |
| **WinUI 3 / Windows App SDK** | ❌ **无任何路径** | ❌ | ✔（最低 **Win10 1809 / build 17763**） | ✔ | 官方明确"Windows 10 版本 1809 或更高"；**从设计上就与 Win7 无缘** |
| WPF（宿主 .NET Framework 4.8） | ✔ | ✔ | ✔ | ✔ | |
| WPF（宿主 .NET 8/10） | ❌ | ❌ | ✔ | ✔ | |

\* 关键细节：微软官方 .NET 支持表中，Windows 10 一行现在只列 "**21H2、1809、1607 LTSC/Enterprise**"。这与 Win10 22H2 的生命周期状态一致——**Win10 22H2 已于 2025-10-14 结束主流支持**（今天已是 2026-09）。含义：**.NET 10 在 22H2 上仍能运行**（表格描述的是"受支持组合"），但 22H2 本机已不再收安全更新。用户"保留 Win10 22H2 兼容"这条**技术上免费**（Win10/Win11 共享核心 API），但要明白这些机器跑的是无安全更新的系统。

### 1.2 直接回答"A 组四个问题"

1. **.NET Framework 4.8 最低 Windows 版本**：官方系统要求表列出可安装至 **Win7 SP1**（含 32/64 位），且 Win11 24H2+ 预装 4.8.1。支持随父 OS 生命周期——即在无安全更新的 Win7 上"能跑"，但微软不再为 Win7 出 .NET Framework 安全补丁。
2. **.NET 8/9/10 是否支持 Win7/8.1**：**全部 ❌**。官方原文："Windows 7 和 Windows 8.1 上已经没有版本支持 .NET 的版本。上次支持的版本是 .NET 6，支持于 2024 年 11 月 12 日结束。"（此前提是 .NET 7 起移除——**核实属实**，且 .NET 6 也已 EOL，所以**没有任何在支持期内的 .NET 能跑 Win7**。）
3. **WinUI 3 / Windows App SDK**：最低 **Win10 1809 (17763)**；**不支持 Win7**（Windows App SDK 本身依赖 Win10 API 集）。Win10 22H2 与 Win11 25H2 均在支持范围内。部署形态：MSIX 打包 / 带外部位置打包 / 未打包三种，Windows App SDK 需装到目标机或随应用 self-contained 打包。
4. **WPF 的 OS 覆盖**：WPF **不是一个独立运行时**，覆盖度完全由宿主决定——宿主 net48 → 覆盖到 Win7；宿主 .NET 8/10 → 与 .NET 相同（Win10 1607+/21H2+，无 Win7）。WPF 本体在 .NET 10 上仍在维护（.NET 10 包含 WPF 性能优化与 Fluent 改进，见第 3.1 节），但**不再新增控件**（DataGrid 自 2010 年起零新特性，见第 3.3 节）。

---

## 2. 可行组合矩阵（B 项）

> 回答核心问题：**如果 Win7 兼容是硬要求，唯一可行的 .NET 栈是什么？**

### 2.1 组合矩阵

| 组合 | Win7 | Win10 22H2 | Win11 25H2 | 可行性 |
|---|:-:|:-:|:-:|---|
| **.NET Framework 4.8 + WPF** | ✅ 唯一路径 | ✅ | ✅ | ✅ 唯一能同时覆盖三者的 .NET 栈 |
| .NET 10 + WPF | ❌ | ✅ | ✅ | ✅（放弃 Win7） |
| .NET 10 + WinUI 3 | ❌ | ✅ | ✅ | ✅（放弃 Win7） |
| .NET Framework 4.8 + WinUI 3 | — | — | — | ❌ 不存在（WinUI 3 需要 Windows App SDK / modern .NET） |
| .NET 6 + WPF（Win7 补票的另一条路） | ✅ | ✅ | ✅ | ❌ **.NET 6 已于 2024-11 EOL**，不能选 |

**结论**：预判正确——**Win7 硬要求 ⇒ 唯一可行栈 = .NET Framework 4.8 + WPF**。WinUI 3 在 Win7 问题上无任何参与资格。

### 2.2 net48 组合的真实代价（这不是免费选项）

| 维度 | 现实 |
|---|---|
| **C# 语言版本** | net48 默认 **LangVersion 7.3**。可手动调到 8.0，但有硬缺口：无默认接口成员、`init`/`record` 需 `IsExternalInit` shim、无内建 range/index 运行时支持。**总体开发体验 = 2019 年的 C#** |
| **NuGet 生态** | net48 只能消费 **netstandard2.0** 及以下的包：`Dapper` ✔（net35+）、`Microsoft.Data.Sqlite` ✔（netstandard2.0）、`NPOI` ✔、`ClosedXML` ✔（netstandard2.0）、**`EF Core 5+` ❌（目标 netstandard2.1，net48 不实现）** |
| **新 API** | 用不了 .NET 8+ 的 API（需 `#if NET8_0_OR_GREATER` 条件编译或在 net48 侧自行降级实现） |
| **工具链** | 不能用 `dotnet new` 的现代模板全家桶（可用 SDK-style csproj multi-target，但调试/分析器体验打折） |
| **安全姿态** | **在 2020-01 起无安全更新的 OS 上运行一个存有全员工资数据的财务软件** —— 这个风险与"要不要 Win7"等价，建议用户直面它 |
| **每版验证** | 每次发布需在 Win7 VM（或真机）做一轮回归，约 +0.5 人日/版 |

### 2.3 放弃 Win7 后可选范围如何扩大

若只要求 Win10 22H2 + Win11 25H2：
- 运行时可上 **.NET 10**（LTS 至 2028-11），获得：C# 12/13 全部语言特性、EF Core 8/9、source generators、现代模板、更好的启动与 AOT；
- UI 可在 **WPF** 与 **WinUI 3** 之间真正二选一（不再是"为了 Win7 被锁死在 net48"）；
- NuGet 生态全开（但商业授权约束仍然生效，EPPlus/商业 DataGrid 依旧出局）。

### 2.4 multi-targeting（WPF 的 Win7 后补票）具体工作量

同一份代码产出 `net48` + `net10.0-windows` 两个产物（`<TargetFrameworks>net10.0-windows;net48</TargetFrameworks>`）：

| 项 | 估算 |
|---|---|
| 初始搭建（SDK-style csproj、条件包引用、`#if` 隔离层） | **1~2 人日** |
| NuGet 选型约束（全项目只用 netstandard2.0 兼容包 → 恰好 = Dapper + Microsoft.Data.Sqlite + NPOI，见第 6 章） | 0（选型已按此设计） |
| 开发期 API 自律 | 持续性税：估计 **+10~15% 开发摩擦**（每次想用新 API 都要停下来想 net48 怎么办） |
| 每版 Win7 回归 | +0.5 人日/版 |
| 何时启用 | **可以推迟**：先用 `net10.0-windows` 单目标开发，等 Win7 需求真出现再加 `net48` 目标。**前提是全程遵守 netstandard2.0 包约束与 API 自律**——这是"后补票"的真实代价：**平时就要为还没出现的需求交税** |

> **这正是 team-lead 要我补的对比维度结论**：选 WPF = 保留 Win7 后补票（代价是持续的开发税），选 WinUI 3 = 门焊死。且这个税**只对 WPF 收**，WinUI 3 想补 Win7 的成本是无穷大。

---

## 3. WPF vs WinUI 3 深度对比

### 3.1 两者现状（查证 2026-09-09）

| 维度 | WPF | WinUI 3（Windows App SDK） |
|---|---|---|
| 官方定位 | "Mature XAML framework"——微软原话是建议用 Windows App SDK interop 做现代化，**不是**推荐新项目首选 | "recommended platform for new development using C#, C++, and XAML on Windows 10 and 11" |
| 活跃度 | .NET 10 仍在投入：性能优化、Fluent 改进、XAML 解析增强；dotnet/wpf 仓库活跃 | Windows App SDK 持续发版（当前稳定线 1.7/1.8 代际）；PowerToys 等采用 |
| 新控件 | **无**。内置 DataGrid 自 .NET 4.0（2010-04）起**零新特性**，仅有 .NET 4.7.1 的无障碍修复 | 新控件有投入，但**没有第一方 DataGrid**（见 3.3） |
| 维护模式定性 | 平台级维护（bug 修复 + 性能 + DPI/无障碍），控件层冻结 | 平台级投入，控件层建设性但残缺（表格是最大缺口） |
| Win10 22H2 | ✅（随宿主） | ✅（最低 1809） |
| Win7 后补 | ✅ multi-target net48 | ❌ **不可能** |
| 视觉基线 | QSS→Style/Template 完全可控，HandyControl（MIT）提供 80+ 控件与三套主题 | Fluent 设计语言**内建**，Mica/亚克力等材质系统级支持 |
| 无边框窗口/自绘标题栏 | ✅ 成熟套路（WindowChrome），对应现在 qframelesswindow 的角色 | ✅ AppWindow/TitleBar 内建（反而更省事） |

> **对"2-3 年后长期维护"的含义**：WPF 处于"平台维护 + 控件冻结"状态。对本项目而言，WPF 平台层的修复（DPI、无障碍、性能）恰好是持续收益；控件层冻结恰好命中本项目最依赖的表格——**这是双刃剑，详见表格能力专项（3.3）**。WinUI 3 是微软的投入方向，但它的表格缺口在"只收开源"的约束下是**结构性**的，不会因为微软投入而自动补上。

### 3.2 22 页 UI 能力对照

| 本项目需求（现状坐标） | WPF | WinUI 3 |
|---|---|---|
| **冻结列**（`table_features.py` 冻结列铺灰、`FrozenTableWidget`） | ✅ 内置 `DataGrid.FrozenColumnCount` | ⚠️ `WinUI.TableView` 社区实现；第一方无 |
| **两级表头/合并表头**（`table_features.py:228 TwoTierHeaderView`） | ❌ 内置不支持，**需自研**（ColumnHeader 模板 + 跨列合并，估 3~5 人日） | ⚠️ 同左（`WinUI.TableView` 支持 merged headers，但属社区代码） |
| **列布局持久化**（`column_layout.py`+`column_state.py`，QSettings） | ❌ 需自研（列宽/顺序/可见性存 JSON 或注册表，估 2~3 人日；Python 侧逻辑可直接平移） | ⚠️ 同左 |
| **数千行表格性能** | ⚠️ 内置 DataGrid 开启虚拟化后数千行可用；但 dotnet/wpf 有**长期未修的 issue：大数据集滚动卡顿、内存泄漏**（Xceed 2026 博客引述） | ⚠️ `WinUI.TableView` 社区实现，虚拟化质量未经本项目规模验证 |
| **打印/预览** | —— | —— |
| 无边框窗口 + 自绘标题栏（`main_window.py:103-167`） | ✅ WindowChrome 套路成熟 | ✅ AppWindow 内建，更省 |
| 侧栏分组折叠 + 动效（`sidebar.py` 569 行自绘） | ✅ 可自绘（Storyboard/Animation） | ✅ 内建动画系统更强 |
| Toast 通知（qfluentwidgets InfoBar） | ⚠️ HandyControl 有 Growl（MIT）✅ | ✅ 内建 InfoBar |
| 皮肤切换（`style.py` 三套皮肤） | ✅ DynamicResource/主题字典，成熟 | ✅ ThemeResource，更系统 |
| 图标自绘（`nav_icons.py` QPainter 187 行） | ✅ Geometry/Path，可平移 | ✅ Path/FontIcon |

> **打印/预览一栏直接删除，理由要讲清楚**：本项目**现在就没有应用内打印**——所有打印都是"导出 Excel → 用户在 Excel 里打印"（5 个导出器的 `page_setup` 全是为这个服务的：`invoice_income_exporter.py:145-147` landscape+fitToWidth 等）。所以"UI 框架的打印预览能力"**不是本项目需求**，比较它没有意义。真正相关的是 Excel 导出质量（第 6 章）。

### 3.3 表格控件专项（决定成败的部分，只收开源）

| 候选 | 许可证 | 平台 | 冻结列 | 合并表头 | 列持久化 | 数千行 | 判定 |
|---|---|---|:-:|:-:|:-:|:-:|---|
| **WPF 内置 DataGrid** | （随 .NET） | WPF | ✅ | ❌ 需自研 | ❌ 需自研 | ⚠️ 虚拟化可用，有滚动卡顿的长期 issue | **可用底座** |
| HandyControl（`DataGridEx`） | **MIT** ✅ | WPF（net40~net8+） | ✅ | ⚠️ | ⚠️ | ⚠️ | 是内置 DataGrid 的**重样式**版，核心行为同源 |
| MaterialDesignThemes | **MIT** ✅ | WPF | — | — | — | — | 纯样式库，无表格逻辑 |
| CommunityToolkit.DataGrid | 免费但**仅 UWP 7.1.0，未移植 WinUI 3**，官方明示"不要在 WinUI 3 中使用" | ❌ | — | — | — | — | **出局** |
| **WinUI.TableView** | 开源（MIT）✅ | WinUI 3 | ✅ | ✅ | ⚠️ | ⚠️ | **WinUI 3 侧唯一免费选项**；社区项目、主要维护者单一（bus factor 风险） |
| Syncfusion/Telerik/DevExpress/Xceed | ❌ 商业 | — | — | — | — | — | **按约束出局** |

**开源控件满足不了的部分，必须直说"自研"并给工作量**（WPF 路线）：

| 自研项 | 工作量 | 说明 |
|---|---|---|
| 两级表头（对应 `TwoTierHeaderView` 65 行 QPainter 自绘） | **3~5 人日** | WPF 用 ColumnHeader ContentTemplate + GridSpan；逻辑比 Qt 自绘简单，但要与 DataGrid 列宽同步 |
| 列布局持久化（对应 `column_layout.py` 490 行 + `column_state.py` 69 行） | **2~4 人日** | 列宽/顺序/可见性/筛选状态序列化；**Python 侧的业务规则可直接平移**（含"列重排后筛选错位"等已踩坑经验，见 `table_features.py:597-614` 注释） |
| 表格右键多重筛选（对应 `TableFilter` 340 行） | **3~5 人日** | WPF 有 CollectionView 过滤，模型更干净，但多列 AND + 跨列搜索逻辑要重写 |
| 选中保色/悬停 tooltip（`TableBehaviorDelegate`） | **1~2 人日** | WPF 触发器 + tooltip，比 Qt 委托简单 |
| **小计（表格基础设施）** | **9~16 人日** | 这些在 Python 里**已经写完并在用**，.NET 版要全部重来一遍 |

### 3.4 推荐

> **推荐 WPF（宿主 .NET 10）**。理由按权重排序：
> 1. **WinUI 3 在"只收免费开源 + 数据密集"的双重约束下没有合格的表格**：第一方没有，CommunityToolkit 没移植，商业的出局，只剩社区 `WinUI.TableView`（单一维护者）。本项目 22 页里绝大多数是表格页——把整个迁移押在一个社区控件上，风险不可接受。
> 2. **WPF 保留 Win7 后补票**，WinUI 3 焊死（用户明确要求保留后补可能性）。
> 3. WPF 的"缺陷"（控件冻结）对本项目影响 = 第 3.3 节那 9~16 人日自研；WinUI 3 的"缺陷"（表格真空）影响 = 要么赌社区控件要么花 20+ 人日自研一个表格控件。
> 4. 迁移期心理成本：WPF 的 XAML/绑定/样式与现有 QSS/QWidget 心智模型相近度更高。

**推荐反转的条件**（满足任意一条可反转为 WinUI 3）：
- 用户接受"**买商业表格控件**"（Syncfusion/Telerik 约一个开发者席位）——WinUI 3 的表格缺口立即补齐，且 Fluent 视觉红利兑现；
- 或愿意**先花 3~5 人日实测 `WinUI.TableView`** 在本项目真实数据规模（数千行、两级表头、冻结列、列重排）下的表现，且结果令人满意；
- 或彻底放弃 Win7 后补可能性且强烈偏好 Fluent 视觉。
- 另一个反转条件在时间轴上：若项目拖到 2028 后，Windows App SDK 生态成熟度会优于今天——**但对 2026 年的决策没有意义**。

---

## 4. .NET 版本与运行时

### 4.1 版本选择

| 选项 | 支持止于 | 判定 |
|---|---|---|
| .NET 8 (LTS) | **2026-11-10（2 个月后）** | ❌ 现在启动一个 2 年迁移选它等于中途换轨 |
| .NET 9 (STS) | 2026-11-10 | ❌ 同上 |
| **.NET 10 (LTS)** | **2028-11-14** | ✅ **唯一合理选择**。2025-11-11 发布，当前 10.0.11，处于 Active 支持期 |
| .NET 11 (预览) | ~2027 | ❌ 未 GA |

> **注意一个陷阱**：网上大量 .NET 8 教程与模板（含 2024-2025 年的中文资料）会引导你选 net8.0。对本项目这种**迁移工期 ≈ .NET 8 剩余寿命**的情形，必须显式钉死 `net10.0-windows`。
> 若走 net48 后补票路线：`<TargetFrameworks>net10.0-windows;net48</TargetFrameworks>`，包选型按 netstandard2.0 收敛（见 2.4 与第 6 章）。

### 4.2 部署方式与 3 台 PC 分发

| 方式 | 体积 | 3 台 PC（Seafile）分发 |
|---|---|---|
| framework-dependent（装 .NET 10 Desktop Runtime） | 应用本体 ~10-30 MB | 每台需装 Runtime（~55 MB，一次性）。**适合 3 台固定机器** |
| **self-contained** | ~70-150 MB | **无需装 Runtime，单目录拷走即用**；可直接放 Seafile 同步目录分发（exe 是少量大文件，Seafile 友好）。**推荐** |
| MSIX（WinUI 3 侧常见） | — | 对内部 3 台 PC 是过度工程 |

**对照现状**：PyInstaller 包 100-300 MB / 启动 1-3 秒 → .NET self-contained 约 70-150 MB / 冷启动 **0.3-1 秒**（JIT 后热启动更快）。**体积与启动确实改善，但不是量级差异**——这是换栈收益里"真实但不惊艳"的部分。

### 4.3 构建产物与 Seafile（结合用户历史事故）

| 目录 | 处置 |
|---|---|
| `bin/`、`obj/` | 必须 `.gitignore` + **绝不放 Seafile 同步目录**（增量编译产生大量小文件——正是 Seafile 最怕的负载模式） |
| NuGet 全局缓存（`%USERPROFILE%\.nuget\packages`） | 在用户目录，**天然不进 Seafile** ✅（比 Python `.venv` 还干净） |
| 3 台 PC 的开发能力 | 每台装 .NET 10 SDK（~1-2 GB）即可开发；比 Rust 的 MSVC+rustup（5-8 GB）友好，与 Python（venv）相当 |
| **风险** | 与 Python 方案同级，**不构成换栈的额外风险**（Rust/TS 方案才构成） |

---

## 5. Excel 层（C/D 项查证，最需要实查的部分）

> 查证日期 2026-09-09。来源：NuGet Gallery、NPOI/ClosedXML 官方仓库与多方 2026 年对比评测（HackerNoon 2026 评测、ADR-011 选型记录）。

### 5.1 许可证核查（先排雷）

| 库 | 许可证 | 商用 | 判定 |
|---|---|---|---|
| **NPOI** | **Apache 2.0** | ✅ 免费 | ✅ **入选** |
| **ClosedXML** | **MIT** | ✅ 免费 | ✅ 入选（限 xlsx） |
| **ExcelDataReader** | **MIT** | ✅ 免费 | ✅ 入选（只读） |
| EPPlus 5+ | **Polyform Noncommercial** | ❌ 商用需付费（~$300/dev/年） | ❌ **按约束出局**。多家 2026 年评测/ADR 明确记载其 v5（2020）由 LGPL 转非商业授权——**核实属实，用户避坑正确** |
| Open XML SDK（微软官方） | MIT | ✅ 免费 | 备选（过于底层：填一个格子约 20 行代码） |
| IronXL / Aspose / Syncfusion / Xceed | 商业 | ❌ | 出局 |

### 5.2 格式覆盖

| 库 | .xls 读 | .xls 写 | .xlsx 读 | .xlsx 写 | .xlsm 读 | .xlsm 写 |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| **NPOI** | ✅（HSSF） | ✅ | ✅（XSSF） | ✅ | ✅ | ✅ |
| ClosedXML | ❌ **不支持 .xls** | ❌ | ✅ | ✅ | ✅ 读（保存宏行为需验证） | ⚠️ |
| ExcelDataReader | ✅ | —（只读） | ✅ | — | ✅ | — |
| 现状 openpyxl+xlrd | ✅ | — | ✅ | ✅ | ✅ | — |

**结论**：本项目硬要求 `.xls/.xlsx/.xlsm` 三格式读取（`excel_reader.py:30`）、写入仅 xlsx。**NPOI 单库即全覆盖，与现有 openpyxl+xlrd 角色一一对应**——这是它相对"ExcelDataReader(读)+ClosedXML(写)"双库方案的决定性优势（少一个依赖、少一套 API 心智）。若追求更现代的写出 API，可用混合方案，代价是双库。

### 5.3 11 类导出特性逐项对照

| # | 特性（本项目坐标） | NPOI | ClosedXML | API 对应 |
|---|---|:-:|:-:|---|
| 1 | 多 sheet ≤33（`staff_income_exporter.py:173`） | ✅ | ✅ | `wb.CreateSheet(name)` / `wb.AddWorksheet(name)` |
| 2 | 合并单元格（`invoice_income_exporter.py:91,95,102,105` 等） | ✅ | ✅ | `sheet.AddMergedRegion(new CellRangeAddress(...))` / `range.Merge()` |
| 3 | 两级表头=横向合并（`staff_income_exporter.py:106`） | ✅（同上） | ✅（同上） | 与 2 同源 |
| 4 | 打印设置 orientation+fitToWidth/Height+fitToPage（`:145-147,253-255` 等） | ✅ | ✅ | NPOI: `sheet.FitToPage=true; sheet.PrintSetup.FitWidth=1`；ClosedXML: `pageSetup.PagesWide/PagesTall + AdjustToPagesPerSheet`（API 名需实写验证） |
| 5 | 页边距 PageMargins（`staff_income_exporter.py:153`） | ✅ | ✅ | `PrintSetup` / `pageSetup.Margins` |
| 6 | **写入真实公式**（`calc_export.py:4-9`：`=A1+B1`、`=SUM(A1:A10)`） | ✅ | ✅ | `cell.SetCellFormula("SUM(A1:A10)")` / `cell.FormulaA1 = ...`；⚠️ 两者的**公式求值引擎均有限**——但本项目**不需要**在写出端求值（openpyxl 同样不计算公式，Excel 打开时才算），语义完全对齐 ✅ |
| 7 | 字体/对齐/边框/填充（5 个导出器全用，`person_settlement_exporter.py:8,15`） | ✅ | ✅ | NPOI 冗长（需 CreateStyle 复用池）；ClosedXML 链式 API 更接近 openpyxl 体验 |
| 8 | 数字格式 `#,##0.00`（`person_settlement_exporter.py:76` 等） | ✅ | ✅ | `DataFormat` / `Style.NumberFormat.Format` |
| 9 | 列宽+行高含自适应（`settlement_report_exporter.py:198-216,185-191`） | ✅ | ✅ | 注意：两者都**不提供"按内容自动计算列宽"**——现有 `min(maxw+2,48)` 逻辑要自己量字符宽（Python 侧也是自算的，平移即可） |
| 10 | 冻结窗格 | ✅ | ✅ | `CreateFreezePane` / `SuspendView.FreezeCells`（现状 openpyxl **未用**冻结窗格，此能力是富余） |
| 11 | 自动筛选 / 数据校验 | ✅ / ✅ | ✅ / ✅ | 现状未用，富余 |

**总评**：**NPOI 11/11 全覆盖**，无结构性缺口。真正的成本不是"能不能"，而是 **API 冗长度**：NPOI 是 Java POI 风格（显式 `CreateRow/CreateCell`、无 A1 寻址、Style 需手动池化），同等写出逻辑代码量约为 openpyxl 的 **1.5~2 倍**。5 个导出器（≈1,100 行 openpyxl 代码）→ 估 **1,800~2,400 行 NPOI 代码**。

### 5.4 读取侧的一个隐性风险（与审计发现呼应）

`date_utils.py:58` 用 `openpyxl.utils.datetime.from_excel` 做 Excel 序列号日期转换，`.xls` 走 xlrd 的日期体系。**换库后 1900/1904 日期系统、闰年 bug 兼容（Excel 的 1900-02-29）等边缘行为必须逐格比对**——日期是本项目核心维度（24 张表中 15+ 张含日期字段）。这必须进 golden-file 回归（第 8.2 节），不能靠肉眼看几个样例。

---

## 6. 数据访问层选型

### 6.1 三个候选

| 维度 | **EF Core 8/9** | **Dapper** | **Microsoft.Data.Sqlite（裸 ADO.NET）** |
|---|---|---|---|
| 许可证 | MIT ✅ | Apache 2.0 ✅ | MIT ✅ |
| net48 兼容 | ❌ **EF Core 5+ 目标 netstandard2.1，.NET Framework 不实现**（最后兼容 net48 的是 EF Core 3.1，已 EOL） | ✅（net35+，零依赖） | ✅（netstandard2.0 → net462+；net48 侧建议钉 8.x，10.x 的 TFM 需实查） |
| 与现有 `db.py` 的映射 | ORM 实体 + 迁移框架——**与现状（手写 SQL + 手写 ALTER 迁移）差异最大** | **最接近现状**：手写 SQL 原样搬，`conn.Query<T>(sql, params)` 对应 `conn.execute(sql, params)` | 最底层，等价于直接用 sqlite3 模块 |
| 引入成本 | 高（要为 24 张表建实体+DbContext+映射，且本项目 SQL 大量使用 strftime/substr/相关子查询，ORM 化反而别扭） | **低** | 低 |
| N+1 是否天然避免 | ❌（懒加载反而是 N+1 制造机；显式 Include 只解决导航属性场景） | ❌ **写什么 SQL 跑什么 SQL** | ❌ |
| LINQ 收益 | 中 | 中（`Query<T>` 强类型化手写 SQL 的结果） | 低 |

### 6.2 推荐：**Dapper + Microsoft.Data.Sqlite**

理由：① 与 `app/db.py` 的手写 SQL 模式**一一对应**，24 张表/25 个索引/11 段迁移的 SQL 可以近乎原样搬运（换 `?` 占位符为 `@param`）；② **同时兼容 net48 与 net10**——这恰好把 multi-targeting 的约束（2.4 节）一并解决，EF Core 则会直接否决 Win7 后补票；③ 学习成本最低，适合单人非全职。

### 6.3 WAL 模式

**无需任何迁移工作**：WAL 是**数据库文件的持久属性**（`db.py:422` 已设 `PRAGMA journal_mode=WAL`，该设置写入 DB 文件头）。C# 侧打开同一文件即继承 WAL。需要做的只是把 Python 侧每次连接都执行的 `PRAGMA foreign_keys=ON`（`db.py:421`，**该 PRAGMA 是每连接的，不持久**）在 C# 连接打开时补上，以及关闭时保留 `wal_checkpoint(TRUNCATE)`（`db.py:559`，Seafile 场景必需）。

### 6.4 Schema 迁移

现状是 `db.py:426-479` 的 11 段"检查列是否存在→ALTER TABLE"手写迁移（幂等）。两条路：
- **原样平移（推荐）**：C# 里写同样的 `PRAGMA table_info` 检查逻辑，~150 行。零学习成本，且保证与现有库文件完全兼容（对第 8 章"共享 SQLite 灰度"是**必要条件**——引入 EF Core 迁移框架会创建自己的 `__EFMigrationsHistory` 表，与 Python 版共存时是脏状态）。
- 引入迁移框架（FluentMigrator 等）：不推荐，理由如上。

### 6.5 关键问题：N+1 在 .NET 下是否天然避免？

**❌ 不会。** 这是本深评必须打掉的幻想：
- `person_settlement._compute` 的 7 处 N+1 是**循环里发查询**的代码模式问题。直译成 C#（`foreach` 里 `cmd.ExecuteReader()`）就是同样的 N+1。
- Dapper/EF 都不会替你把循环查询合并成 JOIN；EF 的懒加载还会**制造**新的 N+1。
- 唯一的区别是每次往返更便宜：Python sqlite3 绑定 ~3-8 μs/次 vs Microsoft.Data.Sqlite ~1.5-2 μs/次（**约 2-5 倍**，非 50 倍——SQLite 本体是同一个 C 库）。
- **结论与总评报告一致**：换栈对 N+1 的改善是 2-5 倍常数因子；改查询模式是 1000 倍。**若为性能换栈，是买椟还珠。**

---

## 7. 按模块代码量实测估算

> 方法：以已读源码的结构为基准（非拍脑袋系数），给出每模块的 Python 行数 → C# 预估区间与膨胀/收缩原因。

| 模块 | Python 行数 | C# 预估 | 膨胀/收缩原因 |
|---|---:|---:|---|
| `app/engine/person_settlement.py`（368 行/74 分支） | 368 | **350~450** | 几乎持平。嵌套 dict 累加逻辑 `Dictionary<string, ...>` 直译；LINQ 在这类多层累加上**帮不上忙**（不是简单的 filter/map）。风险最高的模块：74 个分支口径必须在 golden-file 保护下平移 |
| `app/engine/` 其余（collection 369、calc_data、raw_*、tax_* 等 21 文件 ≈ 5,900 行） | ≈5,900 | **5,500~7,000** | SQL 拼接原样搬（Dapper）；LINQ 可收缩 filter/sum 类循环 ~10%，类型声明膨胀 +10~20% |
| `app/engine/calc_*`（公式引擎 629+227+217+169 ≈ 1,240 行） | ≈1,240 | **1,300~1,600** | 递归下降 parser/tokenizer 平移干净；AST 节点类显式声明比 Python class 略长；`Decimal` 对应 `decimal`（half-up 舍入语义可精确对齐 ✅） |
| `app/importer/`（14 文件 ≈ 2,700 行） | ≈2,700 | **3,200~4,000** | NPOI/calamine 侧读取 API 比 openpyxl 啰嗦；日期解析的 24 分支 `normalize_date` 原样搬；**日期语义必须逐格比对**（5.4 节） |
| `app/exporter/`（5 文件 ≈ 1,100 行） | ≈1,100 | **1,800~2,400** | **膨胀最重**：NPOI 1.5~2×（5.3 节）；若选 ClosedXML 可压到 1.2~1.4× 但引入双库 |
| `app/db.py` | 565 | **500~700** | SCHEMA 字符串 + 11 段迁移逻辑直译（6.4 节推荐不引入迁移框架）；C# 泛型读取省一点装箱代码 |
| `app/ui/`（42 文件 ≈ 13,900 行） | ≈13,900 | **18,000~25,000** | **最大膨胀源**：XAML + code-behind 双份（每页样式进 XAML、逻辑进 .cs）；MVVM 样板（属性通知/命令）；再加第 3.3 节表格基础设施自研 9~16 人日对应的代码量；qfluentwidgets 的 Notion 视觉（290 行 QSS + 自绘 sidebar 569 行 + nav_icons 187 行）全部重做 |
| `main.py` + 杂项 | ≈40 | ~100 | |
| **合计** | **21,523** | **25,000~33,000（1.2~1.5×）** | |

> **换算系数的诚实说明**："Python→C# = 1.2~1.5×"比常见的 1.5~2× 低，因为本项目 Python 侧本就偏啰嗦（显式 dict 结构、大量注释），且部分收缩来自 LINQ 与强类型消除的防御性代码。但 **UI 层是 1.3~1.8×**，而 UI 恰好占大头——所以总量估计要按模块加权，不能拿单一系数乘总数。

---

## 8. 迁移策略（最关键的实操部分）

### 8.1 能否增量？——不能整体增量，但可以**灰度**

桌面单体 GUI 无法"半页 Python 半页 C#"。但第 8.3 节的**共享 SQLite 双轨**让"灰度"成为可能——这是与 Rust/Go/TS 方案（总评报告已判"不可增量"）的**本质区别**，也是本深评最重要的发现。

### 8.2 golden-file 跨语言回归（正确性抓手，必须最先建）

- **机制**：Python 版在固定 fixture 数据上导出 5 类 Excel → 存为基准文件；C# 版对同一 fixture 导出 → 用一个小脚本逐格 diff（值、公式串、合并区域、列宽、number_format、page_setup）。脚本用 Python openpyxl 写（~100 行，读两边产物做对比），**语言无关、可长期复用**。
- **关键点**：审计报告"阶段 0 安全网"（3~4 人日）本来就要建 golden-file——**这部分投入在两条路线间 100% 复用**。无论最终换不换栈，它都是第一笔该花的钱。
- 覆盖面必须包括：`.xls` 读取侧的日期逐格比对（5.4 节）、`calc_export.py` 的公式保留判定（`should_keep_formula`）、33-sheet 文件、列宽自适应。

### 8.3 共享 SQLite 双轨灰度（重点评估：✅ 可行）

**原理**：SQLite 是跨语言文件格式。C#（Microsoft.Data.Sqlite）可直接打开现有 `data/lawfirm.db`，读 Python 版写入的全部 24 张表；WAL 是文件持久属性，无需重设（6.3 节）。

**可行的灰度路径**（写权限单一切换，规避双写冲突）：

| 阶段 | Python 版 | C# 版 | 风险 |
|---|---|---|---|
| G0 | 全功能（现状） | 无 | — |
| G1 | 全功能 | **只读**报表页（各类报表/经办人收款/开票收入——只调 engine+exporter） | 极低：C# 只读不写，SQLite WAL 天然支持多读者并发 |
| G2 | 全功能 | 只读扩到台账查看类页面 | 低 |
| G3 | **收窄为"只负责导入"** | 接管全部查询/编辑/导出 | 中：写权限开始交割 |
| G4 | 退役 | 全功能 | 切换完成 |

**必须正视的三个技术点**：
1. **双写冲突**：两个进程同时写 SQLite（即便 WAL）会碰 `database is locked`。所以灰度纪律是**任一时刻只有一个应用拥有写权限**（上表 G1-G2 期间 C# 纯只读；G3 起 Python 只做导入）。**绝不能两版都可编辑。**
2. **schema 演进耦合**：灰度期间 Python 版若加列（11 段迁移的延续），C# 版读取侧要同步适配。**灰度期应冻结 schema**——这与"业务仍在演进"存在张力，是灰度方案的真实代价。
3. **业务口径漂移**：灰度期两版并存，任何口径修改（如结算规则调整）要改两处。**灰度窗口越短越好**（建议 ≤3 个月），否则双轨成本反噬。

### 8.4 分期路径（若决定走）

| 期 | 内容 | 产出 | 工期 | 结束时系统状态 |
|---|---|---|---:|---|
| **P0** | golden-file 安全网（Python 侧） | 5 类导出基准 + 逐格 diff 脚本 | 3~4 人日 | Python 版被测试保护（**对方案 1 同样有效，可先行**） |
| **P1** | C# 核心库：engine + calc + db（Dapper）+ exporter（NPOI）+ importer，**无 UI**，控制台 harness | 通过 golden-file diff 的 C# 业务内核 | 25~35 人日 | 双版本并存：Python 全功能，C# 核心已验证 |
| **P2** | C# 只读报表 UI（WPF，最常用 4-5 页）+ 共享 SQLite（G1） | 用户日常可用的 C# 报表端 | 12~18 人日 | **双轨灰度开始**，用户真实使用 C# 版 |
| **P3** | 全部 22 页 UI 平移 + 表格基础设施自研 | 功能对齐的 C# 版 | 50~70 人日 | G2→G3 |
| **P4** | 写权限交割、Python 退役、打包分发 | 单一 C# 版 | 5~8 人日 | 完成 |
| **合计** | | | **95~135 人日核心 + 缓冲 ≈ 130~180 人日** | 单人每周 1.5 天 ≈ **1.7~2.3 年** |

### 8.5 4 个 wt-* UI 分支

**在 .NET 栈里全部作废**（它们是 PySide6/QSS 资产）。需要用户明白：这 4 个分支代表的设计探索（页头/tab 切换器三方案）中**有价值的部分是"信息架构决策"而非代码**——那些决策（哪页该有几个 tab、页头放什么）可以平移到 WPF 版。但代码归零，UI 方案选型的必要性在 .NET 路线下**自动消解**（换成"WPF 版视觉规范"这一个新命题）。

---

## 9. 风险清单（针对用户画像）

### 9.1 学习曲线（给可类比描述，不说"陡峭"）

用户现状：能用 Python+PySide6 独立写出 22 页桌面应用、自研公式引擎、处理 Qt 委托自绘。这已经是**中高级 Python 桌面开发水平**。迁移到 C#/WPF 的增量学习项：

| 学习项 | 类比 | 达到"能干活"的量级 |
|---|---|---|
| C# 静态类型系统（泛型/可空引用/接口） | 比 Python type hints 严格一档 | **2~3 周** |
| LINQ | 类似 Python 生成器表达式+itertools 的方法链 | 1~2 周（顺手） |
| async/await、IDisposable/using | Python 有对应物（asyncio/with），语义不同处需踩坑 | 1~2 周 |
| XAML + 数据绑定 + MVVM | **全新范式**，最陡的一段：绑定路径、DataContext 继承、ValueConverter、依赖属性。类比：从"手写 setItem 刷表格"跳到"声明式绑定" | **1~2 个月** |
| Visual Studio / MSBuild / NuGet 工程 | 类比：从单文件脚本+setup.bat 到 IDE 工程化 | 1~2 周 |
| WPF 样式/模板体系 | 类比 QSS 但更强大更繁琐（ControlTemplate 可完全重绘控件） | 2~4 周 |

**合计：从开工到恢复现有生产效率约 2~3 个月全职当量；对每周 1.5 天的非全职节奏即 4~6 个月。** 这段时间里 Python 版必须继续维护——两线作战是单人开发者最危险的处境。

### 9.2 业务演进 vs 2-3 年迁移期

退款模块、invoice 补录、分成计算引擎、全局字号缩放、"?"帮助图标等需求不会停。风险不是"做不完"，而是**双轨维护**：灰度期（8.3 节）内任何口径修改要改两处；UI 冻结（新需求不进 Python 版 UI，否则 P3 工作量雪崩）意味着**迁移期用户 UI 体验停滞**。缓解：P0/P1 与方案 1 的优化共存——**先在 Python 版把性能修好，迁移期至少用得舒服**。

### 9.3 Seafile

第 4.3 节已核：NuGet 缓存在用户目录不进同步 ✅，`bin/obj` 需排除 ⚠️，SDK 每台 1-2 GB。**总体与 Python 方案同级，不构成否决项**（对比：Rust `target/` 与 `node_modules` 才是高危）。

### 9.4 退出成本（投入 60 人日后放弃，能留下什么）

| 留下的 | 价值 | 残值率 |
|---|---|---|
| golden-file 测试资产（P0） | **极高**——对 Python 版立即生效，方案 1 的阶段 0 就是它 | ~100% |
| C# 业务内核（P1：engine/calc/db/exporter，golden 已验证） | 中——若未来重启迁移是现成地基；但对日常 Python 使用无贡献 | ~40%（只在重启时兑现） |
| C# 只读报表端（P2） | 低~中——若用户愿意长期双轨可用，但双轨本身就是负担 | ~20% |
| 学习投入 | 个人资产 | — |
| **未留下的** | 3,911 行 Python 测试的等价物、4 个 wt-* 分支、以及**最宝贵的：2~3 年里本可用于演进业务的时间** | — |

> 对比：方案 1（Python 重组）投入 20~28 人日，**每一分钱都落在现役代码上，退出残值 100%**。

---

## 10. 最终判断

### 10.1 结论

> **现在不值得迁移。** 推荐：**留在 Python，执行方案 1（20~28 人日重组），并用方案 8（2~5 人日）顺手解决启动/体积**。这份深评的查证结果不仅没有推翻、反而**加强**了总评报告的结论——三个新事实都在加码：
> 1. **.NET 8/9 支持只剩 2 个月**（2026-11-10）——连"稳妥的 LTS"都必须是 .NET 10，迁移的版本选型自由度比预想更窄；
> 2. **"只收免费开源"约束下，WinUI 3 的表格是结构性真空**（第一方无 DataGrid、CommunityToolkit 未移植、商业出局），WPF 的内置 DataGrid 也自 2010 年零新特性——**两个候选的表格短板都落在"自研 9~16 人日"上，而这套东西在 Python 里已经写完且在用**；
> 3. **N+1 不会因换栈消失**（6.5 节）——性能痛点（用户最初的动机）在 .NET 里依然要靠同样的调用结构改造解决。

### 10.2 触发条件（什么情况下值得）

| 触发条件 | 说明 |
|---|---|
| **产品化**：要从"律所自用"变为对外销售/多客户部署 | 需要安装器、授权、更小分发体积、更强类型防呆——.NET 优势兑现 |
| **交接**：未来交给 .NET 背景的维护者（如招聘/外包） | 语言匹配度压倒一切迁移成本 |
| **业务规模 ~10 倍以上**（3 万+ 票/年）且 Python 优化后仍不达标 | 但需先用实测数据证明方案 1 优化后确实不达标（当前推演：达标，0.7-1.7 秒@10×） |
| **Windows 11-only 且愿赌社区控件**：接受放弃 Win7+Win10，且实测 `WinUI.TableView` 合格 | 此时 WinUI 3 + Fluent 红利可期 |
| **C# 是用户个人的长期职业方向** | 学习投入的回报计入个人账户，项目只是练手场 |

### 10.3 用户真正想要的收益，都有更便宜的获取方式

| 用户想要的 | .NET 路线成本 | 更便宜的获取方式 |
|---|---|---|
| **类型安全** | 130~180 人日 + 全部重写 | Python 渐进加 type hints + **mypy**（严格模式从新模块开始），~3-5 人日；ruff 全量静态检查 ~1 人日 |
| **架构分层** | 同上（且要重学 MVVM） | 方案 1 阶段 5 的 `service/repository` 局部解耦（已设计，5~7 人日） |
| **启动快/包小** | 同上 | 方案 8：PyInstaller onedir + 延迟导入 + Nuitka 试用，**2~5 人日** |
| **更好的表格** | 同上（自研或赌社区） | **现有 QTableWidget 基础设施已经够用**（审计确认：冻结列/两级表头/筛选/列持久化全部在用且有冒烟测试）——这不是当前短板 |
| **现代视觉** | 全部重做 | qfluentwidgets 主题 + 现有 4 个 wt-* 分支正是为此准备的，成本远低于重写 |

### 10.4 若用户看完仍决定走 .NET：最小可行迁移路径（最先 20 人日）

| 步骤 | 人日 | 产出与验证 |
|---|---:|---|
| 1. golden-file 安全网（= 方案 1 阶段 0，两路线共用） | 3~4 | 5 类导出基准 + 逐格 diff 脚本；**验证**：故意改坏口径测试必须红 |
| 2. .NET 10 + WPF 空壳工程 + Dapper 直连现有 `data/lawfirm.db`（只读） | 3 | 读出 invoice/collection 行数与 Python 版一致；**验证**：SQL 计数与抽样数值比对 |
| 3. C# 移植 `person_settlement._compute` + `collection.invoice_rows/handler_rows`（只读计算，无 UI） | 8~10 | 用 Python 版输出做 golden diff（JSON 序列化对比 12 月×11 键）；**验证**：逐位一致 |
| 4. C# 用 NPOI 重写 1 个导出器（`person_settlement_exporter`） | 3~4 | **验证**：C# 产物 vs Python 基准逐格 diff（含列名/合并/格式/page_setup） |
| **20 人日后的 go/no-go 判据** | | ① golden diff 全绿 ② 用户实测 C# 报表计算耗时 ③ **主观判据：这 20 天里写 C# 的体验是否让你愿意再写 110~160 天**。任一不满足 → 停止迁移，golden 资产转投方案 1（零浪费） |

---

## 附录：查证来源与日期

| # | 事实 | 来源 | 查证日期 |
|---|---|---|---|
| 1 | .NET 8/9/10 OS 支持：Win7/8.1 ❌；"上次支持 Win7/8.1 的是 .NET 6（2024-11-12 结束）"；Win10 行仅列 21H2/1809/1607 LTSC/Enterprise | Microsoft Learn《在 Windows 上安装 .NET》 | 2026-09-09 |
| 2 | .NET 10 LTS：2025-11-11 发布，支持至 **2028-11-14**；.NET 8/9 支持至 **2026-11-10**；LTS=3 年/STS=2 年 | Microsoft《.NET Support Policy》/ dotnet.microsoft.com 下载页 | 2026-09-09 |
| 3 | .NET Framework 4.8 可安装至 Win7 SP1；Win11 24H2+ 预装 4.8.1；表注明 Win7/8.1 "listed but out-of-support"；.NET Framework 支持随父 OS 生命周期 | Microsoft Learn《.NET Framework system requirements》/《生命周期 FAQ》 | 2026-09-09 |
| 4 | WinUI 3 / Windows App SDK 最低 **Windows 10 1809 (build 17763)**；无 Win7 路径；部署需 Windows App SDK 随应用或装到目标机 | Microsoft Learn《WinUI 入门》 | 2026-09-09 |
| 5 | **WinUI 3 无第一方 DataGrid**；"CommunityToolkit DataGrid 仅 UWP 7.1.0，未移植 WinUI 3"；官方建议评估第三方网格或社区 `WinUI.TableView`；官方明示勿在 WinUI 3 用 UWP 版 DataGrid | Microsoft Learn《Migrate WPF app patterns to WinUI 3》《从 UWP 迁移到 WinUI 3 时支持的内容》《在 WinUI 应用中显示表格数据》 | 2026-09-09 |
| 6 | WPF 内置 DataGrid 自 2010（.NET 4.0）零新特性；dotnet/wpf 存在大数据集滚动卡顿/内存泄漏的长期 issue；.NET 10 包含 WPF 性能优化与 Fluent 改进 | Xceed 2026 技术博客（WPF DataGrid in 2026）+ Microsoft 官方文档交叉印证 | 2026-09-09 |
| 7 | Dapper 支持 net35~net481（零依赖）；Microsoft.Data.Sqlite 8.x 目标 netstandard2.0（兼容 net462+） | NuGet Gallery 包页面（Dapper 1.32/2.1.x、Microsoft.Data.Sqlite 8.0.x） | 2026-09-09 |
| 8 | NPOI：Apache 2.0，.xls(HSSF)/.xlsx(XSSF) 读写、数据校验、公式；EPPlus 5+ 转 Polyform Noncommercial（商用需付费）；ClosedXML：MIT、**仅 xlsx**、API 现代、无图表 | HackerNoon《C# Excel Library In-Depth Comparison: Tested for 2026》、ADR-011（2026-02-27）、NPOI/ClosedXML 官方仓库 | 2026-09-09 |
| 9 | HandyControl：**MIT**、80+ 控件、DataGridEx、支持 .NET Framework 4.0 与 .NET 8+（Shared Project 双目标）；MaterialDesignThemes/MahApps.Metro：MIT | GitHub/GitCode 仓库与 2026 选型指南 | 2026-09-09 |

> **未实测声明**：本报告为静态评估，未运行任何 C# 代码或计时测试。性能相关数字（往返开销 2-5 倍、启动 0.3-1 秒等）标注了量级来源与假设；第 10.4 节的 20 人日试点正是为把关键假设变成实测而设计。Win10 22H2 的生命周期状态（2025-10 结束主流支持）为公开生命周期事实，并由来源 1 中"Win10 支持表不再含 22H2 消费版"间接印证。
