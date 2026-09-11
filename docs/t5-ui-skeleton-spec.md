# T5 · Notion 风 UI 骨架 + 异步进度条 · 实现规格

> 版本：v1（2026-09-11）
> 分支：`pilot/dotnet`
> 作者：架构师（高见远）
> 目标读者：工程师（本文件即实现依据）
> 上位依据：`docs/csharp-wpf-refactor-plan-2026-09-09.md`（§1.3 ③ 验收门槛、§2 技术栈锁定、§6 Pilot T5 行）
> 行文精确度对标：`docs/person-settlement-exporter-port-spec.md`
> **本文件只描述设计，不含实现，唯一被允许写入的文件就是本文件本身。不改 Python 侧任何文件、不改任何 C# 源码。**

---

## 0. 任务边界与验收门槛（先读）

**计划原文（§6 T5 行，逐字）**：

> **T5 Notion 风 UI 骨架 + 异步进度条** | 1–2 人日 | `LawFirm.UI/SettlementView.xaml(+cs)`、`ProgressWindow.cs`、HandyControl 主题 | 依赖 T3,T4 | 产出与验证：「各类报表」页 1 页高保真 ≥80% 接近；**导出期间窗口可拖动**

**验收门槛（计划 §1.3 ③，逐字）**：

> 主框架（无边框窗 + 侧栏 + 表格冻结列 + 两级表头 + 列布局持久化）**视觉接近度 ≥ 80%**（对照现有 qfluentwidgets 22 页截图）；完成「各类报表」页 1 页高保真，**用户主观评分 ≥ 4/5**。

**⚠️ 重要范围裁定（本规格的诚实声明）**：

计划把 T5 定为 **1–2 人日**，其列出的 C# 文件只有 3 个（`SettlementView.xaml(+cs)`、`ProgressWindow.cs`、HandyControl 主题）。但 §1.3 ③ 的门槛却要求**主框架全部要素**（无边框窗 + 侧栏 + 冻结列 + 两级表头 + 列布局持久化）+ 1 页高保真。

**这两者自相矛盾**：把 Python 侧 `table_view.py`(471)+`table_features.py`(725)+`column_layout.py`(490)=**1,686 行**的表格基础设施，加上 `sidebar.py`(569)+`main_window.py`(461) 的主框架，压缩进 1–2 人日是不现实的。

**本规格的处置**：诚实拆为 **2 个工作包**，并明确 T5 的「最小可交付」与「完整门槛」的差距：

| 工作包 | 内容 | 预估人日 | 对应门槛 |
|---|---|---|---|
| **T5-MUST**（本次收口必须交付） | 主框架骨架（无边框窗 + 标题栏 + 侧栏 + 页面栈）+ 表格基础设施（冻结列/两级表头/像素滚动/悬停全文/选中保色/列持久化）+ 「各类报表」页 + 异步进度条 | 3–5 人日 | §1.3 ③ 全部 |
| **T5-SHOULD**（时间允许再做） | 按列筛选 + 列设置对话框（`column_settings_dialog` 等价） | +1–2 人日 | 不属于 ③，属全量期 P4 |

→ **本规格覆盖 T5-MUST 全部 + T5-SHOULD 的接口预留**。若用户坚持 1–2 人日不变，则可在 T5-MUST 内砍「两级表头」（仅「年度聘用结算表」tab 用到）与「列设置对话框」，但**冻结列 + 列持久化 + 异步进度条不可砍**（它们是门槛项 + 真实痛点）。

---

## 1. 现状核对（已有 vs 缺失）

### 1.1 已有 C# 资产（实地核对，可直接采信）

| 文件 | 行数 | 状态 | T5 如何使用 |
|---|---:|---|---|
| `csharp/LawFirm.Exporter/SettlementEngine.cs` | 470 | ✅ T3 完成 | T5 调 `SettlementEngine.Build(conn, year, person, personType)` |
| `csharp/LawFirm.Exporter/PersonSettlementExporter.cs` | 340 | ✅ T4 完成（42/42 全绿） | T5 的 UI 导出按钮直接调 `ExportAll` / `ExportOne` |
| `csharp/LawFirm.Exporter/SettlementReportExporter.cs` | 336 | ✅ | 「月度结算表」tab 调 `ExportReport` |
| `csharp/LawFirm.Cli/Program.cs` | 115 | ✅ | 不修改；仅参考其调用范式 |
| `csharp/LawFirm.Data/DbConnection.cs` | 74 | ✅ `FindDatabase()` / `OpenReadOnly(path)` | T5 只读打开 |
| `csharp/LawFirm.App/MainWindow.xaml(+cs)` | 66 | ⚠️ **仅「读表显示行数」的演示窗口** | T5 将**整体替换**（见 §2 决策） |
| `csharp/Directory.Packages.props` | 31 | ✅ CPM 已启用 + CVE pinning | T5 需追加 `CommunityToolkit.Mvvm` + `HandyControl` |
| `csharp/lawfirm.sln` | 39 | ✅ 4 项目 | T5 新增 `LawFirm.UI` 项目 |

### 1.2 缺失（T5 要新建的全部）

| # | 缺口 | 对应 Python 源 | 行数量级 |
|---|---|---|---|
| G1 | 无边框窗口 + 自绘标题栏 | `main_window.py:105-171`（`AppTitleBar`） | ~70 |
| G2 | 分组折叠侧栏 + 图标 + 宽度动画 | `sidebar.py`(569) + `nav_icons.py`(187) | ~750 |
| G3 | 页面栈 + 导航注册表 | `main_window.py:56-93`（`NAV_GROUPS`）、`:198-221` | ~120 |
| G4 | 表格基础设施（6 项交互） | `table_features.py`(725) + `table_view.py`(471) | ~1,196 |
| G5 | 列布局持久化 | `column_layout.py`(490) | ~490 |
| G6 | 「各类报表」页 4 个 tab | `settlement_view.py`(956) | ~956 |
| G7 | 异步进度条 | Python 无（**这是要解决的新痛点**） | ~120 |

### 1.3 Python 侧视觉基线的**精确数值**（T5 必须复刻）

**设计基线（用户已定案 D-Notion clean，简报第 40 行）**：left indent **24px** / top margin **16px** / body spacing **12px**（单位 = `scale.px()`）。

**调色板（`style.py:28-45` `_light()`，逐 token 抄录）**：

| token | 值 | 用途 |
|---|---|---|
| `canvas` | `#f7f6f3` | 内容区底色（`QMainWindow, QWidget#pageArea`） |
| `bg_side` | `#f7f6f3` | 侧栏底色 |
| `bg` | `#FFFFFF` | 卡片/表格底色 |
| `bg_table` | `#fbfbfa` | 表头底 / 悬停提示底 |
| `bg_hover` | `#efedea` | hover 底 |
| `bg_select` | `#e3e1db` | 选中底 |
| `border` | `#e9e9e7` | 主分隔线 |
| `border_2` | `#DADAD7` | 控件描边 |
| `grid` | `#F1F1EF` | 表格网格线 |
| `text` | `#37352F` | 主文字 |
| `text_mute` | `#787774` | 次级文字 |
| `text_faint` | `#9b9a97` | 三级文字（分组标题） |
| `accent_blue` | `#2eaadc` | 主按钮 / tab 下划线 |
| `btn_pri_bg` | `#37352F` | 墨黑主按钮 |

**字体**：`"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif`，基准 **13px**（`style.py:87`）。

**关键尺寸（`sidebar.py:32-40` 基准值，均经 `scale.px()` 派生）**：

| 量 | 值 |
|---|---|
| 侧栏展开宽 | 240 |
| 侧栏收起宽 | 60 |
| 分组行高 | 34 |
| 子项行高 | 31 |
| 侧栏图标 | 20 |
| 子项左 padding | 36（图标 14 + 间隔） |
| 标题栏高 | 36（`main_window.py:111`） |
| 表格行高 | 34（`table_view.py:289`） |
| 冻结列底色 | `#EAEAEA`（`table_features.py:36`） |
| 排序列表头底 | `#E3F2FD`，排序三角 `#1F6FEB`（`table_features.py:116,119`） |
| 两行表头高 | 44（`table_features.py:244`） |

**侧栏分组（`main_window.py:56-93` `NAV_GROUPS` 逐字）**：

```
数据导入：导入台账(import) / 导入记录(batch) / 导入复核(review) / 修改记录(audit) / 发票补录(manual)
台账查看：销项发票(invoice_ledger) / 发票台账(ledger_doc) / 费用台账(expense_ledger) / 工资表(salary_ledger)
业务数据：发票收款情况(invoice) / 经办人发票收款情况(handler_all) / 预收款(prepayment) / 退款(refund)
工资个税：工资累计(salary_summary) / 1-11月个税申报(tax_declaration) / 费用扣除(tax_deduction)
分成计算：计算表(calc)
各类报表：各类报表(settlement)
数据维护：员工管理(staff) / 费用类型(expense_cat) / 数据情况(data_clear) / 快照(snapshot)
```

**T5 Pilot 期只实现「各类报表(settlement)」1 页**，其余 21 项在侧栏渲染为**可点击但显示占位页**（`_PlaceholderPage` 等价），保证导航结构 100% 对齐、视觉接近度达标，同时不虚报功能。

---

## 2. 工程结构决策：新建 `LawFirm.UI` 项目

### 2.1 结论

**新建 `csharp/LawFirm.UI/`（类库，`net10.0-windows`，`UseWPF=true`，`OutputType=Library`）**，并保留 `LawFirm.App` 作为**唯一可执行入口**（WinExe），由它引用 `LawFirm.UI`。

### 2.2 理由（基于实地核对的事实，非偏好）

1. **`LawFirm.App` 现有 66 行演示代码，本来就是一次性验证壳**（`MainWindow.xaml.cs:9-15` 注释自述「Pilot T2 只读验证」）。把 22 页 UI（全量期约 50–70 人日，计划 §7 P4）塞进这个项目，会让「验证壳」与「产品 UI」职责混在一起。
2. **计划 §4.3 已明确定义 `LawFirm.UI.*` 命名空间**（22 页映射表每行都是 `SettlementViewModel / SettlementView`），且 §7 P4 的交付物写的就是 `LawFirm.UI.*` + `TableInfra`。**新建 `LawFirm.UI` 是执行既定计划，不是新决策。**
3. **`LawFirm.App` 保持 WinExe 是必要的**：WPF 的 `App.xaml`/`Application` 必须落在可执行程序集里；类库做 `StartupUri` 会引入隐蔽的启动顺序问题。
4. **依赖方向干净**：`LawFirm.App → LawFirm.UI → LawFirm.Exporter → (LawFirm.Data)`。`LawFirm.UI` 不引用 `LawFirm.Data` 的写路径（Pilot 只读纪律），只经 `LawFirm.Exporter` 的公共 API 触数据。

### 2.3 目标项目结构（相对仓库根）

