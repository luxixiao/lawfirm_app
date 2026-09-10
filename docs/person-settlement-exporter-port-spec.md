# 个人结算总表导出器 · Python→C#/NPOI 移植规格

> 版本：v1（2026）
> 分支：`pilot/dotnet`
> 作者：架构师（高见远）
> 目标读者：工程师（本文件即实现依据）
> 结论来源：全部基于源码事实，行号引用见各节。**本文件只描述设计，不含实现，不修改任何其它文件。**

---

## 1. 目标与范围

把 Python 的「个人结算总表导出器」`app/exporter/person_settlement_exporter.py`（167 行，openpyxl）**逐格等价**地移植到 C#/NPOI，新增 `csharp/LawFirm.Exporter/PersonSettlementExporter.cs`，并通过扩展 `csharp/LawFirm.Cli/Program.cs` 使其可被现有验证流水线（`t2_verify.bat` / `diff_xlsx.py`）逐格比对。计算引擎 `SettlementEngine.Build` 与「月度结算表导出器」`SettlementReportExporter` 已移植完成且验证 PASS，本次**只补导出层**，**不得改动引擎**，**不得破坏现有 `--year/--month` 月度结算表导出路径**。

已确认的关键事实（源码依据）：

- 计算引擎公共 API：`SettlementEngine.Build(conn, year, person=null, personType=null)` 返回 `Dictionary<string, PersonSettlement>`（`SettlementEngine.cs:23-25`）。
- 数据结构 `PersonSettlement.Months` 是 `Dictionary<int, MonthData>`，键为 **1..12 的整数**（构造函数预填 12 个月，`SettlementEngine.cs:449,455-462`）；`MonthData` 的 11 个字段名与 Python 的 11 键一一对应（`SettlementEngine.cs:466-470`），其中 `InvTotal` 即 Python 的 `inv_total`。
- 已完成的同构导出器 `SettlementReportExporter.cs` 提供了全部可复用的 NPOI 建表模式（见第 4 节）。

---

## 2. 输出契约（Q1）

### 2.1 推荐：**保持「每人一个文件」**（对齐 42 个 golden 文件）

**推荐理由（基于源码事实，而非偏好）：**

1. **golden 基准就是每人一文件**。`scripts/dump_golden.py:97-101` 的 `dump_person_settlement()` 直接调用 `export_all(d, year)`，而 `export_all`（`person_settlement_exporter.py:157-166`）对每个 person 调 `export_one` 生成 `个人结算总表_{safe}.xlsx`。仓库实测 `tests/golden/person_settlement_exporter/` 下有 **42 个 xlsx**（`ls | wc -l = 42`），与 `build_settlement(year)` 的人员数一致。
2. **保真度最高、零语义损失**。每人一文件时，C# 产物可与 golden **1:1 配对**，直接调用**未修改的** `diff_xlsx.py` 逐格比对，无需引入任何"合并/拆分"归一化逻辑去掩盖差异。
3. **UI 语义逼真**。Python UI `settlement_view.py` 的 `gen_all`/`gen_selected`（`:347-425`）本就是对每个 person 调 `export_one`（`:417`），C# 侧将来要接管 WPF 导出，保持每人一文件才与产品行为一致。
4. **合并方案会引入赔偿性问题**：合并成一个多 sheet 文件后，golden（42 文件）与 C#（1 文件）结构根本不同，`diff_xlsx.py` 的「sheet 名/顺序」维度必然全红，需要额外写"把 42 个 golden 的 sheet 拼成一个临时 workbook 再比"的预处理器，反而**增加**验证成本与出错面。

**代价**：验证脚本需从"单文件对单文件"升级为"逐文件配对 diff"（见第 6 节，改动很小，只是外层加一个循环）。

### 2.2 输出目录约定

```
csharp/out/person_settlement/
    个人结算总表_丁祥锋.xlsx
    个人结算总表_公共.xlsx
    ...（共 N 个，N = build_settlement(year).Keys.Count）
```

- 目录由导出器自行 `Directory.CreateDirectory`（参考 `SettlementReportExporter.cs:40-41,102` 的既有做法，因为 `csharp/out` 被 `.gitignore` 忽略、全新克隆时不存在）。
- 每个文件名：`个人结算总表_{safe}.xlsx`，其中 `safe = person.Replace("/", "_").Replace("\\", "_").Trim()`，**若结果为空串则用 `未命名`**（`person_settlement_exporter.py:163`）。这是必须复刻的隐含行为（见第 7 节风险 R5）。
- 文件编码：文件名含中文，**必须由 C# 直接用 `FileStream` 写盘**（NPOI 自己处理 zip 内 UTF-8），**不要**在 `t2_verify.bat` 里拼中文路径传给 CLI/命令行（`t2_verify.bat:10-13` 已说明 cmd.exe GBK 坑）。

---

## 3. 计算引擎调用序列（Q2）

### 3.1 `export_one` → C# 调用序列（严格按 Python 语义）

Python `export_one`（`person_settlement_exporter.py:137-154`）：

```
wb = 新建空 Workbook（删掉默认 sheet）
st_all = build_settlement(year, person=person).get(person)          # 不带 type
for ptype in ("合伙","聘用","兼职"):
    st = build_settlement(year, person=person, person_type=ptype).get(person)
    if _has_data(st):
        create_sheet(f"{person}{ptype}"); _write_ws(ws, person, st, year, ptype)
create_sheet(f"{person}汇总"); _write_ws(ws, person, st_all, year, "汇总")   # 恒出
```

对应 C# 序列（**逐条对应，顺序不可变**）：

