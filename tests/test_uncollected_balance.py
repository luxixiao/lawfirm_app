"""未收款明细「同一笔收款被扣两次」修复的回归测试 —— 内存库。

缺陷（既有问题，修复于 2026-10-03，与「三态拆分 + 人员筛选」无关）：
    `_uncollected_rows` 里`rem` 既当「分摊用的实时余额」又当「开票额只读副本」用。
    `allocate_receipt` 的 docstring 明写「会被原地更新」，调用后 `rem` 变成 `开票 − 已收`；
    随后又`uncol = eff - got` 再减一次已收 ⇒ `开票 − 2×已收`。

典型症状（修复前）：
    - 开票 5000收 1000 → 报 3000（应 4000）
    - 开票 3000 收 2000 → 报 0（应 1000）→ `uncol > 0.01` 判空 → **整行消失**
    - 两人各 5000 共用一票、只收 5000 → 各报 0 → **两行一起消失**
    - 全额收讫时巧合正确（rem 已为 0，两次扣减差抵消）⇒ **只有部分收款才暴露**

不变量：**未收 = 开票额 − 已收额**（红冲另需扣减原票份额）。本测试用真实 SCHEMA 内存库
走 `_uncollected_rows` 本体，覆盖部分收款 / 超额收款 / 全额收讫 / 多人分摊 / 红冲 /
多笔收款 / 人员勾选过滤，以及 Σ明细行 == total == Σperson_tot 的勾稽自洽。

运行：python tests/test_uncollected_balance.py
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SCHEMA  # noqa: E402

OK, FAILS = [], []


def check(label, cond, detail=""):
    if cond:
        OK.append(label)
    else:
        FAILS.append(f"{label} {detail}".strip())


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(charge_detail)")]
    if "person_type" not in cols:
        conn.execute("ALTER TABLE charge_detail ADD COLUMN person_type TEXT DEFAULT ''")
    conn.commit()
    return conn


def seed_invoice(conn, no, date, buyer, total, lines):
    """lines: [(person_name, billing_amount), ...]"""
    conn.execute("INSERT INTO invoice(invoice_no, invoice_date, total_amount, buyer) VALUES(?,?,?,?)",
                 (no, date, total, buyer))
    for name, amt in lines:
        conn.execute("INSERT INTO charge_detail(invoice_no, person_name, billing_amount) VALUES(?,?,?)",
                     (no, name, amt))


def seed_receipt(conn, no, amount, date="2025-03-10"):
    conn.execute("INSERT INTO collection(invoice_no, receipt_date, amount) VALUES(?,?,?)",
                 (no, date, amount))


def rows_of(conn, persons=None):
    """调被测函数（延迟导入，避免模块级 import 触发 Qt/DB 依赖）。"""
    from app.exporter.invoice_income_exporter import _uncollected_rows
    return _uncollected_rows(conn, 2025, 12, persons)


def uncol_of(rows, person):
    """该人在明细里的未收合计（缺行= 0.0，模拟「行消失」）。"""
    return round(sum(r[3] for r in rows if r[0] == person), 2)


def main() -> int:
    # ===== 场景1：部分收款（修复前 5000 收 1000 报 3000）=====
    conn = make_conn()
    seed_invoice(conn, "A1", "2025-03-01", "买方一号", 5000, [("张三", 5000)])
    seed_receipt(conn, "A1", 1000)
    rows, total, ptot = rows_of(conn)
    check("场景1 部分收款 5000-1000 未收=4000（修复前报 3000）",
          uncol_of(rows, "张三") == 4000.0, f"got={uncol_of(rows, '张三')}")
    check("场景1 明细行为1 行", len(rows) == 1, f"got={len(rows)} rows={rows}")
    check("场景1 total=4000", total == 4000.0, f"got={total}")
    check("场景1 person_tot={'张三':4000}", ptot == {"张三": 4000.0}, f"got={ptot}")
    conn.close()

    # ===== 场景2：收款额> 开票额（修复前 3000 收 2000 报 0、整行消失）=====
    conn = make_conn()
    seed_invoice(conn, "B1", "2025-04-01", "买方二号", 3000, [("李四", 3000)])
    seed_receipt(conn, "B1", 2000)
    rows, total, ptot = rows_of(conn)
    check("场景2 超额收款 3000-2000 未收=1000（修复前报 0、整行消失）",
          uncol_of(rows, "李四") == 1000.0, f"got={uncol_of(rows, '李四')}")
    check("场景2 明细行仍在（未被 uncol>0.01 判空丢弃）",
          any(r[0] == "李四" for r in rows), f"rows={rows}")
    conn.close()

    # ===== 场景3：全额收讫（修复前后都应为 0，但必须无行）=====
    conn = make_conn()
    seed_invoice(conn, "C1", "2025-05-01", "买方三号", 8000, [("王五", 8000)])
    seed_receipt(conn, "C1", 8000)
    rows, total, ptot = rows_of(conn)
    check("场景3 全额收讫未收=0", uncol_of(rows, "王五") == 0.0, f"got={uncol_of(rows, '王五')}")
    check("场景3 全额收讫不产生明细行", len(rows) == 0, f"rows={rows}")
    check("场景3 total=0", total == 0.0, f"got={total}")
    conn.close()

    # ===== 场景4：两人分摊一票、只收一半（修复前两人整行一起消失）=====
    conn = make_conn()
    seed_invoice(conn, "D1", "2025-06-01", "买方四号", 10000, [("赵六", 5000), ("钱七", 5000)])
    seed_receipt(conn, "D1", 5000)
    rows, total, ptot = rows_of(conn)
    check("场景4 赵六未收=2500（修复前报 0、整行消失）",
          uncol_of(rows, "赵六") == 2500.0, f"got={uncol_of(rows, '赵六')}")
    check("场景4 钱七未收=2500（修复前报 0、整行消失）",
          uncol_of(rows, "钱七") == 2500.0, f"got={uncol_of(rows, '钱七')}")
    check("场景4 两人都在明细里", len(rows) == 2, f"rows={rows}")
    check("场景4 total=5000", total == 5000.0, f"got={total}")
    conn.close()

    # ===== 场景5：多笔收款累计（分摊后余额继续被扣，验证累加正确）=====
    conn = make_conn()
    seed_invoice(conn, "E1", "2025-07-01", "买方五号", 10000, [("孙八", 10000)])
    seed_receipt(conn, "E1", 3000, "2025-07-05")
    seed_receipt(conn, "E1", 2000, "2025-07-15")
    rows, total, ptot = rows_of(conn)
    check("场景5 两笔收款共 5000 → 未收=5000（修复前报 0）",
          uncol_of(rows, "孙八") == 5000.0, f"got={uncol_of(rows, '孙八')}")
    conn.close()

    # ===== 场景6：红冲扣减（红冲额> 已收时，未收应被抵到 0）=====
    conn = make_conn()
    seed_invoice(conn, "F1", "2025-08-01", "买方六号", 6000, [("周九", 6000)])
    seed_receipt(conn, "F1", 1000)
    #红冲票：冲回原票 F1的份额
    conn.execute("INSERT INTO invoice(invoice_no, invoice_date, total_amount, orig_invoice_no) "
                 "VALUES('F1_R','2025-09-01',-3000,'F1')")
    conn.execute("INSERT INTO charge_detail(invoice_no, person_name, billing_amount) VALUES(?,?,?)",
                 ("F1_R", "周九", -3000))
    rows, total, ptot = rows_of(conn)
    # 正确口径：开票 6000 − 红冲 3000 = 有效 3000；已收 1000 → 未收 2000
    check("场景6 红冲 3000 后未收=2000（6000-3000-1000）",
          uncol_of(rows, "周九") == 2000.0, f"got={uncol_of(rows, '周九')} rows={rows}")
    conn.close()

    # ===== 场景7：红冲额 ≥ 有效开票（未应收为 0，不报负数）=====
    conn = make_conn()
    seed_invoice(conn, "G1", "2025-08-01", "买方七号", 4000, [("吴十", 4000)])
    conn.execute("INSERT INTO invoice(invoice_no, invoice_date, total_amount, orig_invoice_no) "
                 "VALUES('G1_R','2025-09-01',-4000,'G1')")
    conn.execute("INSERT INTO charge_detail(invoice_no, person_name, billing_amount) VALUES(?,?,?)",
                 ("G1_R", "吴十", -4000))
    rows, total, _ = rows_of(conn)
    check("场景7 全额红冲未收=0（不为负）", total == 0.0, f"got={total} rows={rows}")
    conn.close()

    # ===== 场景8：勾稽自洽 —— Σ明细行 == total == Σperson_tot（过滤与不过滤都验）=====
    conn = make_conn()
    seed_invoice(conn, "H1", "2025-03-01", "买方八号", 5000, [("张三", 5000)])
    seed_receipt(conn, "H1", 1000)
    seed_invoice(conn, "H2", "2025-04-01", "买方九号", 7000, [("李四", 7000)])
    seed_receipt(conn, "H2", 2000)
    seed_invoice(conn, "H3", "2025-05-01", "买方十号", 3000, [("张三", 3000)])
    rows, total, ptot = rows_of(conn)
    check("场景8 不过滤 total == Σ明细行", round(sum(r[3] for r in rows), 2) == total,
          f"rows_sum={round(sum(r[3] for r in rows), 2)} total={total}")
    check("场景8 不过滤 total == Σperson_tot",
          round(sum(ptot.values()), 2) == total, f"ptot_sum={round(sum(ptot.values()), 2)} total={total}")
    # 只勾张三：李四的行应被过滤掉，且 total 只含张三
    rows2, total2, ptot2 = rows_of(conn, ["张三"])
    check("场景8 勾选后 total 只含所选人", set(ptot2) <= {"张三"}, f"got={ptot2}")
    check("场景8 勾选后 Σ明细行 == total",
          round(sum(r[3] for r in rows2), 2) == total2,
          f"rows_sum={round(sum(r[3] for r in rows2), 2)} total={total2}")
    # 张三:H1 未收 4000 + H3 未收 3000 = 7000
    check("场景8 勾选张三未收=7000（4000+3000）", total2 == 7000.0, f"got={total2}")
    # 0 人勾选 → 空明细（不得回退全量）
    rows3, total3, ptot3 = rows_of(conn, [])
    check("场景8 勾选 0 人 → 空明细、total=0", rows3 == [] and total3 == 0.0, f"got={rows3} {total3}")
    conn.close()

    # ===== 场景9：反向验证 —— 读数改回遍历 rem（buggy 版）必须变红 =====
    # 复刻修复前的写法，断言本测试确实能抓住它（防"测试其实抓不到 bug"）。
    import app.exporter.invoice_income_exporter as iex
    # 构造一个 buggy 变体：把 billing_of 换回 rem（等效于修复前）
    orig_uncol = iex._uncollected_rows

    def buggy_variant(conn, year, month, persons=None):
        # 复制修复后逻辑，但遍历被扣减的 rem
        import sqlite3 as _s
        keep = set(persons) if persons is not None else None
        red_by_orig = {}
        reds = conn.execute(
            "SELECT invoice_no, orig_invoice_no FROM invoice "
            "WHERE total_amount<0 AND substr(invoice_date,1,4)=? AND orig_invoice_no IS NOT NULL",
            (str(year),)).fetchall()
        for red in reds:
            for c in conn.execute("SELECT person_name, billing_amount FROM charge_detail WHERE invoice_no=?",
                                  (red["invoice_no"],)).fetchall():
                red_by_orig.setdefault(red["orig_invoice_no"], {})[c["person_name"]] = abs(c["billing_amount"])
        invs = conn.execute(
            "SELECT invoice_no, invoice_date, buyer FROM invoice "
            "WHERE total_amount>=0 AND substr(invoice_date,1,4)=?"
            " AND EXISTS (SELECT 1 FROM charge_detail cd WHERE cd.invoice_no=invoice.invoice_no)"
            " ORDER BY invoice_date, invoice_no", (str(year),)).fetchall()
        rows, total, ptot = [], 0.0, {}
        for inv in invs:
            cds = conn.execute(
                "SELECT person_name, billing_amount FROM charge_detail WHERE invoice_no=? ORDER BY id",
                (inv["invoice_no"],)).fetchall()
            if not cds:
                continue
            rem = {c["person_name"]: c["billing_amount"] for c in cds}
            got = {}
            for rec in conn.execute("SELECT amount FROM collection WHERE invoice_no=? ORDER BY id",
                                    (inv["invoice_no"],)).fetchall():
                g = iex.allocate_receipt(rem, rec["amount"])
                for name, val in g.items():
                    got[name] = got.get(name, 0.0) + val
            mon = f"{int(inv['invoice_date'].split('-')[1])}月"
            for name, billing in rem.items():   # ← BUG: 遍历被扣减的 rem
                if keep is not None and name not in keep:
                    continue
                eff = max(billing - red_by_orig.get(inv["invoice_no"], {}).get(name, 0.0), 0.0)
                uncol = round(eff - got.get(name, 0.0), 2)
                if uncol > 0.01:
                    rows.append([name, mon, inv["buyer"] or "", uncol])
                    total += uncol
                    ptot[name] = round(ptot.get(name, 0.0) + uncol, 2)
        return rows, round(total, 2), ptot

    # 场景1 数据在 buggy 变体下应报 3000（≠修复后的 4000）→ 证明 bug 真实存在
    conn = make_conn()
    seed_invoice(conn, "Z1", "2025-03-01", "买方", 5000, [("张三", 5000)])
    seed_receipt(conn, "Z1", 1000)
    fixed_rows, _, _ = orig_uncol(conn, 2025, 12, None)
    buggy_rows, _, _ = buggy_variant(conn, 2025, 12, None)
    fixed_v = uncol_of(fixed_rows, "张三")
    buggy_v = uncol_of(buggy_rows, "张三")
    check("场景9 反向验证：buggy 变体确实少报（3000≠4000），修复后为 4000",
          buggy_v == 3000.0 and fixed_v == 4000.0,
          f"buggy={buggy_v} fixed={fixed_v}")
    conn.close()

    print(f"PASS {len(OK)} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
