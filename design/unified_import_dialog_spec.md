# 发票台账导入：问题修正 + 预览确认 合一（方案 2）

## 1. 背景与痛点

旧流程是「两个弹窗、一前一后」：

| 阶段 | 对话框 | 能做什么 | 缺什么 |
|---|---|---|---|
| 1 | `ProblemDialog` 问题行修正 | 逐条填开票日期/总额/经办人分摊/收款 | 看不到源文件备注、经办人原文、收款认定，只能凭空填 |
| 2 | `PreviewDialog` 写前预览确认 | 看全量行、改各经办人已收、看原始台账行 | 问题行已被消化掉，发现填错也回不去改 |

两个界面各自缺对方的信息，用户被迫在「有信息但不能改」和「能改但没信息」之间来回。

## 2. 方案选型（五选一，最终选 2）

| # | 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| 1 | 两个弹窗互跳（预览里点「修正」弹原框） | 改动最小 | 仍是上下文切换，弹窗套弹窗 | ✗ |
| 2 | **统一表格 + 行内修正** | 一张表看全部状态；就地改、即时重算；可反复改 | 需要把修正表单抽成组件、统一行模型 | ✅ 选定 |
| 3 | 向导式分步（修正 → 预览 → 确认） | 每步心智负担低 | 步骤变多，与痛点①（看不到信息）无关 | ✗ |
| 4 | 只保留预览框，问题行做成表格内可编辑行 | 概念最少 | 问题行形态差异大（发票/预收款/金额不可解析），表格内编辑难承载校验 | ✗ |
| 5 | 抽屉式（左表 + 右侧滑出面板） | 与 2 接近，动效更好 | 多一层显隐状态，出错面更大 | ✗ |

## 3. 设计

### 3.1 一张表承载全部行

11 列：`状态 / 类型 / 来源 / 发票号 / 购方 / 金额 / 经办人分摊 / 各经办人已收(双击编辑) / 收款认定 / 源文件备注 / 原因或疑问`

行状态四类，颜色与排序：

| 状态 | 含义 | 颜色 |
|---|---|---|
| 待修正 | 解析失败的问题行 | 红 `#C0392B`，整行浅红底 |
| 待确认 | 低置信（系统判定有疑问） | 琥珀 `#B7791F` |
| 高置信 | 系统判定无疑问 | 绿 `#1E8449` |
| 已修正 / 已跳过 | 已处理过的问题行 | 绿 / 灰 |

排序：待修正 → 待确认 → 已修正 → 已跳过 → 高置信，同类按问题行下标。

### 3.2 筛选胶囊

`需处理（默认）/ 全部 / 待修正 / 待确认 / 高置信`，`chipBtn` 样式（选中态用 accent 反白）。
默认落在「需处理」= 待修正 + 待确认，导入时只会看到需要人看的行。

### 3.3 右侧就地修正

- 上半：当前行信息卡（来源 / 发票号 / 购方 / 金额 / 收款认定 / 备注）
- 按钮：`查看原始台账行`、`编辑各经办人已收`
- 中部：内嵌 `ProblemFixPanel`（从 `problem_dialog.py` 抽出的组件，发票 / 预收款两种形态）
- 底部动作：`保存修改` / `重新修正` / `跳过此行`

保存后 → 立即合并进**工作副本** → 重跑 `evaluate()` 重算置信度 → 刷新表格。
该行当场变为「待确认 / 高置信」，可再点「重新修正」回到表单继续改。

### 3.4 数据流（关键）

```
真实 data ──deepcopy──> work ──修正──> work' ──evaluate──> 表格
                                              │
                              点「确认入库」 ──┘
                                              ▼
                              merged = deepcopy(data) + 修正 + 已收覆盖
                                              ▼
                              validator(merged)  ← 写库前校验（合计勾稽 / 花名册）
                                   失败 ──> 弹提示，留在对话框，data 不变
                                   通过 ──> data.clear(); data.update(merged)
```

- **真实 data 全程不被修改**，取消即零副作用；
- 校验移到对话框内部 → 不再出现「改完一堆才报错、修改全丢」；
- 未处理的待修正行在确认时提示后自动跳过（等同旧流程的 skip）。

## 4. 落地改动