```csharp
var wb = new XSSFWorkbook();                 // 不再 CreateSheet 默认页；NPOI 的 XSSFWorkbook 初始无 sheet，
                                             // 与 openpyxl 需 wb.remove(wb.active) 不同（NPOI 无此需求）
var stAll = SettlementEngine.Build(conn, year, person, null)
                .TryGetValue(person, out var a) ? a : null;

foreach (var ptype in new[] { "合伙", "聘用", "兼职" })
{
    var dict = SettlementEngine.Build(conn, year, person, ptype);
    var st = dict.TryGetValue(person, out var s) ? s : null;
    if (HasData(st))                          // _has_data 的 C# 实现见 3.2
    {
        var ws = wb.CreateSheet(person + ptype);   // sheet 名 = 姓名+身份，无分隔符
        WriteWs(ws, person, st, year, ptype);
    }
}
var wsum = wb.CreateSheet(person + "汇总");         // 恒建
WriteWs(wsum, person, stAll, year, "汇总");
```

**关键点：**

- `SettlementEngine.Build` 的 `personType` 参数是 `string?`，**不传（null）= 不带 type 过滤**（`SettlementEngine.cs:23-25` 默认值 + `:40-41,50-53` 仅在非 null 时加 WHERE）。
- `Build` 返回的字典 key 是**人员姓名**；用 `.TryGetValue(person, ...)` 取，取不到等价于 Python 的 `.get(person)` 返回 `None`。
- **sheet 顺序**：按 ptype 数组顺序（合伙→聘用→兼职，仅 `_has_data` 为真者）+ 末尾恒有汇总。实测 `丁祥锋` = `['丁祥锋合伙','丁祥锋汇总']`；`黄宝根` = `['黄宝根聘用','黄宝根汇总']`；`公共` = `['公共汇总']`（三个身份都无数据）。这三点已在 golden 中核实。

### 3.2 `_has_data` 精确判定（逐条）

Python（`person_settlement_exporter.py:32-45`）逐条翻译：

| 步骤 | Python 条件 | C# 实现规格 |
|---|---|---|
| ① | `st is None → False` | `if (st == null) return false;` |
| ② | 对 `mo=1..12`，若 10 个键任一 `abs(x) > 0.01` → True | 键集合：`RecOpenCur, RecCurYear, RecPrevYear, RecRefundCur, RecRefundPrev, InvOpenReceived, InvOpenUncollected, InvRedCur, InvRedPrev, Income`（`MonthData` 字段，`SettlementEngine.cs:466-470`）。**注意：不含 `InvTotal`！** |
| ③ | `sum(expenses[t][mo]) for all t, mo=1..12 > 0.01` → True | `st.Expenses.Values.Sum(d => d.Values.Sum()) > 0.01`（全年合计；`Dictionary<string,Dictionary<int,double>>`，`SettlementEngine.cs:452`） |
| ④ | 否则 False | `return false;` |

**与现有 `SettlementReportExporter.HasData` 的差异（必须新写，不可复用！）：**

- 现有 `HasData(st, month)`（`SettlementReportExporter.cs:109-121`）是**按单月**判定，且额外检查 `UncollectedMonth[month] > 0.01`；
- 本导出器的 `_has_data` 是**按全年 12 个月**判定，且**不含** `UncollectedMonth`（只在 `expenses` 全年合计里体现）。
- 两者语义不同，**必须为 `PersonSettlementExporter` 单独实现 `HasDataYear(st)`**，不可改写或复用月度版本。

（参考：`_has_data` 的 10 键集合恰好等于 `dump_golden.py:65-69` 中 `_MONTH_KEYS` 去掉 `inv_total`，可交叉印证。）

---

## 4. 完整格式规格表（Q3）

以下全部来自 `_write_ws`（`person_settlement_exporter.py:48-134`）与实测 golden 原始 XML（`个人结算总表_丁祥锋.xlsx` sheet1）。**工程师须逐项对齐。**

### 4.1 标题行（第 1 行）

| 项 | 规格 | 实测证据 |
|---|---|---|
| 合并区域 | `A1:O1`（第 1 行 1~15 列） | `merged=['A1:O1']` |
| 单元格 | A1，值 = `个人结算总表（{year}年）\u3000姓名：{person}\u3000类型：{type_label}` | `'个人结算总表（2025年）\u3000姓名：丁祥锋\u3000类型：合伙'` |
| **分隔符** | **全角空格 U+3000（`\u3000`）**，不是普通空格！紧邻「）」「姓名」与「名」「类型」 | 同上 |
| 字体 | 字号 14、加粗（`Font(size=14, bold=True)`） | `size=14.0 bold=True` |
| 对齐 | 居中+垂直居中（`CENTER`） | `halign center` |
| 行高 | 28 | `row1 h=28.0` |

> **NPOI 注意**：`type_label` 取值为身份标签或 `"汇总"`（`_write_ws` 第 4 参）。合并用 `ws.AddMergedRegion(new CellRangeAddress(0,0,0,14))`。合并单元格只需在左上角 A1 设值/样式（参考 `SettlementReportExporter.cs:146-156`）。

### 4.2 表头行（第 2 行）15 列

| 列 | A | B | C..N | O |
|---|---|---|---|---|
| 值 | `序号` | `项目` | `1月`..`12月` | `合计` |
| 字体 | 加粗 | 加粗 | 加粗 | 加粗 |
| 填充 | `solid` / `F2F2F2` | 同 | 同 | 同 |
| 对齐 | 居中 | 居中 | 居中 | 居中 |
| 边框 | 四边细 `D9D9D9` | 同 | 同 | 同 |

完整表头数组：`["序号","项目","1月","2月","3月","4月","5月","6月","7月","8月","9月","10月","11月","12月","合计"]`（`:59-60`）。实测 `hdr2 fill 00F2F2F2 / solid`。**注意 C..N 是 12 个月，不是"本期/本年累计"**——与月度结算表的 5 列完全不同，不可照抄。

