"""红字行「待补录」判据 —— 2026-09-25 新口径 + `_red_orig_no` 三路取号（离屏 Qt）。

用户口径（2026-09-25 拍板，原话）：
> 「我们补录的是**原蓝字发票**，只需要判断原蓝字发票是否在库中就行，不需要判断
>   红字发票是否在库中。当然要先判断，该红字发票对应的蓝字发票，是否在该账期中
>   （因为该账期还未正式入库）。」

落成四条判据，本文件逐个守住：
  ① 补录对象是**原蓝字票**；判据只有「原票在不在库」；
  ② **不因红字票本身的状态跳过判定**（红字票在不在 invoice 表都不影响）；
  ③ 前置排除：原票在本批台账 → 不补录（它随本批入库，否则撞 `_validate_backfills`
     校验③ 卡死整批）；
  ④ 取不到原票号才不给入口。

取号路径（`_red_orig_no` 三路）：
  · 路径 1 `invoice.orig_invoice_no`（销项导入写的，回归必须保留）；
  · 路径 2 **台账行备注**用 `_RED_RE` 现算（台账导进来的红字票 `orig_invoice_no`
    被硬编码成空串，备注是留着的 → 本次新增的**主路径**）；
  · 路径 3 `refund.orig_invoice_no`（退款台账登记的红蓝配对）。

覆盖：R1 路径1 回归 / R2 已在库 / R3 本批排除 / N1 三路全空不给入口 /
     P2 路径2（「不在 invoice 表」与「在表但空串」两种真实情形）/
     P3 路径3 / X 原票已在库时备注路径也让位于「查看原票」。

运行：python tests/test_red_backfill_orig_paths.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])

import app.ui.unified_import_dialog as U  # noqa: E402

OK, FAILS = 0, []
results: list = []


def check(name, cond, extra=""):
    cond = bool(cond)
    if cond:
        global OK
        OK += 1
    else:
        FAILS.append(f"{name} {extra}".strip())
    results.append((name, cond, extra))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {extra}")


STAFF = ["周立生", "陈娟"]
HEADER = ["发票号码", "购方名称", "价税合计", "经办人", "备注"]
CLEAN_REMARK = {"receipts": [], "remaining": None, "pure_date": None,
                "is_red_remark": False, "is_red_off": False}

# 台账红字行的备注原文（销项导入抓原票号用的同一套正则 `_RED_RE`）。
# ⚠ `_RED_RE` 抓的是 `(\d+)`，即**纯数字**的数电票号码 —— 备注里的原票号不能写成
#   `ORIG-2` 这种带字母的假票号，否则正则匹配不上（真实票号本来也都是纯数字）。
RED_NO = "24412000000012345678"
RED_REMARK = f"冲 2025-01-08 红字；被红冲蓝字数电票号码：{RED_NO}"


# ---------------------------------------------------------------- 数据构造
def mk_inv(no, total, sheet="sheet1", remark_raw="", handler="周立生",
           is_red=None, parsed=None):
    """一行台账发票；`remark_raw` 就是 `review_rebuild` 放进 `_inv["remark_raw"]` 的
    台账行原文备注（三路取号第 2 路要用它现算原票号）。"""
    return {
        "sheet": sheet,
        "sheet_name": {"sheet1": "已开票已入账", "sheet2": "已开票未入账",
                       "sheet3": "应收账款"}.get(sheet, sheet),
        "row_no": 10,
        "header": HEADER,
        "raw_row": [no, "某某公司", str(total), handler, remark_raw],
        "invoice_no": no, "invoice_date": "2025-01-15", "buyer": "某某公司",
        "total_amount": total, "handlers": [(handler, float(total))],
        "handler_text": handler,
        "remark_raw": remark_raw,
        "remark": dict(parsed) if parsed is not None else dict(CLEAN_REMARK),
        "case_no": "", "is_red": (total < 0 if is_red is None else is_red),
        "split_receipts": [],
    }


def mk_red(no, **kw):
    """红字行（金额为负 → `is_red` 为真）。"""
    return mk_inv(no, -3000.0, **kw)


def mk_data(invoices, period="2025-01"):
    return {
        "period": period, "invoices": list(invoices),
        "prepayments": [], "problems": [], "deferred": [],
        "sheet_totals": {"sheet1": 0.0}, "sheet12_total": 0.0,
    }


# ---------------------------------------------------------------- DB 桩
_RED: dict = {}        # 红字票号 → invoice.orig_invoice_no（可为空串 = 台账导入写入）
_REFUND: dict = {}     # 红字票号 → refund.orig_invoice_no（路径 3）
_LIB_DB: set = set()   # 已在库的票号


class _Cur:
    def __init__(self, one=None):
        self._one = one

    def fetchone(self):
        return self._one

    def fetchall(self):
        return []

    def __iter__(self):
        return iter(())

    def __getitem__(self, k):
        return self._one[k]


class _Conn:
    """极简连接桩：为 `_red_orig_no` 的三路查询 + `_invoice_in_library` 服务。

    ⚠ 三路都要认：`invoice.orig_invoice_no`（路径1）/ `refund.orig_invoice_no`
    （路径3）/ `SELECT 1 FROM invoice`（已在库判定）。
    """

    def execute(self, sql, params=()):
        s = " ".join(str(sql).lower().split())
        p = tuple(params or ())
        if "orig_invoice_no from invoice" in s:
            red = p[0] if p else ""
            return _Cur({"orig_invoice_no": _RED.get(red)} if red in _RED else None)
        if "orig_invoice_no from refund" in s:
            red = p[0] if p else ""
            return _Cur({"orig_invoice_no": _REFUND.get(red)} if red in _REFUND else None)
        if s.startswith("select 1 from invoice"):
            return _Cur({"1": 1} if (p[0] if p else "") in _LIB_DB else None)
        return _Cur(None)

    def close(self):
        pass


U.get_conn = lambda: _Conn()


def mk_dlg(invoices, red_map=None, refund_map=None, lib=None, period="2025-01",
           show_all=True):
    _RED.clear(); _RED.update(red_map or {})
    _REFUND.clear(); _REFUND.update(refund_map or {})
    _LIB_DB.clear(); _LIB_DB.update(lib or set())
    import app.importer.ledger_import as LI
    LI.library_invoice_nos = lambda conn=None: set(_LIB_DB)
    dlg = U.UnifiedImportDialog(mk_data(invoices), period, STAFF)
    dlg.show()
    if show_all:
        dlg._grp.button(U.FILTER_ALL).setChecked(True)
        dlg._render()
    return dlg


def red_by(dlg, no):
    return next(r for r in dlg._rows
                if r["kind"] == "invoice" and r["ev"].get("invoice_no") == no)


# ===================================================== R1) 路径 1：回归
d_r1 = mk_dlg([mk_red("RED-1")], red_map={"RED-1": "ORIG-1"})
r1 = red_by(d_r1, "RED-1")
check("R1：路径1（销项写入 orig_invoice_no）→ 落「待补录」", r1["status"] == "待补录",
      r1["status"])
check("R1：补的是引用原票 ORIG-1", r1.get("bf_orig") == "ORIG-1", str(r1.get("bf_orig")))
check("R1：按钮 = 「补录原票」", d_r1.btn_backfill.text() == "补录原票",
      d_r1.btn_backfill.text())
check("R1：红字票不在 invoice 表也照样判（不因红字票状态跳过）",
      d_r1._red_backfill_orig(r1) == "ORIG-1", repr(d_r1._red_backfill_orig(r1)))

# ===================================================== R2) 原票已在库
d_r2 = mk_dlg([mk_red("RED-2")], red_map={"RED-2": "ORIG-2"}, lib={"ORIG-2"})
r2 = red_by(d_r2, "RED-2")
check("R2：原票已在库 → 不落待补录", r2["status"] != "待补录", r2["status"])
check("R2：按钮 = 「查看原票」（只读）", d_r2.btn_backfill.text() == "查看原票",
      d_r2.btn_backfill.text())

# ===================================================== R3) 原票在本批台账
d_r3 = mk_dlg([mk_red("RED-3"), mk_inv("ORIG-3", 3000.0)])
r3 = red_by(d_r3, "RED-3")
check("R3：原票在本批 → 不落待补录（不制造假阳性）", r3["status"] != "待补录", r3["status"])
check("R3：按钮隐藏（否则写前校验③会卡死整批）", not d_r3.btn_backfill.isVisible())

# ===================================================== N1) 三路全空
d_n1 = mk_dlg([mk_red("RED-N")])
r_n1 = red_by(d_n1, "RED-N")
check("N1：三路取不到原票号 → 不给补录入口", d_n1._row_backfill_target(r_n1) is None,
      str(d_n1._row_backfill_target(r_n1)))
check("N1：不落待补录", r_n1["status"] != "待补录", r_n1["status"])
check("N1：按钮隐藏", not d_n1.btn_backfill.isVisible())

# ===================================================== P2) 路径 2：台账行备注现算
# 情形 a：红字票**不在 invoice 表**（`_RED` 无此键）→ 全靠路径 2。
d_p2a = mk_dlg([mk_red("RED-P2", remark_raw=RED_REMARK)])
r_p2a = red_by(d_p2a, "RED-P2")
check("P2-a：红字票不在 invoice 表 + 备注写明原票 → 备注路径取到原票号",
      d_p2a._red_orig_no("RED-P2", RED_REMARK) == RED_NO,
      repr(d_p2a._red_orig_no("RED-P2", RED_REMARK)))
check("P2-a：落「待补录」", r_p2a["status"] == "待补录", r_p2a["status"])
check("P2-a：补录目标 = 原蓝字票（不是红字票自己）", r_p2a.get("bf_orig") == RED_NO,
      str(r_p2a.get("bf_orig")))
check("P2-a：按钮 = 「补录原票」且可见",
      d_p2a.btn_backfill.text() == "补录原票" and d_p2a.btn_backfill.isVisible(),
      f"{d_p2a.btn_backfill.text()} visible={d_p2a.btn_backfill.isVisible()}")
check("P2-a：不因红字票不在 invoice 表而跳过判定", d_p2a._red_backfill_orig(r_p2a) == RED_NO,
      repr(d_p2a._red_backfill_orig(r_p2a)))

# 情形 b：红字票**在 invoice 表但 orig_invoice_no 是空串**——这才是台账导入的真实情况
# （`app/importer/importer.py` 把该列硬编码成 ""），备注是留着的。
d_p2b = mk_dlg([mk_red("RED-P2", remark_raw=RED_REMARK)], red_map={"RED-P2": ""})
r_p2b = red_by(d_p2b, "RED-P2")
check("P2-b：在 invoice 表但 orig_invoice_no 空串 + 备注 → 仍取到原票号",
      d_p2b._red_orig_no("RED-P2", RED_REMARK) == RED_NO,
      repr(d_p2b._red_orig_no("RED-P2", RED_REMARK)))
check("P2-b：落「待补录」", r_p2b["status"] == "待补录", r_p2b["status"])
check("P2-b：按钮 = 「补录原票」且可见",
      d_p2b.btn_backfill.text() == "补录原票" and d_p2b.btn_backfill.isVisible(),
      d_p2b.btn_backfill.text())

# 情形 c：备注路径不得喧宾夺主——原票**已在库**时仍是「查看原票」
d_p2c = mk_dlg([mk_red("RED-P2", remark_raw=RED_REMARK)], lib={RED_NO})
r_p2c = red_by(d_p2c, "RED-P2")
check("P2-c：原票已在库 → 即便备注写明原票也只给「查看原票」",
      d_p2c.btn_backfill.text() == "查看原票", d_p2c.btn_backfill.text())
check("P2-c：且不落待补录", r_p2c["status"] != "待补录", r_p2c["status"])

# 无备注的空红字行不该凭空冒出原票号
d_p2d = mk_dlg([mk_red("RED-P2")])
check("P2-d：无备注 → 路径 2 不生效（不误报原票号）",
      d_p2d._red_orig_no("RED-P2", "") == "", repr(d_p2d._red_orig_no("RED-P2", "")))

# ===================================================== P3) 路径 3：refund 表
d_p3 = mk_dlg([mk_red("RED-P3")], red_map={"RED-P3": ""},
              refund_map={"RED-P3": "ORIG-P3"})
r_p3 = red_by(d_p3, "RED-P3")
check("P3：refund 登记红蓝配对 → 取到 ORIG-P3",
      d_p3._red_orig_no("RED-P3") == "ORIG-P3", repr(d_p3._red_orig_no("RED-P3")))
check("P3：落「待补录」", r_p3["status"] == "待补录", r_p3["status"])
check("P3：补录目标 = ORIG-P3", r_p3.get("bf_orig") == "ORIG-P3", str(r_p3.get("bf_orig")))
check("P3：按钮 = 「补录原票」", d_p3.btn_backfill.text() == "补录原票",
      d_p3.btn_backfill.text())

# 多来源一致：路径1 给 A、路径3 给 A（不是冲突，取首个非空即可）
d_p3b = mk_dlg([mk_red("RED-P3")], red_map={"RED-P3": "ORIG-X"},
               refund_map={"RED-P3": "ORIG-X"})
check("P3-b：多来源同值 → 取到同一个原票号",
      d_p3b._red_orig_no("RED-P3") == "ORIG-X", repr(d_p3b._red_orig_no("RED-P3")))

print(f"\nPASS {OK} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
sys.exit(0 if not FAILS else 1)
