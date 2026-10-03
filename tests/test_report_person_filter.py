"""列表式报表「人员勾选筛选」单元测试（内存库 + 离屏）—— 引擎/导出器层。

覆盖：
- apply_person_filter 纯函数各分支：None / 空集 / 全不命中 / 部分命中 / 保持原序 / 不改入参；
- list_report_persons 的 hire_month 月份过滤（张三 3 月入职 → 1/2 月名单里没有他）；
- 勾选后**预览行数变少**（build_report_rows / build_main_preview 都按 persons 收窄）；
- 勾选导出与预览一致（导出行数 == 预览行数）；
- 模板表**逐月取交集**：3 月入职的张三，在 1/2 月 sheet 里天然不出现，且空交集的 sheet
  **仍生成**（sheet 名/数量只由年月决定，模板形态要能跨次对比）；
- 未收款明细 sheet **跟随 persons 过滤**且合计与明细行自洽（合计 == 明细求和）。

运行：python tests/test_report_person_filter.py
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import openpyxl  # noqa: E402

from app.db import SCHEMA  # noqa: E402
from app.engine import staff_type as st  # noqa: E402
from app.exporter.report_persons import apply_person_filter, list_report_persons  # noqa: E402

OK, FAILS = [], []


def check(label, cond, detail=""):
    if cond:
        OK.append(label)
    else:
        FAILS.append(f"{label} {detail}".strip())


def make_conn():
    """内存库 +复刻 db 迁移（charge_detail/expense_ledger.person_type、类型开关列）。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(staff_type_def)")]
    for c, ddl in (("is_invoice", "INTEGER NOT NULL DEFAULT 0"),
                   ("can_expense", "INTEGER NOT NULL DEFAULT 0"),
                   ("net_basis", "TEXT NOT NULL DEFAULT '收款净额'")):
        if c not in cols:
            conn.execute(f"ALTER TABLE staff_type_def ADD COLUMN {c} {ddl}")
    for tbl in ("charge_detail", "expense_ledger"):
        tcols = [r[1] for r in conn.execute(f"PRAGMA table_info({tbl})")]
        if "person_type" not in tcols:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN person_type TEXT DEFAULT ''")
    conn.commit()
    return conn


def seed_types(conn) -> None:
    for name, invoice, expense, basis in (
            ("合伙", True, True, "开票净额"), ("聘用", True, True, "收款净额")):
        if st.get_type(name, conn) is None:
            st.add_type(name, conn=conn)
            st.set_invoice(name, invoice, conn=conn)
            st.set_can_expense(name, expense, conn=conn)
            st.set_net_basis(name, basis, conn=conn)


def seed_person(conn, name, hire_month=None, amount=1000.0, month=3, buyer="某公司") -> None:
    """一名带开票业务的人员（默认 3 月、全额未收 → 便于验证未收款明细过滤）。"""
    inv = f"INV_{name}_{month:02d}"
    conn.execute("INSERT INTO invoice(invoice_no, invoice_date, buyer, total_amount) "
                 "VALUES(?,?,?,?)", (inv, f"2025-{month:02d}-01", buyer, amount))
    conn.execute("INSERT INTO charge_detail(invoice_no, person_name, billing_amount, person_type) "
                 "VALUES(?,?,?,?)", (inv, name, amount, "合伙"))
    if hire_month:
        conn.execute("INSERT INTO staff_roster(name, hire_month) VALUES(?,?)", (name, hire_month))
    else:
        conn.execute("INSERT INTO staff_roster(name) VALUES(?)", (name,))
    conn.execute("INSERT OR IGNORE INTO staff_type_map(name, type_name, is_primary) VALUES(?,?,1)",
                 (name, "合伙"))
    conn.commit()