```
csharp/
├─ lawfirm.sln                                  [改] 追加 LawFirm.UI 项目 + 构建配置
├─ Directory.Packages.props                     [改] 追加 2 个包版本
├─ LawFirm.App/
│   ├─ LawFirm.App.csproj                       [改] 加 ProjectReference → LawFirm.UI
│   ├─ App.xaml                                 [改] 合并 LawFirm.UI 的资源字典
│   ├─ App.xaml.cs                              [改] 启动改为 new MainWindow()（显式，不用 StartupUri）
│   ├─ MainWindow.xaml                          [删/改] 66 行演示窗 → 改为壳（见下）
│   └─ MainWindow.xaml.cs                       [删/改] 内容迁移到 LawFirm.UI
└─ LawFirm.UI/                                  [新] 类库项目
    ├─ LawFirm.UI.csproj                        [新]
    ├─ Themes/
    │   ├─ Palette.xaml                         [新] 调色板（§1.3 token → ResourceDictionary）
    │   ├─ NotionControls.xaml                  [新] 按钮/下拉/表格/滚动条/标签 样式
    │   └─ Typography.xaml                      [新] 字号/字重（13px 基准）
    ├─ Shell/
    │   ├─ MainShellWindow.xaml(+cs)            [新] 无边框主窗（WindowStyle=None, AllowsTransparency）
    │   ├─ TitleBar.xaml(+cs)                   [新] 自绘标题栏（页名 + 三按钮 + 拖拽）
    │   ├─ Sidebar.xaml(+cs)                    [新] 分组折叠侧栏
    │   ├─ SidebarItem.xaml(+cs)                [新] 子项按钮
    │   ├─ NavIcons.cs                          [新] 7 枚图标（Geometry 静态资源）
    │   └─ NavigationService.cs                 [新] NAV_GROUPS 注册表 + 页面栈
    ├─ Controls/
    │   ├─ NotionDataGrid.cs                    [新] 封装：冻结列 + 像素滚动 + 悬停全文 + 选中保色
    │   ├─ TwoTierHeaderGrid.cs                 [新] 两级表头
    │   ├─ ColumnLayoutManager.cs               [新] 列宽/列序/显隐/冻结 持久化
    │   ├─ TableFilterController.cs             [新] T5-SHOULD，按列筛选（接口先定）
    │   └─ PageHeader.xaml(+cs)                 [新] 页头（标题 + 「?」）
    ├─ Dialogs/
    │   ├─ ProgressWindow.xaml(+cs)             [新] 异步进度条（可拖动 + 可取消）
    │   └─ ExportScopeDialog.xaml(+cs)          [新] 多选经办人（对应 gen_selected）
    ├─ Views/
    │   ├─ PlaceholderView.xaml(+cs)            [新] 「功能待迁移」占位页
    │   └─ Settlement/
    │       ├─ SettlementView.xaml(+cs)         [新] 4 个 tab 宿主
    │       ├─ PersonalSettlementTab.xaml(+cs)  [新] tab1 个人结算总表
    │       ├─ MonthlyReportTab.xaml(+cs)       [新] tab2 月度结算表
    │       ├─ StaffIncomeTab.xaml(+cs)         [新] tab3 年度聘用结算表（两级表头）
    │       └─ InvoiceIncomeTab.xaml(+cs)       [新] tab4 开票收入表
    ├─ ViewModels/
    │   ├─ ViewModelBase.cs                     [新] ObservableObject 基类
    │   ├─ SettlementViewModel.cs               [新] settlement 页总 VM
    │   ├─ PersonalSettlementViewModel.cs       [新]
    │   ├─ MonthlyReportViewModel.cs            [新]
    │   ├─ StaffIncomeViewModel.cs              [新]
    │   ├─ InvoiceIncomeViewModel.cs            [新]
    │   └─ ProgressViewModel.cs                 [新]
    └─ Services/
        ├─ SettlementQueryService.cs            [新] 包 SettlementEngine + 后台线程
        ├─ ExportService.cs                     [新] 包 PersonSettlementExporter/SettlementReportExporter + IProgress
        └─ ColumnStateStore.cs                  [新] 列状态 JSON 读写
```

> **注意**：Python 侧的 `settlement_view.py` 是「1 个 widget 里塞 4 个 tab」，C# 侧拆为 4 个独立 UserControl（可测性 + XAML 可读性），但**外层 tab 结构、tab 名、tab 顺序必须逐字对齐**（`settlement_view.py:153-156`）。

---

## 3. 主框架实现规格

### 3.1 无边框窗口（G1）

**Python 现状**（`main_window.py:173-178, 283-287`）：
- 基类 `qframelesswindow.FramelessWindow`（QWidget，非 QMainWindow）。
- 自建 `AppTitleBar(TitleBar)`，高 36px，**左侧显示当前页名**（无品牌文字、无图标）。
- 内容放进自身 `QVBoxLayout`，顶部 margin = `titleBar.height()`。
- 窗口 `resize(1280, 820)`，`setMinimumSize(1100, 650)`。
- 三按钮 hover/按下态（`main_window.py:155-170`），关闭按钮 hover 红底 `#E81123`。

**WPF 落地规格**：

| 项 | WPF 实现 | 依据 |
|---|---|---|
| 去系统标题栏 | `WindowStyle="None"` + `AllowsTransparency="False"`（**保持 False**，否则失去硬件加速与 Aero 吸附） | resizing 由 `WindowChrome` 提供 |
| 边缘缩放 + 吸附 | `<WindowChrome CaptionHeight="36" ResizeBorderThickness="6" GlassFrameThickness="0" CornerRadius="0"/>` | WPF 官方机制，替代 `FramelessWindow` 库 |
| 拖拽 | `WindowChrome.CaptionHeight=36` 自动使标题栏区域可拖（**无需手写 `DragMove`**） | 比 Python 手写更省 |
| 双击最大化 | 在 `TitleBar` 上挂 `MouseLeftButtonDown`，`ClickCount==2` → 切换 `WindowState` | 对齐 `FramelessWindow` 行为 |
| 高 DPI | csproj 加 `<ApplicationHighDpiMode>PerMonitorV2</ApplicationHighDpiMode>`（默认即 V2）+ `UseLayoutRounding="True"` | 避免 1px 描边模糊 |
| 尺寸 | `Width="1280" Height="820" MinWidth="1100" MinHeight="650"` | 逐字对齐 `main_window.py:178,181` |
| 圆角 | 不设（Python `FramelessWindow` 默认直角） | 视觉一致 |

**⚠️ 已知 WPF 差异（诚实标注）**：WPF `WindowStyle=None` 下，**最大化时窗口会覆盖任务栏**（`WindowChrome` 的经典问题）。处置：处理 `StateChanged`，最大化时用 `MaxHeight = SystemParameters.WorkArea.Height + 8` 补偿，或在 `SourceInitialized` 里挂 `WM_GETMINMAXINFO`。**列入待明确事项 U1**（是否接受小瑕疵）。

### 3.2 标题栏（`TitleBar.xaml`）

```
[ Grid, Height=36, Background={StaticResource BgSide}, BorderBottom=1px {Border} ]
├─ Col 0 (*)  TextBlock  PageNameText  — 13px YaHei / {Text} / Margin=12,0,0,0 / VCenter
│               TextTrimming="CharacterEllipsis"（对应 Python _elide）
└─ Col 1 (Auto) StackPanel Horizontal  — 3 个 46x36 按钮
     ├─ MinButton    (—)  hover bg  rgba(0,0,0,0.10)
     ├─ MaxButton    (☐)  hover bg  rgba(0,0,0,0.10)
     └─ CloseButton  (✕)  hover bg  #E81123 + 白字（main_window.py:169）
```

- **页名来源**：`NavigationService.CurrentPageTitle`（= `NAV_GROUPS` 的显示名），`select(key)` 时更新。
- **图标**：用 `Path` 数据（`M0,5 H10` 等 1.5px 描边）而非 Unicode 字符（Unicode 字符在不同字体下粗细不一，Python 用库自绘）。
- **对比度**：深色档暂无需求（Pilot 只做 `notion_light`），但资源字典按 token 组织，后续加深色只增一个 `PaletteDark.xaml`。

### 3.3 侧栏（`Sidebar.xaml`）

**形态**：单列 6 大类 + 折叠 + 收起为图标列（`sidebar.py` 方案 A），宽 240（展开）/ 60（收起）。

**WPF 结构**：

```
Border  Background={BgSide}  BorderRight=1px {Border}  Width={Binding SidebarWidth}
└─ Grid Rows=[Auto, *, Auto]
   ├─ Row0  StackPanel Horizontal  — [折叠按钮 ›/‹] [「固定」ToggleButton]  (Margin 10,8,10,4)
   ├─ Row1  ScrollViewer VerticalScrollBarVisibility=Auto
   │   └─ ItemsControl ItemsSource={Binding Groups}
   │      └─ ItemTemplate:
   │         StackPanel
   │         ├─ GroupHeaderButton  Height=34  [图标20px] [组名 13px] [chevron 12px 旋转0°/90°]
   │         │    点击 → ToggleGroup(groupKey)
   │         └─ GroupPanel  Height={Binding PanelHeight}  Opacity={Binding PanelOpacity}
   │            └─ ItemsControl ItemsSource={Binding Items}
   │               └─ SidebarItem  Height=31  Padding-Left=36  13px  text_mute
   └─ Row2  (T5 期空；全量期放皮肤/字号 —— Pilot 不做字号缩放)
```

**宽度动画**（`sidebar.py:491-513`）：用 `Storyboard` + `DoubleAnimation` 驱动 `Border.Width`，时长 `rail_in=240ms OutQuart` / `rail_out=180ms OutCubic`。

**分组展开动画**（`sidebar.py:270-305`）：`GroupPanel` 用 `MaxHeight` `DoubleAnimation`（`group_in=220ms OutQuart` / `group_out=160ms InOutCubic`）+ `Opacity`。**WPF 用 `MaxHeight` 动画而非 `Height`**，因为 `Height` 动画需要预知目标值（`Auto` 无法动画）。

**⚠️ 关键陷阱（Python 已踩过，`sidebar.py:241-249`）**：整条侧栏**只允许一层** `OpacityEffect`。WPF 对应物是 `UIElement.Opacity`（无离屏重绘问题），**比 Qt 安全**——因为 WPF 的 `Opacity` 是合成属性，不会像 `QGraphicsOpacityEffect` 那样触发离屏 painter 冲突。**但**：不要对 `SidebarItem` 逐个挂 `Effect`（`BlurEffect`/`DropShadowEffect`），那会触发软件渲染路径。

**Auto-collapse on hover（`sidebar.py:456-468`）**：**T5 期不实现**（Pilot 只做固定展开 + 手动折叠）。理由：Pilot 目标是视觉接近度 80% + 1 页高保真，hover 自动折叠是体验增强，列入 T5-SHOULD。**列入待明确事项 U2**。

**图标**（`nav_icons.py`，7 枚 24 网格线性图标）：在 WPF 中用 `Path` + `Geometry` 静态资源实现。逐枚的绘制指令（`nav_icons.py:123-175`）：

| 组名 | 图元（24 网格，1.5px 描边，Round cap/join） |
|---|---|
| 数据导入 | U 形托盘 + 下箭头：`M3.5,14.5 V17.5 A2,2 0 0 0 5.5,19.5 H18.5 A2,2 0 0 0 20.5,17.5 V14.5` + `M12,3 V14` + `M7.5,9.5 L12,14 L16.5,9.5` |
| 台账查看 | 圆角矩形 + 3 条竖线：`M5.5,3.5 H18.5 A1.5,1.5 ...` + `M4,8.5 H20` + 3×(竖线) |
| 业务数据 | 开口钱包：`M3.5,8.5 A2.5,2.5 ... H17.5 A2.5,2.5 ... V17.5` + `M3.5,9.5 H20.5` + `M16,14.25 H18` |
| 工资个税 | 人形 + 圆币：`圆(6.6,8.6,r3.4)` + `弧(4.2,19.6,r5.8,0°,180°)` + `圆(13.8,16.4,r3.8)` + 3 条线 |
| 分成计算 | 计算器：`圆角矩形(6,3.5,15×17)` + `圆角矩形(7.5,6.5,9×3.5)` + 6 个实心点 |
| **各类报表** | **坐标轴 + 3 柱**：`M4,4 V20 H20` + `矩形(7.5,13.5,3×6.5)` + `矩形(12,9.5,3×10.5)` + `矩形(16.5,15,3×5)` |
| 数据维护 | 放射齿轮：`圆(12,12,r3.6)` + 8 条辐射线 |
| chevron | `M(cx-d/2, cy-d) L(cx+d/2, cy) L(cx-d/2, cy+d)`，d = 0.28×min(w,h) |