| 文件 | 改动 |
|---|---|
| `app/importer/ledger_import.py` | 两处 `problem()` 工厂补 `header` / `raw_row`（问题行也能看原始台账行） |
| `app/importer/importer.py` | `_apply_resolved` 透传 `header`/`raw_row`；新增末尾按 sheet 重算 `sheet_totals`/`sheet12_total`（**修掉修正行不计入合计导致误判校验失败的隐患**）；抽出 `validate_ledger_before_write()`；`import_ledger_file` 新增 `on_confirm` 统一回调 |
| `app/ui/problem_fix_panel.py`（新） | 从 `ProblemDialog` 抽出修正表单组件，校验逻辑逐字复制，避免行为漂移 |
| `app/ui/unified_import_dialog.py`（新） | 统一确认对话框 |
| `app/ui/import_view.py` | 台账导入改走 `on_confirm`；`_resolve_problems`/`_preview_ledger` 保留作回退路径 |
| `app/ui/style.py` | 新增 `QPushButton#chipBtn` 筛选胶囊样式（浅/深双皮肤） |
| `tests/_smoke_unified_import.py`（新） | 40 项 offscreen 冒烟 |

## 5. 验收

`QT_QPA_PLATFORM=offscreen python tests/_smoke_unified_import.py` → 40/40 PASS。
侧栏冒烟 69 项、标题栏冒烟、xlsx 冒烟全部回归通过。

## 6. 遗留

- `problem_dialog.py` / `preview_dialog.py` 保留为回退路径（`preview_dialog.HandlerReceivedDialog`、`_fmt_money` 仍被新对话框复用），未删除。
- 「重新修正」目前是回到修正形态重填；未做「基于已保存值预填下一次」的增强（`_prefill_rows` 只吃 problem 原始值）。如需，可让 `set_problem` 接受 `initial` 覆盖。

## 7. 导入前确认右栏重构（2026-09-02 评审结论）

### 7.1 背景与问题

评审发现右栏按钮分散成「3 块」，意图不清：

| 块 | 现状 | 问题 |
|---|---|---|
| 块 1 | 顶部孤零零一个 `btn_source`「查看原始台账行」（`row_btn` 单行） | 一个按钮独占一行，过于孤立，且与"改"动作平级 |
| 块 2 | `ProblemFixPanel` 内的 `btn_add` / `btn_del`（经办人子表增删） | 已是子表工具条（紧贴 `htable`），属正常就近设计，本次**不动** |
| 块 3 | 底部 5 个等权按钮：`btn_save`(保存修改) / `btn_refix`(重新修正) / `btn_confirm_row`(确认) / `btn_edit`(编辑) / `btn_skiprow`(跳过此行) | 5 个等权，难判断先点哪个；主行动不突出 |

目标：**把三类意图（看 / 改表单 / 对整行做决定）各归其位，视觉统一**，并为"问题"信息单设区块。业务逻辑、信号槽、校验、写回一律不动。

### 7.2 结论方案（方案 A 头部卡 + 信息卡 + 问题块 + 按钮合一）

右栏自上而下改为 6 个纵向分区：

1. **发票头部卡**（新增 `invoiceHeaderCard`，见 7.2a）：占满右栏顶部空白，突出显示状态 / 发票号 / 购方 / 金额
2. **发票信息卡**（`infoCard` + `self._info` 网格）：来源 / 发票号 / 购方 / 金额 / 收款认定 / 备注（原「原因/疑问」从网格移出，见 7.3）
3. **问题 / 异常块**（新增，见 7.3）
4. **工具行**（降级 `btn_source`，见 7.4）
5. **修正表单宿主** `self._fix_host`（含 `ProblemFixPanel`，内部 `btn_add/btn_del` 维持现状作子表工具条）
6. **单一操作栏**（见 7.5）