### 4.3 `put()` 函数行为规格（核心）

Python（`:68-84`）：

| 目标 | 行为 |
|---|---|
| A 列（seq） | 写 `seq`；若 `seq_only=True` 写**空串**（该参数**从未被使用**，见 4.6）。对齐居中。设边框。 |
| B 列（name） | 写 `name`；对齐 **左**+垂直居中；若 `bold` 则字体加粗。设边框。**B 列不设 number_format。** |
| C..O 列（13 个值） | 写 `None if v is None else round(v,2)`；**number_format=`#,##0.00`**；对齐 **右**+垂直居中；设边框。若 `bold` 则**对 C..O 全部**设加粗字体。 |

**关键细节（易错）：**

1. **`#,##0.00` 无条件施加在 C..O 每一个单元格上**，即使值是 `None`。实测 row3（一、上年结余结转，全 None）的 XML 是 `<c r="C3" s="5" t="n"/>` —— 样式 `s=5` 已含 `#,##0.00`，但**无 `<v>` 值**。→ NPOI 必须：**建单元格 + 套样式（含 DataFormat `#,##0.00`）**，但值为 null 时**不要调用 `SetCellValue`**（否则会落盘 `t="n"` 带 0 或不带值，diff 的 `cell` 维度会读出 0 ≠ None）。参考 `SettlementReportExporter.cs:177-181` 处理空 seq 的同款手法。
2. **A 列、B 列不设 number_format**（实测 A3 `s="3"`、B3 `s="4"` 均无 DataFormat）。
3. **数值 round 到 2 位**：`round(v, 2)`。C# 用 `Math.Round(v, 2)`（与 `SettlementReportExporter.cs:189,195` 一致）。容差 1e-6，故 2 位对齐后无末位差。
4. `bold` 时 **A 列不加粗**（A 用 centerText/centerTextBold 二选一——见下注）；B 列加粗走 `nm.font`；C..O 加粗。
   - 实测：`put(r,"二","本月收款金额",...,bold=True)` → B4 bold=True；A4 是否 bold 取决于 seq 样式。**规格建议**：与 Python 一致，A 列样式与 bold 无关（`ws.cell(row_idx,1,seq).alignment=CENTER` 从不改字体）。C# 侧 A 列用「居中、非加粗」样式即可。**注：需向工程师澄清 A 列加粗是否影响 diff**——diff_xlsx 的 7 维**不比较字体**，故 A 列字体是否加粗**不影响验证**，按 Python 原样（非加粗）实现最稳。
5. `seq_only`（`:68-69`）是**死参数**：全仓库仅在定义处出现（`grep -rn "seq_only" app/` 只命中定义行），**无任何调用点传 `True`**。→ C# 侧**不要**实现该参数，直接按 `seq` 写入即可。

### 4.4 六个大节的行构成与顺序（第 3 行起）

起始 `r=3`（`:85`）。按顺序（实测 golden 行号逐一核对）：

| 行 | seq | 项目名 | 值来源 | bold | 备注 |
|---|---|---|---|---|---|
| 3 | `一` | `上年结余结转` | 全 `None`×13（13 个空数值格） | 否 | 留空占位 |
| 4 | `二` | `本月收款金额` | `_subtotal(rec_keys)` | **是** | 5 项小计 |
| 5 | `1` | `本月开收` | `_row_values("rec_open_cur")` | 否 | |
| 6 | `2` | `收本年` | `_row_values("rec_cur_year")` | 否 | |
| 7 | `3` | `收上年` | `_row_values("rec_prev_year")` | 否 | |
| 8 | `4` | `退本年` | `_row_values("rec_refund_cur")` | 否 | |
| 9 | `5` | `退上年` | `_row_values("rec_refund_prev")` | 否 | |
| 10 | `三` | `本月开具发票金额` | **独立计算**（见 4.5） | **是** | 含预收票 |
| 11 | `1` | `本月开收` | `_row_values("inv_open_received")` | 否 | **注意项目名与行 5 同名** |
| 12 | `2` | `本月未收` | `_row_values("inv_open_uncollected")` | 否 | |
| 13 | `3` | `红冲本年` | `_row_values("inv_red_cur")` | 否 | |
| 14 | `4` | `红冲上年` | `_row_values("inv_red_prev")` | 否 | |
| 15 | `四` | `未收款金额` | `uncollected_vals`（见下） | **是** | 合计列=未收总额 |
| 16 | `五` | `业务收入` | `_row_values("income")` | **是** | |
| 17 | `六` | `减：分成报酬及费用` | `exp_sub`（见下） | **是** | |
| 18.. | `1`,`2`,... | 各费用类型名 | `exp[etype]` 逐月 | 否 | 类型顺序见 4.5 |

`rec_keys`（`:93`）= `["rec_open_cur","rec_cur_year","rec_prev_year","rec_refund_cur","rec_refund_prev"]`，与 `MonthData` 前 5 字段对应。

实测 `丁祥锋合伙` 行序列与上表**完全一致**（3=一,4=二,5..9=1..5,10=三,11..14=1..4,15=四,16=五,17=六,18..=费用）。

### 4.5 三个必须单独处理的"非 `_row_values`"计算行