class _KeepAliveConn:
    """代理 sqlite3.Connection：close() 变成空操作。

    导出器按生产约定在 finally 里 close() 连接（tests/test_conn_hygiene规则 B 门禁要求），
    同一连接要跨多次调用复用，故用代理屏蔽 close——生产代码零改动。
    """

    def __init__(self, conn):
        self._conn = conn

    def execute(self, *a, **kw):
        return self._conn.execute(*a, **kw)

    def executemany(self, *a, **kw):
        return self._conn.executemany(*a, **kw)

    def close(self) -> None:
        return None


def patch_conn(conn):
    """把导出器/引擎/分类映射用的 get_conn 全部指向内存库（不碰真实 data/lawfirm.db）。

    注意 person_settlement.report_entries 在不传 conn 时会走 app.db.get_conn 自开连接
    （生产既有行为，导出器就是这条路径），故引擎侧也要一并指向内存库。
    """
    import app.exporter.invoice_income_exporter as iie
    import app.exporter.staff_income_exporter as sie
    import app.engine.expense_cat as ec
    import app.engine.person_settlement as ps
    shared = _KeepAliveConn(conn)
    sie.get_conn = lambda: shared
    iie.get_conn = lambda: shared
    ps.get_conn = lambda: shared
    ec.get_map = lambda conn=None: {"交通费": "汽油费", "工资": "报酬发放"}
    ec.get_by_category = lambda cat, conn=None: (
        ["交通费"] if cat == "汽油费" else ["工资"] if cat == "报酬发放" else [])
    return shared


def test_apply_person_filter() -> None:
    persons = ["张三", "李四", "王五"]
    check("None → 原样返回（= 未筛选，向后兼容 persons=None 默认值）",
          apply_person_filter(persons, None) == persons)
    # 🔴 必修1：[] 与 None 语义必须区分。[] = 用户勾了 0 人 → 空表；
    # 若按真值判断退回「不过滤」，预览/导出会列出全部人员 = 数据泄露。
    check("空列表 [] → 空表（0 人勾选，绝不退回全量）",
          apply_person_filter(persons, []) == [], apply_person_filter(persons, []))
    check("空集合 set() → 空表", apply_person_filter(persons, set()) == [])
    check("空列表 [] × 已经是空的名单 → 仍为空（不崩）",
          apply_person_filter([], []) == [])
    check("空列表 [] × 单人名单 → 空（不是那 1 人）",
          apply_person_filter(["张三"], []) == [])
    check("全选 → 等于原名单（保原序）", apply_person_filter(persons, {"王五", "张三", "李四"})
          == persons)
    check("部分交集 → 只留勾选的（保 persons 原序，不按勾选顺序）",
          apply_person_filter(persons, ["王五", "张三"]) == ["张三", "王五"],
          apply_person_filter(persons, ["王五", "张三"]))
    check("全不命中 → 空表（不为了好看而扩成全量）",
          apply_person_filter(persons, ["赵六"]) == [])
    check("重复项不产生重复行", apply_person_filter(persons, ["张三", "张三"]) == ["张三"])
    # 纯函数：不得改入参
    src = ["张三", "李四"]
    out = apply_person_filter(src, ["张三"])
    check("不修改传入的 persons", src == ["张三", "李四"], src)
    check("返回值是新 list（非同一对象）", out is not src)


def test_roster_and_hire_month(conn) -> None:
    check("名单：3 月全列", list_report_persons(conn, 2025, 3) == ["张三", "李四", "王五"],
          list_report_persons(conn, 2025, 3))
    check("名单：1 月剔除 3 月入职的张三",
          list_report_persons(conn, 2025, 1) == ["李四", "王五"],
          list_report_persons(conn, 2025, 1))
    check("名单：12 月全列（入职月已过）",
          list_report_persons(conn, 2025, 12) == ["张三", "李四", "王五"])
    # 交集：勾选张三，1 月交集为空 → 该 sheet 变空（但不跳sheet）
    check("勾选张三 ×1 月名单 → 空交集",
          apply_person_filter(list_report_persons(conn, 2025, 1), ["张三"]) == [])
    check("勾选张三+李四 ×1 月名单 → 只剩李四",
          apply_person_filter(list_report_persons(conn, 2025, 1), ["张三", "李四"]) == ["李四"])


