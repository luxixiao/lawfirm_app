# 内嵌类 Excel 分成计算引擎 — 设计 Spec

> 目标：在桌面程序里内建一个迷你电子表格，用于核算每个职工的分成报酬。
> 状态：需求澄清已全部锁定，本 spec 待用户过目确认后进入增量实现。
> 约定：求值引擎**自建**（纯 Python），Excel 文件写出**复用 openpyxl**。

---

## 0. 需求回顾（8 条）

| # | 需求 | 落地方式 |
|---|------|----------|
| 1 | 内建类 Excel 模块 | 程序内新模块，提供网格表格 + 公式能力 |
| 2 | 基础运算 + 四舍五入 | `+ - * / ^ ()`；`ROUND(x,n)`；`SUM/AVERAGE/IF/MIN/MAX/ABS` |
| 3 | 表内 + 跨表引用 | `A1`、`A1:B10`、`Sheet2!A1`（**Excel 兼容语法**） |
| 4 | 引用软件内已有数据 | `=DATA(职工,指标,年,月)` + 专用选择器单元格（**两者都要**） |
| 5 | 新建不同表格算不同内容并存程序 | 多个命名表存进程序 DB（SQLite） |
| 6 | 复制某表公式/结构再改 | 克隆一张表（结构+公式+引用），新表上改 |
| 7 | 编辑 / 查看 双模式 | 编辑模式改内容/公式；查看模式只读看结果 |
| 8 | 导出 Excel 可选带/不带公式 | 带公式：用户公式保留为真实 Excel 公式；不带：全填计算值 |

---

## 1. 已锁定决策

| 项 | 决策 |
|----|------|
| 公式语法 | Excel 兼容（`=A1+B1`、`=SUM(A1:A10)`、`=ROUND(x,2)`、跨表 `Sheet2!A1`） |
| 软件内数据引用 | `DATA()` 函数 **+** 选择器单元格，两者等价 |
| 导出规则 | **求值链含 `DATA()`/`PARAM()` 的格子一律填值**（含混合公式如 `=DATA(...)*0.3`）；纯单元格引用公式（`=A1+B1`、`=SUM(A1:A10)`）保留真实 Excel 公式；跨表引用导出**填值**（不合并多 sheet）；不带公式版全填值 |
| 容器 | **单表先做**；`calc_sheet` 预埋 `workbook_id` 列（默认 1），暂不建 `calc_workbook` 表 |
| 导航 | 侧栏**新建大类「分成计算」** |
| 求值引擎 | **自建**轻量引擎（tokenizer→parser→evaluator）；文件写出用 openpyxl |
| 自定义数据 | **A（PARAM 命名参数）+ B（自定义指标）**，统一走 `DATA()` |
| 存储 | **方案甲：JSON 整表**，`calc_sheet.content` 一个字段存整张网格 |

---

## 2. 数据模型与 DB 表结构

挂在现有 `lawfirm_app.db`（WAL）下，新增两张表（工作簿表本期不建）：

### 2.1 `calc_sheet`（表格主表）
```sql
CREATE TABLE calc_sheet (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  workbook_id INTEGER NOT NULL DEFAULT 1,   -- 预埋，本期恒为 1
  name        TEXT    NOT NULL,
  sheet_order INTEGER NOT NULL DEFAULT 0,
  created     TEXT    NOT NULL DEFAULT (datetime('now')),
  updated     TEXT    NOT NULL DEFAULT (datetime('now')),
  content     TEXT    NOT NULL DEFAULT '{}'  -- JSON 整表
);
CREATE UNIQUE INDEX idx_calc_sheet_name ON calc_sheet(name);
```