> **建议**：T5 期只**精确复刻「各类报表」「数据导入」「台账查看」三枚**（其余 4 枚用简化等价图形），理由：只有这 3 枚在首屏可见（默认选中「数据导入」+ 展开「各类报表」）。其余在 T5-SHOULD/全量期补。**列入待明确事项 U3**。

---

## 4. 表格基础设施实现规格（重点）

### 4.1 需求拆解：Python 侧 7 项交互（逐项对照）

从 `table_view.py` + `table_features.py` + `column_layout.py` 提取的**完整能力清单**：

| # | 能力 | Python 实现 | 门槛? |
|---|---|---|:-:|
| C1 | **冻结列**（横向滚动时前 N 列不动） | `FrozenTableWidget.paintEvent` 手动重绘叠加（`widgets.py:216-262`） | ✅ |
| C2 | **两级表头**（第一行大类横向合并跨 2 列） | `TwoTierHeaderView.paintSection` 自绘 + 跨列 `drawText`（`table_features.py:230-322`） | ✅ |
| C3 | **列宽/列序持久化** | `ColumnLayoutManager` + `QSettings("lawfirm_app","lawfirm_app")` 键 `colstate/{page}/{name}`，JSON 存 `order/visible/frozen/widths`（`column_layout.py:39-95`） | ✅ |
| C4 | **严格填充算法**（视口宽 vs 内容宽，两阶段分配） | `compute_fill`（`column_layout.py:98-151`） | ⚠️ 隐式 |
| C5 | **按列筛选**（多选唯一值弹窗 + 跨列模糊搜索，AND） | `TableFilter` + `ColumnFilterDialog`（`table_features.py:390-595`） | ❌ T5-SHOULD |
| C6 | **悬停全文**（文本被压缩时才弹 tooltip） | `TableBehaviorDelegate.helpEvent` + `QFontMetrics`（`table_features.py:81-100`） | ⚠️ 隐式 |
| C7 | **选中保色**（红/绿字行选中后不变白） | `initStyleOption` 把 `HighlightedText` 设为单元格前景色（`table_features.py:71-79`） | ⚠️ 隐式 |
| C8 | **像素滚动**（ScrollPerPixel） | `install_common_features`（`table_features.py:109-110`） | ⚠️ 隐式 |
| C9 | **排序列/冻结列的表头底色高亮** + 排序三角 | `AccentHeaderView` 自绘（`table_features.py:125-227`） | ⚠️ 隐式 |
| C10 | **真·总计行**（底部冻结合计行） | `table_view.py:290-306` 把合计行作为最后一行渲染 | ⚠️ 有（仅 `table_view` 系） |

### 4.2 核心问题：**WPF 原生 `DataGrid` 能不能扛住？** —— 逐项论证

> **结论先行**：**能扛住 C1/C3/C4/C6/C7/C8/C9/C10 共 8 项；C2（两级表头）原生不支持但可用「单表头单元格内嵌 Grid」方案可靠实现；C5（按列筛选）原生不支持但属 T5-SHOULD，自研成本可控。**
> **→ 结论：不需要引入任何第三方表格控件（在「仅免费开源」硬约束下，本来也没有可用的商业级替代），原生 `DataGrid` + 自研 6 个附加类即可满足全部门槛项。**

**逐项依据（含证据来源）**：

| # | 原生 DataGrid 支持？ | 依据 | 落地方式 |
|---|---|---|---|
| **C1 冻结列** | ✅ **原生** | `DataGrid.FrozenColumnCount`（MSDN 官方：*"Gets or sets the number of non-scrolling columns"*，默认 0）。**语义与 Python 完全一致**：冻结列**恒为最左 N 列**；`DataGridColumn.IsFrozen` 只读，判断用 `FrozenColumnCount` | `FrozenColumnCount = {Binding FrozenColumnCount}`。**注意**：冻结列必须是最左列 → 对应 Python「冻结某列 = 冻结该列及其左侧所有列」（`column_layout.py:416-438` 的面板语义），**天然吻合** |
| **C2 两级表头** | ❌ **原生不支持** | MSDN 论坛官方答复（learn.microsoft.com 归档）逐字：*"I am afraid that the built-in DataGrid doesn't support merged or multiple column headers out of the box."* | 用**单表头单元格内嵌 `Grid`** 方案（见 §4.3）：给「大类起始列」的 `DataGridColumnHeader.HeaderStyle` 设 `ControlTemplate`，内部放 2 行 Grid，第一行文字跨过本列+右邻列宽度。**这是社区成熟模式**（同一单元格内 `Grid.ColumnSpan`），无需替换 `DataGridColumnHeadersPresenter`（那条路要重写整个 header panel，风险高） |
| **C3 列持久化** | ✅ **需自研存储层**（机制原生）：`DisplayIndex` / `ActualWidth` 可读写，`ColumnReordered` / `ColumnWidthChanged` 事件齐全 | `column_layout.py` 用 `QSettings`；**WPF 无 QSettings** → 见 §4.4 的 `ColumnStateStore` | 序列化到 `%APPDATA%\LawFirm\ui-state.json`（**不用** `data/prefs.json`，见 §4.4 理由） |
| **C4 严格填充算法** | ✅ **需自研**（DataGrid 只有 `*`/`Auto`/`Fixed` 三种列宽模式，无法表达 Python 的两阶段算法） | `compute_fill`（`column_layout.py:98-151`）是纯函数，**可 1:1 移植** | 移植为 `ColumnLayoutManager.ComputeFill(viewportWidth, cols)`，输出 `Dictionary<string,double>`，再用 `DataGridColumn.Width = new DataGridLength(w, Pixel)` 应用。**纯函数 = 可单测**，无需 UI |
| **C5 按列筛选** | ❌ 原生不支持（`DataGrid` 无内置筛选） | — | T5-SHOULD：在 VM 层过滤 `ICollectionView` 的 `Filter` 谓词（`CollectionViewSource` 原生支持 `Filter`），弹窗用 `Window` + `ListBox` + `CheckBox`。**与 C4 解耦**，可后补 |
| **C6 悬停全文** | ⚠️ **近原生**：`DataGridCell` 原生有 `ToolTip`，但**缺「仅当被压缩时才显示」的判定** | `table_features.py:93-99` 用 `QFontMetrics.horizontalAdvance` 比单元格宽度 | 自研 `NotionDataGrid`：在 `CellStyle` 里挂 `ToolTip`，用 `IValueConverter` 或 `CellStyle` 的 `ToolTipService` + 一个 `AttachedProperty` 计算 `FormattedText.Width > ActualWidth` 才赋 tooltip。**退化方案**：直接 `ToolTip="{Binding RelativeSource=Self, Path=Content.Text}"`（始终显示），视觉损失很小 |
| **C7 选中保色** | ✅ **原生**（用 `DataGridCell` 的 `Style` + `Trigger`） | WPF 中前景色由 `DataGridCell.Foreground` 决定，选中态是 `IsSelected` trigger 改 `Background`；**只要不覆盖 `Foreground` 就天然保色**——比 Qt 简单（Qt 需要 `initStyleOption` hack） | `CellStyle` 里 `Trigger IsSelected` 只改 `Background={BgSelect}`，**不动 `Foreground`**。负数列的红色用 `DataTrigger` 绑数值 |
| **C8 像素滚动** | ✅ **原生**（关键开关） | SO 权威答复：*"you can get pixel-based scrolling AND virtualization by leaving `ScrollViewer.CanContentScroll="true"` and setting `VirtualizingStackPanel.ScrollUnit="Pixel"`"*（WPF 4.5+） | `ScrollViewer.CanContentScroll="True"` + `<ItemsPanel><VirtualizingStackPanel ScrollUnit="Pixel"/></ItemsPanel>`。**⚠️ 不可设 `CanContentScroll=False`**——那会直接关闭虚拟化（同一 SO 答复证据） |
| **C9 表头底色 + 排序三角** | ✅ **原生**（`DataGridColumnHeader` 的 `Style` + `SortDirection`） | `DataGridColumnHeader` 有 `SortDirection` 属性，默认绘制三角；底色用 `Trigger` | `ColumnHeaderStyle` 里：`Trigger SortDirection=IsNotNull` → `Background={HeaderSortBg}`；冻结列用 `FrozenColumnCount` 索引比较（多值转换器） |
| **C10 总计行** | ✅ **原生**（DataGrid 有 `FrozenRowCount`? ❌ **无此属性**） | ⚠️ **DataGrid 没有 `FrozenRowCount`**（只有 `FrozenColumnCount`）。合计行需另做 | 方案：把合计行作为 `Items` 最后一行（`IsTotalRow` 标志），用 `RowStyle` 的 `Trigger` 加粗+灰底。**与 Python 完全一致**（`table_view.py:291-306` 就是这么做的） |

**🔴 结论（诚实表述）**：

1. **原生 `DataGrid` 满足 8/10 项，含全部 3 个门槛项（C1 冻结列 ✅ / C3 列持久化 ✅ / C2 两级表头 ⚠️需自研但可行）。**
2. **两级表头是唯一真正的缺口**，但缺口大小可控（一个 `ControlTemplate` + 一段宽度同步代码），**不需要第三方控件**。
3. **在「仅免费开源」硬约束下，本就没有可用的替代品**：
   - `EPPlus` / `Xceed` / `Syncfusion` / `Telerik` / `DevExpress` 全部**商业授权 → 出局**（计划 §8 许可证硬红线逐字列名）。
   - 开源阵营里 WPF 表格只有 `HandyControl` 的 `DataGrid` 样式（**样式**，不是新控件）+ `AvalonDock`（布局用）/ `gong-wpf-dragdrop`（拖放用），**均不提供两级表头**。
   - → **「不用第三方表格控件」不是妥协，而是唯一可行路径。**
4. **最大技术风险不在原生能力，而在「五者叠加」**：`FrozenColumnCount` + 像素滚动 + 自定义 header 模板 + 列宽手管 + 虚拟化，这五者在 WPF 下互相影响（例：自定义 header 高度会改变 `ColumnHeaderHeight` 与 `FrozenColumnCount` 的分割线对齐）。**→ 必须在 T5.2 一次性打通，不能分散到多个子任务。**（见 §8 子任务分解）

### 4.3 两级表头实现细则（C2）

**目标视觉**（Python `table_features.py:304-316` + `settlement_view.py:706-713`）：

```
┌──────┬──────┬──────┬──────┬──────┬──────┬──────┬──────┬──────┬──────┬──────┬──────┐
│      │      │  本年收入  │  报酬发放  │ 住房公积金 │  保险费  │ 汽油费  │   ← 第1行（大类，跨2列居中）
│ 序号 │ 姓名 ├──────┼──────┼──────┼──────┼──────┼──────┼──────┼──────┤
│      │      │ 本月 │ 累计 │ 本月 │ 累计 │ 本月 │ 累计 │ 本月 │ 累计 │   ← 第2行（细分）
└──────┴──────┴──────┴──────┴──────┴──────┴──────┴──────┴──────┴──────┴──────┴──────┘
  单列（整高居中）      起始列：第1行跨 2 列；第2列：第1行留空
```

**WPF 实现（3 步，全部在 `TwoTierHeaderGrid.cs` 内）**：

