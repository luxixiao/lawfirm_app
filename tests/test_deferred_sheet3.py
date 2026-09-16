"""sheet3（应收账款）→ 补录原票（方案 A）单元测试 —— 内存库。

运行：python tests/test_deferred_sheet3.py

阶段 1（2026-09-16 口径改造，工作包 C）后的新口径：
- 判定**只看票号**：sheet3 且票号不在 `invoice` 表 → `need_backfill=True`（需补录原票）；
  票号已在库 → `need_backfill=False`（**已入库，请确认收款**，D1 甲）。
  不再按开票月份判「期外」（同一张历史应收会持续出现在各期 sheet3；日期解析失败行会被静默漏掉）。
- sheet3 行**一律**移出 `invoices`、只写镜表 —— 绝不走普通票路径
  （否则 `_write_collection_for_invoice` 的 DELETE 会抹掉该票历史收款）。
- 开票日期只服务两件事：A4 数据质量校验（导入期）与复核页人工修正。

四组 + 幂等：
- 【票号判定】is_deferred_invoice / split_deferred / need_backfill / 幂等
- 【A1 问题行】开票日期乱码 → 问题行；**空值放行**
- 【A4 报错】sheet3 开票月份 ≥ 账期 → 中止导入；sheet1/2 不误伤
- 【D1 在库跳过】票号在库的 sheet3 行：不建票 / 无分摊 / **历史 collection 不被删**

沿用（未受阶段 1 影响的既有覆盖）：
- deferred_sheet3_invoices：invoice 缺号才算；预填开票信息与收款
- list_pending_backfill：双源合并（红字引用 / 应收账款）与按票号去重（源 A 优先）
- save_backfill：硬校验（至少一名经办人 + 须在花名册）
- review_compare：sheet3 行保留在复核表并标 needs_backfill（不计入差异）；补录
  （source='manual'）完成后自动转普通比对行（库侧口径含 manual）；
  charge_detail 唯一约束作为「分摊无覆盖歧义」的前提假设
- C8：派生层去掉日期过滤（缺号即纳入，不受开票月份影响）
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SCHEMA  # noqa: E402
from app.engine import backfill as bf  # noqa: E402
from app.engine import raw_ledger as rl  # noqa: E402
from app.engine import review_compare as rc  # noqa: E402
from app.engine import backfill_module as bm  # noqa: E402
from app.engine.backfill import is_period_before  # noqa: E402
from app.importer import importer as imp  # noqa: E402
from app.importer.ledger_import import (  # noqa: E402
    _parse_invoice_sheet,
    is_deferred_invoice,
    split_deferred,
)

OK, FAILS = 0, []


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
        print(f"[PASS] {label}")
    else:
        FAILS.append(f"{label} {detail}".strip())
        print(f"[FAIL] {label} {detail}")


class _ConnProxy:
    """引擎 finally 会 close()；内存库单例连接由本测试持有，close 置空操作。"""

    def __init__(self, c):
        self._c = c

    def execute(self, *a, **k):
        return self._c.execute(*a, **k)

    def executescript(self, *a, **k):
        return self._c.executescript(*a, **k)

    def commit(self):
        self._c.commit()

    def close(self):
        pass

    def __getattr__(self, n):
        return getattr(self._c, n)


HEADER = ["序号", "开票日期", "发票号码", "对方", "金额", "经办人", "备注", "案号"]


def main() -> int:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # SCHEMA 只含建表；person_type / src_sheet / src_row / received_override 等
    # 由 init_db 的迁移补列（见 app/db.py），内存库需照做。
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
    proxy = _ConnProxy(conn)

    # bf 也要换连接：library_invoice_nos()（票号是否在库的唯一口径来源）用它
    for mod in (rl, rc, bm, imp, bf):
        mod.get_conn = lambda: proxy

    imp._auto_snapshot = lambda: None
    imp._archive_file = lambda src, bt, period: ""

    # ------------------------------------------------------------ A) 历史口径（仅 A4 仍用）
    check("期外：跨年 2024-05 < 2025-06", is_period_before("2024-05", "2025-06"))
    check("期外：同年上月 2025-05 < 2025-06", is_period_before("2025-05", "2025-06"))
    check("同月不算期外", not is_period_before("2025-06", "2025-06"))
    check("未来月份不算期外", not is_period_before("2025-07", "2025-06"))
    check("空值不算期外",
          not is_period_before("", "2025-06") and not is_period_before("2025-06", ""))

    # ------------------------------------------------------------ B) 票号判定（C1/C2/C3）
    def _inv(sheet, no, date):
        return {"sheet": sheet, "sheet_name": sheet, "row_no": 2,
                "invoice_no": no, "invoice_date": date,
                "total_amount": 100.0, "handlers": [], "handler_text": ""}

    LIB = {"INLIB"}
    check("is_deferred_invoice：sheet3 缺号 → 需补录",
          is_deferred_invoice(_inv("sheet3", "D1", "2024-05-06"), "2025-06", LIB))
    check("is_deferred_invoice：sheet3 缺号（日期=账期当月）→ 仍需补录（不看日期）",
          is_deferred_invoice(_inv("sheet3", "NEW", "2025-06-03"), "2025-06", LIB))
    check("is_deferred_invoice：sheet3 缺号（日期为空）→ 仍需补录",
          is_deferred_invoice(_inv("sheet3", "D0", ""), "2025-06", LIB))
    check("is_deferred_invoice：sheet3 已在库 → 不需补录",
          not is_deferred_invoice(_inv("sheet3", "INLIB", "2024-05-06"), "2025-06", LIB))
    check("is_deferred_invoice：sheet1 不适用",
          not is_deferred_invoice(_inv("sheet1", "S1", "2024-05-06"), "2025-06", LIB))
    check("is_deferred_invoice：sheet2 不适用",
          not is_deferred_invoice(_inv("sheet2", "S2", "2024-05-06"), "2025-06", LIB))
    check("is_deferred_invoice：空票号不适用",
          not is_deferred_invoice(_inv("sheet3", "", "2024-05-06"), "2025-06", LIB))

    data = {"invoices": [_inv("sheet3", "D1", "2024-05-06"), _inv("sheet3", "INLIB", "2025-06-03"),
                         _inv("sheet1", "S1", "2024-05-06"), _inv("sheet2", "S2", "2025-06-06")],
            "deferred": []}
    moved = split_deferred(data, "2025-06", LIB)
    check("split_deferred 移出**全部** sheet3（2 条，不再只移期外）", moved == 2, str(moved))
    check("sheet3 行全进 deferred（含已在库）",
          sorted(d["invoice_no"] for d in data["deferred"]) == ["D1", "INLIB"],
          str(data["deferred"]))
    check("其余留在 invoices",
          sorted(i["invoice_no"] for i in data["invoices"]) == ["S1", "S2"],
          str(data["invoices"]))
    flags = {d["invoice_no"]: d.get("need_backfill") for d in data["deferred"]}
    check("need_backfill：缺号 True / 已在库 False",
          flags == {"D1": True, "INLIB": False}, str(flags))
    check("split_deferred 幂等（二次移出 0 条）", split_deferred(data, "2025-06", LIB) == 0)
    check("幂等后 deferred 条数与标记保持",
          len(data["deferred"]) == 2
          and {d["invoice_no"]: d.get("need_backfill") for d in data["deferred"]} == flags,
          str({d["invoice_no"]: d.get("need_backfill") for d in data["deferred"]}))

    # ------------------------------------------------------ C) A1 问题行 / A4 硬校验
    def _sheet(amount, handler="周立生500", no="NEG1", date="24.5.6"):
        return [HEADER, ["1", date, no, "乙公司", str(amount), handler, "", ""]]

    # A1：开票日期无法解析 → 问题行（不再整份导入失败）
    items, probs = _parse_invoice_sheet(
        _sheet("500", no="A1X", date="乱码XYZ"), "sheet1", "已开票已入账", "2025-06")
    check("A1：日期乱码不产出 item", items == [], str(items))
    check("A1：日期乱码产生 1 条问题行", len(probs) == 1, str(probs))
    check("A1：问题原因可读（含「无法识别」）",
          bool(probs) and "无法识别" in probs[0]["reason"], str(probs and probs[0]["reason"]))
    check("A1：问题行带原始日期文本供修正",
          bool(probs) and probs[0]["date_text"] == "乱码XYZ",
          str(probs and probs[0].get("date_text")))

    # A1：开票日期为空 → **放行**（改由票号判定）
    items, probs = _parse_invoice_sheet(
        _sheet("500", no="A1E", date=""), "sheet1", "已开票已入账", "2025-06")
    check("A1：日期为空放行（不报错）", len(items) == 1 and probs == [],
          f"items={len(items)} probs={probs}")

    # A4：sheet3 开票月份 ≥ 账期 → 中止导入
    try:
        _parse_invoice_sheet(_sheet("500", no="A4S", date="25.6.3"),
                             "sheet3", "应收账款", "2025-06")
        check("A4：同月 sheet3 报错中止", False, "未抛错")
    except Exception as e:  # noqa: BLE001
        check("A4：同月 sheet3 报错中止（含行号/票号/日期/账期）",
              type(e).__name__ == "ImportError_" and "A4S" in str(e) and "2025-06" in str(e),
              f"{type(e).__name__}: {e}")
    try:
        _parse_invoice_sheet(_sheet("500", no="A4F", date="25.7.1"),
                             "sheet3", "应收账款", "2025-06")
        check("A4：未来月 sheet3 报错中止", False, "未抛错")
    except Exception:
        check("A4：未来月 sheet3 报错中止", True)
    items, probs = _parse_invoice_sheet(_sheet("500", no="A4OK", date="25.5.31"),
                                       "sheet3", "应收账款", "2025-06")
    check("A4：早于账期照常放行", len(items) == 1 and probs == [],
          f"items={len(items)} probs={probs}")
    items, probs = _parse_invoice_sheet(_sheet("500", no="A4S1", date="25.6.3"),
                                       "sheet1", "已开票已入账", "2025-06")
    check("A4：不误伤 sheet1 同月票", len(items) == 1 and probs == [],
          f"items={len(items)} probs={probs}")

    # ------------------------------------------------------------ 数据准备
    conn.execute("INSERT INTO staff (name, staff_type) VALUES ('周立生', '聘用')")
    conn.execute("INSERT INTO staff (name, staff_type) VALUES ('陈娟', '合伙')")
    conn.execute(
        "INSERT INTO import_batch (id, batch_type, period, file_name, status, imported_at) "
        "VALUES (1, 'ledger', '2025-06', '2025.6台账.xlsx', 'active', datetime('now','localtime'))")

    def add_raw(rid, sheet_key, date_raw, no, buyer, amount, handler, remark, batch=1):
        conn.execute(
            "INSERT INTO raw_ledger (id, sheet_key, sheet_name, row_no, seq, invoice_date_raw, "
            "invoice_no, buyer, amount_raw, amount_num, handler_text, remark, case_no, kind, "
            "synced, import_batch_id) "
            "VALUES (?,?,'应收账款',?,?,?,?,?,?,?,?,?,'','invoice',1,?)",
            (rid, sheet_key, 2 + rid, str(rid), date_raw, no, buyer, str(amount), amount,
             handler, remark, batch))

    add_raw(11, "sheet3", "24.5.6", "D100", "乙公司", 500.0, "周立生500", "25.7.10")
    add_raw(12, "sheet3", "25.6.3", "I100", "丙公司", 300.0, "周立生300", "")
    add_raw(13, "sheet3", "24.4.1", "D999", "丁公司", 700.0, "周立生700", "")
    add_raw(14, "sheet1", "24.4.1", "S100", "戊公司", 800.0, "周立生800", "")
    add_raw(15, "sheet3", "24.3.1", "D200", "己公司", 1000.0, "周立生600、陈娟400", "25.7.5")
    # D999 已入库（历史自动建票场景）：不再待补录
    conn.execute("INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source) "
                 "VALUES ('D999', '2024-04-01', '丁公司', 700.0, 'import')")
    conn.execute("INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source) "
                 "VALUES ('I100', '2025-06-03', '丙公司', 300.0, 'import')")
    # 源 A 素材：红字发票引用缺失原票 + 退款引用缺失原票
    conn.execute("INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source, orig_invoice_no) "
                 "VALUES ('RED1', '2025-06-20', '乙公司', -500.0, 'import', 'MISS1')")
    conn.execute("INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source) "
                 "VALUES ('RED2', '2025-06-21', '丙公司', -200.0, 'import')")
    conn.execute("INSERT INTO refund (red_invoice_no, orig_invoice_no, refund_amount, refund_date) "
                 "VALUES ('RED2', 'MISS2', 200.0, '2025-06-25')")
    # 同名票：MISS1 同时出现在 sheet3 期外 → 应以源 A 为准（去重）
    add_raw(16, "sheet3", "24.1.5", "MISS1", "乙公司", 500.0, "周立生500", "")
    # 关键：本测试共用一个内存连接，必须先 commit —— save_backfill 校验失败时会
    # conn.rollback()，否则会把上面未提交的造数一起回滚掉。
    conn.commit()

    # ------------------------------------------------------------ D) 源 B 派生
    rows = rl.deferred_sheet3_invoices(conn=proxy)
    by = {r["invoice_no"]: r for r in rows}
    check("缺号才列出", set(by) == {"D100", "D200", "MISS1"}, str(sorted(by)))
    check("已在库票不在待补录（同月 I100 / 期外 D999 同为已入库）",
          "I100" not in by and "D999" not in by)
    check("sheet1 票不在待补录", "S100" not in by)

    d1 = by["D100"]
    check("来源标记=应收账款", d1["source"] == "应收账款")
    check("预填开票日期已归一化", d1["invoice_date"] == "2024-05-06", d1["invoice_date"])
    check("预填对方/价税合计", d1["buyer"] == "乙公司" and d1["total_amount"] == 500.0,
          f"{d1['buyer']} {d1['total_amount']}")
    check("预填经办人与开票额",
          [(h["name"], h["billing"]) for h in d1["handlers"]] == [("周立生", 500.0)],
          str(d1["handlers"]))
    check("单期收款预填（纯日期备注=收款日/全额）",
          d1["handlers"][0]["received"] == 500.0 and d1["handlers"][0]["date"] == "2025-07",
          str(d1["handlers"][0]))
    check("多经办人按开票份额分摊收款",
          [h["received"] for h in by["D200"]["handlers"]] == [600.0, 400.0],
          str(by["D200"]["handlers"]))
    check("收款汇总", d1["collected"] == 500.0, str(d1["collected"]))

    # ------------------------------------------------------------ E) 双源合并
    pend = bm.list_pending_backfill(conn=proxy)
    pm = {p["invoice_no"]: p for p in pend}
    check("源 A（红字引用）在列", pm.get("MISS1", {}).get("source") == "红字引用",
          str(pm.get("MISS1")))
    check("源 A（退款引用）在列", pm.get("MISS2", {}).get("source") == "红字引用",
          str(pm.get("MISS2")))
    check("源 B（应收账款）在列", pm.get("D100", {}).get("source") == "应收账款")
    check("双源按票号去重（源 A 优先）", pm["MISS1"]["source"] == "红字引用")
    check("待补录总数", len(pend) == 4, str(sorted(pm)))
    check("源 A 预填购方与金额",
          pm["MISS1"]["buyer"] == "乙公司" and pm["MISS1"]["total_amount"] == 500.0
          and pm["MISS1"]["invoice_date"] == "",
          str(pm["MISS1"]))
    check("源 A 标记已被红冲", pm["MISS1"]["status"] == "已被红冲", pm["MISS1"]["status"])

    # ------------------------------------------------------------ F) 补录硬校验
    def _save(no, handlers, date="2024-05-06", amount=100.0):
        bm.save_backfill({"invoice_no": no, "invoice_date": date, "buyer": "乙公司",
                          "total_amount": amount, "handlers": handlers})

    try:
        _save("B1", [])
        check("空经办人被拦", False, "未抛错")
    except ValueError as e:
        check("空经办人被拦", "至少" in str(e), str(e))
    try:
        _save("B1", [{"name": "查无此人", "billing": 100.0}])
        check("不在花名册被拦", False, "未抛错")
    except ValueError as e:
        check("不在花名册被拦", "花名册" in str(e), str(e))

    # 源 B 预填内容直接落地（模拟用户点「补录」→ 不改 → 保存）
    d_pend = next(p for p in bm.list_pending_backfill(conn=proxy) if p["invoice_no"] == "D100")
    _save(d_pend["invoice_no"], d_pend["handlers"],
          date=d_pend["invoice_date"], amount=d_pend["total_amount"])
    check("补录写入 manual invoice",
          conn.execute("SELECT 1 FROM invoice WHERE invoice_no='D100' AND source='manual'").fetchone()
          is not None)
    check("补录写 charge_detail（带 person_type）",
          conn.execute("SELECT person_type FROM charge_detail WHERE invoice_no='D100'").fetchone()
          is not None)
    check("补录写 collection（已收/收款日期）",
          conn.execute("SELECT 1 FROM collection WHERE invoice_no='D100' AND source='manual'").fetchone()
          is not None)
    after = {p["invoice_no"] for p in bm.list_pending_backfill(conn=proxy)}
    check("补录后该票从待补录消失", "D100" not in after, str(sorted(after)))
    check("其它票仍待补录", {"MISS1", "MISS2", "D200"} <= after, str(sorted(after)))

    # ------------------------------------------------------------ G) 写库跳过 sheet3
    P2 = "2025-07"
    conn.execute("INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source) "
                 "VALUES ('S1', '2025-07-10', '甲公司', 100.0, 'import')")
    conn.commit()

    def _row(no, date, buyer, amount, name, sheet="sheet1", sheet_name="已开票已入账"):
        handler_text = f"{name}{int(amount) if float(amount).is_integer() else amount}"
        return {"sheet": sheet, "sheet_name": sheet_name, "row_no": 2,
                "header": HEADER, "raw_row": ["1", date, no, buyer, str(amount), handler_text, "", ""],
                "invoice_no": no, "invoice_date": date, "buyer": buyer,
                "total_amount": amount, "handlers": [(name, amount)],
                "handler_text": handler_text, "remark_raw": "",
                "remark": {"receipts": [], "remaining": None, "pure_date": None,
                           "is_red_remark": False, "is_red_off": False},
                "case_no": "", "is_red": amount < 0}

    data2 = {
        "invoices": [_row("S1", "2025-07-10", "甲公司", 100.0, "周立生")],
        "deferred": [_row("D300", "2024-02-01", "庚公司", 400.0, "陈娟",
                          sheet="sheet3", sheet_name="应收账款")],
        "prepayments": [], "problems": [],
        "sheet_totals": {"sheet1": 100.0, "sheet3": 400.0}, "sheet12_total": 100.0,
    }
    r = imp.commit_ledger_import(data2, P2, "2025.7台账.xlsx")
    check("返回 deferred_count", r["deferred_count"] == 1, str(r))
    check("invoice_count 只算落库票", r["invoice_count"] == 1, str(r))
    check("sheet3 票未建 invoice",
          conn.execute("SELECT 1 FROM invoice WHERE invoice_no='D300'").fetchone() is None)
    check("sheet3 票写入镜表",
          conn.execute("SELECT 1 FROM raw_ledger WHERE invoice_no='D300' AND sheet_key='sheet3'").fetchone()
          is not None)
    check("sheet3 票无 charge_detail",
          conn.execute("SELECT 1 FROM charge_detail WHERE invoice_no='D300'").fetchone() is None)
    check("sheet3 票无 collection",
          conn.execute("SELECT 1 FROM collection WHERE invoice_no='D300'").fetchone() is None)
    check("正常票已落库",
          conn.execute("SELECT 1 FROM invoice WHERE invoice_no='S1'").fetchone() is not None)
    check("sheet3 票进入待补录（源 B）",
          "D300" in {p["invoice_no"] for p in bm.list_pending_backfill(conn=proxy)})

    # ------------------------------- G-2) D1 甲：**已在库**的 sheet3 票不抹历史收款
    P3 = "2025-08"
    conn.execute("INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source) "
                 "VALUES ('OLD1', '2024-01-15', '辛公司', 1000.0, 'import')")
    conn.execute("INSERT INTO collection (invoice_no, amount, receipt_date, person_name, source) "
                 "VALUES ('OLD1', 400.0, '2025-01-10', '周立生', 'import')")
    conn.execute("INSERT INTO collection (invoice_no, amount, receipt_date, person_name, source) "
                 "VALUES ('OLD1', 400.0, '2025-02-10', '周立生', 'import')")
    conn.execute("INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source) "
                 "VALUES ('S8', '2025-08-05', '甲公司', 100.0, 'import')")
    conn.commit()
    _before = [dict(x) for x in conn.execute(
        "SELECT id, amount, receipt_date, person_name, source FROM collection "
        "WHERE invoice_no='OLD1' ORDER BY id")]

    # OLD1 是老票（2024-01 开票）又一次出现在 2025-08 的应收账款里 → 票号在库
    data3 = {
        "invoices": [_row("S8", "2025-08-05", "甲公司", 100.0, "周立生"),
                     _row("OLD1", "2024-01-15", "辛公司", 1000.0, "周立生",
                          sheet="sheet3", sheet_name="应收账款")],
        "deferred": [], "prepayments": [], "problems": [],
        "sheet_totals": {"sheet1": 100.0, "sheet3": 1000.0}, "sheet12_total": 100.0,
    }
    r3 = imp.commit_ledger_import(data3, P3, "2025.8台账.xlsx", in_library={"OLD1", "S8"})
    _after = [dict(x) for x in conn.execute(
        "SELECT id, amount, receipt_date, person_name, source FROM collection "
        "WHERE invoice_no='OLD1' ORDER BY id")]
    check("D1 甲：在库 sheet3 票的历史收款**逐行未被改动**", _after == _before,
          f"before={_before} after={_after}")
    check("D1 甲：在库 sheet3 票的历史收款未被删（仍 2 笔 / 合计 800）",
          len(_after) == 2 and abs(sum(x["amount"] for x in _after) - 800.0) < 0.01,
          str(_after))
    check("D1 甲：在库 sheet3 票无 charge_detail",
          conn.execute("SELECT 1 FROM charge_detail WHERE invoice_no='OLD1'").fetchone() is None)
    check("D1 甲：在库 sheet3 票的 invoice 未被改写（来源仍 import、金额未变）",
          dict(conn.execute("SELECT source, total_amount, invoice_date FROM invoice "
                            "WHERE invoice_no='OLD1'").fetchone())
          == {"source": "import", "total_amount": 1000.0, "invoice_date": "2024-01-15"},
          str(dict(conn.execute("SELECT source, total_amount, invoice_date FROM invoice "
                                "WHERE invoice_no='OLD1'").fetchone())))
    check("D1 甲：在库 sheet3 票写入镜表（1:1 保留台账原文）",
          conn.execute("SELECT 1 FROM raw_ledger WHERE invoice_no='OLD1' AND sheet_key='sheet3' "
                       "AND import_batch_id=?", (r3["batch_id"],)).fetchone() is not None)
    check("D1 甲：invoices 已不含 sheet3 行（结构上不可能走到 collection 重写）",
          all((i.get("sheet") or "") != "sheet3" for i in data3["invoices"]),
          str([i["invoice_no"] for i in data3["invoices"]]))
    check("D1 甲：need_backfill=False（已入库，请确认收款）",
          [d.get("need_backfill") for d in data3["deferred"]] == [False],
          str([(d["invoice_no"], d.get("need_backfill")) for d in data3["deferred"]]))
    check("D1 甲：台账收款信息随行带出（供复核页确认）",
          bool(data3["deferred"]) and "remark" in data3["deferred"][0]
          and "handlers" in data3["deferred"][0],
          str(sorted(data3["deferred"][0].keys()) if data3["deferred"] else []))
    check("D1 甲：同一批次的正常票照常落库",
          conn.execute("SELECT 1 FROM invoice WHERE invoice_no='S8'").fetchone() is not None)
    check("D1 甲：deferred_count 只算 sheet3", r3["deferred_count"] == 1, str(r3))
    check("D1 甲：在库票不在待补录（源 B 排除已入库）",
          "OLD1" not in {p["invoice_no"] for p in bm.list_pending_backfill(conn=proxy)})

    # -------------------------------------------------- H) 复核页保留 + 需补录标记
    batch2 = r["batch_id"]
    _b, rows2 = rc.build_review_rows(P2)
    by2 = {x["invoice_no"]: x for x in rows2}
    check("sheet3 票保留在复核表内", "D300" in by2, str(sorted(by2)))
    check("正常票在复核表内", "S1" in by2, str(sorted(by2)))
    check("sheet3 票带 needs_backfill 标记", by2["D300"]["needs_backfill"] is True)
    check("正常票无 needs_backfill 标记", by2["S1"]["needs_backfill"] is False)
    check("sheet3 票状态仍是仅源有（库中缺失）", by2["D300"]["status"] == "仅源有",
          by2["D300"]["status"])
    check("sheet3 票说明指向补录原票",
          "补录原票" in by2["D300"]["detail"], by2["D300"]["detail"])
    check("标记集合只含本批次缺号票",
          rl.deferred_sheet3_nos(P2, batch2, conn=proxy) == {"D300"},
          str(rl.deferred_sheet3_nos(P2, batch2, conn=proxy)))
    check("其它账期不受影响（2025-06 无本批次）",
          rl.deferred_sheet3_nos(P2, 1, conn=proxy) == set(),
          str(rl.deferred_sheet3_nos(P2, 1, conn=proxy)))

    # ---- H-2) 补录后：manual 数据纳入比对 → 该行转普通行（方案 C 的闭环）----
    bm.save_backfill({"invoice_no": "D300", "invoice_date": "2024-02-01",
                      "buyer": "庚公司", "total_amount": 400.0,
                      "handlers": [{"name": "陈娟", "billing": 400.0,
                                    "received": 0.0, "date": ""}]})
    _b, rows3 = rc.build_review_rows(P2)
    by3 = {x["invoice_no"]: x for x in rows3}
    check("补录后该行仍在复核表内（未被剔除）", "D300" in by3, str(sorted(by3)))
    check("补录后 needs_backfill 消失", by3["D300"]["needs_backfill"] is False)
    check("补录后库侧读到 manual 发票（金额/对方）",
          by3["D300"]["amount_db"] == 400.0 and by3["D300"]["buyer_db"] == "庚公司",
          f"{by3['D300']['amount_db']} / {by3['D300']['buyer_db']}")
    check("补录后库侧读到 manual 分摊", "陈娟" in by3["D300"]["handlers_db"],
          by3["D300"]["handlers_db"])
    check("补录后转为一致", by3["D300"]["status"] == "一致",
          f"{by3['D300']['status']} {by3['D300']['detail']}")
    check("补录后不再进待补录",
          "D300" not in {p["invoice_no"] for p in bm.list_pending_backfill(conn=proxy)})

    # ---- H-3) 前提假设：charge_detail 上 UNIQUE(invoice_no, person_name) 保证
    # 同一票同一人不可能同时存在 import / manual 两行 → 复核读分摊无「谁覆盖谁」歧义。
    # 若此约束将来被去掉，本断言会失败，提醒复核口径需要重新定义。
    try:
        conn.execute("INSERT INTO charge_detail (invoice_no, person_name, billing_amount, source) "
                     "VALUES ('D300', '陈娟', 999.0, 'import')")
        conn.commit()
        check("charge_detail 唯一约束存在（同名不可双写）", False, "未抛 IntegrityError")
    except sqlite3.IntegrityError:
        conn.rollback()
        check("charge_detail 唯一约束存在（同名不可双写）", True)

    # ------------------------------------------------------------ I) sheet3 负数守卫
    # 应收账款金额不允许为负：硬报错（问题行），修正后才可确认入库。
    items, probs = _parse_invoice_sheet(_sheet("-500"), "sheet3", "应收账款", "2025-06")
    check("sheet3 负数不产出 item", items == [], str(items))
    check("sheet3 负数产生 1 条问题行", len(probs) == 1, str(probs))
    check("sheet3 负数问题原因含「负数」",
          probs and "负数" in probs[0]["reason"], str(probs and probs[0]["reason"]))
    check("sheet3 负数问题行带票号", probs and probs[0]["invoice_no"] == "NEG1",
          str(probs and probs[0]["invoice_no"]))

    items, probs = _parse_invoice_sheet(_sheet("500"), "sheet3", "应收账款", "2025-06")
    check("sheet3 正数照常解析", len(items) == 1 and probs == [],
          f"items={items} probs={probs}")

    # sheet1/2 红字发票合法为负，守卫不可误伤
    items, probs = _parse_invoice_sheet(
        _sheet("-500", no="RED9"), "sheet1", "已开票已入账", "2025-06")
    check("sheet1 红字负数不误伤", len(items) == 1 and probs == [],
          f"items={items} probs={probs}")

    # -------------------------------------------------- J) C8：派生层去日期过滤
    # 历史镜表里可能有「开票月份 ≥ 账期」的 sheet3 行（旧口径按月判定会漏掉它们；
    # 新口径在导入期由 A4 拦下，但**派生层**必须只认票号 → 缺号一律纳入待补录）。
    conn.execute("INSERT INTO import_batch (id, batch_type, period, file_name, status, imported_at) "
                 "VALUES (90, 'ledger', '2025-09', '2025.9台账.xlsx', 'active', "
                 "datetime('now','localtime'))")
    add_raw(90, "sheet3", "25.9.3", "SAMEMONTH", "壬公司", 1200.0, "周立生1200", "", batch=90)
    add_raw(91, "sheet3", "25.10.1", "FUTURE1", "癸公司", 1300.0, "周立生1300", "", batch=90)
    add_raw(92, "sheet3", "乱码", "BADDATE1", "子公司", 1400.0, "周立生1400", "", batch=90)
    conn.execute("INSERT INTO invoice (invoice_no, invoice_date, buyer, total_amount, source) "
                 "VALUES ('INLIB90', '2024-06-01', '丑公司', 1500.0, 'import')")
    add_raw(93, "sheet3", "24.6.1", "INLIB90", "丑公司", 1500.0, "周立生1500", "", batch=90)
    conn.commit()
    nos9 = {d["invoice_no"] for d in rl.deferred_sheet3_invoices("2025-09", 90, conn=proxy)}
    check("C8：同月缺号（旧口径漏掉）现纳入待补录", "SAMEMONTH" in nos9, str(sorted(nos9)))
    check("C8：未来月缺号现纳入待补录", "FUTURE1" in nos9, str(sorted(nos9)))
    check("C8：日期解析失败的行不再漏出清单（原 is_period_before(\"\") 恒 False）",
          "BADDATE1" in nos9, str(sorted(nos9)))
    check("C8：已在库票仍排除", "INLIB90" not in nos9, str(sorted(nos9)))
    check("C8：本批次缺号集合完整", nos9 == {"SAMEMONTH", "FUTURE1", "BADDATE1"}, str(sorted(nos9)))
    check("C8：日期失败行不报错、日期回空串",
          next(d for d in rl.deferred_sheet3_invoices("2025-09", 90, conn=proxy)
               if d["invoice_no"] == "BADDATE1")["invoice_date"] == "")

    print(f"\n{OK}/{OK + len(FAILS)} passed")
    if FAILS:
        print("FAILED: " + " | ".join(FAILS))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
