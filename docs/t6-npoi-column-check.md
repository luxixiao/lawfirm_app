# T6 三件验证之①「NPOI 列名 100% 一致」独立核查报告

- **核查人**：严过关（Yan，QA）
- **核查对象**：`csharp\LawFirm.Exporter\PersonSettlementExporter.cs` → `csharp\out\person_settlement\*.xlsx`（42 份）
- **次要对象**：`csharp\LawFirm.Exporter\SettlementReportExporter.cs` → `csharp\out\settlement_report_csharp.xlsx`
- **判定依据**：`scripts\diff_xlsx.py`（未修改，7 个比对维度）
- **核查性质**：独立实测。本报告**不采信「读源码觉得一样」**，全部结论以 xlsx 落盘 XML 为准。
- **未修改任何代码**。

---

## 结论摘要

| 项目 | 结论 |
|---|---|
| **① 列名 100% 一致（硬门槛）** | **✅ PASS** |
| 证据强度 | 42/42 文件、80/80 sheet、**15/15 列逐字相等**，且 Golden 与 C# **双向**产物比对 7 维 **0 差异** |
| 逐格覆盖 | 23,055 个坐标、1,681 行，逐格值 0 差异 |
| 7 维之外发现的差异 | **2 项**（见表 DIFF-1 / DIFF-2）——不影响本次硬门槛判定，但需登记 |
| 证据缺口 | **1 项**（月度结算表真实报表未被产物覆盖，见第四节） |

**一句话**：列名这道硬门槛，是真 PASS，不是 rubber-stamp——我用 independent 的 XML 解析把它称了一遍，并且在过程中**抓出了 2 个 diff_xlsx.py 看不见的真实差异**。

---

## 一、验证方法与为什么可信

沙箱限制：无 `openpyxl` / 无 `xlrd` / 无可用 Python（`python.exe` 是 WindowsApps 存根），因此**不能**跑 `diff_xlsx.py`，也**不能**重跑 Python 导出。

替代方案（两条互相印证的证据链）：

1. **直接解包读 XML**：用 .NET `System.IO.Compression.ZipFile` + `XmlDocument` 读 xlsx 内部的
   `xl/workbook.xml`、`xl/sharedStrings.xml`、`xl/worksheets/sheetN.xml`、`xl/styles.xml`，
   把 sheet 名、首行列名、合并区域、数字格式码、pageSetup/pageMargins 全部**实测取值**。