| 行 | Python 计算 | C# 规格（基于 `MonthData` 字段） |
|---|---|---|
| 三（行 10）**`inv_total` 独立计算** | `inv_total_vals = [round(m[mo]["inv_total"],2) for mo in 1..12]`，再 append 合计（`:102-104`） | 逐月取 `st.Months[mo].InvTotal`（=Python `inv_total`），`Math.Round(_,2)`；合计列 = 12 月 `round` 值之和再 round。**与其它 `_row_values` 行不同：它读的是 `InvTotal`，不是 `_row_values(key)`。** 实测行10 合计=154000。 |
| 四（行 15）未收款 | `[round(st["uncollected_month"][mo],2) for mo in 1..12] + [round(st["uncollected_total"],2)]`（`:111`） | 12 月取 `st.UncollectedMonth[mo]`，**合计列取 `st.UncollectedTotal`（不是 12 月之和！）**。实测行15 合计=20000。 |
| 六（行 17）费用小计 | `exp_sub[mo] = round(sum(exp[t][mo] for t in exp),2)`，append 合计（`:122-124`） | 逐月对**全部** `st.Expenses` 类型求和后 round；合计列 = 12 个月值之和再 round。**注意：与月度结算表不同，本表六节不排除任何费用类型**（月度版 `SettlementReportExporter.cs:266-269` 排除了"折旧/银行结息/城建税/教育附加"，本导出器 Python **无排除**——必须照 Python，不排除）。 |

费用类型排序（`:118-128`）：

```
exp_order = {t:i for i,t in enumerate(ordered_types())}
exp_types = sorted(exp.keys(), key=lambda t: (exp_order.get(t,999), t))
```

C# 对应（`ordered_types` 用 `ExpenseCatHelper.OrderedTypes(conn)`，`ExpenseCatHelper.cs:18`）：

```csharp
var order = ExpenseCatHelper.OrderedTypes(conn).Select((t,i)=>(t,i)).ToDictionary(x=>x.t,x=>x.i);
var expTypes = st.Expenses.Keys
    .OrderBy(t => order.TryGetValue(t, out var i) ? i : 999)
    .ThenBy(t => t, StringComparer.Ordinal)   // Python 默认 str 排序 = codepoint = Ordinal
    .ToList();
```

费用行 seq 从 `"1"` 起递增（`:125`）；每行值 = `[round(exp[etype].get(mo,0.0),2) for mo in 1..12] + [合计]`（`:126-127`）。

实测 `丁祥锋合伙` 费用行顺序：`1 分成报酬 / 2 公积金 / 3 社保 / 4 刷卡汽油费`，与 `ExpenseCatHelper.OrderedTypes` 的分类顺序一致。

### 4.6 列宽

| 列 | 宽度 |
|---|---|
| A | 6 |
| B | 22 |
| C..O | 11 |

（`:131-134`，实测 `A=6.0 B=22.0 C=11.0 O=11.0`。**注：diff_xlsx.py 的 7 个维度不比较列宽/行高**，故列宽仅为"基本可用"，但建议照设以免将来加维度时回归。NPOI 用 `ws.SetColumnWidth(colIndex, 宽度*256)`，NPOI 有 `SheetUtil` 或 `SetColumnWidth(i, 6*256)`；具体见工程师实现。）

---

## 5. 文件与 API 设计（Q4）

### 5.1 复用边界（关键决策）

**应从 `SettlementReportExporter.cs` 抽出为共享工具的部分：**

- **无。** 两导出器的格式契约差异极大，**不建议抽公共基类**：
  - 列数不同（月度 5 列，本表 15 列）；
  - 表头不同（`本期/本年累计/备注` vs `1月..12月/合计`）；
  - 标题文本、行高、合并区域、number_format 应用范围、是否有"备注列/多行备注"、页面设置（本表**无页面设置**，见 6.3）均不同；
  - `HasData` 语义不同（月度=单月，本表=全年）。
- **强行抽取会产生大量带 `bool` 开关的"万能函数"，违反可读性与可测性。** 因此**保持重复**，各自独立、各自可读。

**必须新写（本任务核心）：**

- 新建 `csharp/LawFirm.Exporter/PersonSettlementExporter.cs`。
- CLI 扩展（`Program.cs`）。
- 新建验证脚本 `scripts/t3_verify.bat` + `scripts/t3_filediff.py`（见第 6 节）。

**可在文件内私有复用的局部模式（照抄写法，不抽公共）：** NPOI 建 CellStyle 工厂（`Make`，参考 `SettlementReportExporter.cs:129-137`）、建目录（`:40-41`）、FileStream 写盘（`:102-105`）、`Math.Round`。

### 5.2 新文件 `csharp/LawFirm.Exporter/PersonSettlementExporter.cs` — 公开 API

```csharp
namespace LawFirm.Exporter;

/// <summary>
/// 个人结算总表导出器（Pilot T3，C#/NPOI 端口）。
/// 逐格镜像 Python app/exporter/person_settlement_exporter.py 的 _write_ws /
/// _row_values / _subtotal / _has_data / export_one / export_all。
/// 输出契约：每人一个 xlsx（sheet = 各身份[有数据才出] + 汇总[恒出]）。
/// </summary>
public static class PersonSettlementExporter
{
    /// <summary>
    /// 导出单个员工的个人结算总表（每人一文件多 sheet）。
    /// 对应 Python export_one(person, path, year)。
    /// </summary>
    /// <param name="conn">只读数据库连接</param>
    /// <param name="person">员工姓名（Build 的字典 key）</param>
    /// <param name="path">输出文件绝对/相对路径（会自动创建父目录）</param>
    /// <param name="year">结算年份</param>
    /// <returns>实际写出的文件路径</returns>
    public static string ExportOne(SqliteConnection conn, string person, string path, int year);

    /// <summary>
    /// 为全部员工生成个人结算总表（每人一文件），返回文件路径列表。
    /// 对应 Python export_all(outDir, year)。
    /// 人员集合 = SettlementEngine.Build(conn, year).Keys，按 StringComparer.Ordinal 排序
    ///（对应 Python sorted() 的 codepoint 序，保证与 golden 文件名集合一致）。
    /// </summary>
    /// <returns>生成的文件路径列表（顺序 = 排序后的人员顺序）</returns>
    public static List<string> ExportAll(SqliteConnection conn, string outDir, int year);
}
```