1. **`ColumnHeaderHeight = 44`**（对齐 `table_features.py:244`）。
2. **`ColumnHeaderStyle` 的 `ControlTemplate`** 内含一个 2 行 `Grid`：
   - **单列**（序号/姓名）：`TextBlock` 设 `Grid.RowSpan="2"` + 整高居中。
   - **大类起始列**：第 1 行 `TextBlock` 设 `Grid.ColumnSpan="2"`（**跨到本单元格之外**——WPF 允许 `ColumnSpan` 超出实际列定义数，超出部分被裁剪，故需**手动设宽**），文本居中，宽度 = `本列ActualWidth + 右邻列.ActualWidth`。
   - **非起始列**：第 1 行**留空**（`Visibility=Collapsed`），第 2 行如下。
3. **宽度同步（关键）**：大类文字要真正「跨 2 列居中」，必须知道右邻列宽。做法：
   - 给 `DataGrid` 挂 `LayoutUpdated` 或监听 `ColumnWidthChanged` 事件，在 `TwoTierHeaderGrid` 里缓存各列 `ActualWidth`（`ObservableCollection<double>`）。
   - `HeaderStyle` 的模板用 `MultiBinding` 绑 `本列.ActualWidth` + `右邻列.ActualWidth`，`IMultiValueConverter` 输出 `Sum`，赋给 `TextBlock.Width`。
   - **⚠️ 陷阱**：`DataGridColumnHeader.ActualWidth` **不含** header 之间的分隔线（1px），累计误差会导致最后 1 个字的偏移。**处置**：在 converter 里 `+ 2`（右邻列宽 + 本列宽 + 1px 分隔线 ×1）。**列入待明确事项 U4**（需真机微调）。

**备选方案（若上述 converter 方案在真机抖动）**：用 `Grid` 包裹 `DataGrid`（`Grid.RowDefinitions` 2 行），第 1 行放自绘「大类条」（`ItemsControl` + 每项 `Width` 绑定列宽），第 2 行放 `DataGrid`（`HeadersVisibility="Column"` 且只有次级表头）。**这是社区的「ComplexDataGridHeader」模式**，更稳但代码量更大（+~150 行）。**列为 Plan B。**

**⚠️ 诚实提示**：此方案**未经真机验证**（沙箱无 WPF 运行时）。**列入待明确事项 U5**（T5.2 首日必须先做最小验证：1 列普通 + 2 列两级 + `FrozenColumnCount=1`，看三者能否共存）。

### 4.4 列布局持久化（C3 + C4）

**Python 存储**（`column_layout.py:39-95`）：
- 载体：`QSettings("lawfirm_app", "lawfirm_app")` → Windows 下落在注册表 `HKCU\Software\lawfirm_app\lawfirm_app`。
- 键：`colstate/{page}/{name}`（如 `colstate/settlement/personal`）。
- 值：JSON `{"order":[...], "visible":{k:bool}, "frozen":{k:bool}, "widths":{k:int}}`。
- **列身份 = 稳定标题字符串**（如 `"1月"`、`"合计"`）；**列集合不一致即整份丢弃回退默认**（`column_layout.py:64-65`）——这是解决「结算总表 14 列模式 ↔ 3 列模式切换」串档的关键。

**WPF 落地**：

```
%APPDATA%\LawFirm\ui-state.json
{
  "columns": {
    "settlement/personal": {
      "order":   ["项目","1月",...,"12月","合计"],
      "visible": { "项目": true, "1月": true, ... },
      "frozen":  { "项目": true, "1月": false, ... },
      "widths":  { "项目": 132, "1月": 88 }
    }
  }
}
```

**接口（`Services/ColumnStateStore.cs`）**：

```csharp
public sealed class ColumnStateStore
{
    /// 读。列集合与 saved.order 不一致 → 返回默认（对应 column_layout.py:64-65）
    public ColumnState Load(string page, string name, IReadOnlyList<string> defaultKeys);

    /// 写（整份覆盖）
    public void Save(string page, string name, ColumnState state);

    /// 删（「恢复默认」）
    public void Reset(string page, string name);
}

public sealed class ColumnState
{
    public List<string> Order { get; set; }
    public Dictionary<string, bool> Visible { get; set; }
    public Dictionary<string, bool> Frozen { get; set; }
    public Dictionary<string, double> Widths { get; set; }
}
```

**为何用 `%APPDATA%` 而非 `data/prefs.json`**：Python 的 `prefs.json` 存的是**业务偏好**（skin/font_step/report_persons），放在 `data/` 目录（与 db 同目录，**会被 Seafile 同步**）。列状态存注册表（Python）→ 天然不同步。C# 侧若存进 `data/`，会导致「3 台 PC 列布局互相覆盖」——**违反零数据风险精神的延伸**。故用 `%APPDATA%`（不进 Seafile、不进 git、不污染 db 目录）。**⚠️ 此决策需用户确认**（可能希望列布局跟人走 → 跟 Seafile 同步）。**列入待明确事项 U6**。

**严格填充算法移植（C4）**：`ComputeFill` 是纯函数，逐行移植 `column_layout.py:98-151`。**规格要点**（逐条）：
- 输入 `cols`: `[{Key, ContentW, Frozen, Fixed, Visible}]`，`viewportWidth`。
- `viewport >= Σw` → Phase1 把 `w < contentW` 的列按缺口比例补足（保护 `extra > 0.5` 与 `guard < 8` 两个终止条件）；Phase2 余量按当前宽比例分给 auto 列。
- `viewport < Σw` → 只缩「非冻结且非 fixed」列（下限 `MIN_W = 48`）；若仍不够，兜底缩 auto 列。
- 输出 `Dictionary<string,int>`（`Math.Round`）。
- **常量**：`MIN_W = 48`、`INVOICE_SAMPLE = "25332000000012014331"`（20 位）、列宽上限 = `FormattedText(INVOICE_SAMPLE).Width + 16`。
- **单测**：4 个用例（变宽补足 / 变窄缩非冻结 / 全 fixed 超宽 / 空列集），期望值直接抄 Python 手算。

### 4.5 `NotionDataGrid` 组合控件（C1+C6+C7+C8+C9）

**定位**：一个 `class NotionDataGrid : DataGrid`（`Controls/NotionDataGrid.cs`），在构造时设好所有属性，视图 XAML 只需 `local:NotionDataGrid`。

```csharp
public class NotionDataGrid : DataGrid
{
    static NotionDataGrid()
    {
        DefaultStyleKeyProperty.OverrideMetadata(
            typeof(NotionDataGrid),
            new FrameworkPropertyMetadata(typeof(NotionDataGrid)));
    }

    // 构造时设置（不可在 XAML 里漏项）
    //   CanUserAddRows       = false
    //   CanUserDeleteRows    = false
    //   CanUserResizeRows    = false
    //   IsReadOnly           = true
    //   HeadersVisibility    = Column
    //   AutoGenerateColumns  = false
    //   SelectionUnit        = FullRow          （对齐 setSelectionBehavior(SelectRows)）
    //   SelectionMode        = Single / Extended （见下）
    //   GridLinesVisibility  = Horizontal
    //   HorizontalGridLinesBrush = {Grid}
    //   ScrollViewer.CanContentScroll = True     （★ 虚拟化开关，不可为 False）
    //   VirtualizingStackPanel.ScrollUnit = Pixel （★ 像素滚动 + 虚拟化并存）
    //   VirtualizingStackPanel.IsVirtualizing = True
    //   VirtualizingStackPanel.VirtualizationMode = Recycling
    //   EnableRowVirtualization = True
    //   EnableColumnVirtualization = False       （列少，开启反而致 header 错位）
    //   ColumnHeaderHeight   = 34（单级）/ 44（两级）
    //   RowHeight            = 34
    //   FrozenColumnCount    = 绑定（默认 1）
}
```

**⚠️ 选中保色 + 负数标红的 `CellStyle` 规格**（C7）：

```xml
<Style x:Key="NotionCellStyle" TargetType="DataGridCell">
  <!-- 默认：无边框、无默认高亮（去掉 WPF 的蓝色选中块） -->
  <Setter Property="BorderThickness" Value="0"/>
  <Setter Property="Background"   Value="Transparent"/>
  <Setter Property="Foreground"   Value="{StaticResource Text}"/>
  <Setter Property="Padding"      Value="10,0"/>
  <Setter Property="Template">
    <Setter.Value>
      <ControlTemplate TargetType="DataGridCell">
        <Border Background="{TemplateBinding Background}" Padding="{TemplateBinding Padding}">
          <ContentPresenter VerticalAlignment="Center"/>
        </Border>
      </ControlTemplate>
    </Setter.Value>
  </Setter>
  <Style.Triggers>
    <!-- 选中：只改底色，不改 Foreground → 红/绿字天然保色（≠ Qt 需 hack） -->
    <Trigger Property="IsSelected" Value="True">
      <Setter Property="Background" Value="{StaticResource BgSelect}"/>
    </Trigger>
  </Style.Triggers>
</Style>
```

**负数值标红**：Python 在 `_make_item` 里 `val < 0 → RED`（`table_view.py:335-336`）。WPF 用 `DataTrigger`：

```xml
<DataGridTextColumn Binding="{Binding Value, StringFormat={}{0:N2}}">
  <DataGridTextColumn.CellStyle>
    <Style TargetType="DataGridCell" BasedOn="{StaticResource NotionCellStyle}">
      <Setter Property="TextBlock.TextAlignment" Value="Right"/>
      <Style.Triggers>
        <DataTrigger Binding="{Binding Value, Converter={StaticResource IsNegative}}"
                     Value="True">
          <Setter Property="Foreground" Value="{StaticResource Red}"/>
        </DataTrigger>
      </Style.Triggers>
    </Style>
  </DataGridTextColumn.CellStyle>
</DataGridTextColumn>
```

**⚠️ 数字格式**：Python 用 `f"{v:,.2f}"`（千分位 + 2 位小数）。WPF `StringFormat={}{0:N2}` 在 `zh-CN` 下即 1,234.56，**但 `N2` 受 `CultureInfo.CurrentCulture` 影响**——需在 `App.xaml.cs` 显式 `FrameworkElement.LanguageProperty.OverrideMetadata(typeof(FrameworkElement), new FrameworkPropertyMetadata(XmlLanguage.GetLanguage("zh-CN")))`，否则用系统区域可能出 `1.234,56`。**列入实现注意项**。

**冻结列灰底**（Python `_FROZEN_BG = #EAEAEA`，`table_features.py:63-67`）：WPF 无内置「冻结列底色」→ 在 `CellStyle` 里加一个 `MultiBinding`（`FrozenColumnCount` + 本列 `DisplayIndex`）转换器，`DisplayIndex < FrozenColumnCount` 时赋 `#EAEAEA`。

---

## 5. 「各类报表」页 XAML 结构（G6）

### 5.1 功能清单（从 `settlement_view.py` 956 行提取，逐条）

**外层**（`settlement_view.py:45-161`）：`QTabWidget`，4 个 tab：

| # | tab 名 | 构建函数 | 行号 | T5 是否实现 |
|---|---|---|---|:-:|
| 1 | 个人结算总表 | `__init__` | 53-151 | ✅ |
| 2 | 月度结算表 | `_build_report_tab` | 520-589 | ✅ |
| 3 | 年度聘用结算表 | `_build_staff_income_tab` | 666-733 | ✅（含两级表头） |
| 4 | 开票收入表 | `_build_invoice_income_tab` | 815-879 | ✅ |

> 右上角 `tab_help_corner`（`settlement_view.py:158-161`）：一个「?」按钮，hover 显示说明文字。

**Tab 1「个人结算总表」功能清单**（`settlement_view.py:53-424`）：