2. **关键发现——仓库里已签入 Golden 基准**：
   `tests\golden\person_settlement_exporter\`（42 份 Python 侧产物）、
   `tests\golden\settlement_report_exporter\`（含 73 sheet 的真实月度结算表）。
   于是可以**绕过 openpyxl，直接做 Golden↔C# 的双向产物级比对**——这比读源码强一个数量级。

> 这条路的发现是整个核查的转折点：原本只能「对照源码声称一致」，现在是「两份落盘文件逐格对撞」。

我按 `diff_xlsx.py` 的逻辑**逐条复刻**了 7 个维度（含那个关键的陷阱：
`fitToWidth/fitToHeight` 的 `None→1` schema 默认归一，以及 `number_format` 只在「至少一侧有值」时才比较）。

**自证覆盖率的守卫**：比对脚本内置 coverage guard——若实际参与比较的坐标数为 0，PASS 就是假的。
第一次运行时它正好抓到我自己脚本的 bug（`Get-PathNodes` 未定义导致单元格表全空，
当时只有 4 个 sheet 参与比较），修好后才有下面这些数字。最终覆盖率：

```
sheets compared      = 80
golden rows covered  = 1681
coords examined      = 23055
coords non-empty both= 23055   ← 两侧占位完全对称，无一格「一边有值一边空」
```

---

## 二、逐项证据表

### 表 1：sheet 名 / 顺序 —— ✅ PASS

| 项 | Golden（Python） | C#（NPOI） | 判定 |
|---|---|---|---|
| 文件数 | 42 | 42 | PASS |
| sheet 总数 | 80 | 80 | PASS |
| 命名规则 | `{姓名}{合伙/聘用/兼职}`（有数据才出）+ `{姓名}汇总`（恒出） | 同 | PASS |
| 顺序 | 身份 sheet 在前、汇总 sheet 在后 | 同 | PASS |
| 差异数 | — | — | **0** |

示例（`陈娟.xlsx`）：Golden `陈娟合伙 , 陈娟汇总` ↔ C# `陈娟合伙 , 陈娟汇总`。

### 表 2：首行列名逐字比对 —— ✅ PASS（本次硬门槛）

- Python 期望值来源：`app\exporter\person_settlement_exporter.py` L59–60
- C# 实测来源：42 份 xlsx 中 **80 个 sheet 全部**的第 2 行

| # | 列 | Python 期望 | C# 实测 | 判定 |
|---|---|---|---|---|
| 1 | A | 序号 | 序号 | PASS |
| 2 | B | 项目 | 项目 | PASS |
| 3 | C | 1月 | 1月 | PASS |
| 4 | D | 2月 | 2月 | PASS |
| 5 | E | 3月 | 3月 | PASS |
| 6 | F | 4月 | 4月 | PASS |
| 7 | G | 5月 | 5月 | PASS |
| 8 | H | 6月 | 6月 | PASS |
| 9 | I | 7月 | 7月 | PASS |
| 10 | J | 8月 | 8月 | PASS |
| 11 | K | 9月 | 9月 | PASS |
| 12 | L | 10月 | 10月 | PASS |
| 13 | M | 11月 | 11月 | PASS |
| 14 | N | 12月 | 12月 | PASS |
| 15 | O | 合计 | 合计 | PASS |

**关键量化结论**：全部 80 个 sheet 的表头时序**只有 1 种取值**，且与 Python 期望序列完全相同：

```
序号|项目|1月|2月|3月|4月|5月|6月|7月|8月|9月|10月|11月|12月|合计
```

- header 维度差异：**0 / 80**
- 列数异常（≠15）：**0 / 80**
- 没有任何一处多空格、全角/半角混用、或不可见字符——全部 80 个 sheet 的 15 个表头格在
  Golden↔C# 双向逐格比对中 **`cell` 差异 = 0**，表头本身也是被逐格覆盖的。

### 表 3：合并单元格 —— ✅ PASS

| 项 | Golden | C# | 判定 |
|---|---|---|---|
| 合并区域集合 | `A1:O1` | `A1:O1` | PASS |
| 差异数 | — | — | **0 / 80** |

C# 侧**仅**标题行合并，无多余合并（这正是容易错的点）。

### 表 4：数字格式 —— ✅ PASS

| 项 | Golden | C# | 判定 |
|---|---|---|---|
| C..O 数值格 | `#,##0.00` | `#,##0.00` | PASS |
| A/B 列 | `General` | `General` | PASS |
| 对齐 | right | right | PASS |
| 差异数 | — | — | **0**（23,055 坐标全比对） |

C# 侧向 empty 数值格（如「上年结余结转」行）**只套样式不写值**，与 Golden 的 `None` 语义一致，
未出现 `t="n"` 无值或 `0` 的伪值差异。

### 表 5：pageSetup / 页边距 —— ✅ PASS（U1 定案被正确落实）

| 项 | Golden | C# | 判定 |
|---|---|---|---|
| `<pageSetup>` 元素 | **不存在** | **不存在（80/80）** | PASS |
| orientation | None | None | PASS |
| fitToWidth / fitToHeight | 归一为 1 | 归一为 1 | PASS |
| margin left / right | 0.75 / 0.75 | 0.75 / 0.75 | PASS |
| margin top / bottom | 1 / 1 | 1 / 1 | PASS |
| margin header / footer | 0.5 / 0.5 | 0.5 / 0.5 | PASS |
| 差异数 | — | — | **0 / 80** |

**这是最容易踩雷的一维**，值得说清楚：
`PersonSettlementExporter.cs` 主动**没有**去写 `orientation`（而 `SettlementReportExporter.cs:211`
写了 `Landscape=false` → 落盘 `orientation="portrait"`）。因为本导出器的 Golden **全部 80 个 sheet 无 `<pageSetup>`**，
而 `diff_xlsx.py::_compare_page_setup` **不对 orientation 做归一化**——一旦 C# 落盘 portrait，
就会 80 个 sheet 全红。实测确认：C# 侧 80/80 **确实没有 `<pageSetup>`**，与 Golden 一致。
两个导出器契约不同、不可照抄这一点，做法是**正确的**。

