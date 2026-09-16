"""raw_ledger 镜像：文本字段取【台账原文】，而非解析/人工编辑后的值。

回归背景（2026-09-16）：导入前复核页手工补录经办人（源台账该列本就为空）后，
镜像曾写入人工值 → 导入后复核页「源」侧显示的是人工值而非台账原文，
「源 ⇄ 库」无法对照。现约定：镜像文本字段（经办人/对方/案号）取 raw_row 原文，
仅在拿不到原始行（无 header/raw_row）时回退解析值；发票号码仍取解析值
（问题行常需纠正号码，镜像必须存纠正后的号才能与库对齐）。

运行：python tests/test_raw_ledger_mirror.py
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SCHEMA  # noqa: E402
from app.importer import importer as imp  # noqa: E402

OK, FAILS = 0, []


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
        print(f"[PASS] {label}")
    else:
        FAILS.append(f"{label} {detail}".strip())
        print(f"[FAIL] {label} {detail}")


HEADER = ["序号", "开票日期", "发票号码", "对方", "金额", "经办人", "备注", "案号"]
H4 = ["序号", "收到日期", "发票号码", "对方", "金额", "经办人", "备注", "案号"]


def main() -> int:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    def one(no):
        return conn.execute("SELECT * FROM raw_ledger WHERE invoice_no=?", (no,)).fetchone()

    # ---- 1) 问题行经人工修正：台账「经办人」列本来为空，人工补了张三1000 ----
    item = {
        "sheet": "sheet1", "sheet_name": "已开票已入账", "row_no": 3,
        "header": HEADER,
        "raw_row": ["2", "25.1.8", "T1", "甲公司", "1000", "", "", "(2025)A1"],
        "invoice_no": "T1", "buyer": "甲公司", "total_amount": 1000.0,
        "handlers": [("张三", 1000.0)], "handler_text": "张三1000",
        "remark_raw": "", "case_no": "（人工填的案号）", "is_red": False,
    }
    imp._insert_raw_ledger(conn, item, 1, "invoice")
    r = one("T1")
    check("镜像经办人取台账原文（空列→空）", (r["handler_text"] or "") == "",
          repr(r["handler_text"]))
    check("镜像对方取台账原文", r["buyer"] == "甲公司", r["buyer"])
    check("镜像案号取台账原文（非人工值）", r["case_no"] == "(2025)A1", r["case_no"])
    check("镜像发票号取解析值", r["invoice_no"] == "T1", r["invoice_no"])
    check("镜像金额/开票日期取台账原文",
          r["amount_raw"] == "1000" and r["invoice_date_raw"] == "25.1.8",
          f'{r["amount_raw"]} {r["invoice_date_raw"]}')
    check("镜像 kind / synced", r["kind"] == "invoice" and r["synced"] == 1)

    # ---- 2) 无原始行（程序化构造）：回退解析值，不可变空 ----
    item2 = {
        "sheet": "sheet1", "sheet_name": "已开票已入账", "row_no": 9,
        "invoice_no": "T2", "buyer": "乙公司", "total_amount": 500.0,
        "handlers": [("李四", 500.0)], "handler_text": "李四500",
        "remark_raw": "", "case_no": "C2",
    }
    imp._insert_raw_ledger(conn, item2, 1, "invoice")
    r2 = one("T2")
    check("无原始行→回退解析值（经办人）", r2["handler_text"] == "李四500",
          repr(r2["handler_text"]))
    check("无原始行→回退解析值（对方/案号）",
          r2["buyer"] == "乙公司" and r2["case_no"] == "C2",
          f'{r2["buyer"]} {r2["case_no"]}')

    # ---- 3) 人工编辑「对方」后：镜像仍是台账原文 ----
    item3 = dict(item)
    item3.update({"invoice_no": "T3", "buyer": "丙公司（人工改）",
                  "raw_row": ["3", "25.1.9", "T3", "丙公司", "2000", "王五2000", "", ""],
                  "handler_text": "王五2000"})
    imp._insert_raw_ledger(conn, item3, 1, "invoice")
    r3 = one("T3")
    check("编辑对方后镜像仍是台账原文",
          r3["buyer"] == "丙公司" and r3["handler_text"] == "王五2000",
          f'{r3["buyer"]} {r3["handler_text"]}')

    # ---- 4) sheet4 预收款：经办人同样取原文 ----
    p = {
        "sheet": "sheet4", "sheet_name": "已入账未开票", "row_no": 2,
        "header": H4,
        "raw_row": ["1", "25.1.5", "", "丁公司", "300", "赵六", "", ""],
        "received_date": "2025-01-05", "buyer": "丁公司", "amount": 300.0,
        "person_text": "赵六（人工改）", "remark": "", "case_no": "",
    }
    imp._insert_raw_ledger(conn, p, 1, "prepayment")
    rp = conn.execute(
        "SELECT handler_text, recv_date_raw, kind FROM raw_ledger "
        "WHERE kind='prepayment'").fetchone()
    check("预收款镜像经办人取原文", rp["handler_text"] == "赵六", repr(rp["handler_text"]))
    check("预收款镜像收到日期原文", rp["recv_date_raw"] == "25.1.5", rp["recv_date_raw"])

    conn.close()
    print(f"\n{OK}/{OK + len(FAILS)} passed")
    if FAILS:
        print("FAILED: " + " | ".join(FAILS))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