| 元素 | 交互 | Python 行号 |
|---|---|---|
| 筛选栏：年份 `ComboBox`（今年往前 3 年） | 变更 → 重载人员列表 | 62-68 |
| 筛选栏：经办人 `ComboBox`（「请选择经办人」+ 全员，min 宽 150） | 变更 → 刷新表格 | 70-74 |
| 筛选栏：月份 `ComboBox`（「全部月份」+ 1..12） | 变更 → 刷新（**会改变表格列数**：选 mo 月时列 = 项目 + 1..mo 月 + 合计） | 76-82 |
| 筛选栏：类型 `ComboBox`（汇总/合伙/聘用/兼职，**按该人实际身份动态重建**） | 变更 → 刷新；单身份自动选中 | 84-89, 217-232 |
| 下拉显式 QSS（白底 `#FFFFFF` / 描边 `#DADAD7` / 圆角 6 / 选中底 `#E9E9E7` / 选中字 `#37352F`） | — | 93-108 |
| 结算总表表格（`FrozenTableWidget`，14 列，冻结 1，无边线；**纯展示、禁编辑、禁排序三角**） | — | 111-119 |
| 按钮「导出全部员工」（主按钮） | → `gen_all`：选目录 → 导出全部 → 日志 | 123-124, 347-363 |
| 按钮「导出指定人员」（次按钮） | → `gen_selected`：多选弹窗（含全选）→ 导出选中 | 125-126, 365-425 |
| 按钮「导出月度结算表」（次按钮） | → `gen_report`：月份 + 多选员工（记忆上次选择）→ 导出 | 127-128, 445-517 |
| 摘要标签（右下，如「丁祥锋（合伙）·全年」） | — | 129, 277-278 |
| 生成日志 `QPlainTextEdit`（只读，max 高 120，placeholder「生成日志…」） | 追加 `✓ 文件名` / `✗ 生成失败: ...` | 137-141, 360-362 |
| 默认选中「最近有数据的年份」（从今年往前找 4 年） | — | 143-150, 172-179 |
| `showEvent` → 重载人员 + 刷新 | — | 195-198 |

**表格行构成（`_build_rows`，`settlement_view.py:280-333`）—— 逐行精确规格**：

| # | 行标签 | 值来源 | 粗体 |
|---|---|---|:-:|
| 1 | `▶ {staff_type}` | 全 `None`（占位行） | 是（`▶` 前缀触发） |
| 2 | `一、上年结余结转` | 全 `None` | 是 |
| 3 | `二、本月收款金额` | `Σ(rec_open_cur, rec_cur_year, rec_prev_year, rec_refund_cur, rec_refund_prev)` 逐月 | 是 |
| 4-8 | `1.本月开收` / `2.收本年` / `3.收上年` / `4.退本年` / `5.退上年` | 对应键逐月 | 否 |
| 9 | `三、本月开具发票金额` | `Σ inv_total`（**独立计算，含预收票**） | 是 |
| 10-13 | `1.本月开收` / `2.本月未收` / `3.红冲本年` / `4.红冲上年` | `inv_open_received` / `inv_open_uncollected` / `inv_red_cur` / `inv_red_prev` | 否 |
| 14 | `四、未收款金额` | `uncollected_month[mo]`；**末列合计 = `uncollected_total`（存量口径，非 12 月之和）** | 是 |
| 15 | `五、业务收入` | `income` 逐月 | 是 |
| 16 | `六、减：分成报酬及费用` | `Σ` 全部费用类型逐月 | 是 |
| 17+ | `{i}.{费用类型}` | 按 `expense_cat.sort_order` 排序，未维护的按名称兜底 | 否 |

- 子项标签前缀 **全角空格 `　`**（`settlement_view.py:299`）。
- 合计列 = 当前显示各月之和（全年模式 = 12 月；选月模式 = 1..mo 月）。
- 数值格式 `f"{v:,.2f}"`；`None` → 空串。
- 粗体判定：标签以 `一、`..`十、` 开头 或 `▶` 开头 → 整行加粗，首列左对齐。

**Tab 2/3/4**：结构同构（筛选栏 + `FrozenTableWidget` + 底部按钮 + 摘要），差异仅在列定义与导出函数。因篇幅不逐行复制，**实现时以 `settlement_view.py:520-956` 为唯一依据，逐函数翻译**。

### 5.2 XAML 结构（`SettlementView.xaml`）

```xml
<UserControl x:Class="LawFirm.UI.Views.Settlement.SettlementView">
  <Grid Background="{StaticResource Canvas}">
    <!-- 行0: tab 头 + 右上角 ? -->
    <TabControl Style="{StaticResource PageTitleTabStyle}"
                SelectedIndex="{Binding SelectedTabIndex}">
      <TabItem Header="个人结算总表">
        <views:PersonalSettlementTab DataContext="{Binding Personal}"/>
      </TabItem>
      <TabItem Header="月度结算表">
        <views:MonthlyReportTab DataContext="{Binding Monthly}"/>
      </TabItem>
      <TabItem Header="年度聘用结算表">
        <views:StaffIncomeTab DataContext="{Binding StaffIncome}"/>
      </TabItem>
      <TabItem Header="开票收入表">
        <views:InvoiceIncomeTab DataContext="{Binding InvoiceIncome}"/>
      </TabItem>
    </TabControl>

    <!-- 右上角 ? 帮助（对应 tab_help_corner） -->
    <controls:HelpCorner HorizontalAlignment="Right" VerticalAlignment="Top"
                         Margin="0,10,12,0"
                         HelpText="个人结算总表：选择经办人查看结算总表；可导出全部或指定经办人（支持多选）。口径：收款/开票/未收/业务收入/费用。" />
  </Grid>
</UserControl>
```

**`PageTitleTabStyle`**（对齐 `style.py:237-243` `QTabBar#pageTitleBar::tab`）：tab 字号 20px / 字重 700 / `Padding 8,20` / 未选中 `text_mute` / 选中 `text` + 底边 2px `accent_blue`。

**`PersonalSettlementTab.xaml` 结构骨架**：

```xml
<Grid Margin="8,10,8,10">   <!-- 对齐 settlement_view.py:55 setContentsMargins(8,10,8,10) -->
  <Grid.RowDefinitions>
    <RowDefinition Height="Auto"/>  <!-- 筛选栏 -->
    <RowDefinition Height="*"/>     <!-- 表格 -->
    <RowDefinition Height="Auto"/>  <!-- 底部按钮 + 摘要 -->
    <RowDefinition Height="Auto"/>  <!-- 日志（MaxHeight=120） -->
  </Grid.RowDefinitions>

  <!-- 行0: 年份 | 经办人 | 月份 | 类型 -->
  <StackPanel Orientation="Horizontal" Spacing="8">
    <TextBlock Style="{StaticResource CaptionLabel}" Text="年份"/>
    <ComboBox Style="{StaticResource NotionCombo}" MinWidth="90"
              ItemsSource="{Binding Years}" SelectedItem="{Binding Year}"
              DisplayMemberPath="Label"/>
    <TextBlock Style="{StaticResource CaptionLabel}" Text="经办人"/>
    <ComboBox Style="{StaticResource NotionCombo}" MinWidth="150"
              ItemsSource="{Binding Persons}" SelectedItem="{Binding Person}"/>
    <TextBlock Style="{StaticResource CaptionLabel}" Text="月份"/>
    <ComboBox Style="{StaticResource NotionCombo}"
              ItemsSource="{Binding Months}" SelectedItem="{Binding Month}"
              DisplayMemberPath="Label"/>
    <TextBlock Style="{StaticResource CaptionLabel}" Text="类型"/>
    <ComboBox Style="{StaticResource NotionCombo}"
              ItemsSource="{Binding Types}" SelectedItem="{Binding PersonType}"
              DisplayMemberPath="Label"/>
  </StackPanel>

  <!-- 行1: 表格 -->
  <controls:NotionDataGrid x:Name="Grid" Grid.Row="1"
                           FrozenColumnCount="1"
                           ColumnHeaderHeight="34"
                           Columns="{Binding ColumnDefs}"
                           ItemsSource="{Binding Rows}"/>

  <!-- 行2: 按钮 + 摘要 -->
  <Grid Grid.Row="2" Margin="0,10,0,0">
    <StackPanel Orientation="Horizontal" Spacing="10">
      <Button Style="{StaticResource PrimaryButton}" Content="导出全部员工"
              Command="{Binding ExportAllCommand}"/>
      <Button Style="{StaticResource NotionButton}" Content="导出指定人员"
              Command="{Binding ExportSelectedCommand}"/>
      <Button Style="{StaticResource NotionButton}" Content="导出月度结算表"
              Command="{Binding ExportMonthlyCommand}"/>
    </StackPanel>
    <TextBlock Style="{StaticResource CaptionLabel}"
               HorizontalAlignment="Right" VerticalAlignment="Center"
               Text="{Binding SummaryText}"/>
  </Grid>

  <!-- 行3: 日志 -->
  <TextBox Grid.Row="3" Style="{StaticResource LogBox}" MaxHeight="120"
           IsReadOnly="True" Text="{Binding LogText}" AcceptsReturn="True"
           VerticalScrollBarVisibility="Auto"/>
</Grid>
```

**动态列数（月份模式）**：`ColumnDefs` 是 `ObservableCollection<DataGridColumn>`，选月份时 VM 重建为「项目 + 1..mo 月 + 合计」。**⚠️**：重建列会触发 `ColumnStateStore` 的「列集合不一致 → 丢弃存档」逻辑——**这正是 Python 的行为**（`column_layout.py:64-65`），必须复刻，否则会出现「第二列是 2 月」的串档 bug。

---

## 6. 异步进度条方案（G7）

### 6.1 痛点（Python 现状，逐行证据）

`settlement_view.py:354-355`：`gen_all` 里 `files = export_all(out, year)` **在 UI 线程同步调用**。`export_all` 对 N 人各跑一次 `Build`（每人最多 5 次 Build：3 身份 + 汇总 + 全量），无进度、无取消、**主窗口完全冻结**。

计划 §4.2 已认定这是顽疾（「打开报表页 3.6–8.4s」+「导出全部员工 1+4N 次」）。

### 6.2 WPF 方案（`async/await` + `IProgress<T>` + `CancellationToken`）

**分层**：

```
View (XAML)                    → 绑定 IsBusy / ProgressValue / StatusText
  ↓ Command (RelayCommand)
ViewModel                      → 调 Service，传 IProgress<ProgressReport> + CancellationToken
  ↓ await Task.Run(...)
Service (ExportService)        → 后台线程跑 NPOI / SettlementEngine
  ↓ progress.Report(...)
ProgressWindow (弹出式)         → 显示进度条 + 取消按钮
```

**接口（`Services/ExportService.cs`）**：

```csharp
public sealed record ProgressReport(int Done, int Total, string CurrentItem)
{
    public double Percent => Total <= 0 ? 0 : (double)Done / Total * 100.0;
}

public sealed class ExportService
{
    /// 导出全部员工（每人一文件）。对应 Python export_all。
    /// 逐人 Report(进度)，每人间检查 token.IsCancellationRequested。
    public Task<IReadOnlyList<string>> ExportAllAsync(
        SqliteConnection conn, string outDir, int year,
        IProgress<ProgressReport>? progress, CancellationToken ct);

    /// 导出指定人员。对应 Python 对每人的 export_one 循环（settlement_view.py:415-417）。
    public Task<IReadOnlyList<string>> ExportSelectedAsync(
        SqliteConnection conn, string outDir, int year, IReadOnlyList<string> persons,
        IProgress<ProgressReport>? progress, CancellationToken ct);
}
```

**⚠️ 数据层纪律（不可破）**：

