"""阶段 6（6-1 + 6-2b）单元测试 —— 红字发票 ⇄ 蓝字原票 一致性口径（引擎层）。

运行：python tests/test_red_consistency.py

覆盖：
- `import_confidence._handler_amounts`：兼容 tuple / dict 两种形态、负数取绝对值、
  同名聚合、空姓名与非数值跳过
- `import_confidence.red_orig_diff`：**一致返回 None** / 不一致返回 {fields, detail}；
  三项（总金额 / 经办人 / 经办人金额）逐一可独立触发；0.01 容差；
  **符号相反算一致**（红字 -5000 vs 蓝字 5000 —— 用户 2026-09-18 明确口径）；
  **6-2b**：`orig_persons_known=False`（蓝字侧无 charge_detail）→ 只比总金额，
  绝不把「缺数据」当「经办人不一致」
- `backfill_module.load_red_reference`：红字票取法单一口径（金额取绝对值、只认负数票、
  多张红字取最早、无 charge_detail 返空）
- `backfill_module.prefill_red_original`：重构为复用 `load_red_reference` 后**行为不变**
  （原票号回填、**开票日期恒留空**、handlers 带 received/date 键）

不碰真库：全部用 `:memory:` + `SCHEMA`。
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SCHEMA  # noqa: E402
from app.engine import backfill_module as bm  # noqa: E402
from app.engine.import_confidence import _handler_amounts, red_orig_diff  # noqa: E402

OK, FAILS = 0, []


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
        print(f"[PASS] {label}")
    else:
        FAILS.append(f"{label} {detail}".strip())
        print(f"[FAIL] {label} {detail}")


def build_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for stmt in (
        "ALTER TABLE charge_detail ADD COLUMN person_type TEXT DEFAULT ''",
        "ALTER TABLE charge_detail ADD COLUMN received_override REAL DEFAULT NULL",
        "ALTER TABLE charge_detail ADD COLUMN src_sheet TEXT DEFAULT ''",
        "ALTER TABLE charge_detail ADD COLUMN src_row INTEGER DEFAULT 0",
        "ALTER TABLE collection ADD COLUMN src_sheet TEXT DEFAULT ''",
        "ALTER TABLE collection ADD COLUMN src_row INTEGER DEFAULT 0",
        "ALTER TABLE invoice ADD COLUMN src_sheet TEXT DEFAULT ''",
        "ALTER TABLE invoice ADD COLUMN src_row INTEGER DEFAULT 0",
        "ALTER TABLE staff ADD COLUMN hire_month TEXT DEFAULT ''",
    ):
        conn.execute(stmt)
    conn.commit()
    return conn


def add_invoice(conn, no, total, orig="", date="2024-03-01", buyer="甲", handlers=()):
    conn.execute(
        "INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, kind, source, "
        "orig_invoice_no, created_at) VALUES (?,?,?,?,?,?,?, datetime('now','localtime'))",
        (no, date, buyer, total, "", "import", orig),
    )
    for i, (nm, amount) in enumerate(handlers):
        conn.execute(
            "INSERT INTO charge_detail (invoice_no, person_name, billing_amount, source) "
            "VALUES (?,?,?,?)",
            (no, nm, amount, "import"),
        )
    conn.commit()


def main() -> int:
    # ==================================================================== #
    # A) _handler_amounts
    # ==================================================================== #
    check("A1：tuple 形态（红字负数）→ 取绝对值",
          _handler_amounts([("张三", -2000.0)]) == {"张三": 2000.0},
          str(_handler_amounts([("张三", -2000.0)])))
    check("A2：dict 形态（补录 payload）→ 取 billing 绝对值",
          _handler_amounts([{"name": "张三", "billing": -2000.0}]) == {"张三": 2000.0},
          str(_handler_amounts([{"name": "张三", "billing": -2000.0}])))
    check("A3：两种形态混用同一口径",
          _handler_amounts([("张三", -3000.0), {"name": "李四", "billing": 1000.0}])
          == {"张三": 3000.0, "李四": 1000.0},
          str(_handler_amounts([("张三", -3000.0), {"name": "李四", "billing": 1000.0}])))
    check("A4：同名多行聚合（与 build_backfill 的 charge 同口径）",
          _handler_amounts([("张三", -1000.0), ("张三", -500.0)]) == {"张三": 1500.0},
          str(_handler_amounts([("张三", -1000.0), ("张三", -500.0)])))
    check("A5：空姓名跳过",
          _handler_amounts([("", 100.0), ("  ", 100.0), ("张三", 100.0)]) == {"张三": 100.0})
    check("A6：非数值金额跳过（不炸）",
          _handler_amounts([("张三", "abc"), ("李四", None), ("王五", 100.0)])
          == {"李四": 0.0, "王五": 100.0},
          str(_handler_amounts([("张三", "abc"), ("李四", None), ("王五", 100.0)])))
    check("A7：空输入 → {}",
          _handler_amounts(None) == {} and _handler_amounts([]) == {})

    # ==================================================================== #
    # B) red_orig_diff —— 一致
    # ==================================================================== #
    check("B1：**符号相反算一致**（红字 -5000 / 蓝字 5000，用户原话口径）",
          red_orig_diff(-5000.0, [("张三", -5000.0)], 5000.0, [("张三", 5000.0)]) is None)
    check("B2：两侧同号也按绝对值判一致",
          red_orig_diff(-5000.0, [("张三", -5000.0)], -5000.0, [("张三", -5000.0)]) is None)
    check("B3：多人分布相同、符号相反 → 一致",
          red_orig_diff(-5000.0, [("张三", -3000.0), ("李四", -2000.0)],
                        5000.0, [{"name": "李四", "billing": 2000.0},
                                 {"name": "张三", "billing": 3000.0}]) is None)
    check("B4：差异 ≤0.01 容差内 → 一致",
          red_orig_diff(-2000.0, [("张三", -2000.0)], 2000.005, [("张三", 2000.0)]) is None)
    check("B5：两侧都无经办人且金额一致 → 一致",
          red_orig_diff(-2000.0, [], 2000.0, []) is None)

    # ==================================================================== #
    # C) red_orig_diff —— 不一致
    # ==================================================================== #
    d = red_orig_diff(-2000.0, [("张三", -2000.0)], 1800.0, [("张三", 1800.0)])
    check("C1：总金额不同 → fields 含「总金额」", d and "总金额" in d["fields"], str(d))
    check("C1b：detail 列出两侧实际金额",
          d and "2,000.00" in d["detail"] and "1,800.00" in d["detail"], str(d))

    d2 = red_orig_diff(-5000.0, [("张三", -5000.0)], 5000.0,
                       [("张三", 2500.0), ("李四", 2500.0)])
    check("C2：经办人集合不同 → fields 含「经办人」", d2 and "经办人" in d2["fields"], str(d2))
    check("C2b：detail 列出两侧姓名",
          d2 and "张三" in d2["detail"] and "李四" in d2["detail"], str(d2))

    d3 = red_orig_diff(-3000.0, [("张三", -3000.0)], 3000.0,
                       [("张三", 1000.0), ("李四", 2000.0)])
    check("C3：人数相同但分布不同 → 含「经办人金额」", d3 and "经办人金额" in d3["fields"], str(d3))
    check("C3b：此时不误报「总金额」", d3 and "总金额" not in d3["fields"], str(d3))

    d4 = red_orig_diff(-5000.0, [("张三", -3000.0), ("李四", -2000.0)], 2000.0,
                       [("张三", 1000.0), ("王五", 1000.0)])
    check("C4：三项都不同 → fields 顺序 [总金额, 经办人, 经办人金额]",
          d4 and d4["fields"] == ["总金额", "经办人", "经办人金额"], str(d4))

    # 姓名**完全不重叠**时没有可比对的人 → 只报「经办人」，不为凑数刷「经办人金额」
    # （detail 已逐字列出两侧姓名，用户一眼能看出差异）
    d4b = red_orig_diff(-5000.0, [("张三", -5000.0)], 5000.0, [("李四", 5000.0)])
    check("C4b：姓名完全不重叠 → 只报「经办人」，不刷「经办人金额」",
          d4b and d4b["fields"] == ["经办人"], str(d4b))

    check("C5：差异 0.02（超容差）→ 判不一致",
          red_orig_diff(-2000.0, [("张三", -2000.0)], 2000.02, [("张三", 2000.0)]) is not None)

    check("C6：红字有经办人 / 蓝字无 → 判「经办人」不一致",
          (lambda x: x and "经办人" in x["fields"])(
              red_orig_diff(-2000.0, [("张三", -2000.0)], 2000.0, [])))
    check("C7：两侧都无经办人但金额不同 → 只报「总金额」",
          (lambda x: x and x["fields"] == ["总金额"])(
              red_orig_diff(-2000.0, [], 1800.0, [])))

    # ---- C8–C10 阶段 6-2b：蓝字侧无分摊数据 → 只比总金额（缺数据 ≠ 不一致） ----
    check("C8：蓝字无分摊数据 + 金额一致 → 一致（不拿缺数据当「经办人」不一致）",
          red_orig_diff(-2000.0, [("张三", -2000.0)], 2000.0, [],
                        orig_persons_known=False) is None)
    check("C9：蓝字无分摊数据 + 金额不符 → 只报「总金额」",
          (lambda x: x and x["fields"] == ["总金额"]
           and "经办人" not in x["detail"])(
              red_orig_diff(-2000.0, [("张三", -2000.0)], 1800.0, [],
                            orig_persons_known=False)))
    check("C9b：**同一组数据**把开关关掉（默认 True）→ 报「经办人」（证明开关真的在起作用）",
          (lambda x: x and "经办人" in x["fields"])(
              red_orig_diff(-2000.0, [("张三", -2000.0)], 2000.0, [])))
    check("C10：蓝字无分摊数据 + 红字也无经办人 + 金额一致 → 一致",
          red_orig_diff(-2000.0, [], 2000.0, [], orig_persons_known=False) is None)

    # ==================================================================== #
    # D) load_red_reference / prefill_red_original
    # ==================================================================== #
    conn = build_conn()
    # 蓝字原票（正数）—— 用于确认「只认红字」
    add_invoice(conn, "BLUE-1", 2000.0, orig="", handlers=[("张三", 2000.0)])
    # 红字票 ORIG-9：引用缺失原票，经办人金额为负（与解析侧同口径）
    add_invoice(conn, "RED-1", -2000.0, orig="ORIG-9", date="2024-05-10",
                buyer="红字购方", handlers=[("张三", -2000.0)])
    # 更早的一张红字票（同引用）—— 取最早
    add_invoice(conn, "RED-0", -2000.0, orig="ORIG-9", date="2024-04-01",
                buyer="更早", handlers=[("张三", -2000.0)])
    # 红字票但无 charge_detail
    add_invoice(conn, "RED-2", -500.0, orig="ORIG-NOCD", date="2024-06-01")

    ref = bm.load_red_reference(conn, "ORIG-9")
    check("D1：找到红字票（取开票日期最早的一张）",
          ref and ref["invoice_no"] == "RED-0", str(ref))
    check("D2：total_amount 取绝对值",
          ref and ref["total_amount"] == 2000.0, str(ref and ref["total_amount"]))
    check("D3：handlers 的 billing 取绝对值",
          ref and ref["handlers"] == [{"name": "张三", "billing": 2000.0}], str(ref and ref["handlers"]))
    check("D4：带出红字票的 buyer",
          ref and ref["buyer"] == "更早", str(ref and ref["buyer"]))

    check("D5：引用不存在 → None", bm.load_red_reference(conn, "ORIG-NONE") is None)
    check("D6：只有蓝字票同号时不算红字引用（只认 total<0）",
          bm.load_red_reference(conn, "BLUE-1") is None)
    check("D7：红字票无 charge_detail → handlers 为空列表",
          (lambda x: x and x["handlers"] == [])(bm.load_red_reference(conn, "ORIG-NOCD")))

    pre = bm.prefill_red_original(conn, "ORIG-9")
    check("D8：prefill 的票号 = **原票号**（不是红字票号）",
          pre["invoice_no"] == "ORIG-9", str(pre["invoice_no"]))
    check("D9：**开票日期恒留空**（绝不用红字票日期顶替）",
          pre["invoice_date"] == "", str(pre["invoice_date"]))
    check("D10：prefill 金额/经办人为红字的绝对值",
          pre["total_amount"] == 2000.0
          and pre["handlers"] == [{"name": "张三", "billing": 2000.0,
                                   "received": 0.0, "date": ""}],
          str(pre))
    check("D11：无红字 → 空骨架（金额 None / handlers 空）",
          bm.prefill_red_original(conn, "ORIG-NONE")
          == {"invoice_no": "ORIG-NONE", "invoice_date": "", "buyer": "",
              "total_amount": None, "handlers": []})

    # ---- 一致性比对与 prefill 同源：同一份红字数据喂两边，结论必然一致 ----
    lib_handlers = [{"name": "张三", "billing": 2000.0}]
    same = red_orig_diff(ref["total_amount"], ref["handlers"],
                         pre["total_amount"], lib_handlers)
    check("D12：prefill 出来的蓝字信息与红字比对 → 一致（无差异）", same is None, str(same))

    conn.close()

    print(f"\n{OK}/{OK + len(FAILS)} passed")
    if FAILS:
        print("FAILED: " + ", ".join(FAILS))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