**内部私有成员（建议，非强制签名）：**

- `private static void WriteWs(ISheet ws, string person, PersonSettlement st, int year, string typeLabel)`
- `private static bool HasDataYear(PersonSettlement? st)`
- `private static double[] RowValues(PersonSettlement st, int[] months, Func<MonthData,double> key)`
  - 返回 13 个值：12 月 round(2) + 合计 round(2)。
- `private static double[] Subtotal(PersonSettlement st, int[] months, Func<MonthData,double>[] keys)`
- `private static string SafeFileName(string person)` → `person.Replace("/","_").Replace("\\","_").Trim()`，空则 `"未命名"`。

**`ExportAll` 的 outDir 约定**：调用方传 `csharp/out/person_settlement`（见 2.2），`ExportAll` 内部 `Directory.CreateDirectory(outDir)`。

### 5.3 CLI 扩展（绝不破坏现有路径）

现有 `Program.cs`（`:25-71`）仅支持 `--year/--month/--out`，且**默认行为 = 月度结算表导出**（`SettlementReportExporter.ExportReport`，`:56`）。已有 `t2_verify.bat` 依赖该路径（`t2_verify.bat:83`）——**必须原样保留**。

**扩展方案（新增一个互斥的 `--report` 选择器，默认值保持 `month`）：**

| 参数 | 取值 | 默认 | 说明 |
|---|---|---|---|
| `--report` | `month` \| `person` | `month` | **新增**。`month`=现有月度结算表（行为不变）；`person`=新的个人结算总表。 |
| `--year` | int | 当前年 | 复用 |
| `--month` | int | 12 | 仅 `--report month` 时使用；`--report person` 时忽略 |
| `--out` | 路径 | `csharp/out/settlement_report_csharp.xlsx` | `--report month` 时的单文件路径 |
| `--out-dir` | 目录 | `csharp/out/person_settlement` | **新增**，仅 `--report person`：个人总表的输出目录 |
| `--person` | 姓名（可选，可多次） | 无 | **新增可选**，仅 `--report person`：只导出指定人（对应 `export_one`）；缺省 = 全员（`export_all`） |

伪代码（在现有 switch 中加 case，末尾按 `report` 分派）：

```csharp
string report = "month";
string outDir = "csharp/out/person_settlement";
var onlyPersons = new List<string>();
// ... 解析 --report / --out-dir / --person ...
if (report == "person")
{
    string result = onlyPersons.Count == 0
        ? string.Join(", ", PersonSettlementExporter.ExportAll(conn, outDir, year))
        : string.Join(", ", onlyPersons.Select(p =>
              PersonSettlementExporter.ExportOne(conn, p,
                  Path.Combine(outDir, $"个人结算总表_{SafeFileName(p)}.xlsx"), year)));
    Console.WriteLine($"[OK] 已导出 {result}");
    return 0;
}
// 否则：现有月度结算表逻辑，逐字不动
```

**铁律**：`--report` 缺省 = `month`，**现有 `t2_verify.bat` 与所有已 PASS 的命令行零改动**。新增参数不得改变 `--year/--month/--out` 的既有语义。

---

## 6. 验证策略（Q5）

### 6.1 现状与问题

- `diff_xlsx.py` 是**单文件对单文件**（`main(argv)` 收 `baseline`、`candidate` 两路径，`:329-357`）。
- `t2_diff.py` 据 `--year/--month` 拼**单条** golden 路径（`:37-39`），再调 `diff_xlsx.main`。
- golden 是**每人一文件、42 个**（第 2 节）。

因此需要一层"**目录配对 + 批量调用 diff_xlsx**"的编排，**不修改 `diff_xlsx.py` 本体**（它是被验证的可信基准工具）。

### 6.2 新增脚本规格

**`scripts/t3_filediff.py`（UTF-8 安全，负责中文路径拼接与批量 diff）：**

- 参数：`--year`、`--golden-dir tests/golden/person_settlement_exporter`、`--candidate-dir csharp/out/person_settlement`、`--verbose`、`--json <汇总json>`。
- 逻辑：
  1. 列出 golden 目录下所有 `*.xlsx`，取**文件名集合**（不含路径）作为基准。
  2. 列出 candidate 目录下所有 `*.xlsx`，比对**集合是否一致**（多文件/少文件各记一条差异）。
  3. 对**每个同名文件**，调用 `diff_xlsx.main([golden/name, candidate/name, ...])`，收集返回码与 JSON。
  4. 汇总：任一文件非 0 → 整体非 0（退出码沿用 0/1/2/3 约定）。
  5. 打印 `[PASS]/[FAIL] <文件名>` 逐文件结论 + 总差异数。
  6. **【新增断言建议】** 除调用 `diff_xlsx` 外，额外**直读 C# 产物每个 sheet 的 XML，断言「没有 `<pageSetup>` 元素」**（或等价地在 diff 报告里显式检查 `page_setup` 的 `orientation` 维度）。**理由**：golden 无 pageSetup（orientation 读回 None），而 `diff_xlsx` 对 orientation **不做归一化**，一旦 C# 侧误调 `Landscape=false` 就会落盘 `portrait` → 全红。该坑最隐蔽、最易静默踩到，故加一道独立断言提前暴露（`zipfile` + 正则 `<pageSetup` 即可，无需 openpyxl）。
- **不得**在 `.bat` 里出现中文（沿用 `t2_verify.bat:10-13` 的 ASCII-only 铁律）。

**`scripts/t3_verify.bat`（纯 ASCII 编排，镜像 `t2_verify.bat`）：**

