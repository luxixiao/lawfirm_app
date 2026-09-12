# T7 · 员工管理 / 费用类型 两页 UI 实现规格

> 版本：v3（据 lead 二轮裁定修订：Q4 禁用 / Q5' 不许自行降级 / Q7 写失败必回读 / Q8 change_log 分表）
> 分支：`pilot/dotnet`
> 作者：架构师（Bob / ui-arch）
> 目标读者：工程师（本文件即实现依据）
> 上位依据：`docs/t5-ui-skeleton-spec.md`（视觉/工程基线）、`design/sidebar_redesign_spec.md`（Notion 视觉基线）
> 行为真源（Python）：`app/ui/staff_view.py`、`app/ui/expense_cat_view.py`、`app/engine/staff_type.py`、`app/engine/expense_cat.py`
> **本文件只描述设计，不含实现。不改任何 `.py` / `.cs` / `.xaml` 源码，不跑 git。**

---

## 0. 任务边界与范围裁定（先读）

| 项 | 内容 |
|---|---|
| **范围** | 把 Python 的两个**基础数据维护页**（会写库）移植为 WPF 页：`StaffView`（nav key = `staff`）、`ExpenseCatView`（nav key = `expense_cat`）。 |
| **不做的** | 写库层（`WriteGuard.Execute(dbPath, reason, work)` + `LawFirm.Data` 服务层）由另一名工程师并行实现；本文**只声明调用点与语义**，不设计其内部。 |
| **两页目前状态** | 均为 `PlaceholderView`（`NavigationService.GetOrCreatePage` 未注册 → 占位）。本任务后 `staff` / `expense_cat` 两 key 注册为真实页。 |
| **视觉硬约束（D-Notion clean）** | 左缩进 **24px** / 上边距 **16px** / 正文间距 **12px**，22 页统一。⚠️ Python `expense_cat_view.py:296` 用的是 `(28,24,28,20)`，**本规格统一归一到 24/16/12**（有意偏差，见 §8-Q1）。 |
| **两页的写库性质** | 这是 Pilot 首次出现**写库页**。所有写点必须经 `WriteGuard.Execute`，并在写后**单次重读**。见 §6。 |

> **⚠️ 一个必须叫停的 Python 行为（lead 已裁定，且比原建议更严格）**：
> `expense_cat_view.py:337` 的 `refresh()` 内部**无条件**调用 `ec.sync_from_ledger()`（**会写库**），而 `refresh()` 又被 `showEvent`（`:331-333`）调用——即 **Python 版「只要打开费用类型页就写一次库」**。
> **本规格禁止复制该行为。** C# 侧硬约束：
> - `refresh()` / 页面 `showEvent` = **纯读：只有 SELECT，零写、零备份**。
> - `ensure_defaults()` / `ensure_categories()` **不得**放进 `refresh()`。
> - 缺失的基础数据补齐，改由 **`BaseDataBootstrap.EnsureAll()`** 承担：**先 SELECT 检查、确实缺才补**，在 Shell 首次加载时跑一次（或首次进入 `staff` / `expense_cat` 时**惰性跑一次**）。稳态（真实库）下它就是一条 `SELECT COUNT`，**纯读**。
> - 理由（**写进契约**）：本轮写通道的保险之一是「**每次写操作前做一次 SQLite 在线备份**」。若 `refresh()` 沿用 Python 那样每次调 `sync_from_ledger()` + `ensure_*()`，就会变成**每看一次页面就产生一份备份**，把 `data/backups` 刷爆。因此 `refresh()` 必须纯读。
>
> **写操作分两类（务必区分，见 §6）**：
> | 类别 | 触发 | 是否备份 |
> |---|---|---|
> | **不带备份** | `BaseDataBootstrap.EnsureAll()`（幂等 `INSERT OR IGNORE` 固定集合） | ❌ 不备份（仅走写闸门串行） |
> | **带备份** | 所有**用户 CRUD**（员工名单 / 员工类型 / 费用类型 / 导入）+「从费用台账同步」按钮 | ✅ 每次写前一份在线备份 |
>
> 两类**都经写闸门串行**；差别只在「是否触发备份」。

---

## 1. 现状与复用资产核对

### 1.1 可直接复用的**已存在** style key / 资源（**不得改名、不得重造**）

来源：`Themes/Palette.xaml`、`Themes/Typography.xaml`、`Themes/NotionControls.xaml`、`Themes/Generic.xaml`。

| 类别 | 可用 key | 用途 |
|---|---|---|
| 底色 | `Canvas` `Bg` `BgSide` `BgTable` `BgHover` `BgSelect` | 卡底 `Bg`，hover `BgHover`，选中 `BgSelect` |
| 描边 | `Border` `Border2` `GridLine` | 卡片描边用 `Border`，控件描边用 `Border2` |
| 文字 | `Text` `TextMute` `TextFaint` | 主文字 `Text`，次要/caption `TextMute` |
| 强调 | `AccentBlue` `Red` | 主按钮、tab 下划线；红不用于本页 |
| 字体 | `AppFontFamily` `FontBaseSize`(13) `FontCaptionSize`(12) `FontPageTitleSize`(20) `FontDialogTitleSize`(15) | — |
| 文本样式 | `BodyText` `CaptionLabel` `PageTitleText` | caption 说明行用 `CaptionLabel` |
| 按钮 | `NotionButton`（次）`PrimaryButton`（主/`AccentBlue`） | 主按钮「导入职工清单」；其余均 `NotionButton` |
| 下拉 | `NotionCombo` `NotionComboItem` | 员工编辑对话框的「类型」下拉 |
| tab | `PageTitleTabControl` `PageTitleTabItem` | **两页 tab 头统一复用**（20px/700 + 选中 2px `AccentBlue` 下划线） |
| 表格 | `NotionDataGridStyle` `NotionCellStyle` `NotionRowStyle` `NotionColumnHeaderStyle` `HeaderGripperStyle` `FrozenCellStyle` | 列表页 DataGrid |
| 滚动条 | `NotionScrollBar` `NotionScrollThumb` | 卡片区 ScrollViewer |
| 转换器 | `IsNegative`（`Generic.xaml` 定义） | 本页用不到数值负列 |

### 1.2 **需要新增**的 style key（明确标注 NEW，落在新字典 `Themes/Cards.xaml`）

| key（NEW） | 定义要点 | 依据 |
|---|---|---|
| `CardBorder` | `Border` 模板：`Background={Bg}`、`BorderBrush={Border}`、`BorderThickness=1`、`CornerRadius=10`、`Padding=10,8` | 对齐 `expense_cat_view.py:145-149`（`objectName="card"`、`setContentsMargins(10,8,10,8)`） |
| `CardTitleText` | `TextBlock`：`FontFamily=AppFontFamily`、13px、**SemiBold**、`Foreground={Text}` | `expense_cat_view.py:153-154`（`cardTitle`） |
| `CardButton` | `Button`：`BasedOn=NotionButton`，`Padding=8,2`、`FontSize=12`、**`MinWidth` 按文字宽度**（见下） | `expense_cat_view.py:160,200-206`（`cardBtn` 紧凑样式 + 按 `fontMetrics` 设最小宽） |
| ~~`PageTitleTabControlIndented`~~ | **不需要**（Q3 裁定改**共享** `PageTitleTabControl` 的 TabPanel margin，全局对齐 24px，见 §3.1/§7.2） | — |

> `CardButton` 的「按文字宽度设最小宽」在 WPF 里**用 `MinWidth` 写死不安全**（不同字体度量不同）。落地做法：`CardButton` 设 `MinWidth=44`（两字 13px≈26 + padding 16 ≈ 42，取 44 留余量）；「移动」按钮因带下拉箭头设 `MinWidth=64`。这样窄卡片下 5 个按钮一行不裁字，与 Python 的 `fm.horizontalAdvance(text)+26/+40` 同效。

### 1.3 现有 C# 房屋风格（`StaffView`/`ExpenseCatView` 必须照抄）

| 约定 | 依据文件 |
|---|---|
| VM 继承 `ViewModelBase`（`ObservableObject`），用 `[ObservableProperty]` + `partial void OnXChanged` | `ViewModels/PersonalSettlementViewModel.cs` |
| 命令用 `[RelayCommand(CanExecute=...)]`，`CanExecute` 返回 `bool` | 同上 `:197` |
| 选项用嵌套 `record`（`Label`/`Value`） | 同上 `:33-48` |
| **页必须自己设 `DataContext`**（历史 bug：`SettlementView` 曾漏设导致整页绑定落空） | `Views/Settlement/SettlementView.xaml.cs:12` |
| 提示/报错用 `MessageDialog.Info(title,msg)` / `MessageDialog.Error(...)` | `Dialogs/MessageDialog.xaml.cs` |
| 表格列由代码后置用 `GridColumnFactory.TextColumn(key, numeric, frozen, minWidth, header)` 构造 | `Controls/GridColumnFactory.cs`、`Views/Settlement/PersonalSettlementTab.xaml.cs` |
| 列宽持久化 `ColumnStateStore` + `StatePage`/`StateName` | `Controls/NotionDataGrid.cs`、`Services/ColumnStateStore.cs` |
| 页头用 `Controls/PageHeader`（`Title` + `HelpText` 两个 DP） | `Controls/PageHeader.xaml.cs` |