def test_preview_rows_shrink(conn) -> None:
    """勾选后预览行数变少（两张报表都验）。"""
    from app.exporter.invoice_income_exporter import build_main_preview
    from app.exporter.staff_income_exporter import build_report_rows
    _, rows_all, _ = build_report_rows(2025, 3, False)
    check("年度结算表 未筛选 3 行", len(rows_all) == 3, len(rows_all))
    _, rows_sel, tot_sel = build_report_rows(2025, 3, False, ["张三"])
    check("年度结算表 勾选张三 → 1 行", len(rows_sel) == 1, len(rows_sel))
    check("年度结算表 勾选后行名= 张三", rows_sel[0][0] == "张三", rows_sel[0][0])
    check("年度结算表 合计只含勾选的人（1000）", tot_sel[0] == 1000.0, tot_sel[0])
    # 不改旧行为：persons=None 与不传等价
    _, rows_none, _ = build_report_rows(2025, 3, False, None)
    check("persons=None 与不过滤一致", rows_none == rows_all)

    _, m_all, _ = build_main_preview(2025, 3, False)
    check("开票收入表 未筛选 3 行", len(m_all) == 3, len(m_all))
    _, m_sel, _ = build_main_preview(2025, 3, False, ["李四", "王五"])
    check("开票收入表 勾选 2 人 → 2 行", len(m_sel) == 2, len(m_sel))
    check("开票收入表 勾选后行名保序", [r[0] for r in m_sel] == ["李四", "王五"],
          [r[0] for r in m_sel])
    # 拆分态下勾选仍生效（行数≠人数，但过滤按人）
    _, sp_sel, _ = build_main_preview(2025, 3, "split_all", ["张三"])
    check("开票收入表 拆分态勾选张三 → 1 行", len(sp_sel) == 1, len(sp_sel))
    check("开票收入表 拆分态勾选后行名带类型", sp_sel[0][0] == "张三（合伙）", sp_sel[0][0])


def test_export_matches_preview(conn, tmpdir) -> None:
    """导出行集合 == 预览行集合（同一 persons 口径）。"""
    from app.exporter.invoice_income_exporter import build_main_preview, export_invoice_income_month
    from app.exporter.staff_income_exporter import build_report_rows, export_staff_income_month
    sel = ["李四"]

    _, p_rows, _ = build_report_rows(2025, 3, False, sel)
    f1 = export_staff_income_month(Path(tmpdir) / "si.xlsx", 2025, 3, False, sel)
    wb = openpyxl.load_workbook(f1)
    ws = wb[wb.sheetnames[0]]
    x_names = [ws.cell(r, 2).value for r in range(5, ws.max_row + 1) if ws.cell(r, 2).value
               not in (None, "合计")]
    check("年度结算表 导出行名 == 预览行名", x_names == [r[0] for r in p_rows],
          f"xlsx={x_names} preview={[r[0] for r in p_rows]}")

    _, m_rows, _ = build_main_preview(2025, 3, False, sel)
    f2 = export_invoice_income_month(Path(tmpdir) / "ii.xlsx", 2025, 3, False, sel)
    wb2 = openpyxl.load_workbook(f2)
    ws2 = wb2[wb2.sheetnames[0]]
    x2 = [ws2.cell(r, 2).value for r in range(5, ws2.max_row + 1)
          if ws2.cell(r, 2).value not in (None, "合计")]
    check("开票收入表 导出行名 == 预览行名", x2 == [r[0] for r in m_rows],
          f"xlsx={x2} preview={[r[0] for r in m_rows]}")
    wb.close()
    wb2.close()