> **布局约束（2026-09-02 补）**：主体区域改为 `QHBoxLayout [left_panel | divider | right(stretch=1)]`：
> - `left_panel` 是左表容器，宽度在展开态固定为 760px、折叠态收窄为 36px 细轨，承载 `self.table` 与折叠细轨 `self.left_rail`。
> - `right` 为流体区域（`stretch=1`），内容包在 `QScrollArea`（`rightScroll`，`widgetResizable=True`）中，按自然高度**顶部对齐**（通常比左表矮），左表依旧撑满高度并独立滚动；右栏内容过高时自行滚动。单一操作栏钉在右栏底部、与左表底部对齐，不随内容滚动。
> - 保留 4px 拖拽条 `_Divider`（展开态可见）在左栏右边缘，拖动改变左栏宽度并作为后续展开宽度记忆。
> - 折叠机制见 7.10。

#### 7.2a 发票头部卡（填补顶部空白）

- 置于右栏最顶端，填补原先「信息卡上方大片空白」的问题。
- 内容：
  - 第一行：当前行状态（`待确认 / 高置信 / 已跳过`），按状态着色（琥珀 / 绿 / 灰）。
  - 第二行：发票号码，大字号（22px）加粗，作为视觉锚点。
  - 第三行：左购方、右金额，金额大字号（18px）加粗右对齐。
- 数据来源：复用 `_row_field(r, "no")`、`_row_field(r, "buyer")`、`_row_field(r, "amt")`，发票行 / 问题行统一填充。
- 无选中行时全部显示「—」并清空状态样式。



### 7.3 问题 / 异常块（仅问题、不加建议）

- 新增 `self.lbl_issue`（QLabel，`setWordWrap(True)`），置于信息卡下方独立块。
- 数据来源：复用 `_row_field(r, "reason")`（发票行 = `ev["reasons"]` 拼接；问题行 = `p.get("reason")`）。
- **只显示原始问题文本，禁止追加任何"建议：…"类系统文案。**
- 双态：
  - 有问题时（amber，objectName `issueWarn`）：左边框 3px `#BA7517`、背景 `#FAEEDA`、标题"问题 / 异常" `#854F0B`。
  - 无问题时（文本为空 / 以"✓ 系统判定无疑问"开头，objectName `issueOk`）：淡化态，灰字、极淡边框。
- `_load_right()` 中：`self.lbl_issue.setText(...)` 后按是否有 issue 切换 objectName 并 `style().unpolish()/polish()`。

### 7.4 工具行（降级 btn_source）

- 原 `row_btn` 仅含 `btn_source`，改为信息卡与表单之间的「工具行」：`btn_source` 走 quiet/ghost 风格（objectName `toolLink`，无填充、accent 文字 + 小图标方块），文字"查看原始台账行"。
- 语义：它是"查看"不是"改动"，不与操作栏平级。实现上保留单按钮 QHBoxLayout，仅改 objectName 套 ghost 样式。

### 7.5 单一操作栏（3 块合一）

- 用单个 `QHBoxLayout action_bar` 替换原 `ab` 行，内分两组：
  - **次级组（靠左，弱按钮）**：`btn_edit`(编辑) / `btn_refix`(重新修正) / `btn_skiprow`(跳过此行)
  - **主行动（靠右，accent 高亮）**：同一时刻至多一个 —— `btn_confirm_row`(确认并移出待处理) 或 `btn_save`(保存修改)
- 主行动判定（在 `_set_actions` 内实现，沿用现有 `save/skip/refix/confirm/edit` 可见性开关）：
  - `confirm` 为真 → `btn_confirm_row` 显示并设为主行动；`btn_save` 仅当处于编辑态才显示且降级为次级（避免两个主行动并存）。
  - `save` 为真且 `confirm` 假 → `btn_save` 设为主行动。
  - `edit/refix/skiprow` 恒为次级。
- 新增"主/次"样式区分：主行动 objectName `actionPrimary`（accent 填充 `#185FA5` + 白字）；次级 `actionSecondary`（透明 + `border-secondary`）。

### 7.6 样式钩子（style.py）

- `QFrame#invoiceHeaderCard`：背景 `bg_table`、边框 `border`、圆角 10px、内边距 14px 16px。
- `QLabel#headerStatus` / `#headerNo` / `#headerBuyer` / `#headerAmt`：状态小字、发票号 22px 加粗、购方 13px、金额 18px 加粗右对齐。
- `QPushButton#toolLink`：无背景、无边框、accent 文字、hover 淡底/下划线。
- `QWidget#issueWarn` / `#issueOk`：背景与左边框差异（issueWarn 琥珀，issueOk 极淡灰）。
- `QPushButton#actionPrimary`：`#185FA5` 填充 + 白字；`#actionSecondary`：透明 + `border-secondary`。
- 圆角 6–10px、字号 12–22px，套 D-Notion 浅色基线；深皮肤走对应变量。

