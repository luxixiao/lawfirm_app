"""发票台账页「身份」列回归（offscreen）。

验证：
1) rl.identity_map 聚合口径：「人-类型 / 人-类型」、空类型=未标、按 charge_detail 行序；
   全库预取与单票（IN 列表）两种取法一致；空列表/未知票返回空。
2) rl.set_invoice_identity：更新成功 + change_log 留痕；值未变 / 行不存在返回 False；
   回设未标（""）合法。
3) InvoiceLedgerDocView 冒烟：表头含「身份」列、refresh() 后行 dict 携带聚合身份。

运行：python tests/test_ledger_doc_identity.py
"""
import os
import sys
import sqlite3
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from pathlib import Path  # noqa: E402
import app.db as dbmod  # noqa: E402
from app.db import init_db  # noqa: E402
from app.engine import raw_ledger as rl  # noqa: E402
import app.ui.invoice_ledger_doc_view as DV  # noqa: E402


def _make_conn(tmp):
    c = sqlite3.connect(tmp)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("PRAGMA journal_mode = WAL")
    return c


FAILS = []


def check(cond, msg) -> None:
    if cond:
        print(f"  ✅ {msg}")
    else:
        print(f"  ❌ {msg}")
        FAILS.append(msg)


def main() -> int:
    tmp = os.path.join(tempfile.gettempdir(), "lawfirm_ledger_identity.db")
    for s in ("", "-wal", "-shm"):
        p = tmp + s
        if os.path.exists(p):
            os.remove(p)
    dbmod.DB_PATH = Path(tmp)
    init_db(backfill=False)

    # 注入：所有 get_conn 指向临时库
    for mod in (dbmod, rl, DV):
        mod.get_conn = lambda: _make_conn(tmp)

    conn = _make_conn(tmp)
    # 幂等补列（与 db.py 迁移同款；防 init_db 版本差异）
    cols = [r[1] for r in conn.execute("PRAGMA table_info(charge_detail)")]
    if "person_type" not in cols:
        conn.execute("ALTER TABLE charge_detail ADD COLUMN person_type TEXT DEFAULT ''")
    # 发票 A1：两经办人（一个有身份一个空）；发票 A2：一经办人
    conn.execute("INSERT INTO invoice(invoice_no, invoice_date, buyer, total_amount) "
                 "VALUES ('A1','2025-01-05','甲公司',1000)")
    conn.execute("INSERT INTO invoice(invoice_no, invoice_date, buyer, total_amount) "
                 "VALUES ('A2','2025-01-06','乙公司',2000)")
    conn.execute("INSERT INTO charge_detail(invoice_no, person_name, billing_amount, person_type) "
                 "VALUES ('A1','张三',600,'聘用')")
    conn.execute("INSERT INTO charge_detail(invoice_no, person_name, billing_amount) "
                 "VALUES ('A1','李四',400)")
    conn.execute("INSERT INTO charge_detail(invoice_no, person_name, billing_amount, person_type) "
                 "VALUES ('A2','王五',2000,'合伙')")
    conn.execute("INSERT INTO import_batch (id, batch_type, period, file_name, status, imported_at) "
                 "VALUES (1, 'ledger', '2025-01', 'x.xlsx', 'active', datetime('now','localtime'))")
    conn.execute("INSERT INTO raw_ledger(sheet_key, sheet_name, row_no, invoice_no, buyer, "
                 "amount_raw, handler_text, kind, import_batch_id) "
                 "VALUES ('sheet1','x',1,'A1','甲公司','1000','张三、李四','invoice',1)")
    conn.execute("INSERT INTO raw_ledger(sheet_key, sheet_name, row_no, invoice_no, buyer, "
                 "amount_raw, handler_text, kind, import_batch_id) "
                 "VALUES ('sheet1','x',2,'A2','乙公司','2000','王五','invoice',1)")
    conn.commit()
    conn.close()

    print("[1] identity_map 聚合口径")
    m = rl.identity_map()
    check(m.get("A1") == "张三-聘用 / 李四-未标", f"A1 聚合=「张三-聘用 / 李四-未标」（实际：{m.get('A1')}）")
    check(m.get("A2") == "王五-合伙", f"A2 聚合=「王五-合伙」（实际：{m.get('A2')}）")
    m2 = rl.identity_map(["A1"])
    check(m2 == {"A1": "张三-聘用 / 李四-未标"}, "单票取法与全库一致")
    check(rl.identity_map([]) == {}, "空票号列表 → 空 dict")
    check(rl.identity_map(["NOPE"]) == {}, "未知票号 → 空 dict")
    check(rl.identity_map(["", None]) == {}, "空串/None 过滤后 → 空 dict")

    print("[2] set_invoice_identity 更新 + 留痕")
    ok = rl.set_invoice_identity("A1", "李四", "合伙")
    check(ok is True, "设置成功返回 True")
    check(rl.identity_map(["A1"]).get("A1") == "张三-聘用 / 李四-合伙", "聚合文本随更新变化")
    conn = _make_conn(tmp)
    n = conn.execute(
        "SELECT COUNT(*) FROM change_log WHERE table_name='charge_detail' "
        "AND field='person_type' AND new_value='合伙'").fetchone()[0]
    conn.close()
    check(n >= 1, f"change_log 已留痕（{n} 条）")
    check(rl.set_invoice_identity("A1", "李四", "合伙") is False, "值未变 → False（不重复留痕）")
    check(rl.set_invoice_identity("A1", "赵六", "合伙") is False, "经办人行不存在 → False")
    check(rl.set_invoice_identity("", "李四", "合伙") is False, "空票号 → False")
    ok2 = rl.set_invoice_identity("A1", "李四", "")
    check(ok2 is True and rl.identity_map(["A1"]).get("A1") == "张三-聘用 / 李四-未标",
          "回设未标（空串）合法且显示「未标」")

    print("[3] InvoiceLedgerDocView 冒烟")
    view = DV.InvoiceLedgerDocView()
    view.refresh()
    headers = [view.table.horizontalHeaderItem(c).text() for c in range(view.table.columnCount())]
    check("身份" in headers, f"表头含「身份」列（实际：{headers}）")
    check(headers.index("身份") == headers.index("经办人") + 1, "身份列紧跟经办人列之后")
    row_a1 = next((r for r in view._rows_by_id.values()
                   if (r.get("invoice_no") or "").strip() == "A1"), None)
    check(row_a1 is not None and row_a1.get("_identity") == "张三-聘用 / 李四-未标",
          "行 dict 携带聚合身份（refresh 预取）")
    row_a2 = next((r for r in view._rows_by_id.values()
                   if (r.get("invoice_no") or "").strip() == "A2"), None)
    check(row_a2 is not None and row_a2.get("_identity") == "王五-合伙", "A2 行身份正确")
    # 无发票号行不崩（raw_ledger 无票号行 → _identity 为空串）
    conn = _make_conn(tmp)
    conn.execute("INSERT INTO raw_ledger(sheet_key, sheet_name, row_no, invoice_no, buyer, "
                 "amount_raw, kind, import_batch_id) "
                 "VALUES ('sheet4','x',1,'','某预收','500','prepayment',1)")
    conn.commit()
    conn.close()
    view2 = DV.InvoiceLedgerDocView()
    view2.refresh()
    check(True, "含无票号行时 refresh 不崩")

    print()
    if FAILS:
        print(f"FAIL：{len(FAILS)} 项未通过")
        return 1
    print("全绿：发票台账身份列 14 项断言全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