- `SqliteConnection` **不是线程安全的**，且 **不能在非创建线程上使用**（`Microsoft.Data.Sqlite` 无跨线程支持）。
- Python 侧 `export_all` 内部自己开连接；C# 侧 `PersonSettlementExporter.ExportAll(conn, ...)` 收外部连接。
- **规格裁定**：**在后台线程内 `using var conn = DbConnection.OpenReadOnly(DbConnection.FindDatabase())` 自建连接**，UI 线程的连接不跨线程传递。`DbConnection.OpenReadOnly` 已设 `query_only + ReadOnly`（`DbConnection.cs`），只读纪律不破。
- **`query_only` 在异步期间保持**（连接生命周期 = 后台任务生命周期）。

**ViewModel 骨架**：

```csharp
[RelayCommand]
private async Task ExportAllAsync()
{
    string? outDir = _folderPicker.PickFolder("选择输出目录");
    if (outDir is null) return;                       // 用户取消 → 静默返回（对齐 Python:348-350）

    _cts = new CancellationTokenSource();
    var progress = new Progress<ProgressReport>(r =>
    {
        ProgressValue = r.Percent;
        StatusText    = $"正在导出 {r.CurrentItem}（{r.Done}/{r.Total}）…";
    });

    var win = new ProgressWindow { Owner = Application.Current.MainWindow, DataContext = this };
    win.Show();                                        // 非模态？→ 见下
    try
    {
        var files = await _exportService.ExportAllAsync(conn, outDir, Year, progress, _cts.Token);
        LogText += $"\n完成：共 {files.Count} 份，输出目录：{outDir}";
        _dialog.Info("生成完成", $"已生成 {files.Count} 份个人结算总表\n输出目录：{outDir}");
    }
    catch (OperationCanceledException)
    {
        LogText += "\n已取消。";
    }
    catch (Exception ex)
    {
        LogText += $"\n✗ 生成失败: {ex.Message}";
        _dialog.Error("生成失败", ex.Message);
    }
    finally
    {
        win.Close();
        _cts.Dispose(); _cts = null;
    }
}
```

### 6.3 「导出期间窗口可拖动」的实现细则（门槛项）

**这是验收门槛的硬要求**，实现要点：

1. **UI 线程不能被阻塞**：`Task.Run` 把 `ExportAllAsync` 的同步体重（NPOI 写盘 + `SettlementEngine.Build`）推到线程池。**关键**：`await` 让 UI 线程回消息循环 → 窗口可拖动、可重绘。
2. **进度上报必须回 UI 线程**：`Progress<T>` 在**构造它的线程的 `SynchronizationContext`** 上回调（即 UI 线程）——**这是 `Progress<T>` 的设计保证**，无需手写 `Dispatcher.Invoke`。
3. **`ProgressWindow` 的模态选择**：
   - 若 `ShowDialog()`（模态）→ **会阻塞 UI 线程的消息泵吗？不会**：WPF 模态是**禁用父窗口输入**，但消息泵继续跑 → 窗口仍可拖动/重绘。**但父窗口被禁用，用户感觉「卡住」**。
   - **规格裁定**：用 **`Show()`（非模态）+ `Owner` 设置**。理由：门槛要求「窗口可拖动」，最直观的体感是**主窗也活着**。同时 `ProgressWindow` 自身带取消按钮。
   - **⚠️ 陷阱**：非模态时用户可**重复点击导出** → 产生并发导出。**处置**：`ExportAllCommand.CanExecute = !IsBusy`（`RelayCommand` 的 `CanExecute` 自动禁用按钮）。
4. **取消**：`ProgressWindow` 的取消按钮 → `_cts.Cancel()` → 后台线程在**每人之间**检查 `ct.ThrowIfCancellationRequested()`（**不能在 `ExportAll` 内部检查**，因为它是同步 API 且不可改）。**粒度 = 每人一个文件**。部分文件已落盘 → **不删除**（与用户预期一致：取消 = 不再继续，已完成的不回滚）。**⚠️ 列入待明确事项 U7**。
5. **进度总量**：`Total = persons.Count`（导出全部）；`Total = selected.Count`（导出指定）。**不做「每人 5 步」的细粒度**（`ExportAll` 是黑盒，只能按人报告）。

**`ProgressWindow.xaml` 结构**：

```
Window  WindowStyle=None  ResizeMode=NoResize  SizeToContent=WidthAndHeight
        Width=420  Topmost=False  ShowInTaskbar=False  WindowStartupLocation=CenterOwner
└─ Border (圆角 10, 描边 {Border}, 底色 {Bg}, 阴影)
   └─ Grid Margin=24
      ├─ Row0  TextBlock  标题「正在生成个人结算总表」  15px 粗
      ├─ Row1  TextBlock  StatusText  12px {TextMute}   （「正在导出 丁祥锋（3/42）…」）
      ├─ Row2  ProgressBar Value={ProgressValue} Minimum=0 Maximum=100  Height=6
      │          样式：底 {Border} 圆角 3 / 填充 {AccentBlue}
      ├─ Row3  TextBlock  {ProgressValue:N0}%  12px 右对齐
      └─ Row4  Button「取消」  Style=NotionButton  Command=CancelCommand  HorizontalAlignment=Right
```

**⚠️ WPF `ProgressBar` 默认样式很丑**（有渐变 + 边框）→ 必须重写 `ControlTemplate`（一个 `Border` + 一个宽度绑 `Value` 的 `Rectangle`）。

---

## 7. 依赖包清单（NuGet，仅免费开源）

### 7.1 需新增（写入 `Directory.Packages.props`）

```xml
<PackageVersion Include="CommunityToolkit.Mvvm" Version="8.4.0" />
<PackageVersion Include="HandyControl" Version="3.5.1" />
```

### 7.2 逐包核实结果

| 包 | 版本 | 许可证 | .NET 10 兼容性 | 核实依据 | 是否必需 |
|---|---|---|---|:-:|---|
| `CommunityToolkit.Mvvm` | **8.4.0** | **MIT** | ✅ 目标 `netstandard2.0/2.1` → **向前兼容 net10.0**；官方支持 | 计划 §2.1 锁定 8.x；计划 §8 | ✅ **必需**（`[ObservableProperty]`/`[RelayCommand]` 源码生成器，手写 `INotifyPropertyChanged` 会增加 ~500 行样板） |
| `HandyControl` | **3.5.1** | **MIT** | ✅ **明确支持**：GitHub 仓库有 `feat: support for .net 10.0` commit（2025-12-03）；NuGet 页 `.NET net10.0-windows was computed` | WebSearch 核实（nuget.org + github.com/handyorg/handycontrol） | ⚠️ **可选**（见 §7.3） |

### 7.3 HandyControl 引入与否 —— **结论：T5 期不引入（推迟）**

**证据**：
- ✅ **许可证 MIT**（`LICENSE` 文件已核实）→ 满足硬约束。
- ✅ **.NET 10 兼容性已确认**（仓库 2025-12 的 `feat: support for .net 10.0`）。
- ⚠️ **但 3.5.1 的 NuGet 元数据未标 `net10.0`**（NuGet 页显示 `net10.0-windows was computed` = NuGet 客户端「推断」的兼容，不是包内显式 TFM）。**含 `computed` 即代表包本身没为 net10 编译过，是用 net8.0/net9.0 资产回退**——**通常可用，但存在运行时 `XamlParseException` 风险**。

**是否真的需要？逐项评估**：

| 需求 | HandyControl 提供？ | 原生 WPF + 自绘能否达成？ | 结论 |
|---|:-:|---|:-:|
| Notion 风配色 | ❌（它是 Fluent 风，非 Notion） | ✅ 资源字典 20 个 token | 原生够 |
| 圆角按钮 hover/press | ✅ | ✅ `ControlTemplate` ~15 行 | 原生够 |
| 下拉框样式 | ✅ | ✅ `ControlTemplate` ~40 行 | 原生够 |
| 表格（两级表头） | ❌ **它的 DataGrid 只是样式，无两级表头** | ✅ 自研（§4.3） | 原生必须自研 |
| 进度条 | ✅ | ✅ `ControlTemplate` ~10 行 | 原生够 |
| 消息对话框 | ✅（`MessageBox` 是它主要的实用件） | ✅ 用一个自建 `InfoDialog`（~60 行） | 原生够 |
| 通知条（InfoBar 等价） | ✅ `Growl` | ⚠️ 自建 toast（~100 行） | 边界 |

**裁定与理由**：

1. **Notion 风与 Fluent 风是两套视觉语言**。HandyControl 的主题是 Fluent（圆角大、色饱和、有渐变），**与 Python `style.py` 的 Notion 基线（`#37352F` 暖灰、发丝线、无阴影、无渐变）冲突**。引入后**反而要大量覆盖它的样式**，成本高于自绘。
2. **它解决不了 T5 最大的难点（两级表头）**——那是自研，与是否引入 HandyControl 无关。
3. **Pilot 期引入一个「NuGet 元数据未显式支持 net10」的包，会给「沙箱无 NuGet 网络、工程师无法 build」的局面再加不确定性**（若编译失败，工程师无法自行排查）。
4. **计划 §2.1/§8 已把 HandyControl 标为「控件/主题」角色**，但该决策在「表格原生可行」这一新认知下**可以且应当收窄**：**HandyControl 推迟到全量期**，届时若 toast/对话框/日期选择器等件数量上来，再评估。

**→ 规格裁定：T5 期引入 `CommunityToolkit.Mvvm` 8.4.0（必需），不引入 `HandyControl`。**

**⚠️ 此项与计划 §2.1 的锁定组合不一致**（计划把 HandyControl 3.x 列为锁定项）。**必须由用户拍板**。列入待明确事项 **U8**（附上我的推荐：推迟）。

### 7.4 不需要新增

| 已足够 | 说明 |
|---|---|
| `Microsoft.Data.Sqlite` 10.0.0 | 已在 `Directory.Packages.props:16` |
| `Dapper` 2.1.35 | 已在 `:15`（T5 的 UI 查询用 `SettlementEngine`，Dapper 透传） |
| `NPOI` 2.7.2 | 已在 `:17`（经 `LawFirm.Exporter` 透传） |

**CVE pinning 不受影响**：新增的 2 个包（实际只 1 个）无传递依赖冲突（`CommunityToolkit.Mvvm` 零依赖）。

### 7.5 `LawFirm.UI.csproj` 骨架

```xml
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net10.0-windows</TargetFramework>
    <UseWPF>true</UseWPF>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
    <LangVersion>latest</LangVersion>
    <OutputType>Library</OutputType>
    <RootNamespace>LawFirm.UI</RootNamespace>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="CommunityToolkit.Mvvm" />
    <!-- HandyControl 暂不引入（见规格 §7.3，待 U8 裁定） -->
  </ItemGroup>
  <ItemGroup>
    <ProjectReference Include="..\LawFirm.Exporter\LawFirm.Exporter.csproj" />
    <ProjectReference Include="..\LawFirm.Data\LawFirm.Data.csproj" />
  </ItemGroup>
</Project>
```

> **注意**：`LawFirm.Exporter` 依赖 `LawFirm.Data`；`LawFirm.UI` 显式引两者（显式优于隐式，且 `DbConnection` 需要 `LawFirm.Data`）。

---

## 8. 子任务分解表

> 约束：≤5 个子任务、每任务 ≥3 文件、按依赖排序、首个为基础设施。

### T5.1 —— UI 工程基础设施 + 主题 + 主框架骨架（P0）