### 7.7 验收

- `QT_QPA_PLATFORM=offscreen python tests/_smoke_unified_import.py` 通过。
- 新增断言：
  1. 右栏顶部存在 `invoiceHeaderCard`，且头部卡显示当前行状态、发票号、购方、金额。
  2. 问题块在待确认行显示 `issueWarn` 文本且不含"建议"字样。
  3. 工具行 `btn_source` 存在并走 ghost 样式。
  4. 操作栏同一时刻至多一个主行动可见。
- 人工核对三种场景（待确认 / 高置信只读 / 已修正）操作栏主行动正确。

### 7.9 嵌入 ImportReviewView 时的页头处理

`UnifiedImportDialog` 同时承担「独立弹窗」与「导入复核页嵌入面板」两种角色：

- **独立弹窗**：保留标题 `发票台账导入确认 — {period}` 与说明文案，维持原有视觉层级。
- **嵌入页面**：外层 `ImportReviewView` 已提供「导入前 · 待确认 {period}」模式徽章与账期下拉，对话框内部隐藏自身标题与说明，避免双重页头；上间距由 18px 收紧为 10px，使内容区域整体上顶，减少窗体顶部留白。

判定：构造时 `parent is not None` 即视为嵌入模式（`ImportReviewView` 以 `parent=self` 创建 `page_pre`）。

### 7.10 左栏折叠 / 展开（方案 C：IDE 式自动隐藏）

评审后用户要求「把右边栏和左边栏分离，左边栏做成可以折叠的」，从 A/B/C/D/E 五个方案中选定 **方案 C（IDE 式自动隐藏）**，并确认：
- (a) 保留手动拖拽调宽；
- (b) 默认触发方式：鼠标进入右侧区域自动折叠，进入左栏（含细轨）自动展开；
- (c) 细轨宽度 36px。

实现概要：

- 新增 `_CollapsiblePanel`：基于 `QWidget` 的 `w` 属性驱动宽度，`QPropertyAnimation` 动画时设置 `setFixedWidth`，折叠态同步调整内部细轨几何。
- 新增 `_Divider`：4px 宽竖条，带 `SplitHCursor`，`mousePress/Move/Release` 计算拖拽偏移并设置左栏宽度，同时通过 `_track_expanded_w()` 把该宽度记忆为下一次展开的目标。
- 细轨 `self.left_rail`：36px 宽，置于 `left_panel` 内部；显示竖向「台账表格」、当前行数、展开箭头 `›`。折叠态显示，展开态隐藏。
- 悬停触发：在 `left_panel` 及其所有子部件、以及 `right` 及其所有子部件上安装 `eventFilter`，收到 `QEvent.Enter` 时判断对象所属区域：
  - 左栏子树（表格、细轨等）→ `expand()`
  - 右栏子树（滚动区、信息卡、修正表单、操作栏等）→ `collapse()`
  - 顶部筛选栏、底部按钮栏、拖拽条等不属于左右子树，不改变状态。
- 动画：`style.motion_enabled()` 为 True 时使用 180ms `OutCubic` 动画；为 False 时直接 `setFixedWidth` 到目标值，便于测试与关闭动效的场景。
- 默认状态：**展开**（`left_panel` 宽 760px，表格可见，细轨隐藏，拖拽条可见）。
- 折叠时隐藏表格与拖拽条，展开时恢复，避免表格在窄宽动画中被挤压。

验收：

- `QT_QPA_PLATFORM=offscreen python tests/_smoke_unified_import.py` 新增断言通过：默认展开、折叠后宽度为 36px、细轨/表格/拖拽条可见性正确、拖拽宽度记忆、eventFilter 左右 Enter 触发折叠/展开、顶部筛选栏 Enter 不改变状态。

### 7.8 范围外（本次不做）

- 不改 `ReviewPostView`（导入后模式）：本就是单列表，无三块按钮问题。
- 不改任何数据写回 / 校验 / 信号槽逻辑。