### 1.4 导航注册现状

`Shell/NavigationService.cs:120-126` 的 `NAV_GROUPS` **已含** `("staff","员工管理")` 与 `("expense_cat","费用类型")`，只是未注册页面工厂（`:140` 只注册了 `settlement`）。本任务**必须改的就是这一处**：

```csharp
// NavigationService 构造函数追加（放在 settlement 注册之后）
RegisterPage("staff",       () => new Views.Staff.StaffView());
RegisterPage("expense_cat", () => new Views.ExpenseCat.ExpenseCatView());
```

### 1.4.1 写通道与「基础数据引导」（`BaseDataBootstrap`）

| 项 | 规格（lead 裁定） |
|---|---|
| 写闸门 | 所有写操作**串行**经写闸门（`WriteGuard.Execute`）。 |
| 备份策略 | **用户 CRUD + 同步按钮 = 写前一份 SQLite 在线备份**；**幂等引导 = 不备份**。 |
| 引导入口 `LawFirm.Data/BaseDataBootstrap.EnsureAll()` | **先 SELECT 检查、缺才补**，补的是**幂等 `INSERT OR IGNORE` 固定集合**：① 员工类型内置三类 + 预置 `挂靠/其他`（= `staff_type.ensure_defaults`，`staff_type.py:54-69`）；② 费用 5 分类（= `expense_cat.ensure_categories`，`expense_cat.py:57-67`）。 |
| 调用时机 | Shell **首次加载**跑一次；或首次进入 `staff` / `expense_cat` 时**惰性跑一次**（二选一，推荐前者以便一次性就绪）。 |
| 稳态成本 | 真实库中已存在 → 就是一条 `SELECT COUNT`，**纯读、零写、零备份**。 |
| **禁止** | 把 `EnsureAll()` 或其等价物放进 `refresh()` / `showEvent`。 |

---

## 2. 两页行为 ↔ Python 真源对照（速查）

| 页 | Python 真源 | 关键行 |
|---|---|---|
| `StaffView` | `app/ui/staff_view.py` | tab 宿主 `:31-49`；员工名单 `:54-86`；员工类型 `:91-129`；CRUD `:198-444` |
| 员工类型规则 | `app/engine/staff_type.py` | 内置三类 `:21`；改名禁内置 `:137-156`；删除禁内置+在用 `:168-183`；员工删除引用检查 `:240-257` |
| `ExpenseCatView` | `app/ui/expense_cat_view.py` | 页 `:290-470`；卡片 `:140-259`；拖拽 `:32-133` |
| 费用类型规则 | `app/engine/expense_cat.py` | 5 分类 `:18`；`ordered_types()` `:197-204`；`save_layout` `:343-350`；`type_reference_count` `:259-267` |

---

## 3. `StaffView` 规格（nav key = `staff`）

### 3.1 页骨架 + **tab 头与「单功能页标题」的对齐**（用户明确要求）

**结构**（对齐 `staff_view.py:31-49`）：

```
StaffView : UserControl   ← 构造器里 DataContext = new StaffViewModel();
└─ Grid  Background={Canvas}  Margin="24,16,24,12"      ← D-Notion 基线 24/16/12
   └─ TabControl  Style={PageTitleTabControl}   SelectedIndex={Binding SelectedTabIndex, Mode=TwoWay}
      ├─ TabItem Header="员工名单" Style={PageTitleTabItem} → <views:StaffListTab DataContext="{Binding List}"/>
      └─ TabItem Header="员工类型" Style={PageTitleTabItem} → <views:StaffTypeTab DataContext="{Binding Types}"/>
   └─ Button "?"  HorizontalAlignment=Right VerticalAlignment=Top Margin="0,0,4,0"   ← 右上角帮助（对应 :43-46 tab_help_corner）
```

**tab 头对齐规则（关键，逐条）**：

| 项 | 规格 | 理由 |
|---|---|---|
| 复用样式 | `PageTitleTabControl` + `PageTitleTabItem`（**与 `SettlementView` 同款**） | 保证与现有真实页「同一视觉语言」，不重造 |
| 字号/字重 | 20px / Bold / 未选中 `TextMute`、选中 `Text` + 底部 2px `AccentBlue` | 由 `PageTitleTabItem` 模板给出，**无需改样式文件** |
| **左对齐（已裁定：统一 24px）** | 首 tab 文字左缘必须落在 **24px**（= 全局左缩进）。`PageTitleTabItem` 内容 `Margin="8,10,8,6"`，`PageTitleTabControl` 的 `TabPanel` 默认 `Margin="12,0,12,0"` → 默认首字左缘 12+8 = 20px，**差 4px**。**做法：改共享样式**——把 `Themes/NotionControls.xaml` 里 `PageTitleTabControl` 的 `TabPanel Margin` 由 `12,0,12,0` 改为 **`16,0,12,0`** → 16+8 = **24px**，使 `StaffView` 与 `SettlementView` **同时**对齐 24px。**不再需要 `PageTitleTabControlIndented` 局部覆盖。** | lead 裁定：两页 tab 左缘统一 24，**并顺带把 `SettlementView` 也对齐 24**（用户明确要求「Tab 页与单功能页标题样式一致」）。已列入真机视觉点检清单，需确认不影响 `docs/t5-visual-checklist.md` |
| 兼容性 | 改的是共享模板的**内边距**（12→16），字体/字号/字重/下划线/18px 行高全部不变；`SettlementView` 视觉仅左移 4px。须在真机与 T5 visual checklist 比对确认 | — |
| 上对齐 | tab 头文字上缘对齐 **16px**：外容器 `Margin="24,16,24,12"` 已提供 16px；`PageTitleTabItem` 自身内容 `Margin` 顶部为 10 → 实际首字顶距窗口 = 16+10 = 26px 偏高。**处置**：把 tab 头所在行的外 margin 改为 `"24,6,24,12"`，使 6+10 = **16px** | 视觉上「tab 标题 = 页标题」，顶距与其他页 `PageHeader`（16px）一致；此项**需真机微调**（见 §8-Q3） |
| 帮助气泡 | 右上角 `?`，`ToolTip` 文字 = `"职工花名册（基础数据）：台账导入时校验经办人是否在此名单中。类型中只有「合伙 / 聘用 / 兼职」参与业务收入计算。"`（`staff_view.py:44-45`） | 对齐 Python `tab_help_corner` |

> **裁定**（Q3）：**改共享 `PageTitleTabControl` 模板的 TabPanel margin（12→16）**，`StaffView` 与 `SettlementView` 一并左缘 24px；**不再引入 `PageTitleTabControlIndented`**。上对齐 16px 属真机微调项。

**刷新语义**（对齐 `staff_view.py:49,132-141`）：Python 在 `__init__`、`showEvent`、`tabChanged` 三处调 `refresh()`（全部重读）。C# 侧：`PageHost` 会缓存页实例，故在 `Loaded`（每次重新挂载）+ `TabControl.SelectionChanged` 时执行 `StaffViewModel.RefreshCommand`；写操作成功后也触发一次 RefreshCommand（单次重读）。

### 3.2 Tab「员工名单」

**工具栏按钮（顺序=从左到右，`staff_view.py:59-75`）**：

| 顺序 | 文案 | 样式 | 命令 | 无选中时 |
|:-:|---|---|---|---|
| 1 | 导入职工清单（模板） | `PrimaryButton` | `ImportCommand` | 始终可用 |
| 2 | 手动添加 | `NotionButton` | `AddCommand` | 始终可用 |
| 3 | 修改 | `NotionButton` | `EditCommand`（`CanExecute=HasSelection`） | **禁用** |
| 4 | 删除 | `NotionButton` | `DeleteCommand`（`CanExecute=HasSelection`） | **禁用** |
| 5 | 停用 / 启用 | `NotionButton` | `ToggleActiveCommand`（`CanExecute=HasSelection`） | **禁用** |
| — | （右侧）`*` 占满 | — | — | 对齐 `btns.addStretch()` |

> 与 Python 差异：Python 无选中时是「点了弹提示」，本规格改为**按钮禁用** + 双保险（`HasSelection` 加在 `CanExecute` 上）。双击行仍触发 `EditCommand`（`staff_view.py:81`）。

**DataGrid（5 列，`staff_view.py:77-84`）**：`controls:NotionDataGrid`，`StatePage="staff"`，`StateName="main"`，`SelectionMode=Single`（构造器默认），`FrozenColumnCount=1`。

| # | 表头 | 绑定 | 宽（默认） | 对齐 | 备注 |
|:-:|---|---|--:|---|---|
| 1 | 姓名 | `[Name]` | 150 | 左 | 冻结列（`frozen:true`，灰底） |
| 2 | 类型 | `[Type]` | 120 | 左 | — |
| 3 | 状态 | `[Status]` | 90 | 左 | **`停用` → `Foreground={TextFaint}`；`在职` → `{Text}`**（`staff_view.py:156-158`，用 `DataTrigger` 绑 `IsActive`） |
| 4 | 入职月份 | `[HireMonth]` | 120 | 左 | 空串显示空 |
| 5 | 备注 | `[Note]` | `*`（拉伸） | 左 | 悬停全文（`GridColumnFactory` 默认挂 ToolTip） |