```
[1/4] dotnet build csharp\lawfirm.sln
[2/4] dotnet run --project csharp\LawFirm.Cli -- --report person --year %YEAR% --out-dir csharp\out\person_settlement
[3/4] python scripts\dump_golden.py --year %YEAR% --only person_settlement
[4/4] python scripts\t3_filediff.py --year %YEAR% --candidate-dir csharp\out\person_settlement --verbose
```

> **注意** `dump_golden.py --only person_settlement`：`dump_golden.py` 支持 `--only`（`:200-203`），且它**只允许在用户本机 venv 运行**（`:5-6`）。本机验证时用 `%USERPROFILE%\.lawfirm_venv\Scripts\python.exe`（与 `t2_verify.bat:59` 同款定位逻辑）。

### 6.3 必须验证的维度（来自 `diff_xlsx.py` 的 7 维）

| 维度 | diff_xlsx 实现 | 对本导出器的要求 |
|---|---|---|
| 1. sheet 名/顺序 | `compare_workbooks:185-195` | 身份 sheet（有数据才出，顺序 合伙/聘用/兼职）+ 汇总恒出 |
| 2. 列名序列 | `_detect_header_row` + `_header_sequence`（`:129-148`） | 第 2 行 15 列表头逐格一致 |
| 3. 合并区域 | `_merged_list`（`:162-164`） | 仅 `A1:O1`，**不可多不可少** |
| 4. 合计/汇总/结余行 | `_summary_rows` 关键词 `合计/汇总/结余`（`:45,151-159`） | 本表 B 列**无** `合计/汇总` 字样（表头"合计"在 O 列第 2 行，其值列会被当摘要行扫描）。**实测需核对**：行 2 含 "合计" 字符串 → 会被 `_summary_rows` 抓入做整行比较；只要两侧行 2 一致即无差异。 |
| 5. 数字格式 | 逐格 `number_format`（`:216-219`，仅当有值一侧非 None 时比较） | C..O 全为 `#,##0.00`；A/B 不设 |
| 6. 页面设置 | `_compare_page_setup`（`:250-283`）；**orientation 不归一化**，fit 有 None≡1 归一，只比 4 边 margin（不含 header/footer） | **见下 6.4，最大风险点**：1) 必须显式写 margin `0.75/0.75/1.0/1.0`（double）；2) **禁止**设 `Landscape`/orientation |
| 7. 逐格值 | `_cell_map` 全网格（`:117-126,207-219`） | 所有值 + None 逐格一致 |

### 6.4 ⚠️ 页面设置风险（`diff_xlsx.py` 第 6 维）——**极高优先级**（U1 已定案）

**源码 + 实测事实（team-lead 已用直读 XML 全扫 80 个 sheet 复现）：**

- Python 导出器 `_write_ws` **完全没有触碰页面设置**（`person_settlement_exporter.py:48-134` 无任何 `page_setup`/`page_margins` 赋值）。
- 实测 golden 全部 80 个 sheet：**含 `<pageSetup>` 者 0 个**；**含 `<pageMargins>` 者 80 个**，且恒为
  `<pageMargins left="0.75" right="0.75" top="1" bottom="1" header="0.5" footer="0.5"/>`（openpyxl 默认页边距）。
- `diff_xlsx.py` 第 6 维读 `page_setup.orientation/fitToWidth/fitToHeight` 与 `page_margins.left/right/top/bottom`（`:265-283`）。**关键（证据 2）：`orientation` 不做任何归一化**（`attrs` 里 `orientation` 直接取值比较），而 `fitToWidth/fitToHeight` 走 `fit(v){ return 1 if v is None else v; }`（`:261-263`）。→ `orientation` 是 `None` vs `"portrait"` **会直接报差异**。
- **`diff_xlsx.py` 只比 4 边 margin，不比 header/footer**（证据 4）：`:276` 为 `for side in ("left","right","top","bottom")`。旁证：现有 C# 空数据产物写 `header=0.3 footer=0.3`，golden 是 `0.5/0.5`，**二者不一致却 PASS** → 证明 header/footer 不参与比对。

**openpyxl 读回的期望值：** `orientation=None`、`fitToWidth=None`（`fit()` 归一为 1）、`fitToHeight=None`（→1）；margins `0.75/0.75/1.0/1.0`。

**NPOI 默认值：** 若 C# 侧新建 `XSSFWorkbook` 后**不设置任何页边距**，NPOI 默认落盘 `0.7/0.7/0.75/0.75`（与 `SettlementReportExporter.cs:52-55` 注释记载的事实一致），**与 golden 的 0.75/0.75/1.0/1.0 不一致 → 4 处 margin 差异 → FAIL**。

---

#### 6.4.1 🔴 U1 定案：**禁止设置 `PrintSetup.Landscape`（含 `= false`）**

**结论（team-lead 源码+实测定案，覆盖此前"需实测确认"的假设）：**

- **绝对不能**调用 `ws.PrintSetup.Landscape = false;`（或任何设置 orientation/横纵向的 API）。
- **原因（证据 3 —— 比 margin 更隐蔽的坑）**：现有 `SettlementReportExporter.cs:211` 写了 `ws.PrintSetup.Landscape = false`，它能 PASS **只是因为它的 golden（`2025年12月`）恰好**也带 `orientation="portrait"`。而 **person_settlement 的 golden 完全没有 `<pageSetup>` → openpyxl 读回 `orientation=None`**。若照抄 `Landscape = false`，NPOI 会落盘 `orientation="portrait"` → `None != "portrait"` → **80 个 sheet 全部报 `orientation` 差异**（且由于该坑只在"golden 无 pageSetup"时暴露，极易静默踩到）。
- **正确做法**：**完全不碰 orientation**，不调用任何 `PrintSetup`/orientation 相关 API，让 NPOI 保持默认（不落盘 `<pageSetup>`），openpyxl 读回 `None`，与 golden 一致。
- 同理 `FitToPage`/`FitWidth`/`FitHeight` 也**不要设置**（golden 无 pageSetup，不落盘 fit 属性即最安全；`fit()` 把 None 归一为 1）。

