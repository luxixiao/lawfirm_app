#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dump_golden.py — 生成 5 个导出器的 golden 基准 xlsx + 结算引擎 golden JSON

⚠️ 本脚本**只允许在用户本机运行**（需 PySide6/qfluentwidgets 的 venv + 已初始化的 data/lawfirm.db）。
   沙箱环境无 qfluentwidgets、无 data/lawfirm.db，请勿在沙箱执行。

设计原则
--------
- 仅调用现有导出器（app/exporter/*）与结算引擎（app/engine/person_settlement），
  **不改写任何业务逻辑或列名**——golden 必须精确反映当前正确输出。
- 5 个导出器在 import 时**均不硬拉 UI**（已核实：只依赖 app.db 与 app.engine.*），
  故本脚本可在纯 openpyxl 环境下无头导入并运行，无需启动 PySide6 GUI。
- 每个导出器产出的 golden 落到 tests/golden/<exporter>/ 子目录，文件名与
  应用 UI 导出时的命名一致，便于后续 C# 版逐格 diff 对比。

导出参数（确定性，保证可复现）
------------------------------
- year        : 默认自动探测「最近一个有数据的年份」(同 app/ui/settlement_view._latest_data_year)；
               可用 --year 覆盖。
- month_to    : 开票收入表 / 聘用业务收入结算表的截止月，默认 12（全年）。
- report_month: 月度结算表的月份，默认 12（年末，最完整）。
- persons     : 月度结算表的员工名单，默认 = 应用「全选」名单
               sorted(build_settlement(year).keys())，与 UI 勾选全部一致。

用户本机运行（仓库根目录 lawfirm_app/ 下，venv 在 %USERPROFILE%\\.lawfirm_venv）：
    cd lawfirm_app
    %USERPROFILE%\\.lawfirm_venv\\Scripts\\python.exe scripts\\dump_golden.py

可选参数：
    --year 2025         指定年份（默认自动探测）
    --month-to 12       开票收入/聘用结算表截止月
    --report-month 12   月度结算表月份
    --out-dir tests/golden    golden 输出根目录
    --only <name>       只生成某个导出器：person_settlement|invoice_income|
                         settlement_report|staff_income|calc|settlement_json
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

# ---- 把仓库根加入 sys.path，使 `import app` 可用（无需安装为包）----
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 以下导入均为纯引擎/导出器，无 PySide6 依赖（已核实 app/__init__ 与 engine/exporter 包均无 UI 引入）
from app.db import get_conn  # noqa: E402
from app.engine import calc_sheet, person_settlement  # noqa: E402
from app.exporter import (  # noqa: E402
    calc_export,
    invoice_income_exporter,
    person_settlement_exporter,
    settlement_report_exporter,
    staff_income_exporter,
)


# 结算引擎 12 月 × 11 键（与 person_settlement._empty_month 一致）
_MONTH_KEYS = (
    "rec_open_cur", "rec_cur_year", "rec_prev_year", "rec_refund_cur", "rec_refund_prev",
    "inv_open_received", "inv_open_uncollected", "inv_red_cur", "inv_red_prev",
    "inv_total", "income",
)


def _round2(x: Any) -> Any:
    """数值保留两位小数，非数值原样返回。"""
    if isinstance(x, bool):
        return x
    if isinstance(x, (int, float)):
        return round(float(x), 2)
    return x


def _latest_data_year() -> int:
    """最近一个有数据的年份（从今年往前找，最多 5 年）；无则退回今年。"""
    cur = _dt.date.today().year
    for y in range(cur, cur - 5, -1):
        try:
            if person_settlement.build_settlement(y):
                return y
        except Exception:  # noqa: BLE001
            # 表不存在等异常时跳过该年
            continue
    return cur


# ---------------------------------------------------------------------------
# 各导出器 golden 生成
# ---------------------------------------------------------------------------
def dump_person_settlement(year: int, out_dir: Path) -> List[Path]:
    """个人结算总表：每人一个文件（app/exporter/person_settlement_exporter.export_all）。"""
    d = out_dir / "person_settlement_exporter"
    files = person_settlement_exporter.export_all(d, year)
    return list(files)


def dump_invoice_income(year: int, month_to: int, out_dir: Path) -> List[Path]:
    """开票收入表：1~month_to 主表（从新到旧）+ 未收款明细。"""
    d = out_dir / "invoice_income_exporter"
    d.mkdir(parents=True, exist_ok=True)
    p = invoice_income_exporter.export_invoice_income(
        d / f"{year}年度开票收入.xlsx", year, month_to)
    return [p]


def dump_staff_income(year: int, month_to: int, out_dir: Path) -> List[Path]:
    """聘用律师业务收入结算表：1~month_to 各一个 sheet（从新到旧）。"""
    d = out_dir / "staff_income_exporter"
    d.mkdir(parents=True, exist_ok=True)
    p = staff_income_exporter.export_staff_income(
        d / f"{year}年度业务收入结算表（聘用律师）.xlsx", year, month_to)
    return [p]


def dump_settlement_report(year: int, month: int, out_dir: Path) -> List[Path]:
    """月度结算表：多 sheet（每人各身份 + 汇总 + 公共费用），名单 = 全选。

    0 人场景已由导出器自身兜底（生成「无数据」占位 xlsx，合法且可比对），
    故此处不再特判跳过——导出器即唯一事实来源。
    """
    d = out_dir / "settlement_report_exporter"
    d.mkdir(parents=True, exist_ok=True)
    persons = sorted(person_settlement.build_settlement(year).keys())
    p = settlement_report_exporter.export_report(
        d / f"{year}年{month}月结算表.xlsx", year, month, persons)
    return [p]


def dump_calc(out_dir: Path) -> List[Path]:
    """计算表（mini-excel）：遍历 calc_sheet 全部表，导出带公式版 xlsx。"""
    d = out_dir / "calc_export"
    d.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    try:
        sheets = calc_sheet.list_sheets(conn)
    finally:
        conn.close()
    files: List[Path] = []
    for s in sheets:
        sid = s["id"]
        name = s.get("name") or f"sheet{sid}"
        fname = calc_export.suggest_filename(name, with_formula=True)
        p = calc_export.export_sheet(sid, str(d / fname), with_formula=True)
        files.append(Path(p))
    return files


def dump_settlement_json(year: int, out_dir: Path) -> List[Path]:
    """结算引擎 golden JSON：每人 12 月 × 11 键（供 C# 版逐位 diff 对比）。"""
    d = out_dir / "person_settlement_json"
    d.mkdir(parents=True, exist_ok=True)
    data = person_settlement.build_settlement(year)
    payload: Dict[str, Any] = {
        "year": year,
        "generated_by": "lawfirm_app scripts/dump_golden.py",
        "generator_note": "person_settlement._compute 输出：每人 12 月 × 11 键 + 未收 + 费用",
        "month_keys": list(_MONTH_KEYS),
        "persons": {},
    }
    for person, st in data.items():
        months = {
            str(m): {k: _round2(st["months"][m][k]) for k in _MONTH_KEYS}
            for m in range(1, 13)
        }
        uncollected_month = {str(m): _round2(st["uncollected_month"][m]) for m in range(1, 13)}
        expenses = {
            t: {str(m): _round2(v.get(m, 0.0)) for m in range(1, 13)}
            for t, v in (st.get("expenses") or {}).items()
        }
        payload["persons"][person] = {
            "staff_type": st.get("staff_type"),
            "months": months,
            "uncollected_month": uncollected_month,
            "uncollected_total": _round2(st.get("uncollected_total", 0.0)),
            "expenses": expenses,
        }
    out = d / f"{year}_settlement.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return [out]


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="生成 5 个导出器 golden xlsx + 结算引擎 golden JSON（须在用户本机 venv 运行）",
    )
    parser.add_argument("--year", type=int, default=None, help="数据年份（默认自动探测最近有数据年）")
    parser.add_argument("--month-to", type=int, default=12, help="开票收入/聘用结算表截止月（默认 12）")
    parser.add_argument("--report-month", type=int, default=12, help="月度结算表月份（默认 12）")
    parser.add_argument("--out-dir", default=str(ROOT / "tests" / "golden"), help="golden 输出根目录")
    parser.add_argument(
        "--only", default=None,
        help="只生成某个：person_settlement|invoice_income|settlement_report|staff_income|calc|settlement_json",
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    year = args.year or _latest_data_year()
    print(f"[INFO] 数据年份 year={year}  month_to={args.month_to}  report_month={args.report_month}")
    print(f"[INFO] golden 输出目录: {out_dir}")

    generated: Dict[str, List[Path]] = {}
    failed: List[str] = []
    only = {args.only} if args.only else None

    def run(key: str, fn) -> None:
        if only and key not in only:
            return
        print(f"[...] 生成 {key} ...")
        try:
            files = fn()
        except Exception as exc:  # noqa: BLE001
            # 单个导出器失败不应拖垮整个一次性流程（如 settlement_report 因无数据崩溃）。
            # 记录失败并继续，让其它导出器的 golden 仍能产出。
            print(f"  [WARN] {key} 生成失败，跳过（不影响其它导出器）: {exc!r}")
            generated[key] = []
            failed.append(key)
            return
        if not files:
            print(f"  [SKIP] {key} 未产出文件（无数据或被主动跳过）")
        generated[key] = files
        for f in files:
            print(f"  ✓ {f}")

    run("person_settlement", lambda: dump_person_settlement(year, out_dir))
    run("invoice_income", lambda: dump_invoice_income(year, args.month_to, out_dir))
    run("settlement_report", lambda: dump_settlement_report(year, args.report_month, out_dir))
    run("staff_income", lambda: dump_staff_income(year, args.month_to, out_dir))
    run("calc", lambda: dump_calc(out_dir))
    run("settlement_json", lambda: dump_settlement_json(year, out_dir))

    total = sum(len(v) for v in generated.values())
    print("-" * 60)
    if failed:
        print(f"[WARN] 以下导出器生成失败: {', '.join(failed)}")
    ok = [k for k in generated if generated[k]]
    print(f"[DONE] 共生成 {total} 个 golden 文件（year={year}）；成功 {len(ok)}/{len(generated)} 个导出器")
    print("[NEXT] 后续 C# 版产出后，用 diff_xlsx.py 逐格比对：")
    print("       python scripts/diff_xlsx.py <golden.xlsx> <csharp.xlsx> [--json out.json]")
    print("[NEXT] 结算引擎 JSON 比对：直接对 tests/golden/person_settlement_json/*.json 做结构化 diff")
    # 只要产出了至少一个 golden 即视为成功（部分导出器因无数据跳过属正常），
    # 仅当全部失败时返回非 0，触发 bat 的告警逻辑。
    return 0 if total > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