排序：默认按 `is_active DESC, name`（`staff_view.py:148-149`），在 VM 侧排序后赋值，不依赖 DataGrid 内置排序（Python 是 `NoEditTriggers` + 无排序三角；DataGrid 需 `CanUserSortColumns=false` 或列 `CanUserSort=false`）。

**编辑/新增对话框字段**：

| 对话框 | 标题 | 字段 | 依据 |
|---|---|---|---|
| 手动添加 | `手动添加职工` | 姓名(`TextBox`,必填)、类型(`NotionCombo`,选项=全部员工类型)、入职月份(`TextBox`, placeholder `如 2025-04，留空=始终在名单`)、备注(`TextBox`) | `staff_view.py:322-339` |
| 编辑员工 | `编辑员工：{name}` | 类型(`NotionCombo`,预选当前类型)、入职月份、备注（**无姓名**，姓名是主键不可改） | `staff_view.py:374-388` |

**编辑时的「类型变更二次确认」**（`staff_view.py:392-400`）：若新旧 `staff_type` 不同，弹 `ConfirmDialog`，文案逐字：
> `将 {name} 的人员类型从「{old}」改为「{new}」？\n注意：类型决定业务收入口径（合伙=按开票，聘用/兼职=按收款，其余=0）；历史数据的身份不受影响。`
取消 → 不落库。确认 → 执行 UPDATE。

**对话框实现**：单个 `StaffEditDialog.xaml(+cs)`，`Mode=Add|Edit` 两态（Add 多一个「姓名」行）。返回 `StaffEditResult?`（null=取消）。

### 3.3 Tab「员工类型」

**说明行（caption，`staff_view.py:95-97`，逐字）**：
> 自定义员工类型。合伙 / 聘用 / 兼职 为内置结算类型：禁止删除与改名，说明可改；其余类型仅作身份标签，不参与业务收入计算。

用 `TextBlock Style={CaptionLabel} TextWrapping=Wrap`，置于工具栏**上方**。

**工具栏按钮（顺序，`staff_view.py:99-116`）**：

| 顺序 | 文案 | 命令 | 启用条件 |
|:-:|---|---|---|
| 1 | 新增类型 | `AddTypeCommand` | 始终可用 |
| 2 | 改名 | `RenameTypeCommand` | `HasSelection && !SelectedIsBuiltin` |
| 3 | 编辑说明 | `EditNoteCommand` | `HasSelection`（内置**可改说明**） |
| 4 | 删除类型 | `DeleteTypeCommand` | `HasSelection && !SelectedIsBuiltin` |
| 5 | 上移 | `MoveUpCommand` | `HasSelection && !IsFirstRow` |
| 6 | 下移 | `MoveDownCommand` | `HasSelection && !IsLastRow` |

> **启用/禁用规则（用户明确要求 + Q4 裁定）**：`改名` 与 `删除类型` 必须在**选中的类型是内置（`is_builtin=1`）时禁用**（不是点了再报错）。内置三类 = `合伙 / 聘用 / 兼职`（`staff_type.py:21`）。`上移/下移` 越界（首行/末行）与**未选中**时一律**禁用**（不沿用 Python 的静默返回）——与「内置直接禁用」同原则。

**DataGrid（4 列，`staff_view.py:118-128`）**：`StatePage="staff_type"`，`StateName="main"`，`SelectionMode=Single`。

| # | 表头 | 绑定 | 宽 | 对齐 | 特殊渲染 |
|:-:|---|---|--:|---|---|
| 1 | 类型 | `[Name]` | 120 | 左 | **内置类型 → 加粗**（`staff_view.py:171-174`，`DataTrigger` 绑 `IsBuiltin` → `FontWeight=Bold`） |
| 2 | 参与结算 | `[Computable]` | 80 | **居中** | `是`（`text-align:center`）/ `—`（不参与）。`Computable = is_computable(name)`（`staff_type.py:110-113`：名含 `合伙/兼职/聘用` 之一） |
| 3 | 说明 | `[Note]` | `*` | 左 | — |
| 4 | 人数 | `[StaffCount]` | 60 | **右** | `staff_count`（`staff_type.py:93` 子查询） |

> ⚠️ 「参与结算」列的值是「是 / —」，不是布尔勾选框（`staff_view.py:176-180`：`"是" if is_computable else "—"`）。**不要用 `DataGridCheckBoxColumn`**。

**类型 CRUD 对话框**：

| 操作 | 对话框 | 依据 |
|---|---|---|
| 新增类型 | 两步：①名称（≤20 字符）②说明（可空，含「合伙/聘用/兼职」则参与结算——提示语 `不含「合伙 / 聘用 / 兼职」则不参与结算计算`） | `staff_view.py:198-212`；`staff_type.py:116-134` |
| 改名 | `TextInputDialog`「新类型名称」，预填原名 | `staff_view.py:214-227` |
| 编辑说明 | `TextInputDialog`「「{name}」的说明：」预填现有 note | `staff_view.py:229-240` |
| 删除类型 | `ConfirmDialog`「删除员工类型「{name}」？」 | `staff_view.py:242-255` |

> **新增类型的错误分流**（`staff_type.py:120-126`）：名称空 / >20 字符 / 重名 → 后端抛 `StaffTypeError`，UI 用 `MessageDialog.Error("新增失败", msg)` 显示。所以 UI 侧**只做空校验**，长度/重名交给后端（避免规则双份漂移）。

### 3.4 员工删除的引用检查与「改用停用」提示（**必须原样传递**）

`staff_view.py:411-430` + `staff_type.py:240-257`：

1. 先 `ConfirmDialog`：`确定从花名册中删除「{name}」？\n（该操作不可恢复）`。
2. 确认后调 `delete_staff` → 若该员工在 `charge_detail / collection / expense_ledger / raw_salary` 有引用（`staff_reference_count`），抛 `StaffInUseError`，**UI 用 `MessageDialog.Error("无法删除", e.Message)` 原样显示**。错误文案（`staff_type.py:248-251`）已含「请改用「停用 / 启用」」的引导，**UI 不得改写或截断**。

> 停用/启用是 `UPDATE staff SET is_active = 1 - is_active`（`staff_view.py:440`），**无引用检查**，永远可执行。

### 3.5 员工名单「导入职工清单（模板）」（**lead 裁定：本期实现，不占位**）

Python 真源：`app/ui/staff_view.py:276-320`（UI 流程）+ `app/importer/staff_import.py`（解析）+ `app/engine/staff_type.py:72-83`（`ensure_types`）。

#### 3.5.1 解析器端口（`LawFirm.Exporter/StaffImportParser.cs`，用 NPOI）

| 项 | Python 行为（`staff_import.py`） | C# 端口规格 |
|---|---|---|
| 支持格式 | `OpenFileDialog` 过滤 `*.xls *.xlsx *.xlsm`（`staff_view.py:277-279`） | `Microsoft.Win32.OpenFileDialog{ Filter="Excel 文件 (*.xls;*.xlsx;*.xlsm)\|*.xls;*.xlsx;*.xlsm" }` |
| 读取 | `.xlsx`→openpyxl；`.xls`→xlrd | **NPOI**：`.xls`→`HSSFWorkbook`；`.xlsx`/`.xlsm`→`XSSFWorkbook`（按扩展名分派） |
| ⚠️ Python 缺口 | 错误信息声称支持 `.xlsm`（`:53`），但代码只分派 `.xlsx`/`.xls`，`.xlsm` 落入 else **报错** | **C# 修好它**：`.xlsm` 按 `.xlsx` 处理（NPOI 直接可开）。**这是一处有意修正，不是偏差。** |
| 取值 | `_cell_text`：None→""；整数浮点→int 字符串；其余 strip（`:16-21`） | 同规则（注意把 `1.0` 转成 `"1"`） |
| 表头定位 | `_find_header_row`：找**同时**含「姓名」和「类型」的行，返回下标；找不到→抛错（`:24-30,55-57`） | 同规则；找不到 → `StaffImportException("未找到表头（需包含'姓名'和'类型'列）")` |
| 列定位 | 姓名必含、类型必含、**备注可选**（`next(..., -1)`，`:60-62`） | 同 |
| 数据行 | 姓名为空的跳过；按列下标取值，越界给 ""（`:64-71`） | 同 |
| 空数据 | 无有效行 → 抛「文件中没有有效的职工数据」（`:73-74`） | 同 |
| 文件指纹 | `md5(文件字节).hexdigest()`（`:76`） | `MD5` over raw bytes（写 `import_batch.file_hash` 用） |
| 返回 | `([(name, staff_type, note)], file_hash)` | `StaffImportResult(List<StaffImportRow>, string FileHash)` |

#### 3.5.2 交互流程（按钮 → 选文件 → 解析 → **预览/结果** → 写）