```csharp
// ✅ 正确：只写页边距，绝不触碰 orientation / fit
// ⚠️ 禁止：ws.PrintSetup.Landscape = false;      （会让 80 sheet 全红）
// ⚠️ 禁止：ws.FitToPage = true; / PrintSetup.FitWidth = 1; 等（无需设置）
ws.SetMargin(MarginType.LeftMargin, 0.75);    // double 字面量！
ws.SetMargin(MarginType.RightMargin, 0.75);
ws.SetMargin(MarginType.TopMargin, 1.0);       // 注意 top/bottom 是 1，不是 0.75
ws.SetMargin(MarginType.BottomMargin, 1.0);
ws.SetMargin(MarginType.HeaderMargin, 0.5);    // 不参与比对，照 golden 写保持一致
ws.SetMargin(MarginType.FooterMargin, 0.5);    // 不参与比对
```

> 复核记录：现有月度结算表 golden 是有 `orientation="portrait"` 的（故它显式 `Landscape=false` 才 PASS）；本导出器的 golden 无 pageSetup，两者页面设置契约**不同**，**不可照抄月度导出器的页面设置代码**。

#### 6.4.2 margin 规格精确化

| 边 | 值 | 是否参与 diff |
|---|---|---|
| left | `0.75`（double） | ✅ 参与 |
| right | `0.75`（double） | ✅ 参与 |
| top | **`1.0`**（double，**不是 0.75**） | ✅ 参与 |
| bottom | **`1.0`**（double，**不是 0.75**） | ✅ 参与 |
| header | `0.5`（建议写，照 golden） | ❌ 不参与 |
| footer | `0.5`（建议写，照 golden） | ❌ 不参与 |

- **必须用 `double` 字面量**（`0.75` / `1.0`），不能写 `0.75f` / `0.3f` —— `SettlementReportExporter.cs:220` 已记载 float 字面量会存成 `0.30000001192092896` 的精度坑。
- 只提供一种处理方案：**显式写 4 边 margin（0.75/0.75/1.0/1.0）**；不修改 `diff_xlsx.py`（保持被验证基准工具原样）。

### 6.5 golden 数量 vs 目录：`公共/暂估/车辆/管理人补助` 的隐含行为

实测这些 person **只有汇总 sheet**（三个身份 `_has_data` 全 False）。这要求 `HasDataYear` 对"仅费用/无收款无开票"的人返回 False（身份 sheet 不出），而汇总恒出。工程师实现后须用 golden 逐一核对 sheet 集合。人员集合来源 = `Build(conn, year).Keys`，与 CLI 现有"全选"逻辑（`Program.cs:53-54`）完全一致——**可直接复用该排序写法**（`OrderBy(k=>k, StringComparer.Ordinal)`）。

---

## 7. 依赖与风险

### 7.1 依赖

- **NPOI**（已用于 `SettlementReportExporter.cs`，无需新增包）；`Microsoft.Data.Sqlite`（经 `LawFirm.Data`，已有）。
- 验证侧：`openpyxl`（`diff_xlsx.py` 已依赖）、Python venv `%USERPROFILE%\.lawfirm_venv`。
- 无第三方新增依赖。

### 7.2 风险清单

| ID | 风险 | 影响 | 处理 |
|---|---|---|---|
| R1 | 页面设置维度（第 6 维）误报：margin 不写 → 4 处差异；**误设 `Landscape=false` → orientation 全红（80 sheet）** | 全 FAIL | 按 6.4：显式写 margin `0.75/0.75/1.0/1.0`（double）；**绝不触碰 orientation/`Landscape`**（见 6.4.1 U1 定案） |
| R2 | 空数值格写成 0 而非 None | row3 全部 C..O + 其它 None 格 → 大量 diff | NPOI 对 null 值**只套样式不 SetCellValue**（参考 `SettlementReportExporter.cs:177-181`） |
| R3 | 标题分隔符用普通空格 | 42 文件 × 每 sheet 的 A1 值全变 → 全 FAIL | 用全角空格 `\u3000` |
| R4 | 六节费用行**误加**类型排除（照抄月度版的 exclude） | 费用行数/值不一致 | 本导出器**不排除任何费用类型** |
| R5 | 文件名非法字符替换遗漏 | 与 golden 文件名不匹配 → 配对 diff 全缺 | `Replace("/","_").Replace("\\","_").Trim()`，空→`未命名` |
| R6 | `四`行合计误用"12 月之和"而非 `UncollectedTotal` | 合计列不一致 | 合计列取 `st.UncollectedTotal` |
| R7 | 三行 `inv_total` 误用 `_row_values` | 行 10 不一致 | 独立读 `InvTotal`（`MonthData.InvTotal`） |
| R8 | `_has_data` 误复用月度单月版 | 身份 sheet 出/不出错位 | 实现全年版 `HasDataYear`，键集合**不含 `InvTotal`**、不含 `UncollectedMonth` |
| R9 | sheet 未删除默认空页 | candidate 多出 `Sheet1` → sheet 维度差异 | NPOI `new XSSFWorkbook()` 默认无 sheet，无需删；确认不 `CreateSheet` 多余页 |
| R10 | float 字面量致页边距精度噪声（如 0.3f） | margin 比较超容差 | 用 `double` 字面量（`SettlementReportExporter.cs:220` 已记载该坑） |

### 7.3 待明确事项（U — 需用户/工程师澄清或实测）

