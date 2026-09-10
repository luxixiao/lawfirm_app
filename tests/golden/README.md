# Golden 回归基准（Pilot T1 安全网）

本目录存放 **lawfirm_app（Python 版）当前正确导出结果** 的基准快照，用于 C#/WPF 重写期的跨语言回归比对：未来 C# 版每导出一份报表，就用 `scripts/diff_xlsx.py` 与这里的 golden 逐格 diff，保证 **列名 / 合并单元格 / 数字格式 / 合计行 / 页面设置 / 每个数据格** 零偏差（计划 T1 验证项 ①：一致率 = 100%）。

> 这些 golden 文件是**回归基线，应提交进 git**（见下文 §3）。它们是只读参照，C# 版不要修改它们。

---

## 1. 目录结构

```
tests/golden/
├── README.md
├── person_settlement_exporter/    # 个人结算总表（每人一个 xlsx，多 sheet）
│   └── 个人结算总表_<姓名>.xlsx
├── invoice_income_exporter/       # 开票收入表（1~12月主表 + 未收款明细）
│   └── <year>年度开票收入.xlsx
├── settlement_report_exporter/    # 月度结算表（多 sheet）
│   └── <year>年<month>月结算表.xlsx
├── staff_income_exporter/         # 聘用律师业务收入结算表
│   └── <year>年度业务收入结算表（聘用律师）.xlsx
├── calc_export/                   # 计算表（mini-excel）逐表导出（带公式版）
│   └── <sheet名>.xlsx
└── person_settlement_json/        # 结算引擎 golden JSON（每人 12 月 × 11 键）
    └── <year>_settlement.json
```

> 文件名与 Python 应用 UI 导出时的命名**完全一致**，便于一一对应比对。

---

## 2. 如何在用户本机重新生成基准

> ⚠️ **必须在本机 venv 中运行**（已装 PySide6 6.11.1 + qfluentwidgets + openpyxl + xlrd），
> 且 `data/lawfirm.db` 已初始化、含当前业务数据。沙箱（CI/无 GUI 环境）**不能**运行本脚本。

本机 venv 路径（由 `setup.bat` 创建）：`%USERPROFILE%\.lawfirm_venv`

```bat
cd C:\Users\Bingo\Desktop\buddy2\lawfirm_app
%USERPROFILE%\.lawfirm_venv\Scripts\python.exe scripts\dump_golden.py
```

可选参数（默认已按「最完整、可复现」设定）：

```bat
%USERPROFILE%\.lawfirm_venv\Scripts\python.exe scripts\dump_golden.py ^
    --year 2025 ^            -- 指定数据年份（默认自动探测最近有数据年）
    --month-to 12 ^          -- 开票收入/聘用结算表截止月（默认 12）
    --report-month 12 ^      -- 月度结算表月份（默认 12）
    --out-dir tests/golden   -- golden 输出根目录
    --only settlement_json   -- 只重新生成某个：person_settlement|invoice_income|settlement_report|staff_income|calc|settlement_json
```

**生成时使用的确定性参数**（保证 golden 可复现，C# 版须用相同口径比对）：

| 导出器 | year | month / month_to | persons |
|---|---|---|---|
| person_settlement_exporter | 自动探测最近有数据年 | — | 全部（`export_all`） |
| invoice_income_exporter | 同上 | month_to=12 | — |
| staff_income_exporter | 同上 | month_to=12 | — |
| settlement_report_exporter | 同上 | report_month=12 | 应用「全选」名单 `sorted(build_settlement(year).keys())` |
| calc_export | — | — | 遍历 `calc_sheet` 全部表（带公式版） |
| person_settlement_json | 同上 | — | 全部人员，12 月 × 11 键 |

> 若数据库年份/数据有更新，重新跑一次 `dump_golden.py` 即可刷新全部基准（覆盖写）。

---

## 3. 如何跑逐格 diff（diff_xlsx.py）