`content` JSON 结构：
```json
{
  "rows": 50,
  "cols": 12,
  "cells": {
    "0,0": {"raw": "职工",        "kind": "text"},
    "1,0": {"raw": "=DATA(A2,\"开票金额\",2025,1)", "kind": "formula"},
    "2,1": {"raw": "=B1*PARAM(\"提成比例\")",        "kind": "formula"}
  },
  "params": { "提成比例": 0.3, "个税档": 0.2 },   -- A 层：本表命名参数
  "col_headers": ["", "金额", "分成"],
  "row_headers": []
}
```
- `kind` ∈ `formula | data | param | text | number | blank`
- 公式/引用全部以原文（`raw`）落库，计算值不存，展示时实时求值。
- **复制表 = 复制 `content` JSON + 改名 + `sheet_order` 置末**，公式与引用天然全保留。

### 2.2 `calc_indicator`（自定义指标 / B 层）
```sql
CREATE TABLE calc_indicator (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT NOT NULL,                    -- 指标名，如 "净分成"
  definition  TEXT NOT NULL,                    -- 公式模板，用 $职工/$年/$月 占位
  note        TEXT
);
CREATE UNIQUE INDEX idx_calc_ind_name ON calc_indicator(name);
```
- `definition` 例：`DATA($职工,"开票金额",$年,$月) - DATA($职工,"退款金额",$年,$月)`
- 调用时 `=DATA(职工,"净分成",年,月)`：引擎先查内置指标，未命中则查 `calc_indicator`，把 `$职工/$年/$月` 替换为实参后求值。
- **A+B 因此统一进 `DATA()`**：内置指标由引擎算，自定义指标由 `definition` 算，对用户透明。

### 2.3 内置指标清单（首批，映射现有引擎）
由新增 `app/engine/calc_data.py` 实现，复用既有 `collection` / `person_settlement` / `salary` / `expense_ledger` 等口径，**不重复实现**：

| 指标 | 来源 |
|------|------|
| 开票金额 | invoice |
| 已收金额 | collection |
| 已收净额 | collection 引擎（净额口径） |
| 红冲金额 | invoice `red_abs` |
| 退款金额 | refund |
| 每月工资 | salary |
| 代扣个税 | salary |
| 公积金 | salary |
| 实发金额 | salary |
| 业务收入 | person_settlement 第五节（合伙=开票净额；聘用/兼职=收款净额）——**分成计算的主要基数** |
| 收款净额 | person_settlement 第二节（①~⑤合计，退为负） |
| 开票净额 | person_settlement 第三节（⑥~⑨合计，红冲为负） |
| 未收款金额 | person_settlement 第四节 |
| 费用合计 | expense_ledger |

> **「四、未收款金额」口径（person_settlement 第四节，2026-09-23 与需求方确认）**：
> - 各月列 = 本月未收，仅含蓝字发票（=⑦），**不涉及红冲、不涉及往期发票**。
> - 合计列 = **累计未收（截止该月，含红冲与退款冲减）** = Σ(三·本月开具发票金额 − 二·收款净额) = Σ(⑦本月未收 + ⑧红冲本年 + ⑨红冲上年 − ②收本年 − ③收上年 − ④退本年 − ⑤退上年)；①本月开收 = ⑥本月开收 互相抵消。
> - 红冲（⑧⑨）/ 退款（④⑤）均**冲减**累计未收：例——1 月开票 1000 未收、2 月红冲 −1000，则累计未收 1 月=1000、2 月=0、全年=0。
> - 代码实现：`app/engine/person_settlement.py` 的 `cumulative_uncollected(st, mo=0)`（mo=0 表示全年）。

> **不设「分成/提成」内置指标**（经查证 person_settlement 无此结果项："分成"仅出现于第六节「减：分成报酬及费用」，是费用台账费用类型「分成（报酬发放）」的支出列示，非分成计算结果）。**应得分成由用户在计算表中用公式自行计算**（基数=业务收入等内置指标）——这正是本引擎的核心用途。

> 指标注册表数据驱动：新增内置指标 = 在 `calc_data.py` 加一个映射函数，无需改表结构。

---

## 3. 公式文法范围