### 表 6：逐格值（全量）—— ✅ PASS

| 维度 | 差异数 |
|---|---|
| cell（值） | 0 |
| number_format | 0 |
| summary_row（合计/汇总/结余行） | 0 |
| sheet | 0 |
| merged | 0 |
| header | 0 |
| page_setup | 0 |

42 对文件 **全部 clean（42/42）**。

---

## 三、DIFF 清单（在 7 个维度**之外**，diff_xlsx.py 抓不到，但确实存在差异）

### DIFF-1｜表头行丢失「加粗 + 灰色底纹」 —— 🟡 视觉回归（我判：应修）

| 项 | Golden（Python） | C#（NPOI） |
|---|---|---|
| 表头行第 2 行样式 | `bold=True`，`fill=solid / rgb=00F2F2F2`，`align=center` | `bold=False`，`fill=none`，`align=center` |

**证据**（`陈娟.xlsx / 陈娟合伙` sheet，单元格 A2/B2/C2/O2，四格同一结论）：

```
row2 col1: gold=[序号] csharp=[序号]
      gold  style: bold=True sz= fill=solid/00F2F2F2 align=center numFmt=General
      csharp style: bold=False sz=11 fill=none/ align=center numFmt=General
      STYLE IDENTICAL? False
```

- **影响范围**：全部 80 个 sheet 的第 2 行（15 格 × 80）。
- **为什么 diff_xlsx.py 抓不到**：它只比对「值 / number_format / merged / header / summary_row / page_setup / sheet」，**不比对 font 与 fill**。所以 7 维全绿的同时，用户打开 xlsx 会看到一张「表头不加粗、没有灰底」的表。
- **根因（已定位到行）**：
  - `PersonSettlementExporter.cs:194`：`var centerText = Make(false, HorizontalAlignment.Center, false);`（bold=false）
  - `PersonSettlementExporter.cs:222`：`c.CellStyle = centerText;   // 加粗 + 居中 + 填充 + 边框`
  - 该行**注释声称**「加粗 + 居中 + 填充 + 边框」，但代码传的是 `bold=false`，且 `Make(...)` 内部**从未设置过任何 fill**（既无 `FillPattern` 也无 `FillForegroundColor`）。注释与实现不符，这是 bug 的直接来源。
  - 对照：`PersonSettlementExporter.cs:190-198` 的 `Make` 工厂确实没有 fill 分支，而 C# 另两处常量（`LEFT_HEAD_FILL` 用途等）也无表头填充。
- **对照参考（正确做法就在同仓库）**：`SettlementReportExporter.cs:139` 定义了 `centerTextBold`，并在 L169 用于第 3 行表头——**那个导出器做对了**，本导出器漏了。
- **修复建议（不实施，仅供抉择）**：新增 `centerTextBold` 并给表头行加 `FillForegroundColor = IndexedColors.Grey25Percent`（或 `F2F2F2`）+ `FillPattern = SolidForeground`，第 2 行改用该样式即可。注意 Python 侧 `HEAD_FILL = PatternFill("solid", fgColor="F2F2F2")`。

### DIFF-2｜月度结算表「空名单占位 sheet」的 header/footer 页边距不一致 —— 🟢 轻微

| 项 | Golden 占位 sheet | C# 占位 sheet |
|---|---|---|
| margin left/right/top/bottom | 0.75 / 0.75 / 1 / 1 | 0.75 / 0.75 / 1 / 1 |
| **margin header / footer** | **0.5 / 0.5** | **0.3 / 0.3** ❌ |

- **为什么 diff_xlsx.py 抓不到**：`_compare_page_setup` 只比对 `left/right/top/bottom` 四边，**不比对 header/footer**。
- **根因**：`SettlementReportExporter.cs` 的空名单分支（L57–60）只显式写了 Left/Right/Top/Bottom，
  **没写 Header/Footer**，于是落盘用了 NPOI 默认值 **0.3**；而 openpyxl 侧默认值是 **0.5**。
  同一文件的注释里已经解释了「NPOI 默认 0.7/0.7/0.75/0.75 所以要显式写——这四处写对了，
  但漏了 header/footer 这一对。
- **仅影响** `persons == null || count == 0` 的兜底分支；正常路径 `WriteSheet`（L225–226）已正确写了 0.5/0.5。
- **风险**：一旦将来 `diff_xlsx.py` 扩展到比对 header/footer，此处会立刻报错。建议顺手补两行。