```
点「导入职工清单（模板）」(PrimaryButton)
  → OpenFileDialog（见上）
  → StaffImportParser.Parse(path)                        // 纯读，不碰 DB
  → 解析成功 → 打开 StaffImportPreviewDialog（预览/修正）
        · 表格列：姓名 | 类型 | 备注        （对齐模板「姓名|类型|备注」）
        · 「类型」列：在下拉里选（选项 = 现有类型；未知类型默认高亮，可「按原值自动补入」）
        · 空姓名行 / 重复姓名行：**高亮**并允许「删除该行」或「跳过」（不阻断其余行）
        · 底部实时计数：待新增 X 人 / 待更新 Y 人（按 name 是否已存在于 staff 判定）
        · 按钮：确定导入 / 取消
  → 用户确定 → WriteGuard.Execute(dbPath, "导入职工清单", work: 单事务，见 3.5.3)   // ★ 带备份
  → 结果对话框：MessageDialog.Info("导入完成", "新增 {n_new} 人，更新 {n_dup} 人。")   // 文案逐字对齐 staff_view.py:314
```

#### 3.5.3 写点（逐条，**全部在同一个事务内；本次写=带备份**）

| # | 写动作 | SQL 语义 | Python 依据 |
|:-:|---|---|---|
| I1 | `import_batch` 插 **1 行** | `INSERT INTO import_batch(batch_type,period,file_name,file_hash,imported_at) VALUES('staff','0000',{文件basename},{md5},now)` | `staff_view.py:290-294`（`period` 固定 `"0000"`；`file_name` = 路径 basename 且把 `\`→`/` 后再取尾段） |
| I2 | 未知类型**自动补入**员工类型表 | 对文件中出现、但不在 `staff_type_def` 的类型，`INSERT OR IGNORE staff_type_def(name, is_builtin=0, note='')`（`ensure_types`） | `:295-296` + `staff_type.py:72-83` |
| I3 | `staff` 逐人 **upsert by name** | `SELECT id FROM staff WHERE name=?`：命中 → `UPDATE staff SET staff_type,note,is_active=1`（计 `n_dup`）；未命中 → `INSERT staff(name,staff_type,is_active=1,note,source='import',import_batch_id=batch)`（计 `n_new`） | `:297-312` |
| I4 | 每人的类型空值兜底 | 文件该行类型为空 → 用 `聘用`（`:298-300`） | `:298-300` |
| I5 | 提交 | 全部成功 → `COMMIT`；任一步异常 → `ROLLBACK` 并报错（`:313-317`） | `:313-317` |
| I6 | 写后重读 | 触发 `RefreshCommand`（R1，**单次重读**） | `:320` |

> `batch_id = cur.lastrowid` 用于 I3 的 `import_batch_id` 外键（`:294,309-311`）。

#### 3.5.4 解析失败**不得崩溃** —— 修正对话框 / 降级方案

| 情形 | 处置 |
|---|---|
| **首选（与项目既有约定一致）** | 解析期收集**问题行**（空姓名、类型缺失/异常、行结构异常），**不抛异常中断**，而是像 `app/ui/problem_dialog.py` / `app/engine/import_confidence.py` 的既有约定那样，**转成「问题行修正」交互对话框**让用户逐行修正或跳过，确认后再进 3.5.2 的写流程。员工清单行结构极简（3 列），此对话框比发票版轻得多。 |
| **降级方案（如成本过高）** | 解析遇**致命错误**（文件不存在 / 格式不支持 / 找不到表头 / 无有效数据）→ `MessageDialog.Error("导入失败", 原文)` 后 **中止，不写库**（等价 Python `staff_view.py:283-285`）；**非致命**问题行 → 在预览对话框里高亮 + 允许删除/跳过，不做独立修正对话框。 |
| **任何情况下** | ① **不回滚已存在的库**（写是单事务，失败即 rollback）；② 不弹未捕获异常；③ 崩溃路径**零备份**（因为没走到写闸门）。 |

> 若采降级方案，请在 spec 交付时**显式告知 lead**（本文件已列入 §8-Q5'）。

---

## 4. `ExpenseCatView` 规格（nav key = `expense_cat`）

### 4.1 页骨架

对齐 `expense_cat_view.py:290-327`：

```
ExpenseCatView : UserControl   ← 构造器里 DataContext = new ExpenseCatViewModel();
└─ Grid  Background={Canvas}  Margin="24,16,24,12"     ← D-Notion 基线
   ├─ Row0  controls:PageHeader  Title="费用类型"
   │           HelpText="按 5 类分组维护费用类型。卡片内拖动可调整顺序，拖到别的卡片即改归类；双击类型名可改名。顺序与归类决定结算表的费用列序。"   （:299-303）
   ├─ Row1  ScrollViewer  VerticalScrollBarVisibility=Auto  HorizontalScrollBarVisibility=Auto
   │           HorizontalContentAlignment=Stretch
   │        └─ ItemsControl  ItemsSource={Binding Cards}  VerticalAlignment=Top
   │              ItemsPanel = UniformGrid  Columns=3  Rows=2
   │              ItemTemplate → <views:CategoryCard .../>
   └─ Row2  Grid  说明 hint（左） + 「从费用台账同步」「刷新」按钮（右）   （:317-327）