- 单元格以 `=` 开头 → 公式；否则按 `number` / `text` / `blank` 处理。
- 运算符：`+ - * / ^`、括号 `()`、一元负号 `-`。
- 引用：表内 `A1`、`A1:B10`（区域，用于 SUM 等）；跨表 `Sheet2!A1`、`Sheet2!A1:B10`。
- 函数（首批）：`SUM` / `ROUND` / `AVERAGE` / `IF` / `MIN` / `MAX` / `ABS`（可后续扩）。
- 数据源函数：
  - `DATA(职工, 指标, 年, [月])` — 月省略=全年累计；指标可为内置名或 `calc_indicator` 名。
  - `PARAM("名称")` — 本表命名参数；跨表 `SheetX!PARAM("名称")`。
- 区域在 `SUM/AVERAGE/MIN/MAX` 中展开为数值列表。
- 四舍五入：`ROUND(x,n)` 显式调用；基础运算结果不自动舍入，按需包 `ROUND`。

---

## 4. 求值引擎（自建，纯 Python）

- **三层**：`tokenizer`（词法）→ `parser`（递归下降，输出 AST）→ `evaluator`（带依赖缓存的求值 + 循环检测）。
- **依赖图**：解析公式收集引用的单元格 / `DATA` / `PARAM`；求值前做惰性求值 + 环检测（循环引用报 `#CIRC`）。
- **`DATA()` 求值**：调 `app/engine/calc_data.py` 指标函数返回数值；职工/指标/账期找不到 → `#REF`。
- **`PARAM()` 求值**：从本表 `params` JSON 取；跨表从目标表取；未定义 → `#NAME`。
- **选择器单元格**：UI 弹窗选 `职工 + 指标 + 账期`（或选已定义指标）→ 写入 `=DATA(...)` 原文；与手写公式完全等价，可参与运算、可被跨表引用。
- **编辑模式**显示 `raw`；**查看模式**显示计算值。

---

## 5. 编辑 / 查看 双模式

- **编辑模式**：可改单元格内容；输入 `=` 进公式编辑；点「数据引用」按钮弹选择器；显示 `raw`。
- **查看模式**：只读；所有格显示计算值；`DATA`/选择器显示为值。
- 模式切换在工具栏；默认进查看模式（防误操作）。

---

## 6. 新建 / 复制 / 保存

- **新建表**：默认空白网格（如 50×12），命名（唯一约束）。
- **复制表**：克隆 `content` JSON（公式/引用/参数全保留），改名，`sheet_order` 置末。
- **保存**：编辑即落库（`content` 整体 UPDATE + `updated` 时间戳），或显式「保存」按钮。
- **删除**：从 `calc_sheet` 删行（需确认，防误删）。

---

## 7. 导出 Excel（openpyxl）

- 生成 `.xlsx`，文件名默认 `<表名>.xlsx`，显式动作触发。
- **带公式版**：
  - **求值链含 `DATA()`/`PARAM()` 的格子一律写入计算值**——含混合公式（用户公式内嵌 DATA/PARAM，如 `=DATA(...)*0.3`），因外部 Excel 不识别 `DATA()`；
  - 纯单元格引用公式（`=A1+B1`、`=SUM(A1:A10)`）→ 原样写入真实 Excel 公式；
  - 跨表引用 `Sheet2!A1` → **写入计算值**（单表导出不合并 sheet，保留公式会断链）。
- **不带公式版**：所有单元格一律写**计算值**。
- 列头/行头一并写出。

---

## 8. 导航与页面结构

- 侧栏**新建大类「分成计算」**（NAV/PAGES 当前 21 → 22，须核对平衡）。
- 该大类页面：
  - `CalcSheetListView`：表列表（新建 / 复制 / 打开 / 删除）。
  - `CalcSheetView`：网格 + 公式栏 + 模式切换 + 导出按钮 + 参数管理 + 自定义指标管理入口。
- 沿用 D-Notion clean 视觉，不改其它软件。

---

## 9. 增量实现阶段（每步可验证）

