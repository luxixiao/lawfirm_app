# T5 任务简报（主理人 → 架构师 高见远）

> 本文件是主理人对 T5 的上下文交接，含我实地核对的源码事实。请以本文件 + 你自行研读的源码为准。

## 你的任务

产出 **T5 实现规格**（设计任务，**不写代码、不改文件**），供工程师照着实现。范围 = Pilot 收口的最后一环。

T5 完成后，Pilot 的 T1–T5 全绿，即可进入 T6（三件验证 + go/no-go 评审）。

## 已核对的项目现状（我实地扫过的，可直接采信）

### 已有 C# 代码（1,590 行）

| 文件 | 行数 | 作用 |
|------|-----:|------|
| `csharp/LawFirm.Exporter/SettlementEngine.cs` | 470 | person_settlement 计算引擎（已逐行镜像 Python） |
| `csharp/LawFirm.Exporter/PersonSettlementExporter.cs` | 340 | 个人结算总表导出器（**端到端 42/42 全绿**） |
| `csharp/LawFirm.Exporter/SettlementReportExporter.cs` | 336 | 月度结算表导出器（**t2_verify.bat 已验证**） |
| `csharp/LawFirm.Exporter/ExpenseCatHelper.cs` | 39 | 费用分类辅助 |
| `csharp/LawFirm.Cli/Program.cs` | 115 | CLI：`--report month\|person`、`--year`、`--month`、`--out`、`--out-dir`、`--person` |
| `csharp/LawFirm.Data/DbConnection.cs` | 74 | SQLite 只读连接（query_only + ReadOnly 双保险） |
| `csharp/LawFirm.Data/InvoiceRepo.cs` + `InvoiceRow.cs` | 87 | invoice 表读取 |
| `csharp/LawFirm.Data/CollectionRepo.cs` + `CollectionRow.cs` | 63 | collection 表读取 |
| `csharp/LawFirm.App/MainWindow.xaml(+cs)` | 66 | **仅"读表显示行数"的演示窗口，无真实 UI** |
| `csharp/LawFirm.App/App.xaml(+cs)` | 10 | 应用入口 |

技术栈已锁定：**.NET 10 + WPF + NPOI 2.7.x + Dapper 2.1.x + Microsoft.Data.Sqlite 10.x + CPM**（`Directory.Packages.props` 已启用传递依赖 pinning，CVE 锁定：ImageSharp 2.1.13 / Crypto.Xml+Pkcs 10.0.12 / SQLitePCLRaw.bundle_e_sqlite3 2.1.13）。

**注意**：`CommunityToolkit.Mvvm` 和 `HandyControl` **尚未引入**，NuGet 源里目前只有 Dapper / Microsoft.Data.Sqlite / NPOI。要引入需在 `Directory.Packages.props` 加版本 + 各 csproj 加引用。

### Python 侧参照（T5 要对标的视觉与行为基准）

- 目标页：`app/ui/settlement_view.py`（**956 行**，全部页面里最大的一个）—— 侧栏「各类报表」页
- 视觉基线：`app/ui/style.py`（453 行）+ `app/ui/scale.py`（105 行）
- 表格基础设施：`app/ui/table_view.py`（471 行）、`app/ui/table_features.py`（725 行）、`app/ui/column_layout.py`（490 行）
- 页头组件：`app/ui/widgets.py`（271 行，含 `PageHeader`）
- 侧栏：`app/ui/sidebar.py`（569 行）+ `app/ui/nav_icons.py`（187 行）

**设计基线（用户已定案的 D-Notion clean）**：left indent **24px** / top margin **16px** / body spacing **12px**（单位 = `scale.px()`），跨 22 页统一。

### 计划文档原文（T5 一行）

> **T5 Notion 风 UI 骨架 + 异步进度条** | 1–2 人日 | `LawFirm.UI/SettlementView.xaml(+cs)`、`ProgressWindow.cs`、HandyControl 主题 | 依赖 T3,T4 | 产出与验证：「各类报表」页 1 页高保真 ≥80% 接近；**导出期间窗口可拖动**

### T5 的验收门槛（来自计划 §1.3 ③）

> 主框架（无边框窗 + 侧栏 + 表格冻结列 + 两级表头 + 列布局持久化）**视觉接近度 ≥ 80%**（对照现有 qfluentwidgets 22 页截图）；完成「各类报表」页 1 页高保真，**用户主观评分 ≥ 4/5**。

## 需要你的规格覆盖的内容（这是我的判断，你可增删但要说理由）

1. **工程结构决策**：新建 `LawFirm.UI` 项目，还是扩展现有 `LawFirm.App`？给理由（考虑现有 `LawFirm.App` 只有 66 行的演示代码，以及 sln 已有 4 个项目）。
2. **主框架骨架**：无边框窗口（用户现有 Python 版是 FramelessWindow + 自绘标题栏，含最小化/最大化/关闭 + 拖拽）如何在 WPF 落地；侧栏（分组折叠 + 图标 + 宽度动画）的实现路径。
3. **表格基础设施**（这是全量重写的**最大风险点**，请重点设计）：
   - 冻结列（Python 用 QTableView + 自算列）
   - 两级表头（横向合并）
   - 列宽/列序持久化（Python 用 `column_layout.py` + QSettings → WPF 对应什么？）
   - 按列筛选 / 悬停全文 / 像素滚动 / 选中保色（Python `table_features.py` 的 5 项交互）
   - **WPF 原生 DataGrid 能不能扛住？还是需要第三方？** 给出明确结论 + 依据（注意硬约束：**仅免费开源**，EPPlus/Xceed/Syncfusion/Telerik/DevExpress 等商业授权一律排除）
4. **「各类报表」页高保真**：`settlement_view.py` 956 行的功能清单（有什么按钮/表格/交互），以及 XAML 布局结构。
5. **异步进度条**：Python 版导出会冻结 UI（这是要解决的痛点）。WPF 里用 `async/await` + `IProgress<T>` + 可取消 `CancellationToken` 的方案，窗口保持可拖动。
6. **HandyControl 引入评估**：许可证（MIT）、.NET 10 兼容性、是否真的需要（能否用原生 WPF + 自绘样式达成 Notion 风？）。
7. **任务分解**：T5 拆成有序子任务（T5.1 / T5.2 …），每项含文件路径、依赖、验收标准。
8. **待明确事项**：你无法从源码确定的，列出来由我转交用户。

## 硬约束（务必遵守）

- **仅免费开源**（MIT/Apache/BSD/GPL）；任何商业授权组件排除
- **数据零风险**：Pilot 期只读，`query_only` 纪律不能破
- **不改 Python 侧任何文件**
- **本任务不写代码**：只出规格文档，落盘到 `docs/t5-ui-skeleton-spec.md`
- 技术栈已锁定为 .NET 10 + WPF，不要重新论证选型（选型已在 `docs/csharp-wpf-refactor-plan-2026-09-09.md` 定案）
- **沙箱无 NuGet 网络**：工程师无法 build，所以规格必须精确到能被静态审查

## 交付物

`docs/t5-ui-skeleton-spec.md`，结构建议：
- 现状核对（哪些已有、哪些缺）
- 工程结构决策 + 理由
- 主框架 / 侧栏 / 表格基础设施 三块的实现规格
- 「各类报表」页 XAML 结构
- 异步进度条方案
- 依赖包清单（NuGet，含版本与许可证）
- 子任务分解表（文件路径 + 依赖 + 验收标准）
- 风险与待明确事项