| 项 | 内容 |
|---|---|
| **文件** | `csharp/LawFirm.UI/LawFirm.UI.csproj`（新）<br>`csharp/LawFirm.UI/Themes/Palette.xaml`（新）<br>`csharp/LawFirm.UI/Themes/Typography.xaml`（新）<br>`csharp/LawFirm.UI/Themes/NotionControls.xaml`（新）<br>`csharp/LawFirm.UI/Shell/MainShellWindow.xaml(+cs)`（新）<br>`csharp/LawFirm.UI/Shell/TitleBar.xaml(+cs)`（新）<br>`csharp/LawFirm.UI/Shell/NavIcons.cs`（新）<br>`csharp/LawFirm.UI/Shell/NavigationService.cs`（新）<br>`csharp/LawFirm.UI/Views/PlaceholderView.xaml(+cs)`（新）<br>`csharp/LawFirm.App/App.xaml(+cs)`（改）<br>`csharp/LawFirm.App/MainWindow.xaml(+cs)`（改为壳）<br>`csharp/LawFirm.App/LawFirm.App.csproj`（改）<br>`csharp/lawfirm.sln`（改）<br>`csharp/Directory.Packages.props`（改） |
| **依赖** | 无 |
| **内容** | ① 新建 `LawFirm.UI` 类库 + sln 挂载 + CPM 追加 `CommunityToolkit.Mvvm` 8.4.0；② 3 个资源字典（§1.3 全部 token + 字体/字重 + 按钮/下拉/表格/滚动条/进度条样式）；③ 无边框主窗（`WindowStyle=None` + `WindowChrome`，1280×820，Min 1100×650）；④ 自绘标题栏（页名 + 三按钮 + hover 红底关闭）；⑤ 7 枚导航图标 `Geometry`；⑥ `NavigationService`（`NAV_GROUPS` 注册表 + 页面栈 + `Select(key)` 更新标题）；⑦ `PlaceholderView`；⑧ `App.xaml` 合并 UI 资源字典；⑨ `MainWindow` 改为承载 `MainShellWindow` 的壳 |
| **验收标准** | ① `dotnet build csharp\lawfirm.sln` 在**用户本机**通过（沙箱无法 build）；② 启动后出现无边框窗口，标题栏显示「导入台账」；③ 拖拽标题栏可移动窗口、双击最大化、边缘可缩放；④ 关闭按钮 hover 变 `#E81123` 红底白字；⑤ 点侧栏任一项，标题栏页名实时更新；⑥ 21 个未迁移页显示占位页「功能待迁移」；⑦ 窗口拉窄至 900px 标题不压按钮（`TextTrimming`） |
| **预估行数** | ~900（XAML 600 + C# 300） |

### T5.2 —— 表格基础设施（P0，最大风险）★

| 项 | 内容 |
|---|---|
| **文件** | `csharp/LawFirm.UI/Controls/NotionDataGrid.cs`（新）<br>`csharp/LawFirm.UI/Controls/TwoTierHeaderGrid.cs`（新）<br>`csharp/LawFirm.UI/Controls/ColumnLayoutManager.cs`（新）<br>`csharp/LawFirm.UI/Controls/TwoTierWidthConverter.cs`（新）<br>`csharp/LawFirm.UI/Controls/NotionDataGrid.xaml`（新，`Themes/Generic.xaml`）<br>`csharp/LawFirm.UI/Services/ColumnStateStore.cs`（新）<br>`csharp/LawFirm.UI/Controls/PageHeader.xaml(+cs)`（新） |
| **依赖** | T5.1 |
| **内容** | ① `NotionDataGrid`（§4.5 全部属性 + `CellStyle`/`RowStyle`/`ColumnHeaderStyle`）；② 像素滚动 + 虚拟化（`ScrollUnit=Pixel` + `CanContentScroll=True`）；③ `FrozenColumnCount` 绑定 + 冻结列灰底（`MultiBinding` 转换器）；④ 两级表头（§4.3 的 `ColumnHeaderStyle` 模板 + 宽度同步 converter）；⑤ `ColumnStateStore`（`%APPDATA%\LawFirm\ui-state.json`，列集合不一致丢弃）；⑥ `ColumnLayoutManager.ComputeFill`（§4.4 纯函数移植，**含 4 个单测**）；⑦ 悬停全文（`CellStyle` tooltip + 宽度判定）；⑧ 选中保色（`IsSelected` 只改 `Background`）；⑨ `PageHeader`（标题 + 「?」） |
| **验收标准** | ① **首日必须做最小验证**：1 列普通 + 2 列两级 + `FrozenColumnCount=1` 三者共存、横向滚动时冻结列不动、两级表头文字宽度随列宽实时同步（**此验证失败则立即上报，见 U5**）；② 鼠标滚轮为平滑像素滚动（非整行跳）；③ 拖动列宽后重开应用，宽度/顺序被记住；④ 把列集合改掉（模拟 14 列↔3 列切换）后重开，**不串档**；⑤ 负数单元格显示红色；⑥ 选中一行，红色数字仍是红色（不变白）；⑦ `ComputeFill` 4 个单测通过 |
| **预估行数** | ~1,000（C# 700 + XAML 300） |

### T5.3 —— 「各类报表」页（4 tab）+ ViewModel + 导出集成（P0）

| 项 | 内容 |
|---|---|
| **文件** | `csharp/LawFirm.UI/Views/Settlement/SettlementView.xaml(+cs)`（新）<br>`.../PersonalSettlementTab.xaml(+cs)`（新）<br>`.../MonthlyReportTab.xaml(+cs)`（新）<br>`.../StaffIncomeTab.xaml(+cs)`（新）<br>`.../InvoiceIncomeTab.xaml(+cs)`（新）<br>`csharp/LawFirm.UI/ViewModels/SettlementViewModel.cs` + 4 个子 VM（新）<br>`csharp/LawFirm.UI/Services/SettlementQueryService.cs`（新）<br>`csharp/LawFirm.UI/Views/PlaceholderView` 的注册更新（改）<br>`NavigationService` 注册 settlement（改）<br>`csharp/LawFirm.UI/Dialogs/ExportScopeDialog.xaml(+cs)`（新） |
| **依赖** | T5.1、T5.2 |
| **内容** | ① 4 tab 宿主 + 页标题化 tab 样式 + 右上角「?」；② Tab1 个人结算总表（§5.1 全部功能 + §5.1 表格行构成逐行复刻）；③ Tab2/3/4 同构实现（以 `settlement_view.py:520-956` 逐函数为唯一依据）；④ `SettlementQueryService`（后台跑 `SettlementEngine.Build`，返回可直接绑定的行模型）；⑤ 导出三按钮接线到 `ExportService`（T5.4 的接口先在此定义签名）；⑥ `ExportScopeDialog`（多选经办人 + 全选，对应 `gen_selected`） |
| **验收标准** | ① 4 个 tab 名与顺序与 Python 逐字一致；② Tab1 选「丁祥锋」+「汇总」+「全年」，**17+ 行内容与 Python 页逐格一致**（列名 `项目/1月../12月/合计`，数值格式 `1,234.56`，`一、`..`六、` 行加粗）；③ 选月份 = 3 时，列变为 `项目/1月/2月/3月/合计`；④ 「四、未收款金额」的合计列 = `uncollected_total`（**不是 12 月之和**）；⑤ 类型下拉按该人身份动态重建、单身份自动选中；⑥ Tab3 的两级表头正确显示（序号/姓名 整高 + 5 组各跨 2 列）；⑦ 人员列表 = `Build(year).Keys` 按 `StringComparer.Ordinal` 排序；⑧ 默认选中「最近有数据的年份」 |
| **预估行数** | ~1,400（XAML 700 + C# 700） |

### T5.4 —— 异步进度条 + 导出服务（P0，门槛项）

| 项 | 内容 |
|---|---|
| **文件** | `csharp/LawFirm.UI/Services/ExportService.cs`（新）<br>`csharp/LawFirm.UI/Dialogs/ProgressWindow.xaml(+cs)`（新）<br>`csharp/LawFirm.UI/ViewModels/ProgressViewModel.cs`（新）<br>`csharp/LawFirm.UI/Dialogs/MessageDialog.xaml(+cs)`（新，替代 `QMessageBox`）<br>`csharp/LawFirm.UI/Views/Settlement/*.xaml.cs`（改，接线命令） |
| **依赖** | T5.3 |
| **内容** | ① `ExportService`（`ExportAllAsync`/`ExportSelectedAsync`，**后台线程自建只读连接**，`IProgress<ProgressReport>` 逐人上报，`ct` 每人之间检查）；② `ProgressWindow`（重写 `ProgressBar` 模板 + 取消按钮 + 状态文本 + 百分比）；③ `ProgressViewModel`（`ProgressValue`/`StatusText`/`CancelCommand`/`IsBusy`）；④ `MessageDialog`（Notion 风，替代 `QMessageBox.information/critical`）；⑤ 三按钮命令接线 + `CanExecute = !IsBusy` 防重入 |
| **验收标准** | ① **导出全部员工（42 人）期间，主窗口可拖动、可重绘、进度条实时前进**（★ 门槛项）；② 进度文本显示「正在导出 {姓名}（{n}/42）…」；③ 点「取消」后 ≤1 个文件的时间内停止，日志显示「已取消。」；④ 导出期间「导出全部员工」按钮为禁用态（防重入）；⑤ 导出完成后弹「生成完成：已生成 42 份…」；⑥ 导出失败弹「生成失败」+ 异常信息，**应用不崩溃**；⑦ 全程 `query_only` 保持（无任何写库调用） |
| **预估行数** | ~500（XAML 200 + C# 300） |

### T5.5 —— 收口验证 + 视觉对标（P0）

| 项 | 内容 |
|---|---|
| **文件** | `docs/t5-visual-checklist.md`（新，截图对标记录）<br>`csharp/LawFirm.UI/Controls/ColumnLayoutManager.Tests.cs`（新，或放入既有测试项目）<br>`scripts/t5_smoke.bat`（新，纯 ASCII，构建 + 启动冒烟）<br>可能的小修：`Themes/*.xaml`、`Shell/*.xaml`（按截图微调） |
| **依赖** | T5.1–T5.4 |
| **内容** | ① 逐项核对 §9 视觉验收清单（对比 Python 真机截图，逐条打勾）；② `ComputeFill` 单测落地；③ 冒烟脚本；④ 按截图微调（间距/圆角/字号）；⑤ 产出「视觉接近度自评 %」记录，供用户打分 |
| **验收标准** | ① 视觉接近度自评 ≥80%（逐条列出「已对齐 / 有意差异 / 未实现」）；② 冒烟脚本在用户本机跑通；③ 无未处理异常；④ 输出 `docs/t5-visual-checklist.md` 供用户主观评分（≥4/5 门槛） |
| **预估行数** | ~150 + 微调 |

**规模汇总**：5 个子任务，**最大单项预估约 1,400 行**（T5.3），合计约 3,950 行。**远超计划 §6 的「1–2 人日」**——见 §0 的范围裁定。

---

## 9. 视觉验收清单（供用户打分）