---

## 四、证据缺口 / 未覆盖项（必须在本机补验）

### GAP-1｜月度结算表的真实列名没有被现有产物覆盖

`csharp\out\settlement_report_csharp.xlsx` 目前是**空名单兜底产物**：

```
sheets = 1     names: 无数据
A1=[无结算数据]
A2=[2026年无结算人员数据，未生成结算表。]
pageSetup   = <ABSENT>
pageMargins = left=0.75 right=0.75 top=1 bottom=1 header=0.3 footer=0.3
```

即：它是用 **year=2026** 且**人员名单为空**导出的，走的是 `SettlementReportExporter.cs:43` 的兜底分支，
**根本没经过 `_write_sheet` 写表头**。所以：

- Python 侧真实期望表头（L157）：`序号 | 项目 | 本期 | 本年累计 | 备注`（在第 **3** 行）
- C# 侧 `Header`（L33）：同上；按 `SettlementReportExporter.cs:164-170` 写入第 3 行
- 但**这份产物无法验证它**。我只验证了「期望值本身是对的」：Golden
  `tests\golden\settlement_report_exporter\2025年12月结算表.xlsx` 有 **73 个 sheet，全部 73/73 行3 表头 = PASS**：

```
[PASS] 丁祥锋合伙 row3=[序号|项目|本期|本年累计|备注] maxC=5
...（73/73 全部 PASS）
```

Golden 侧该报表还顺带确认了另外三项契约，C# 源码与之相符：
`merges=A1:E1`、`pageSetup=orientation=portrait fitToWidth=1 fitToHeight=1`、
`pageMargins=left=0.25 right=0.25 top=0.3 bottom=0.3 header=0.5 footer=0.5`。

**结论**：月度结算表列名需在**本机按 year=2025 重导一次真实名单**才能闭环（命令见下节）。

---

## 五、用户本机验证命令（一条命令 + 判读方法）

在本机 `cmd.exe`（**不要**用含中文的多行粘贴，`cmd` 会按 GBK 解析导致乱码 —— 见 `scripts\t2_verify.bat` 的注释），仓库根目录 `C:\Users\Bingo\Desktop\buddy2\lawfirm_app` 下执行：

**① 个人结算总表（本次 T6 之①，42 份逐对比对）**

```bat
cd /d C:\Users\Bingo\Desktop\buddy2\lawfirm_app
if not exist "csharp\out" md "csharp\out"
%USERPROFILE%\.lawfirm_venv\Scripts\python.exe scripts\dump_golden.py --year 2025 --only person_settlement && dotnet build csharp\lawfirm.sln -m:1 -nodereuse:false && dotnet run --project csharp\LawFirm.Cli -- --report person --year 2025 --out-dir csharp\out\person_settlement && for /f "delims=" %F in ('dir /b "tests\golden\person_settlement_exporter\*.xlsx"') do @%USERPROFILE%\.lawfirm_venv\Scripts\python.exe scripts\diff_xlsx.py "tests\golden\person_settlement_exporter\%F" "csharp\out\person_settlement\%F" || echo [DIFF-FAIL] %F
```

**② 月度结算表（补齐 GAP-1，真实 2025-12 名单）**

```bat
cd /d C:\Users\Bingo\Desktop\buddy2\lawfirm_app
scripts\t2_2025.bat
```

（`t2_2025.bat` = `t2_verify.bat 2025 12`，内含完整的 build → C# 导出 → 重导 golden → diff 四步。）

> 若 `dotnet` 不在 PATH：把上面命令里的 `dotnet` 换成
> `%USERPROFILE%\.dotnet\dotnet.exe`；沙箱验证时还需先
> `set APPDATA=%USERPROFILE%\AppData\Roaming`、`set ProgramFiles=C:\Program Files`（本机通常不需要）。

### 判读方法

- **全绿 = 7 维 0 差异**：每个文件都输出 `PASS ✓ 两侧完全一致（列名/合并/数字格式/页面设置/逐格 全绿）`；
  同时因为用了 `|| echo [DIFF-FAIL] %F`，**只要屏幕上没有出现任何 `[DIFF-FAIL]` 行，就是全绿**。