def test_template_monthly_intersection(conn, tmpdir) -> None:
    """模板表逐月取交集：3 月入职的张三在 1/2 月 sheet 里天然不出现，空交集 sheet 仍生成。"""
    from app.exporter.staff_income_exporter import export_staff_income
    sel = ["张三", "李四"]
    out = export_staff_income(Path(tmpdir) / "tpl.xlsx", 2025, 4, False, sel)
    wb = openpyxl.load_workbook(out)
    check("sheet 名/数量只由年月决定（3→1 共 4 个）",
          wb.sheetnames == ["202504", "202503", "202502", "202501"], wb.sheetnames)
    names_by_sheet = {}
    for sn in wb.sheetnames:
        ws = wb[sn]
        names_by_sheet[sn] = [ws.cell(r, 2).value for r in range(5, ws.max_row + 1)
                              if ws.cell(r, 2).value not in (None, "合计")]
    check("4 月 sheet：有勾选的两人", names_by_sheet["202504"] == ["张三", "李四"],
          names_by_sheet["202504"])
    check("3 月 sheet：勾选两人都在（3 月已入职）",
          names_by_sheet["202503"] == ["张三", "李四"], names_by_sheet["202503"])
    check("1 月 sheet：只李四（张三 3 月入职，勾选**不扩成全量**）",
          names_by_sheet["202501"] == ["李四"], names_by_sheet["202501"])
    check("2 月 sheet：只李四", names_by_sheet["202502"] == ["李四"],
          names_by_sheet["202502"])
    # 空交集：只勾张三 → 1/2 月 sheet 仍生成但为空（表头 + 合计 0）
    out2 = export_staff_income(Path(tmpdir) / "tpl2.xlsx", 2025, 2, False, ["张三"])
    wb2 = openpyxl.load_workbook(out2)
    check("空交集仍生成两个 sheet（不跳过）",
          wb2.sheetnames == ["202502", "202501"], wb2.sheetnames)
    ws = wb2["202501"]
    check("空交集 sheet 只有表头 + 合计行",
          ws.max_row == 5 and ws.cell(5, 2).value == "合计", ws.max_row)
    check("空交集 sheet 合计全0", ws.cell(5, 3).value == 0, ws.cell(5, 3).value)
    wb.close()
    wb2.close()


def test_uncollected_sheet_follows_filter(conn, tmpdir) -> None:
    """未收款明细跟随 persons 过滤，且合计 == 明细求和（表内自洽）。"""
    from app.exporter.invoice_income_exporter import export_invoice_income_month
    sel = ["张三"]
    out = export_invoice_income_month(Path(tmpdir) / "ii2.xlsx", 2025, 3, False, sel)
    wb = openpyxl.load_workbook(out)
    dname = f"未收款明细2025年03月"
    check("未收款明细 sheet 名不变", dname in wb.sheetnames, wb.sheetnames)
    ws = wb[dname]
    rows = []
    total_cell = None
    for r in range(4, ws.max_row + 1):
        nm = ws.cell(r, 1).value
        if nm == "合计":
            total_cell = ws.cell(r, 4).value
            break
        rows.append((nm, ws.cell(r, 4).value))
    check("明细只含勾选的人", {n for n, _ in rows} == {"张三"}, rows)
    check("合计 == 明细求和（过滤发生在累加之前）",
          abs((total_cell or 0) - sum(v for _, v in rows)) < 0.01,
          f"total={total_cell} rows_sum={sum(v for _, v in rows)}")
    check("张三未收金额=1000", abs(sum(v for _, v in rows) - 1000.0) < 0.01, rows)
    wb.close()