| # | 项 | Python 基线 | 达标判据 |
|---|---|---|---|
| V1 | 无边框窗 + 自绘标题栏（36px、页名左对齐、三按钮右） | `main_window.py:105-171` | 截图并排无法一眼区分 |
| V2 | 标题栏底色 = 侧栏底色（`#f7f6f3`）+ 底 1px 分隔线 | `main_window.py:138-148` | 同上 |
| V3 | 侧栏 240px、暖灰底、右 1px 分隔线 | `style.py:108` | 同上 |
| V4 | 侧栏 6 个大类，字号 13px，组名 `text_mute`→hover `text` | `style.py:113-121` | 分组数、名称逐字一致 |
| V5 | 分组图标 20px、1.5px 描边、`text_mute`→hover `text` | `nav_icons.py:123-175` | 形状可辨认为同一图标 |
| V6 | 子项缩进 36px、行高 31px、选中底 `#e3e1db` + 字重 600 | `style.py:113-121` | 选中态视觉一致 |
| V7 | 页标题 20px/700；tab 标题化（20px/700 + 选中底边 2px `#2eaadc`） | `style.py:167,237-243` | tab 视觉一致 |
| V8 | 表格：底色 `#FFFFFF`、圆角 10、表头底 `#fbfbfa` + 底边 `#e9e9e7`、网格线 `#F1F1EF` | `style.py:199-215` | 圆角/描边/网格线可见 |
| V9 | 表格行高 34、单元格内边距 6/10、选中底 `#e3e1db` | `table_view.py:289`、`style.py:204-205` | 行高与 Python 测量一致（±2px） |
| V10 | 冻结首列横向滚动不动 + 灰底 `#EAEAEA` | `table_features.py:36,63-67` | 滚动到最右仍见「项目」列 |
| V11 | 两级表头（Tab3）：第 1 行大类跨 2 列居中、第 2 行细分、总高 44 | `table_features.py:244,304-316` | 跨列文字居中对齐（±2px） |
| V12 | 按钮：次按钮透明底 + `#DADAD7` 描边 + 圆角 8 + 内边距 7/16；主按钮 `#2eaadc` 白字 | `style.py:172-184` | hover 变 `#efedea` |
| V13 | 下拉框：白底 + `#DADAD7` 描边 + 圆角 7 + 展开项选中底 `#e3e1db` | `style.py:187-196` | 展开列表视觉一致 |
| V14 | 滚动条 10px 宽、圆角把手 `text_faint`、无箭头按钮 | `style.py:218-224` | 滚动条细且无箭头 |
| V15 | 数字格式 `1,234.56`、右对齐、负数红 `#eb5757` | `table_view.py:18,335-336` | 千分位 + 右对齐 |
| V16 | 布局基线：左缩进 24 / 上边距 16 / 间距 12 | 简报第 40 行（用户定案） | 与 Python 页并排比较 |

---

## 10. 风险与待明确事项

### 10.1 风险清单

| ID | 风险 | 等级 | 影响 | 处理 |
|---|---|:-:|---|---|
| R1 | **两级表头 + 冻结列 + 像素滚动三者叠加未验证** | **高** | 可能整个表格方案要返工 | T5.2 **首日**做最小验证（1+2+冻结）；失败立即上报；Plan B 见 §4.3（ComplexDataGridHeader 模式） |
| R2 | 自定义表头高度与冻结列分割线错位 | 中 | 视觉粗糙 | T5.5 截图微调；必要时 `ColumnHeaderHeight=44` 逐像素对齐 |
| R3 | 两级表头跨列文字宽度累计误差（±1px/列） | 中 | 文字偏 1–2px | converter 里 `+2` 补偿；T5.5 真机微调（U4） |
| R4 | `net10.0-windows` 下 WPF 运行时行为差异 | 中 | 编译/运行失败 | **沙箱无 build 能力** → 工程师在本机首次 build 时若有问题立即上报；本规格所有 API 均为 WPF 长期稳定 API |
| R5 | `CommunityToolkit.Mvvm` 8.4.0 在 net10 的源生成器兼容性 | 低 | 编译失败 | 官方支持 netstandard2.0 → 应无问题；若失败，**降级到手写 `INotifyPropertyChanged`**（备用方案：不用 MVVM 库，用 `ObservableObject` 手写基类） |
| R6 | 后台线程 + SQLite 连接的线程纪律 | **高** | `InvalidOperationException` / 数据风险 | §6.2 已定：**后台线程自建只读连接**；禁止跨线程传 `SqliteConnection` |
| R7 | 非模态进度窗导致重复导出 | 中 | 并发写盘 / UI 错乱 | `CanExecute = !IsBusy` 禁用按钮（§6.3 第 3 点） |
| R8 | WPF 默认 `ToolTip` 始终显示（未做宽度判定） | 低 | 视觉噪音 | §4.2 C6 的退化方案；T5-SHOULD 补宽度判定 |
| R9 | `StringFormat N2` 受 `CurrentCulture` 影响 | 中 | 千分位显示错（`1.234,56`） | `App.xaml.cs` 里 `OverrideMetadata` 强制 `zh-CN`（§4.5 注意项） |
| R10 | 范围远超 1–2 人日 | **高** | 进度失控 | §0 已诚实拆包；请用户重新给 T5 定人日（见 U9） |
| R11 | 数据零风险纪律 | **高** | 污染 Python 版数据 | 全程 `query_only + ReadOnly`；UI 层无任何写库调用；导出只写用户选的目录 |
| R12 | Pilot 期用户要求「真实可用」压力 → 工程师为赶进度抄写 Python 而未逐行核对 | 中 | 数值口径偏差 | T5.3 验收标准②要求「与 Python 页逐格一致」，需真机对拍 |

### 10.2 待明确事项（U — 需用户/主理人裁定）

| ID | 事项 | 我的建议 |
|---|---|---|
| **U1** | 无边框窗最大化时是否覆盖任务栏？ | 用 `StateChanged` + `MaxHeight = WorkArea.Height + 8` 补偿；若不接受瑕疵需挂 `WM_GETMINMAXINFO`（+~30 行 P/Invoke） |
| **U2** | 侧栏「鼠标移入自动展开 / 移出自动收回」（`sidebar.py:456-468`）是否 T5 实现？ | **T5 不实现**（固定展开 + 手动折叠）；列 T5-SHOULD。理由：这是体验增强，不属 §1.3 ③ 门槛项 |
| **U3** | 7 枚导航图标是否全部精确复刻？ | T5 精做 3 枚（各类报表/数据导入/台账查看），其余 4 枚用简化等价图形；全量期补精确版 |
| **U4** | 两级表头跨列文字宽度的 ±1px 补偿值 | 先 `+2`，T5.5 真机微调 |
| **U5** | 两级表头 + 冻结列 + 像素滚动能否共存（**未验证**） | T5.2 首日最小验证；**失败则整个表格方案需重新评估**（会动摇「WPF 原生够用」的结论） |
| **U6** | 列布局持久化存 `%APPDATA%`（不跟 Seafile 同步）还是 `data/prefs.json`（跟 Seafile 同步）？ | 建议 `%APPDATA%`。理由：Python 存注册表（不同步）；存 `data/` 会导致 3 台 PC 互相覆盖；且 `data/` 是 db 目录，不应放 UI 状态 |
| **U7** | 取消导出时，已落盘的部分文件是否删除？ | 建议**不删除**（取消 = 不再继续）。若用户要求「撤销」需加清理逻辑 |
| **U8** | **HandyControl 是否引入**（与计划 §2.1 锁定组合冲突） | **T5 不引入**（§7.3 论证：Notion 风 vs Fluent 风冲突；解决不了两级表头；net10 元数据未显式支持）。推迟到全量期评估 |
| **U9** | **T5 人日重估**：计划定 1–2 人日，本规格拆出 5 子任务 ≈3,950 行（远超） | 建议 T5 定为 **4–6 人日**；或按 §0 的 T5-MUST 砍掉 Tab2/3/4（只做 Tab1 + 主框架 + 表格 + 进度条），仍可拿 §1.3 ③ 门槛（门槛只要求「1 页高保真」，Tab1 即 1 页） |
| **U10** | 视觉对标素材：**仓库内无任何截图**（已 `find *.png = 0`） | 请用户提供 Python 版真机截图（或允许工程师自行 `run_app.bat` 截图） | 
| **U11** | 其余 21 页占位策略 | T5 用统一「功能待迁移」占位页；确认可接受 |
| **U12** | 深色皮肤（`notion_dark`）是否 T5 做？ | **不做**（Pilot 只做 `notion_light`）；资源字典按 token 组织，后续加 `PaletteDark.xaml` 即可 |

### 10.3 沙箱限制声明（重要）

- **沙箱无 NuGet 网络** → 工程师**无法 build**，本规格所有代码结构需通过**静态审查**。
- **沙箱无 WPF 运行时** → 所有视觉/交互项**以用户本机真机验收为准**（与 `design/titlebar_dynamic_spec.md:140` 的既有约定一致）。
- 本规格中所有 API 名称（`FrozenColumnCount`、`VirtualizingStackPanel.ScrollUnit`、`WindowChrome`、`ColumnHeaderStyle` 等）均为 WPF 长期稳定 API，**已逐项核实存在**；但**组合行为（U5）无法在沙箱验证**。

---

## 11. 关键源码行号索引（便于工程师核对）

| 主题 | 文件:行 |
|---|---|
| 侧栏分组定义 | `app/ui/main_window.py:56-93` |
| 标题栏（页名 + 三按钮 + 配色） | `app/ui/main_window.py:105-171` |
| 无边框窗 + 布局 | `app/ui/main_window.py:173-287` |
| 侧栏几何基准常量 | `app/ui/sidebar.py:32-40` |
| 侧栏宽度动画 / 分组动画 | `app/ui/sidebar.py:270-305, 491-513` |
| 侧栏 hover 自动折叠 | `app/ui/sidebar.py:456-468` |
| 图标绘制（7 枚） | `app/ui/nav_icons.py:123-175` |
| 调色板（浅色全部 token） | `app/ui/style.py:28-45` |
| 字体 + 表格 + 按钮 + 滚动条 QSS | `app/ui/style.py:84-224` |
| tab 标题化样式 | `app/ui/style.py:237-243` |
| 冻结列手动重绘 | `app/ui/widgets.py:216-262` |
| 两级表头自绘 | `app/ui/table_features.py:230-322` |
| 悬停全文 / 选中保色 | `app/ui/table_features.py:71-100` |
| 像素滚动安装 | `app/ui/table_features.py:109-110` |
| 表头底色 + 排序三角 | `app/ui/table_features.py:125-227` |
| 按列筛选引擎 | `app/ui/table_features.py:390-595` |
| 列状态存储（QSettings/JSON） | `app/ui/column_layout.py:39-95` |
| 严格填充算法 | `app/ui/column_layout.py:98-151` |
| 列设置应用（显隐/重排/定宽） | `app/ui/column_layout.py:195-264` |
| 冻结切换（面板语义） | `app/ui/column_layout.py:416-446` |
| 表格渲染 + 合计行 | `app/ui/table_view.py:278-322` |
| 负数红 / 千分位格式 | `app/ui/table_view.py:18, 328-354` |
| settlement 4 个 tab | `app/ui/settlement_view.py:45-161, 520-589, 666-733, 815-879` |
| settlement 表格行构成 | `app/ui/settlement_view.py:280-333` |
| settlement 导出（全部/指定/月度） | `app/ui/settlement_view.py:347-363, 365-425, 445-517` |
| 字号基准（px 派生） | `app/ui/scale.py:23-101` |
| 结算引擎 API | `csharp/LawFirm.Exporter/SettlementEngine.cs:18-25, 446-470` |
| 个人总表导出 API | `csharp/LawFirm.Exporter/PersonSettlementExporter.cs:56, 100` |
| 月度结算表导出 API | `csharp/LawFirm.Exporter/SettlementReportExporter.cs:35-36` |
| 只读连接 | `csharp/LawFirm.Data/DbConnection.cs`（`FindDatabase` / `OpenReadOnly`） |
| 现有演示窗（将被替换） | `csharp/LawFirm.App/MainWindow.xaml(+cs)` 全文 |
| CPM + CVE pinning | `csharp/Directory.Packages.props:1-31` |
| 计划 T5 行 + 门槛 | `docs/csharp-wpf-refactor-plan-2026-09-09.md:42, 300` |
| 技术栈锁定 | 同上 `:65-107` |
| 22 页映射表 | 同上 `:182-209` |