- **出现 `FAIL ✗ 不一致: N 处  [xxx=..]`**：按 `category` 定位维度
  （`header`=列名 / `merged`=合并 / `number_format`=格式 / `page_setup`=页面 / `cell`=逐格值），
  加 `--verbose` 看逐条 expect→actual。
- **退出码**：`0`=一致，`1`=存在差异，`2`=读取失败，`3`=缺黄金件/缺产物。
- ⚠️ **注意**：由于 DIFF-1/DIFF-2 不在 7 维之内，**即使本机全绿也不代表视觉完全一致**——
  打开 `个人结算总表_xxx.xlsx` 看第 2 行表头是否加粗、是否有灰底，是必要的补充人工检查。

---

## 附录 A：原始 XML 硬证据（C# 产物 `个人结算总表_车辆.xlsx` / sheet `车辆汇总`）

第 2 行表头（15 格，全部 `t="s"` 走 sharedStrings，共用同一 style `s="1"`）：

```xml
<row r="2"><c r="A2" s="1" t="s"><v>1</v></c><c r="B2" s="1" t="s"><v>2</v></c><c r="C2" s="1" t="s"><v>3</v></c><c r="D2" s="1" t="s"><v>4</v></c><c r="E2" s="1" t="s"><v>5</v></c><c r="F2" s="1" t="s"><v>6</v></c><c r="G2" s="1" t="s"><v>7</v></c><c r="H2" s="1" t="s"><v>8</v></c><c r="I2" s="1" t="s"><v>9</v></c><c r="J2" s="1" t="s"><v>10</v></c><c r="K2" s="1" t="s"><v>11</v></c><c r="L2" s="1" t="s"><v>12</v></c><c r="M2" s="1" t="s"><v>13</v></c><c r="N2" s="1" t="s"><v>14</v></c><c r="O2" s="1" t="s"><v>15</v></c></row>
```

`xl/sharedStrings.xml` 对应条目（index 1..15）：

```xml
<si><t xml:space="preserve">序号</t></si><si><t xml:space="preserve">项目</t></si><si><t xml:space="preserve">1月</t></si><si><t xml:space="preserve">2月</t></si><si><t xml:space="preserve">3月</t></si><si><t xml:space="preserve">4月</t></si><si><t xml:space="preserve">5月</t></si><si><t xml:space="preserve">6月</t></si><si><t xml:space="preserve">7月</t></si><si><t xml:space="preserve">8月</t></si><si><t xml:space="preserve">9月</t></si><si><t xml:space="preserve">10月</t></si><si><t xml:space="preserve">11月</t></si><si><t xml:space="preserve">12月</t></si><si><t xml:space="preserve">合计</t></si>
```

sheet 尾部（合并 + 页边距，**且无 `<pageSetup>`**）：

```xml
<mergeCells><mergeCell ref="A1:O1"/></mergeCells><pageMargins left="0.75" right="0.75" top="1" bottom="1" header="0.5" footer="0.5"/></worksheet>
```

`xl/workbook.xml`：

```xml
<sheets><sheet name="车辆汇总" sheetId="1" r:id="rId3"></sheet></sheets>
```

## 附录 B：覆盖率自证（防假 PASS）

最终一轮的 coverage guard 输出，证明结论建立在真实数据量之上而非空跑：

```
files                     = 42
total sheets              = 80
header DIFF sheets        = 0
sheets with maxcol != 15  = 0
sheets merges != A1:O1    = 0
sheets pageSetup ABSENT   = 80      ← U1 定案落实
sheets pageSetup PRESENT  = 0
sheets compared           = 80
golden rows covered       = 1681
coords examined           = 23055
coords non-empty both     = 23055
pairs fully clean         = 42 / 42
```

**诚实声明**：上表中的双向比对，是我**按 `diff_xlsx.py` 逻辑复刻**的独立实现（沙箱无 openpyxl 无法直接执行官方脚本）。
其判定规则对齐了官方脚本的关键分支（数值容差 1e-6、`fitTo*` 的 `None→1` 归一、`number_format` 仅在一侧有值时比较、
`_detect_header_row` 的「前 5 行字符串最多且 ≥2」策略）。虽在我自己的实现上与官方脚本一致，
仍建议用第五节的本机命令跑一次**官方 `diff_xlsx.py`** 作为最终验收。
