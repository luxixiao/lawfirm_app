# T2 — C# 只读复算 + NPOI 导出（回归安全网）

> 分支：`pilot/dotnet`
> 目标：用 C#/WPF + .NET 10 + Dapper + Microsoft.Data.Sqlite + NPOI **只读**复算结算引擎，
> 并把 `月度结算表` 导出为 xlsx，交给 `scripts/diff_xlsx.py` 与 Python 应用导出的 golden
> 逐格比对，形成不回归的安全网。

## 1. 架构

```
lawfirm.sln
├── LawFirm.Data      (已有) 只读 DbConnection + invoice/collection 仓储 + DTO
├── LawFirm.App       (已有) WPF 壳（只读展示样本）
├── LawFirm.Exporter  (新增) 结算引擎 C# 端口 + NPOI 导出器
│   ├── SettlementEngine.cs       端口 person_settlement._compute / allocate_receipt
│   ├── ExpenseCatHelper.cs       端口 expense_cat.ordered_types（费用行顺序）
│   └── SettlementReportExporter.cs  端口 settlement_report_exporter 的 NPOI 格式化
└── LawFirm.Cli       (新增) 命令行入口：打开 DB → 跑引擎 → 写 xlsx
```

设计原则（与 Python 侧一致）：
- **零写入**：仅 `DbConnection.OpenReadOnly`（Mode=ReadOnly + `PRAGMA query_only=ON`）。
- 引擎端口**逐行镜像** `app/engine/person_settlement.py`，金额用 `double`（= IEEE-754 double，
  与 Python `float` 算术一致），仅导出层 `round` 到 2 位；`diff_xlsx.py` 数值容差 1e-6。
- NPOI 格式化**只对齐 diff_xlsx.py 的 7 个维度**（sheet 名/顺序、值+number_format、
  合并 A1:E1、表头第 3 行、结余汇总行、页面设置）。列宽/行高 diff 不比较，故不强制对齐。

## 2. 构建

需要 .NET 10 SDK。在仓库根 `lawfirm_app/` 下：

```bat
dotnet build csharp/lawfirm.sln
```

> 若 `dotnet restore` 报 NPOI 版本不存在，调整 `LawFirm.Exporter/LawFirm.Exporter.csproj`
> 里 `<PackageReference Include="NPOI" Version="2.7.2" />` 为你本地可用的 NPOI 版本
> （2.7.x 均可），或执行 `dotnet add csharp/LawFirm.Exporter package NPOI`。

## 3. 运行（生成 C# 版 xlsx）

```bat
dotnet run --project csharp/LawFirm.Cli -- ^
    --year 2026 --month 12 --out csharp/out/settlement_report_csharp.xlsx
```

数据库定位优先级（同 `DbConnection.FindDatabase`）：
1. 环境变量 `LAWFIRM_DB`（指向任意副本的绝对路径）；
2. 从程序目录向上查找 `data/lawfirm.db`；
3. 兜底相对路径。

## 4. 与 golden 逐格比对

先在**用户本机 venv** 重新生成 Python 侧 golden（2026 需有数据才会产出真实 xlsx；
无数据则产出「无数据」占位，C# 侧同样产出占位，diff 仍通过）：

```bat
%USERPROFILE%\.lawfirm_venv\Scripts\python.exe scripts\dump_golden.py --year 2026 --report-month 12
```

再比对：

```bat
python scripts/diff_xlsx.py ^
    tests/golden/settlement_report_exporter/2026年12月结算表.xlsx ^
    csharp/out/settlement_report_csharp.xlsx --verbose
```

退出码 `0` = 两侧完全一致（列名/合并/数字格式/页面设置/逐格 全绿）。

## 5. 已闭环的 NPOI 坑（T2 真机验证结论，改代码前务必先读）

以下 5 条都是**实测**结论（`scripts/t2_verify.bat 2025 12` 真实台账 72 sheet 零 diff）：

1. **属性名**：NPOI 的 `IPrintSetup` 只有 `FitWidth` / `FitHeight`（`short`），**没有**
   openpyxl 那套 `FitToWidth` / `FitToHeight`。写错直接 CS1061。
2. **默认页边距不同**：NPOI 新建 sheet 默认 `0.7/0.7/0.75/0.75`（header/footer `0.3/0.3`），
   openpyxl 默认 `0.75/0.75/1.0/1.0`（header/footer `0.5/0.5`）。必须逐边 `SetMargin` 显式写。
3. **页边距要传 `double` 字面量**：`SetMargin(MarginType.TopMargin, 0.3f)` 会把 float
   `0.3f` 隐式转成 double，落盘成 `0.30000001192092896`，与 golden 的 `0.3` 不等。
   写 `0.3`（不带 `f` 后缀）才是精确的 0.3。
4. **`fitToWidth` / `fitToHeight` 落不了盘**：NPOI 的 `CT_PageSetup.fitToWidth` 带
   `[DefaultValue(1)]`，XmlSerializer 在「值 == 默认值」时**省略**该属性，所以设成 1 后
   xlsx 里根本不出现。这在 SpreadsheetML 里与显式写 1 语义等价（`fitToPage="1"` 仍在），
   因此改的是 `diff_xlsx.py`：page_setup 比对时把 `None` 归一成 schema 默认值 1。
   注意 `sheetPr/pageSetUpPr/fitToPage` 是**能**正常落盘的。
5. **空字符串单元格**：`cell.SetCellValue("")` 落盘后 openpyxl 读成 `''`，而 openpyxl
   自己写空串读回来是 `None`。结余行 seq 为空时只建单元格套样式、**不写值**。

## 5b. 已知风险 / 待迭代点

1. **NPOI 版本**：锁定 2.7.2，本地若无则用 `dotnet add package` 调整。
2. **引擎数值对齐**：`allocate_receipt` 迭代分摊、红冲/退款分摊对浮点顺序敏感；
   若 diff 报 `cell` 数值差 > 1e-6，先确认是浮点尾差还是逻辑偏差。
3. **sheet 顺序**：名单排序用 `StringComparer.Ordinal`（对应 Python `sorted` 的 codepoint
   序）。若某些姓名含非常规字符导致顺序不一致，检查排序口径。
4. **费用行顺序**：依赖 `expense_cat` 的 `sort_order`；与 Python `ordered_types` 必须同源。
5. **构建偶发挂死**：`dotnet build` 多节点复用在部分机器上会卡住/被杀，统一加
   `-m:1 -nodereuse:false`（已写进 `t2_verify.bat`）。
6. **golden 必须真实数据**：空库（0 人）只产出 `无数据` 占位 sheet，只能证明「链路通」，
   证明不了数值。回归请以 `scripts/t2_verify.bat 2025 12`（真实台账）为准。

## 6. 后续 Phase 2（本次未做）

- `invoice_income_exporter` / `staff_income_exporter` 的 C# 端口（复用同一引擎 + NPOI 辅助）；
- 把 `calc_export`（mini-excel 引擎）纳入 C# 比较需另起一套公式求值，暂不在 T2 范围。

> `测试\lawfirm_app` 为独立 git 仓库，本目录 `csharp/` 的改动需按既有同步流程（Seafile/手动）
> 同步到 `测试\lawfirm_app`，避免覆盖写。