| 阶段 | 内容 | 验证 |
|------|------|------|
| 1 | DB schema（`calc_sheet`/`calc_indicator`）+ `app/engine/calc_data.py` 指标函数 | 临时库建表 + 指标查询冒烟 |
| 2 | 公式引擎内核（tokenizer/parser/evaluator + 单测） | 纯 Python 单测，不碰 UI |
| 3 | `calc_sheet` 存/取/复制引擎层 + 单测 | 保存/覆盖/复制/删除 |
| 4 | 网格 UI（`CalcSheetView`）+ 编辑/查看模式 + 公式栏 | 本机 offscreen 冒烟 |
| 5 | `DATA()`/`PARAM()` 选择器单元格 + 指标下拉 + 自定义指标管理 | 交互冒烟 |
| 6 | 导出（带/不带公式）+ 单测/冒烟 | 导出文件打开核对 |
| 7 | 导航接入「分成计算」+ `py_compile` + 本机 `run_app.bat` 冒烟 + git 提交推送 | 导航平衡 22=22 |

---

## 10. 风险 / 待定细节

- **B 层指标参数绑定**：用 `DATA` 第四/五参统一传 `职工/年/月`，`definition` 内以 `$职工/$年/$月` 占位替换。
- **循环引用**：引擎层必须做环检测，避免死循环。
- **指标口径一致性**：`calc_data.py` 必须复用现有引擎（collection/person_settlement 等），不可另写一套，确保与台账/结算数字一致。
- **跨表求值顺序**：打开多表时按需惰性求值，避免全量重算卡顿。
- **JSON 整表的单元格级反查**（如"哪些格引用了周立生"）本期不做，未来如需再用 SQLite JSON 函数或冗余索引解决。

---

## 11. v1 范围冻结（2026-08-30 第二轮确认）

### 11.1 业务总则
- **只读不回写**：计算表从主库取数、自主计算，结果**绝不写回** invoice/collection/结算等主数据；定位为「试验田/个性化核算」，正式结算仍走 person_settlement 内置报表。

### 11.2 导出规则（定稿）
- 混合公式（用户公式内嵌 DATA/PARAM）→ 填值；仅纯单元格引用公式保留真公式。
- 跨表引用导出 → 填值（不合并多 sheet）。

### 11.3 命名校验
- 表名：不得匹配单元格模式（`A1`~`ZZ99`）、不含空格与 `!`（避免 `Sheet2!A1` 解析歧义）。
- 参数名/指标名：不得与内置函数（SUM/ROUND/AVERAGE/IF/MIN/MAX/ABS/DATA/PARAM）重名。

### 11.4 数据语义
- 职工按姓名匹配；重名 → `#REF` + 状态栏提示；员工编号引用留 v2。
- 全年累计（月省略）：逐指标写清口径（开票/收款 Σ1-12月；退款分退本年/退上年；工资=年总额），实现时与现有引擎逐个对齐。
- Excel 语义：空格=0、SUM 忽略文本、错误值传播到引用格并标红显示。
- 显示：数值全局两位小数+千分位；每格独立格式 v1 不做。

### 11.5 交互范围
- 行列插入/删除：**不做**（避免公式引用重写）；支持尾部加行/列。
- **Excel 区域粘贴（值）**：**做**——支持 TSV 粘贴一片区域（从旧表迁移刚需）。
- 撤销/重做：不做。
- content JSON 加 `"version": 1` 字段。

### 11.6 工程
- DATA 请求级缓存：同 职工+指标+账期 会话内只查一次，重开表自然刷新。
- **DB 在 Seafile 同步范围内（已确认）**：跨机同时编辑=后写覆盖。`calc_sheet` 加 `updated_by`/`updated_at` 并在列表与打开时展示"最后编辑人/时间"，降低覆盖风险。
- 自定义指标管理对话框：增删改 + definition 解析校验 + 删除前被引用检测。

### 11.7 澄清（2026-08-30 第三轮）
- person_settlement（个人结算总表）六个节点中**无"分成/提成"结果项**："分成"仅出现在第六节「减：分成报酬及费用」（费用台账「分成（报酬发放）」类支出的列示）。
- 故不设"分成"内置指标；应得分成由用户在计算表中以公式自行计算，基数取「业务收入」（第五节口径）等内置指标。