```

### 4.2 卡片网格的**响应式 3 列**与最小宽（用户明确要求「卡片随窗口变宽、但不压缩到按钮裁字」）

| 项 | 规格 | 依据 |
|---|---|---|
| 面板 | `ItemsPanelTemplate = UniformGrid Columns=3` | 对齐 `expense_cat_view.py:353`（`for col in range(3): setColumnStretch(col,1)`） |
| 卡片最小宽 | **300**（`card.MinWidth = 300`） | `expense_cat_view.py:349-350`「300 是按钮行不裁字的最小宽度」 |
| 卡片最小高 | **240** | `expense_cat_view.py:351` |
| 卡片变宽 | `ScrollViewer.HorizontalContentAlignment="Stretch"` → 视口变宽时 `ItemsControl` 内容宽 = 视口宽 → `UniformGrid` 单元等比变宽（每单元 =（内容宽 − 2×12）/3） | 对应 Python 列 stretch=1 |
| **不压缩** | `ItemsControl.MinWidth = 3*300 + 2*12 = 924`。窗口窄于 924 时，`ScrollViewer` 出**横向滚动条**，卡片仍保持 300（绝不压到裁字） | 对应 Python `grid.setSizeConstraint(SetMinimumSize)`（`:313`）+ 外层 `QScrollArea` |
| 行高 | `UniformGrid Rows=2` → 两行等高 = 最高卡片高度；`ItemsControl VerticalAlignment=Top` 使网格贴顶、不吃满剩余高度 | 对应 Python 用 `setRowStretch(len(cats)//3+1, 1)` 加一个空行吸收高度（`:357`） |
| 间距 | 卡片间 12px（`UniformGrid` 无内建 spacing → 用 `ItemContainerStyle` 的 `Margin="0,0,12,12"`，右边与底部各留 12） | 对应 `self.grid.setSpacing(12)` |

> 5 张卡片固定为 `报酬发放 / 住房公积金 / 保险费 / 汽油费 / 其他`（`expense_cat.py:18` 的 `CATEGORIES`），**分类不可增删**，故恒为 3+2 布局。

### 4.3 卡片内部结构（`expense_cat_view.py:140-259`）

```
CategoryCard : UserControl        背景 = CardBorder（白底 + 1px Border + 圆角 10 + Padding 10,8）
└─ Grid  Rows = [Auto(头), Auto(说明), *(类型列表), Auto(按钮条)]
   ├─ Row0  头： Grid  Cols=[*, Auto, Auto]
   │        ├─ [0] StackPanel Horizontal： TextBlock 卡名（CardTitleText） + TextBlock 计数（CaptionLabel，「N 项」）
   │        └─ [2] Button「说明」 Style=CardButton  Command=EditNoteCommand      （:159-166）
   ├─ Row1  说明： TextBlock Style=CaptionLabel  TextWrapping=Wrap
   │              空值时显示占位「（点击「说明」填写本类的口径说明）」（:169-173）
   ├─ Row2  类型列表： controls:TypeListBox  ItemsSource={Binding Types}
   │              SelectionMode=Extended   双击 → 改名命令（:52,125-133）
   └─ Row3  按钮条： StackPanel Horizontal  Spacing=4
                   新增 / 上移 / 下移 / 移动▾ / 删除   全 Style=CardButton
                   （右侧 addStretch → 用 Spacer 或 HorizontalAlignment=Left）
```

**卡片计数文案**：`"{count} 项"`（`expense_cat_view.py:214`）；列表变化时（含拖拽 drop）更新 `Count`。

### 4.4 卡片按钮（`expense_cat_view.py:179-209`）

| 按钮 | 命令 | 工具提示 | 无选中时 |
|---|---|---|---|
| 新增 | `AddTypeCommand` | — | 始终可用 |
| 上移 | `MoveUpCommand` | `在本类内向上移一位` | 禁用（`HasSelection && !IsFirst`） |
| 下移 | `MoveDownCommand` | `在本类内向下移一位` | 禁用（`HasSelection && !IsLast`） |
| 移动 ▾ | `MoveToCommand(category)` | `移动到其它分类` | **按钮本身禁用**（`HasSelection`） |
| 删除 | `DeleteCommand` | — | 禁用（`HasSelection`） |

- **越界/无选中一律「禁用」而非「静默」（Q4 裁定）**：`上移/下移/删除/移动` 在**未选中任何类型**时全部**置灰**；已选中但位于类首（上移）/类尾（下移）时，对应按钮**置灰**。与 §1「内置类型直接禁用改名/删除而非先点再报错」保持同一原则；**不要**沿用 Python 的越界静默 no-op。
- **「移动」是下拉菜单**：菜单项 = 其余 4 个分类（`expense_cat_view.py:192-196`，`for c in CATEGORIES if c != name`）。
  WPF 落地：`Button` + `ContextMenu`（items 绑 `OtherCategories`），点击按钮时 `ctx.PlacementTarget=btn; ctx.Placement=Bottom; ctx.IsOpen=true`（用 `Click` 事件，不要依赖 `Button.ContextMenu` 的默认右键弹出）。
- **多选语义**（对齐 Python）：`上移/下移` 只作用**第一个选中项**（`expense_cat_view.py:245` 用 `sel[0]`）；`删除` 与 `移动` 作用于**全部选中项**（`:252-259`）。VM 命令据此区分。

### 4.5 页脚（`expense_cat_view.py:317-327`）

| 元素 | 规格 |
|---|---|
| 左：hint | `CaptionLabel`，文案 `共 {total} 个费用类型，{M} 个分类`（`total = Σ类内类型数`，`M` 恒为 5） |
| 右：从费用台账同步 | `NotionButton` → `SyncCommand`（**写库**，见 §6）→ 完成后 `MessageDialog.Info("已同步","已从费用台账补齐缺失的费用类型。")` |
| 右：刷新 | `NotionButton` → `RefreshCommand`（**纯读**，不 sync） |

### 4.6 说明对话框（`NoteDialog`，`expense_cat_view.py:262-283`）

- 标题 `说明 — {category}`，窗口 420×220。
- 顶部 `CaptionLabel`：`「{category}」这一类的口径说明，可自定义填写（留空即可）：`
- 多行 `TextBox`（`AcceptsReturn=True`、`TextWrapping=Wrap`、`VerticalScrollBarVisibility=Auto`），预填现有 note。
- 底部 `确定`（`PrimaryButton`）/ `取消`（`NotionButton`）。确定 → 写 `expense_category.note`（**写库**）。

### 4.7 费用类型的 CRUD 与删除前的引用提示（`expense_cat_view.py:383-438`）

| 操作 | UI 流程 | 写点 |
|---|---|---|
| 新增 | `TextInputDialog`「在「{category}」下新增费用类型」→ 空则忽略；`ExpenseCatError` → `MessageDialog.Error("无法新增", msg)` | `ec.add_type` + `log_change` |
| 改名 | 双击 / 卡片内 rename → `TextInputDialog`「新的费用类型名称」 | `ec.rename_type` + `log_change` |
| 删除 | `ConfirmDialog`，文案含**引用条数**：单选 `确定删除费用类型「{name}」？` +（若有引用）`该类型在费用台账中有 {n} 条记录。删除后这些记录仍保留，但结算表不会再单列该类型。`；多选 `确定删除选中的 {k} 个费用类型？`+ `这些类型在费用台账中共 {n} 条记录。`（`:414-428`） | 逐个 `ec.delete_type` + 逐个 `log_change` |
| 移动 | 菜单选目标分类 → 逐个 `ec.set_category` + `log_change` | 见 §5 |

---

## 5. **拖拽设计（本任务最难部分，用户要求与 Python 完全对齐）**

Python 真源：`expense_cat_view.py:32-133`（`TypeListWidget`）。要点复述：

- 类内重排 **和** 跨卡片搬运（后者改 `category`）。
- `ExtendedSelection`（可多选，多选可整体拖）。
- 落点下标 = 命中项 + `dropIndicatorPosition()==BelowItem` 时 +1；命不中 → 末尾（`:71-78`）。
- **落库只在 drop 时发生一次**（`apply_drop` 末尾 `self._view.persist()`，`:117`）。

### 5.1 机制总览（WPF）

**每个卡片 = 一个 `controls:TypeListBox : ListBox`**（新增控件），负责自身的拖与放；卡片 VM 暴露 `Types: ObservableCollection<TypeItem>`。

```
拖起：TypeListBox.PreviewMouseMove   （隧道，先于子元素——解决「拖拽源自 TextBlock」）
拖过：TypeListBox.DragOver           （e.Effects = Move，设 e.Handled=true，否则光标显示 No）
放下：TypeListBox.Drop               （算插入下标 → 改两个卡片的集合 → 通知 View 落库一次）
```

### 5.2 三个事件 + **最小拖拽阈值**（防「正常点击/选行」被劫持）

- `PreviewMouseLeftButtonDown`：记录 `_dragStart = e.GetPosition(this)` 与 `_pressed=true`。
- `PreviewMouseLeftButtonUp`：`_pressed=false`，清状态。
- `PreviewMouseMove`：
  ```
  if (!_pressed || e.LeftButton != MouseButtonState.Pressed) return;      // ★ 必须有按下键才能 DoDragDrop
  var p = e.GetPosition(this);
  if (Math.Abs(p.X-_dragStart.X) < SystemParameters.MinimumHorizontalDragDistance &&
      Math.Abs(p.Y-_dragStart.Y) < SystemParameters.MinimumVerticalDragDistance) return;   // ★ 阈值
  _pressed = false;                                                       // 防止重复触发
  var payload = SelectedItems.Cast<TypeItem>().ToList();
  if (payload.Count == 0) return;
  DragDrop.DoDragDrop(this, new TypeDragData(this, payload), DragDropEffects.Move);
  ```
  - **阈值用 `SystemParameters.MinimumHorizontalDragDistance` / `MinimumVerticalDragDistance`**（系统标准，≈4px），不要写死数字。
  - **`DoDragDrop` 调用前必须处于「物理按下」状态**，`PreviewMouseMove` 的 `e.LeftButton` 必须为 `Pressed`；否则 OLE 拖拽立即返回（经典「拖不起来」根因）。
  - `PreviewMouseMove` 在阈值未达时**不要**设 `e.Handled=true`，否则会吃掉 ListBox 内部的滚动/焦点逻辑。

- `DragOver`（目标卡片）：`if (e.Data.GetDataPresent(typeof(TypeDragData))) { e.Effects = DragDropEffects.Move; e.Handled = true; } else e.Effects = DragDropEffects.None;`
  - **必须**在 `DragOver` 设 `e.Handled=true` 并把 `e.Effects=Move`，否则默认 `None`、光标显示禁止、`Drop` 不触发。
- `Drop`：见 5.3/5.4。

### 5.3 落点插入下标的计算

```
int ComputeInsertIndex(DragEventArgs e):
    var pos = e.GetPosition(this);                        // 相对本 TypeListBox
    var hit = InputHitTest(pos) as DependencyObject;      // 命中元素（可能是 ListBoxItem 或其内 TextBlock）
    var item = ItemsControl.ContainerFromElement(this, hit) as ListBoxItem;
    if (item == null) return Items.Count;                 // ★ 落在空白区 / 空列表 → 追加到末尾
    var idx = ItemContainerGenerator.IndexFromContainer(item);
    var rel = e.GetPosition(item).Y / item.ActualHeight;  // 命中项内的相对纵向位置
    return (rel > 0.5) ? idx + 1 : idx;                   // 下半 → 插到其后；上半 → 插到其前
```

- 命中项**上半 → 插到该项之前**，**下半 → 之后**（等价 Python 的 `AboveItem/BelowItem`）。
- **落在列表空白处 / 空列表**（`ContainerFromElement` 返回 null）→ 一律**追加末尾**，与 Python `indexAt` 无效时返回 `count()`（`:73-74`）同效。
- `Items` 为空时：`Items.Count==0` → 返回 0（插到唯一位置）。

### 5.4 把移动应用到绑定集合 + **落库恰好一次** + 单次重读

`Drop` 内（在 VM 层完成，View 只做「收集 + 落库 + 重读」）：

```
void OnDrop(object s, DragEventArgs e):
    if (!e.Data.GetDataPresent(typeof(TypeDragData))) { e.Handled = true; return; }
    var drag = (TypeDragData)e.Data.GetData(typeof(TypeDragData));
    int insert = ComputeInsertIndex(e);
    e.Handled = true;
    _vm.ApplyDrop(drag.SourceCardVm, this.CardVm, drag.Items, insert);   // ① 改内存集合
    // ② 落库 + 单次重读，均由 VM 内部做（见下），此处不再触发任何逐项写
```

**VM.ApplyDrop 的算法（移植 `apply_drop`，`:92-117`）**：

- **同一卡片（类内重排）**：`removed_before = 原列表中 drop 下标之前被移走的个数`；`insert_at = clamp(drop_row − removed_before, 0, 剩余长度)`；按序插回。
- **跨卡片**：先从源卡集合移除这些项，再 `insert_at = clamp(drop_row, 0, 目标剩余长度)` 插入。
- **多选保持相对顺序**（Python `:105-106,112-113` 用 `for k,n` 顺序插）。

**落库与重读（关键：一次 drop = 一次写 = 一次读）**：

```
// ① 先改内存（ObservableCollection），界面即时反映，无闪烁
// ② 再落库：一次 WriteGuard.Execute，写整份 {category:[types]} 布局（等价 ec.save_layout）
WriteGuard.Execute(dbPath, "费用类型：拖拽排序/改归类",
    work: db => { ExpenseCatLayout.Save(db, currentLayout()); });   // save_layout 一次性写回 category + 全局 sort_order
// ③ 再单次重读：一次 ReadOnly 打开，list_categories + types_by_category，回填各卡片
//    （对齐 Python refresh 的重读；额外刷新「N 项」计数与脏分类归一）
```

- **必须整份落库**（`save_layout` 语义，`expense_cat.py:343-350`）：全局 `sort_order` 是「分类顺序 + 类内顺序」拼接，逐项改会写坏全局序。
- **禁止在拖过（DragOver）阶段落库**，也**禁止对每个被拖项各自落库一次**——只允许 drop 后一次。
- 重读用 **新的只读连接**（不能复用写连接的读结果，须真读）；重读后各卡片集合整体替换。

### 5.5 必须设计规避的 WPF 陷阱（逐条）

| # | 陷阱 | 规避 |
|:-:|---|---|
| P1 | **ListBox 自带拖拽 vs 自定义处理器** | WPF `ListBox` **不会**自动发起拖拽顺序调整（无内建 reorder），所以可安全在其上挂自定义处理器；**但**不要把 `AllowDrop` 与 `DragDrop.DoDragDrop` 交给 `ListBoxItem` 的默认行为——统一在 `TypeListBox` 上一次性处理。`AllowDrop=True`、`SelectionMode=Extended`。 |
| P2 | **`DoDragDrop` 需要按下状态** | `PreviewMouseMove` 里判 `e.LeftButton==Pressed` 且过阈值才调；否则拖不动。 |
| P3 | **`e.Handled`** | `DragOver` 必须设 `Handled=true`（否则 `Effects` 被重置为 `None`，`Drop` 不来）；`Drop` 设 `Handled=true`（阻止冒泡到外层 ScrollViewer 造成误滚动/误放）；`PreviewMouseMove` 仅在真正发起拖拽后设 `Handled=true`。 |
| P4 | **拖拽源自子元素（TextBlock）** | 卡片项模板里是 `TextBlock`；用 **`PreviewMouseMove`（隧道）** 挂在 `TypeListBox` 上，可在子元素之前捕获，无需给每个 `TextBlock` 单独挂事件。命中测试用 `ItemsControl.ContainerFromElement` 从命中的 `TextBlock` 反查 `ListBoxItem`。 |
| P5 | **`SelectedItems` 在 `PreviewMouseMove` 时可能是旧值** | 阈值判断放在 `PreviewMouseMove`（此时若用户先点选，选中已更新）；拖拽负载在 `DoDragDrop` 前一刻从 `SelectedItems` 快照。**不要在 `PreviewMouseLeftButtonDown` 抓选中**。 |
| P6 | **拖拽过程中源集合被改** | `TypeDragData` 只存**名字列表快照**（`List<TypeItem>` 的 `Name`），不存 `ListBoxItem` 引用（拖拽后容器会因虚拟化被回收）。 |
| P7 | **主窗口 `WindowChrome` 误拖窗** | 见 5.6。 |
| P8 | **虚拟化 + 容器查找** | 卡片类型列表项数少（个位到十几），**关闭该 `ListBox` 的虚拟化**（`VirtualizingPanel.IsVirtualizing=False`）可避免 `ContainerFromElement` 在屏幕外返回 null；若保留虚拟化，则「找不回容器」时按下标 0/末尾兜底。 |

### 5.6 **`WindowChrome` 拖窗问题（用户点名要核实）**

- 主窗 `Shell/MainShellWindow.xaml` 用 `WindowChrome CaptionHeight="36"`。**窗口拖动区域 = 窗口顶部 36px 的标题栏行**（`Grid.Row=0` 的 `TitleBar`）。页面（`PageHost`）位于 `Grid.Row=1`，**y ≥ 36px**，落在 caption 区域之外。
- **结论**：在页面内发起 `DragDrop.DoDragDrop` **不会拖动窗口**。原因：`WindowChrome` 的拖窗是**非客户区命中测试（`WM_NCHITTEST`）**驱动的，只在 caption 区域内生效；页面属于客户区，且 OLE 拖拽期间鼠标捕获归拖拽源，窗口不会收到用于移动的 NCHITTEST。
- **必须遵守的两条禁忌**（否则会破坏上述结论）：
  1. **不要**在页面/卡片上设置 `shellChrome:WindowChrome.IsHitTestVisibleInChrome="True"`（会让该元素被当作 caption，从而拖窗）。
  2. **不要**在 `TypeListBox`/卡片的 `MouseLeftButtonDown` 里调用 `Window.DragMove()` 或自绘拖窗（本工程标题栏用 `CaptionHeight` 机制，页面内无 DragMove）。
- **若真机仍出现拖窗**（怀疑 caption 命中区被放大或页面被放进 caption 区）：先确认页面容器不在 `Grid.Row=0`；次选**缓解手段** = 在拖拽起始处对页面根元素设置 `shellChrome:WindowChrome.IsHitTestVisibleInChrome="False"`（显式声明非 caption），并在 `TypeListBox` 的 `PreviewMouseLeftButtonDown` 中 `e.Handled` 仅限阈值达到后。
- **列为待真机确认**（§8-Q6）：沙箱无 WPF 运行时，此结论为**静态推断**，需工程师在本机做「在卡片列表里拖一下，看窗口是否跟着动」的最小验证。

### 5.7 **多选拖拽裁定（用户要求明确决定并说明）**

- **裁定：支持多选整体拖拽**（与 Python `ExtendedSelection` 对齐）。理由：① 用户明确要求「与 Python 版本完全对齐」；② 实现成本低——拖拽负载本就是 `SelectedItems` 快照，只要插入算法按序插入即可（§5.4）；③ Python 的多选删除/多选移动已有相同语义，保持一致。
- **多选 drop 的行为**：
  - 多个类型**保持它们在源卡片中的相对顺序**，连续插入到落点下标处。
  - **跨卡片**多选 drop：这些类型**全部改归到目标分类**（等价逐项 `set_category`）。
  - **类内**多选 drop：整体重排到落点，`removed_before` 校正按 Python `:102-104` 计算。
- **边界**：若用户多选后把其中部分拖到**自身卡片**且落点落在选区内部 → 相当于原地/邻近重排，允许（不做特判）；与 Python 行为一致。
- **若后续要降级为单拖**：则多选 drop 时只移动 `SelectedItems[0]`（需 lead 明确；当前**不降级**）。

### 5.8 拖拽时序图

```mermaid
sequenceDiagram
    participant U as 用户
    participant Src as TypeListBox(源卡片)
    participant VM as CategoryCardViewModel
    participant Tgt as TypeListBox(目标卡片)
    participant WG as WriteGuard.Execute
    participant DB as lawfirm.db

    U->>Src: 按下并拖过阈值
    Src->>Src: PreviewMouseMove: 判按下+阈值
    Src->>Src: DragDrop.DoDragDrop(TypeDragData)
    U->>Tgt: 移动鼠标到目标卡片
    Tgt->>Tgt: DragOver → e.Effects=Move, e.Handled=true
    U->>Tgt: 松开
    Tgt->>Tgt: Drop → ComputeInsertIndex()
    Tgt->>VM: ApplyDrop(srcCard, thisCard, items, insertIdx)  (改内存集合, 一次)
    VM->>WG: Execute(dbPath, "费用类型拖拽", work: save_layout(currentLayout))
    WG->>DB: BEGIN; UPDATE expense_cat SET category/sort_order ...; COMMIT
    VM->>DB: (重读一次) SELECT expense_category / expense_cat
    VM-->>Tgt: 回填各卡片集合 + 计数
```

---

## 6. **写库点 / 重读点总清单**（UI 必须显式调用的位置）

> 约定：所有写点一律经 `WriteGuard.Execute(dbPath, reason, work)`（并行工程师实现）。**每次写成功后，UI 触发一次重读刷新。**
>
> **备份列含义**：✅ = 写前触发一份 SQLite 在线备份；❌ = 不备份（仅走写闸门串行）。分类依据见 §0 与 §1.4.1。
>
> **写失败必回读（Q7 裁定）**：任何写操作失败后（**尤其** `DbBusyException`），UI 必须**重新读取该页数据 + 展示错误**，使界面与库恢复一致——失败可能发生在悲观更新之后（或部分乐观更新之后），不回读会造成「界面显示与库不一致」这一本项目最忌讳的**静默不一致**。实现：`WriteGuard.Execute` 抛异常的分支统一 `catch` → `MessageDialog.Error` + `RefreshCommand`（按 DB 覆盖内存）。
>
> **审计口径（Q8 裁定，分表）**：
> - `expense_cat` 的 **新增 / 改名 / 删除 / 改归类** → **写 `change_log`**（`friendly_table = 费用类型`，`reason = 费用类型维护`），对齐 `expense_cat_view.py:394,408,434,454`。
> - `staff` 的 **增 / 改 / 删 / 停用 / 导入** → **不写 `change_log`**（Python `staff_view.py` 全都不调；「修改记录」页的 `TABLE_OPTIONS` 里没有 `staff`，写了也过滤不出来，只会制造 Python 侧没有的审计噪音、让三机 `change_log` 对不上）。
> - **若日后需要 staff 审计，先扩「修改记录」页的表选项**（`TABLE_OPTIONS`）——本规格不扩。

### 6.1 写点（共 20 处）

| # | 页 | 触发 | Python 真源 | 写内容 | change_log | 备份 |
|:-:|---|---|---|---|:-:|:-:|
| **W0** | 全局 | `BaseDataBootstrap.EnsureAll()`（Shell 首加载 / 首次进页，**幂等**） | `staff_type.py:54-69` + `expense_cat.py:57-67` | 先 `SELECT` 检查；缺才 `INSERT OR IGNORE` 固定集合（内置三类 + 挂靠/其他 + 5 分类） | ❌ | ❌ **不备份** |
| W1 | Staff 名单 | 导入职工清单（模板） | `staff_view.py:287-320` + `staff_import.py` | `import_batch` INSERT×1 + `staff_type_def` ensure + `staff` upsert（**一个事务**，见 §3.5.3） | ❌ | ✅ |
| W2 | Staff 名单 | 手动添加 | `:346-357` | `INSERT OR IGNORE staff` | ❌ | ✅ |
| W3 | Staff 名单 | 编辑员工 | `:401-408` | `UPDATE staff SET staff_type,hire_month,note` | ❌ | ✅ |
| W4 | Staff 名单 | 删除员工 | `:422-423` | `DELETE staff`（先引用检查） | ❌ | ✅ |
| W5 | Staff 名单 | 停用/启用 | `:438-442` | `UPDATE staff SET is_active=1-is_active` | ❌ | ✅ |
| W6 | Staff 类型 | 新增类型 | `staff_type.py:116-134` | `INSERT staff_type_def` | ❌ | ✅ |
| W7 | Staff 类型 | 改名 | `:137-156` | `UPDATE staff_type_def` + `UPDATE staff` | ❌ | ✅ |
| W8 | Staff 类型 | 编辑说明 | `:159-165` | `UPDATE staff_type_def.note` | ❌ | ✅ |
| W9 | Staff 类型 | 删除类型 | `:168-183` | `DELETE staff_type_def` | ❌ | ✅ |
| W10 | Staff 类型 | 上移/下移 | `:186-203` | `UPDATE staff_type_def.sort_order` ×N | ❌ | ✅ |
| W11 | Expense | 从费用台账同步 | `expense_cat.py:282-291` | `ensure_types`（INSERT 缺失类型） | ❌ | ✅ **显式按钮才写** |
| W12 | Expense | 新增费用类型 | `:207-224` + `:394` | `INSERT expense_cat` + `INSERT change_log` | ✅ | ✅ |
| W13 | Expense | 改名 | `:227-245` + `:408` | `UPDATE expense_cat` + `UPDATE expense_ledger` + `change_log` | ✅ | ✅ |
| W14 | Expense | 删除费用类型 | `:270-279` + `:434`（每项） | `DELETE expense_cat` + `change_log` | ✅ | ✅ |
| W15 | Expense | 类内上移/下移 | `:298-318`（无 log） | `_write_layout`（UPDATE sort_order） | ❌ | ✅ |
| W16 | Expense | 移动到其它分类 | `:248-256` + `:454`（每项） | `UPDATE expense_cat SET category` + `change_log` | ✅ | ✅ |
| W17 | Expense | **拖拽 drop** | `:343-350`（`save_layout`，无 log） | `UPDATE expense_cat SET category,sort_order`（**整份一次**） | ❌ | ✅ §5.4 |
| W18 | Expense | 分类说明编辑 | `:117-124` / `expense_cat_view.py:226-231`（**无 log_change**） | `UPDATE expense_category.note` | ❌ ⚠️ | ✅ |
| W19 | Expense | （Python 的）`refresh→sync` | `expense_cat_view.py:337` | ❌ **本规格不复制**（refresh 纯读） | — | — |

> **汇总**：W0 = 无审计、**不备份**（幂等引导）；W1–W10（staff 系）= 无审计、带备份；W11–W18（expense 系）= 按上表审计、带备份。**不存在「refresh 触发的写」**。
>
> **⚠️ W18 说明（与 lead 列表的一处出入，请确认）**：lead 的 Q8 列了「改说明」要写 `change_log`；但 Python `expense_cat_view.py:226-231` 的说明编辑**只调 `ec.set_category_note`、不调 `log_change`**。为守住「**三机 `change_log` 对齐**」这一 Q8 的原始理由，本表按 Python 口径把 W18 定为 ❌；若 lead 明确要求纳入审计，回一句我改成 ✅（并同步说明「这是对 Python 的有意增强」）。

### 6.2 重读点（写后 / 进入页 / 切 tab）

| # | 触发 | 读取 |
|---|---|---|
| R1 | `StaffView` `Loaded` / 切 tab / 任意 W1–W10 写后 | `staff` 全表（按 `is_active DESC,name`）+ `list_types()` |
| R2 | `ExpenseCatView` `Loaded` / 任意 W11–W18 写后 / 「刷新」按钮 | `list_categories()` + `types_by_category()` + 计数 |
| R3 | Expense「从费用台账同步」后 | 同 R2（且 `MessageDialog.Info` 告知） |

> **写纪律**：写用 `WriteGuard.Execute`（内部应为可写连接 + 事务）；读沿用 `DbConnection.OpenReadOnly`（`query_only`）。**读写连接不跨线程共享**（沿用 T5 §6.2 的 SQLite 线程纪律）。

---

## 7. 新增 / 修改文件清单

### 7.1 新增（Views / ViewModels / Controls / Dialogs / Themes）

| 文件 | 类型 | 一行用途 |
|---|---|---|
| `csharp/LawFirm.UI/Views/Staff/StaffView.xaml`(+`.cs`) | 新 | nav=`staff` 宿主；tab 头 + 右上角 `?`；构造器设 `DataContext=new StaffViewModel()` |
| `csharp/LawFirm.UI/Views/Staff/StaffListTab.xaml`(+`.cs`) | 新 | 员工名单 tab：工具栏 + 5 列 DataGrid |
| `csharp/LawFirm.UI/Views/Staff/StaffTypeTab.xaml`(+`.cs`) | 新 | 员工类型 tab：说明行 + 工具栏 + 4 列 DataGrid |
| `csharp/LawFirm.UI/Views/ExpenseCat/ExpenseCatView.xaml`(+`.cs`) | 新 | nav=`expense_cat` 宿主：PageHeader + 卡片 ScrollViewer + 页脚 |
| `csharp/LawFirm.UI/Views/ExpenseCat/CategoryCard.xaml`(+`.cs`) | 新 | 单张分类卡片（头/说明/类型列表/按钮条） |
| `csharp/LawFirm.UI/Controls/TypeListBox.cs` | 新 | 可拖拽 ListBox（`AppPreviewMouseMove/DragOver/Drop` + `ComputeInsertIndex`） |
| `csharp/LawFirm.UI/Controls/TypeDragData.cs` | 新 | 拖拽负载（源卡片引用 + 类型名快照） |
| `csharp/LawFirm.UI/ViewModels/StaffViewModel.cs` | 新 | 页总 VM（List + Types 子 VM；SelectedTabIndex；Refresh） |
| `csharp/LawFirm.UI/ViewModels/StaffListViewModel.cs` | 新 | 员工名单 VM（`StaffRow` 集合 + 5 命令 + 对话框调用） |
| `csharp/LawFirm.UI/ViewModels/StaffTypeViewModel.cs` | 新 | 员工类型 VM（`StaffTypeRow` 集合 + 6 命令 + 内置/边界启用逻辑） |
| `csharp/LawFirm.UI/ViewModels/ExpenseCatViewModel.cs` | 新 | 费用类型页 VM（`Cards` 集合 + hint + Sync/Refresh） |
| `csharp/LawFirm.UI/ViewModels/CategoryCardViewModel.cs` | 新 | 单卡片 VM（`Types` + Count + 按钮命令 + `ApplyDrop`） |
| `csharp/LawFirm.UI/ViewModels/TypeItem.cs` | 新 | 类型项模型（`Name`；`INotifyPropertyChanged` 视需要） |
| `csharp/LawFirm.UI/Dialogs/StaffEditDialog.xaml`(+`.cs`) | 新 | 员工新增/编辑（Mode=Add/Edit） |
| `csharp/LawFirm.UI/Dialogs/TextInputDialog.xaml`(+`.cs`) | 新 | 单行文本输入（类型名 / 说明） |
| `csharp/LawFirm.UI/Dialogs/NoteDialog.xaml`(+`.cs`) | 新 | 分类多行说明编辑 |
| `csharp/LawFirm.UI/Dialogs/ConfirmDialog.xaml`(+`.cs`) | 新 | Yes/No 确认（`MessageDialog` 只有「确定」，需补确认件） |
| `csharp/LawFirm.UI/Dialogs/StaffImportPreviewDialog.xaml`(+`.cs`) | 新 | 员工清单导入的**预览/修正**对话框（§3.5.2 / §3.5.4） |
| `csharp/LawFirm.Exporter/StaffImportParser.cs` | 新 | 用 NPOI 端口 `staff_import.py`（`.xls/.xlsx/.xlsm` 解析 + md5） |
| `csharp/LawFirm.UI/Themes/Cards.xaml` | 新 | `CardBorder` / `CardTitleText` / `CardButton`（**不再需要 `PageTitleTabControlIndented`**，见 §3.1/§7.2） |
| `docs/t7-staff-expense-ui-spec.md` | 新 | 本文件 |

> 由**并行工程师（write-channel-eng）**新增（本规格只声明依赖与语义）：`LawFirm.Data/WriteGuard.cs`、`LawFirm.Data/StaffRepo.cs`、`LawFirm.Data/StaffTypeRepo.cs`、`LawFirm.Data/ExpenseCatRepo.cs`（含 `save_layout` 语义）、**`LawFirm.Data/BaseDataBootstrap.cs`**（§1.4.1：先 SELECT 检查、缺才幂等补，**不备份**）。

### 7.2 必须**修改**的既有文件

| 文件 | 改动 |
|---|---|
| `Shell/NavigationService.cs` | 构造函数追加两行 `RegisterPage("staff"...)` / `RegisterPage("expense_cat"...)`（§1.4）。 |
| `Themes/NotionControls.xaml` | **`PageTitleTabControl` 模板的 `TabPanel Margin` 由 `12,0,12,0` 改为 `16,0,12,0`**（§3.1：使 tab 头左缘 = 24px，`StaffView` 与 `SettlementView` 同时对；仅改内边距，不动字体/下划线）。 |
| `App/App.xaml` | `MergedDictionaries` 追加 `Themes/Cards.xaml`（放在 `NotionControls.xaml` 之后；`Cards.xaml` 引用前两者的 key）。 |
| `LawFirm.Exporter.csproj` / `LawFirm.UI.csproj` | 员工导入**需 NPOI**（写 `StaffImportParser.cs`）。NPOI 2.7.x 已在技术栈锁定，只是新增引用；`Microsoft.Win32.OpenFileDialog` 属 WPF，无需 NuGet。 |

---

## 8. 开放问题与风险（**需 lead 裁定**，不要默默决定）

| ID | 事项 | 状态 / 裁定 |
|---|---|---|
| **Q1** | `ExpenseCatView` 边距：Python `(28,24,28,20)` vs 基线 `(24,16,12)` | ✅ **已裁定：统一 24/16/12**（不沿用 Python 的 28/24）。已落 §0/§4.1。 |
| **Q2** | Python `refresh()` 每次调都 `sync_from_ledger()`（写库），连 `showEvent` 都写 | ✅ **已裁定：严格纯读**。`refresh()`/`showEvent` = 零写零备份；`ensure_*` 移出 refresh，改由 `BaseDataBootstrap.EnsureAll()`（幂等、**不备份**）承担；显式同步/CRUD 才写（**带备份**）。理由见 §0（防 backup 刷爆）。 |
| **Q3** | tab 头像素（24px 左 / 16px 上）需真机微调 | ✅ **已裁定：两页 tab 左缘统一 24，并顺带把 `SettlementView` 也对齐 24**（改共享 `PageTitleTabControl` 的 TabPanel margin，§3.1/§7.2）；已列入**真机视觉点检清单**，需确认不影响 `docs/t5-visual-checklist.md`。上对齐 16px 仍需真机微调。 |
| **Q4** | `上移/下移` 越界 / 无选中：静默返回 vs 禁用按钮 | ✅ **已裁定：一律「禁用」**（类首禁上移、类尾禁下移；未选中则上移/下移/删除/移动全禁用）。与 §1「内置类型直接禁用」同原则。已落 §3.3 / §4.4。 |
| **Q5** | 员工「导入清单」解析器 | ✅ **已裁定：本期实现，不占位**。端口方案见 §3.5（NPOI，`.xls/.xlsx/.xlsm`）。 |
| **Q5'** | 导入解析失败的处理 | ✅ **已裁定：先按完整方案接既有 `ProblemDialog` 约定，不得自行降级**。若实现时发现 C# 侧该约定不存在或复用成本过高，**回报 lead 后再决定**（此为项目硬规则：「解析失败不崩溃、转交互式修正对话框」）。已落 §3.5.4。 |
| **Q6** | **`WindowChrome` 下页面内拖拽不会拖窗** | ⏳ **静态推断，需真机最小验证**（§5.6）。T7 首个实现日：卡片列表里拖一下 → 看主窗是否移动；若会拖窗按 §5.6 缓解手段处理。 |
| **Q7** | 写失败时界面与库不一致 | ✅ **已裁定：写失败必回读**（尤其 `DbBusyException`）→ `MessageDialog.Error` + 按 DB 重读覆盖内存。已落 §6 引言。 |
| **Q8** | 费用类型增删改 / 同步是否写 `change_log` | ✅ **已裁定：分表**——`expense_cat` 的增/改名/删除/改归类写 `change_log`；`staff` 全系**不写**（Python 无、`TABLE_OPTIONS` 无 `staff`）。⚠️ **W18「改说明」我按 Python 口径定为❌（有出入待你确认）**，见 §6.1 脚注。 |
| **Q9** | 多选拖拽 | ✅ **已裁定：支持**（§5.7，与 Python `ExtendedSelection` 对齐）。 |

---

## 9. 验收清单（供 QA / 用户）

**StaffView**
- [ ] 侧栏「数据维护 → 员工管理」进入真实页（非占位）。
- [ ] tab 头「员工名单 / 员工类型」为 20px/700，选中下方 2px `#2eaadc`；左缘对齐 24px、顶距 16px；右上角有 `?`。
- [ ] 员工名单 5 列（姓名/类型/状态/入职月份/备注）；`停用` 行状态列灰字；单行选中；双击进编辑。
- [ ] 无选中时「修改/删除/停用启用」为禁用态。
- [ ] 改类型时弹二次确认（文案逐字）。
- [ ] 删有业务引用的员工 → 报错含「请改用「停用 / 启用」」，且**未删除**。
- [ ] 员工类型 4 列（类型/参与结算/说明/人数）：内置三类型名**加粗**；参与结算列 `是`/`—` **居中**；人数**右对齐**。
- [ ] 选中内置类型时「改名」「删除类型」**置灰**；选中自定义类型时可用。
- [ ] 删除在用类型 → 报错「还有 N 名员工属于该类型…」，且未删除。
- [ ] **导入清单**：`.xls/.xlsx/.xlsm` 均可解析；表头需含「姓名+类型」；预览对话框可改类型/删行；确定后提示「新增 N 人，更新 M 人。」；解析失败**不崩溃**（错误提示或修正对话框）。

**ExpenseCatView**
- [ ] 侧栏「费用类型」进入真实页；页头标题 + `?`。
- [ ] 5 张卡片，3 列布局；窗口变宽卡片等比变宽；窗口 < 924px 出横向滚动、卡片不裁字。
- [ ] 每卡片：卡名 + `N 项`、说明行、类型列表、`新增/上移/下移/移动/删除` + 头部`说明`。
- [ ] 类内拖拽可重排；跨卡片拖拽改归类；**松手后仅落库一次**（抓包/日志确认一次 UPDATE 批）。
- [ ] 拖动**不会**带动主窗口移动（§5.6）。
- [ ] 多选拖拽：多个类型整体搬运、相对顺序保持。
- [ ] 双击类型名可改名；`移动` 下拉仅列其余 4 分类。
- [ ] 删除时提示引用条数；页脚 hint「共 N 个费用类型，M 个分类」（M=5）。
- [ ] **打开/刷新页面不写库**（Q2 裁定）——日志中 refresh 无任何 UPDATE/INSERT。

**跨页**
- [ ] 两页视觉与既有「各类报表」页一致（同一 Canvas 底、同一边距、同一按钮/lang 风格）。
- [ ] 写后单次重读，界面与 DB 一致；写失败时界面回读恢复（Q7）。
- [ ] **tab 头左缘统一 24px**：`StaffView` 与 `SettlementView` 首 tab 文字左缘在同一竖线上；**纳入真机视觉点检**，确认未破坏 `docs/t5-visual-checklist.md`。
- [ ] **`refresh()` / `showEvent` 零写零备份**：反复切 tab / 反复进页，`data/backups` **不增长**，日志中无任何 `UPDATE/INSERT`（Q2）。
- [ ] **`BaseDataBootstrap.EnsureAll()`**：真实库下只跑一条 `SELECT COUNT`、**不产生备份**；空库/缺项时能一次性补齐内置三类 + 挂靠/其他 + 5 分类。
- [ ] **备份分类正确**：用户 CRUD / 同步按钮**每次写前有备份**；引导补齐**无备份**。
- [ ] **按钮禁用（Q4）**：卡片未选中时 `上移/下移/删除/移动` 全置灰；选中的类型在类首/类尾时对应 `上移`/`下移` 置灰（不弹提示、不静默 no-op）。
- [ ] **change_log 分表（Q8）**：费用类型的新增/改名/删除/改归类**在 `change_log` 有记录**（`friendly_table=费用类型`）；员工/员工类型的增改删停用导入**不产生 `change_log` 行**。
- [ ] **写失败回读（Q7）**：模拟写失败（如锁库）→ 弹错后界面按库重读，**不残留与库不一致的显示**。