`scripts/diff_xlsx.py` 为**纯 openpyxl、无 UI 依赖、可无头运行**的逐格比对脚本（不需要 PySide6）。

```bat
cd C:\Users\Bingo\Desktop\buddy2\lawfirm_app
python scripts\diff_xlsx.py <golden.xlsx> <csharp版.xlsx> [--json out.json] [--verbose] [--tol 1e-6]
```

- `<golden.xlsx>`：本目录下的基准文件
- `<csharp版.xlsx>`：C#/NPOI 重写的导出产物（路径自定）
- `--json out.json`：额外输出结构化 diff（便于 CI / 程序解析）
- `--verbose`：逐条打印每处不一致详情
- `--tol 1e-6`：数值比较容差（容忍 C# decimal 与 Python float 末位差异）

**比对维度**（任一不一致 → 退出码非 0，判定 FAIL）：

1. sheet 名 / 顺序
2. 每 sheet 列名序列（自动探测表头行并比较值序列）
3. 合并单元格区域
4. 合计 / 汇总 / 结余 行（逐格比较）
5. 数字格式（每格 `number_format`）
6. 页面设置（`orientation` / `fitToWidth` / `fitToHeight` / 四边 `margins`）
7. 逐格比对（覆盖所有数据格、表头、合计行、表尾，含值 + 数字格式）

**结算引擎 JSON 比对**：`person_settlement_json/<year>_settlement.json` 为结构化 JSON（每人 12 月 × 11 键），C# 版可直接对其做 JSON 级 diff（键名/数值），无需 openpyxl。

---

## 4. 交付说明（给团队 lead / C# 工程师）

- **导出器是否硬拉 UI？** 经核实，5 个导出器在 `import` 时**均不硬依赖 PySide6 / qfluentwidgets**：
  - `person_settlement_exporter` → 仅 `app.engine.person_settlement`
  - `invoice_income_exporter` → `app.db` / `app.engine.person_settlement` / `app.engine.split`
  - `settlement_report_exporter` → `app.engine.person_settlement`
  - `staff_income_exporter` → `app.db` / `app.engine.expense_cat` / `app.engine.person_settlement`
  - `calc_export` → `app.engine.calc_eval` / `app.engine.calc_formula`（进一步仅依赖 `app.db` / `app.engine.calc_data` / `app.engine.person_settlement`）
  - `app/__init__.py` 仅含版本号；`app/engine`、`app/exporter` 为隐式命名空间包，无 UI 引入。
  - 因此 `dump_golden.py` 可在**纯 openpyxl 环境**无头导入并运行，无需启动 GUI。
  - **最小无头入口说明**：若日后某导出器在模块顶层新增了 `from PySide6 import ...`（当前没有），则 `dump_golden.py` 顶部 `from app.exporter import ...` 会失败。届时最小修复点 = 把该 UI 导入改为**函数内懒导入**（与 `settlement_report_exporter` 中 `from app.engine.expense_cat import ordered_types` 的写法一致），**不要改动 app/ 业务或列名逻辑**。本次 T1 无需此修复。
- **未改动任何 `app/` 逻辑或列名**：golden 精确反映当前正确输出。
- **许可证**：仅用仓库已有的 `openpyxl` / `xlrd`（开源）；未引入任何商业依赖。
- **本目录文件作为回归基准提交**：golden 一旦生成并 review 通过，应 `git add tests/golden/` 提交，作为后续 C# 版逐格 diff 的永久基线。

---

## 5. 参考：相关计划章节

- 计划文档：`docs/csharp-wpf-refactor-plan-2026-09-09.md`
  - 第 1.3 节 验证项 ①（NPOI 列名覆盖，一致率 100%）
  - 第 6 节 Pilot 任务分解 T1（golden 安全网，3–4 人日）
  - 附录「关键坐标」：`app/engine/person_settlement.py:111-353`（`_compute` 74 分支）