def test_zero_selection_no_leak(conn, tmpdir) -> None:
    """🔴 必修1 端到端：0 人勾选时预览与导出都必须是空的（数据泄露回归锁）。"""
    from app.exporter.invoice_income_exporter import (
        _uncollected_rows, build_main_preview, export_invoice_income_month)
    from app.exporter.staff_income_exporter import build_report_rows, export_staff_income_month

    names, rows, totals = build_report_rows(2025, 3, False, [])
    check("0 人勾选：年度结算表预览 0 人 0 行", names == [] and rows == [], (names, len(rows)))
    check("0 人勾选：年度结算表合计全 0", totals == [0.0] * 10, totals)
    inames, irows, itotals = build_main_preview(2025, 3, False, [])
    check("0 人勾选：开票收入表预览 0 人 0 行", inames == [] and irows == [],
          (inames, len(irows)))
    check("0 人勾选：开票收入表合计全 0", itotals == [0.0] * 6, itotals)
    # 拆分态同样不得漏（哪怕 types 范围很大）
    _, srows, _ = build_report_rows(2025, 3, "split_all", [])
    check("0 人勾选：拆分态也是 0 行", srows == [], srows)

    # 导出（绕过置灰直接调）也必须是空的
    f1 = export_staff_income_month(Path(tmpdir) / "z1.xlsx", 2025, 3, False, [])
    wb = openpyxl.load_workbook(f1)
    ws = wb[wb.sheetnames[0]]
    body = [ws.cell(r, 2).value for r in range(5, ws.max_row + 1)]
    check("0 人勾选：导出的 sheet 无人员行（只有合计）",
          [b for b in body if b not in (None, "合计")] == [], body)
    check("0 人勾选：导出合计为 0", ws.cell(5, 3).value == 0, ws.cell(5, 3).value)
    wb.close()

    # 未收款明细 sheet 同样必须为空（否则泄露对方抬头 + 欠款）
    f2 = export_invoice_income_month(Path(tmpdir) / "z2.xlsx", 2025, 3, False, [])
    wb2 = openpyxl.load_workbook(f2)
    dname = "未收款明细2025年03月"
    check("0 人勾选：未收款明细 sheet 存在（形态稳定，不跳过）",
          dname in wb2.sheetnames, wb2.sheetnames)
    ws2 = wb2[dname]
    body2 = [ws2.cell(r, 1).value for r in range(4, ws2.max_row + 1)]
    check("0 人勾选：未收款明细无任何人员行",
          [b for b in body2 if b not in (None, "合计")] == [], body2)
    wb2.close()
    # 直接打 _uncollected_rows 的 0 人路径（绕过 sheet，最小面）
    shared = _KeepAliveConn(conn)
    ur, ut, up = _uncollected_rows(shared, 2025, 12, [])
    check("0 人勾选：_uncollected_rows 空明细 + 合计 0",
          ur == [] and ut == 0.0 and up == {}, (ur, ut, up))
    # 回归对照：persons=None 仍是「不过滤」（向后兼容，绝不能被这次修改带崩）
    ur2, ut2, _ = _uncollected_rows(shared, 2025, 12, None)
    check("回归：persons=None 仍不过滤（有明细）", len(ur2) > 0 and ut2 > 0, (len(ur2), ut2))
    ur3, _, _ = _uncollected_rows(shared, 2025, 12, [])
    check("对照：同一人群下 [] 与 None 结果不同（语义确实分开了）",
          len(ur2) != len(ur3), (len(ur2), len(ur3)))


def main() -> int:
    conn = make_conn()
    seed_types(conn)
    # 张三 3 月入职；李四/王五全年在册。三人各一张全额未收发票（便于验证明细过滤）
    seed_person(conn, "张三", hire_month="2025-03", amount=1000.0, month=3, buyer="甲公司")
    seed_person(conn, "李四", amount=2000.0, month=3, buyer="乙公司")
    seed_person(conn, "王五", amount=3000.0, month=3, buyer="丙公司")

    patch_conn(conn)

    test_apply_person_filter()
    test_roster_and_hire_month(conn)
    test_preview_rows_shrink(conn)
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        test_export_matches_preview(conn, tmpdir)
        test_template_monthly_intersection(conn, tmpdir)
        test_uncollected_sheet_follows_filter(conn, tmpdir)
        test_zero_selection_no_leak(conn, tmpdir)
    conn.close()

    if FAILS:
        print("FAILED:\n" + "\n".join(FAILS))
        return 1
    print(f"PASS {len(OK)} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