| ID | 事项 | 说明与建议 |
|---|---|---|
| U1 | ~~NPOI 默认 `orientation` 落盘值~~ **✅ 已定案（team-lead 源码+实测定案）** | **禁止设置 `PrintSetup.Landscape`（含 `= false`），也不设 `FitToPage/FitWidth/FitHeight`**。原因：`diff_xlsx.py` 对 `orientation` **不做归一化**，而 golden 全部 80 个 sheet **无 `<pageSetup>` → 读回 `orientation=None`**；一旦显式设 `Landscape=false`，NPOI 落盘 `orientation="portrait"` → `None != "portrait"` → 80 sheet 全红。（现有月度导出器 `SettlementReportExporter.cs:211` 写 `Landscape=false` 能 PASS，只因**它的 golden 恰好有 `portrait`**——两导出器页面设置契约不同，**不可照抄**。）正确做法：只写 4 边 margin，完全不碰 orientation。详见 6.4.1。 |
| U2 | **验证脚本落地位置** | 建议新增 `scripts/t3_verify.bat` + `scripts/t3_filediff.py`，**不改** `t2_*`。请用户确认命名（t3 vs 复用 t2 加参数）。 |
| U3 | **输出目录默认值** | 规格建议 `csharp/out/person_settlement`；若用户希望与 golden 同构目录名 `person_settlement_exporter`，请指定。 |
| U4 | **`--report person` 下 `--out` 是否弃用** | 规格用 `--out-dir`；若用户希望 `--out` 兼容单文件语义，请指定。 |
| U5 | **WPF UI 后续接入** | 本次只做 CLI；WPF 侧调用 `ExportAll`/`ExportOne` 的时机（目录选择、进度）待 UI 任务再定。 |
| U6 | ~~`ExportAll` 是否跳过 0 人~~ **✅ 已确认** | 按 Python 行为执行：**0 人 = 0 文件、不产占位文件**（`export_all` 对空字典返回空列表）。**不**照抄 `SettlementReportExporter` 的"空名单兜底占位 xlsx"逻辑（`:43-65`）——因 golden 是每人一文件，占位文件会与 golden 集合不匹配。 |
| U7 | **`A` 列 bold 与否** | diff 不比较字体，故不影响验证；按 Python（A 列不加粗）实现。仅为记录。 |

---

## 8. 任务分解（供工程师执行，按依赖排序）

> 遵循"≤5 任务、每任务 ≥3 文件、首个任务为基础设施"的约束。本任务为**单导出器移植**，文件面较小，实际落地为 3 个任务。

### T01 —— 导出器实现（P0）
- **源文件**：新建 `csharp/LawFirm.Exporter/PersonSettlementExporter.cs`；只读参考 `SettlementEngine.cs` / `SettlementReportExporter.cs` / `ExpenseCatHelper.cs` / `person_settlement_exporter.py`。
- **依赖**：无（引擎已就绪）。
- **内容**：`ExportOne`/`ExportAll`/`WriteWs`/`HasDataYear`/`RowValues`/`Subtotal`/`SafeFileName`；六节行构成；三处独立计算行；格式表（4 节）；页边距按 6.4.2 显式写 `0.75/0.75/1.0/1.0`（double），**禁止**触碰 orientation/`Landscape`（6.4.1）。

### T02 —— CLI 接入（P0）
- **源文件**：修改 `csharp/LawFirm.Cli/Program.cs`。
- **依赖**：T01。
- **内容**：新增 `--report/--out-dir/--person`；默认 `month` 行为不变；`person` 分支调 `ExportAll`/`ExportOne`。

### T03 —— 验证流水线 + 执行（P0）
- **源文件**：新建 `scripts/t3_filediff.py`、`scripts/t3_verify.bat`；只读 `scripts/diff_xlsx.py` / `dump_golden.py` / `t2_verify.bat`。
- **依赖**：T01、T02。
- **内容**：目录配对批量 diff；build→run→golden→diff 一键脚本；**额外直读 XML 断言 C# 产物无 `<pageSetup>`**（6.2 第 6 条）；**在用户本机 venv 跑通并对 42 个 golden 全绿**。

---

## 9. 关键源码行号索引（便于工程师核对）

| 主题 | 文件:行 |
|---|---|
| 标题/表头/put/format | `person_settlement_exporter.py:48-84` |
| 六节行构成 | `person_settlement_exporter.py:85-128` |
| 三行独立 inv_total | `person_settlement_exporter.py:102-104` |
| 四行 uncollected | `person_settlement_exporter.py:111` |
| 六行费用小计/排序 | `person_settlement_exporter.py:118-128` |
| 列宽 | `person_settlement_exporter.py:131-134` |
| export_one | `person_settlement_exporter.py:137-154` |
| export_all + 文件名替换 | `person_settlement_exporter.py:157-166` |
| `_has_data` | `person_settlement_exporter.py:32-45` |
| 引擎 API | `SettlementEngine.cs:23-25` |
| PersonSettlement / MonthData | `SettlementEngine.cs:446-470` |
| NPOI 建样式/空 seq 处理 | `SettlementReportExporter.cs:129-143,177-181` |
| NPOI 页边距默认值坑 | `SettlementReportExporter.cs:52-60,220-226` |
| **Landscape 误设坑（勿照抄）** | `SettlementReportExporter.cs:211`（`Landscape=false`；月度 golden 有 portrait 才 PASS，本导出器禁止） |
| diff 7 维 | `diff_xlsx.py:179-283` |
| page_setup 归一 | `diff_xlsx.py:250-283` |
| golden 生成 | `dump_golden.py:97-101,200-203` |
| CLI 现有逻辑 | `Program.cs:25-71` |
| 人员名单排序 | `Program.cs:53-54` |
