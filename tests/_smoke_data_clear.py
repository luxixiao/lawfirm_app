"""offscreen 冒烟：清空数据 → 复核页残留状态（2026-09-17 用户报障）

报障原话：「清空数据后，导入复核页面还存在数据」。

根因（两条，同一类：清空数据只删库，不收拾页面态）：
1. **`ImportReviewView` 是全项目唯一没有 `showEvent`/`refresh` 的页面**。主窗口
   `select()` 的刷新契约是「由页面 showEvent 负责」（其余 20+ 页都实现了），本页
   没有 → 清空后回到本页仍显示清空前渲染的旧表格。
2. **待确认队列是内存态**（不在库里，`DELETE` 清不掉）→ 清空数据后队列仍在：
   侧栏角标仍显示非零待确认数；点「确认入库」会撞上写前校验（销项已不在库）
   而卡在「校验未通过 → 返回修改」。

覆盖：
- A. `ImportReviewView.refresh()` / `showEvent`：库变空后重渲染 → 表格清空；
  账期选择被保留；**队列在跑时不动队列**（可「离开再回来继续」是本页既有语义）。
- B. `reset_pending()`：清掉队列 + 回导入后模式 + `pending_count()==0`。
- C. `DataClearView._do_clear` 成功后发 `data_cleared`；字样不对则不发。
- D. MainWindow 接线：收到 `data_cleared` → 重置复核页队列 + 清空侧栏角标 +
  收起导入页提示条。（D 直接 emit 信号，**不真清库** → 绝不触碰 data/lawfirm.db）

打桩：`build_review_rows` / `_ledger_periods` / `split_deferred` /
`library_invoice_nos` / `validate_ledger_before_write` / QMessageBox；
C 段把 `app.db.DB_PATH` 临时指向 %TEMP% 下的空库（用完还原）。
"""
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtGui import QShowEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import app.db as DB  # noqa: E402
import app.engine.review_compare as RC  # noqa: E402
import app.ui.data_clear_view as DCV  # noqa: E402
import app.ui.import_review_view as RV  # noqa: E402
import app.ui.main_window as MW  # noqa: E402
from app.ui.data_clear_view import DataClearView  # noqa: E402
from app.ui.import_review_view import ImportReviewView  # noqa: E402

results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond), extra))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {extra}", flush=True)


# ---------------------------------------------------------------- QMessageBox 桩
class _MBtn:
    def __init__(self, text, role=None):
        self._text, self.role = text, role

    def text(self):
        return self._text


class _MB:
    """QMessageBox 桩：warning/information/critical 只记录；question 返回 Yes。"""
    calls = []
    Icon = type("Icon", (), {"Question": 4, "Information": 1, "Warning": 2})
    ButtonRole = type("ButtonRole", (), {"AcceptRole": 1, "RejectRole": 2,
                                         "ActionRole": 3})
    StandardButton = type("StandardButton", (), {"Yes": 1, "No": 2})

    def __init__(self, *a, **k):
        self._btns = []

    def setWindowTitle(self, *_a):
        pass

    def setIcon(self, *_a):
        pass

    def setText(self, t):
        self._text = t

    def setInformativeText(self, *_a):
        pass

    def setDefaultButton(self, *_a):
        pass

    @staticmethod
    def question(*a, **k):
        _MB.calls.append(("question", a[1] if len(a) > 1 else ""))
        return _MB.StandardButton.Yes

    @staticmethod
    def warning(*a, **k):
        _MB.calls.append(("warning", a[1] if len(a) > 1 else ""))
        return None

    @staticmethod
    def information(*a, **k):
        _MB.calls.append(("information", a[1] if len(a) > 1 else ""))
        return None

    @staticmethod
    def critical(*a, **k):
        _MB.calls.append(("critical", a[1] if len(a) > 1 else ""))
        return None


DCV.QMessageBox = _MB
RV.QMessageBox = _MB
MW.QMessageBox = _MB
import app.ui.problem_fix_panel as _PFP  # noqa: E402
_PFP.QMessageBox = _MB
import app.ui.unified_import_dialog as _UID  # noqa: E402
_UID.QMessageBox = _MB

# ---------------------------------------------------------------- 打桩：库/查询
_periods = ["2025-12", "2025-11"]
_rows = {
    "2025-12": [{
        "invoice_no": "INV-A", "source": "sheet1", "status": "差异",
        "buyer_src": "甲公司", "buyer_db": "甲公司",
        "amount_src": "1000", "amount_db": "1000",
        "handlers_src": "张三1000", "handlers_db": "张三1000",
        "recv_src": "", "recv_db": "", "needs_backfill": False,
        "confirmed_note": "", "detail": "",
    }],
    "2025-11": [],
}
RV._ledger_periods = lambda: list(_periods)
RC.build_review_rows = lambda p: ({"id": 1, "period": p} if p else None,
                                  list(_rows.get(p, [])))
RV.split_deferred = lambda *a, **k: 0
RV.library_invoice_nos = lambda: set()
RV.validate_ledger_before_write = lambda *a, **k: None

_data = {"invoices": [], "prepayments": [], "problems": [],
         "sheet_totals": {}, "sheet12_total": 0.0, "period": "2025-01"}

# ---------------------------------------------------------------- A) 重渲染
v = ImportReviewView()
check("A0 页面有 showEvent（主窗口刷新契约）",
      "showEvent" in type(v).__dict__ or hasattr(type(v), "showEvent"))
check("A0 页面有 refresh() 钩子", callable(getattr(v, "refresh", None)))
check("A1 有账期时渲染出表格行", v.page_post.table.rowCount() == 1,
      f"rows={v.page_post.table.rowCount()}")
check("A1 账期下拉含 2 项", v.combo_period.count() == 2,
      str(v.combo_period.count()))
check("A1 当前账期 = 最新 2025-12", v.combo_period.currentData() == "2025-12",
      str(v.combo_period.currentData()))

# 用户切到 2025-11（空账期）→ 表格应为 0 行
v.combo_period.setCurrentIndex(1)
check("A2 切到空账期 → 表格 0 行", v.page_post.table.rowCount() == 0,
      f"rows={v.page_post.table.rowCount()}")
v.combo_period.setCurrentIndex(0)
check("A2 切回 2025-12 → 1 行", v.page_post.table.rowCount() == 1,
      f"rows={v.page_post.table.rowCount()}")

# ---- 核心复现：库被清空（账期列表变空）→ showEvent 重渲染必须清空表格 ----
_periods.clear()
_rows.clear()
check("A3 清空前（未重渲染）：表格仍是旧内容（复现报障）",
      v.page_post.table.rowCount() == 1, f"rows={v.page_post.table.rowCount()}")
v.showEvent(QShowEvent())                     # = 从别的页面切回复核页
check("A3 showEvent → 重渲染后表格清空（修复点）",
      v.page_post.table.rowCount() == 0, f"rows={v.page_post.table.rowCount()}")
check("A3 账期下拉同步清空", v.combo_period.count() == 0,
      str(v.combo_period.count()))
check("A3 统计文案回到「无批次」",
      "该账期没有已导入的发票台账批次" in v.page_post.lbl_stat.text(),
      v.page_post.lbl_stat.text())

# ---- refresh() 保留当前选中账期（不因回页而跳回最新）----
_periods.extend(["2025-12", "2025-11"])
_rows["2025-11"] = [{
    "invoice_no": "INV-B", "source": "sheet2", "status": "差异",
    "buyer_src": "乙公司", "buyer_db": "乙公司",
    "amount_src": "2000", "amount_db": "2000",
    "handlers_src": "李四2000", "handlers_db": "李四2000",
    "recv_src": "", "recv_db": "", "needs_backfill": False,
    "confirmed_note": "", "detail": "",
}]
v.refresh()
v.combo_period.setCurrentIndex(1)             # 选中 2025-11
check("A4 选到 2025-11", v.combo_period.currentData() == "2025-11",
      str(v.combo_period.currentData()))
v.refresh()
check("A4 refresh 保留当前选中账期", v.combo_period.currentData() == "2025-11",
      str(v.combo_period.currentData()))
check("A4 表格仍是 2025-11 的 1 行", v.page_post.table.rowCount() == 1,
      f"rows={v.page_post.table.rowCount()}")

# ---- 队列在跑时 refresh 不动队列（既有语义：离开再回来可继续）----
v.open_pending(dict(_data), "2025-01", ["张三"], "2025.1台账.xlsx")
check("A5 open_pending → 导入前模式", v.stack.currentWidget() is v.page_pre)
check("A5 队列 1 项", v.pending_count() == 1, str(v.pending_count()))
v.refresh()
check("A5 队列在跑时 refresh 不清队列", v.pending_count() == 1,
      str(v.pending_count()))
check("A5 仍停在导入前模式", v.stack.currentWidget() is v.page_pre)

# ---------------------------------------------------------------- B) reset_pending
v.reset_pending()
check("B1 reset_pending → 队列清空", len(v._queue) == 0 and not v._queue_active,
      f"n={len(v._queue)} active={v._queue_active}")
check("B1 pending_count()==0", v.pending_count() == 0, str(v.pending_count()))
check("B1 回导入后模式", v.stack.currentWidget() is v.page_post)
check("B1 账期下拉重新启用", v.combo_period.isEnabled())
check("B1 离开确认不再拦（无待确认）", v.confirm_leave() is True)

# ---------------------------------------------------------------- C) 清空发信号
_real_path = DB.DB_PATH
_tmp = Path(tempfile.mkdtemp(prefix="dcv_")) / "t.db"
DB.DB_PATH = _tmp
try:
    _c = DB.get_conn()
    _c.executescript(DB.SCHEMA)
    _c.execute("INSERT INTO import_batch (batch_type, period, file_name, "
               "archive_path, file_hash, imported_at) VALUES "
               "('ledger','2025-01','2025.1台账.xls','','','2026-01-01 00:00:00')")
    _c.commit()
    _c.close()

    got = []
    dcv = DataClearView()
    dcv.data_cleared.connect(lambda: got.append(1))
    check("C1 空库建页不崩且有确认按钮", dcv.btn_clear is not None)
    check("C1 未输入字样时按钮禁用", not dcv.btn_clear.isEnabled())

    # 字样不对 → 不弹最终确认、不发信号
    dcv.input.setText("清空数据")
    dcv._do_clear()
    check("C2 字样不对 → 不发信号", got == [], str(got))

    dcv.input.setText("我确认清空数据")
    check("C2 字样正确 → 按钮启用", dcv.btn_clear.isEnabled())
    _before = DB.get_conn()
    _n_before = _before.execute("SELECT COUNT(*) FROM import_batch").fetchone()[0]
    _before.close()
    check("C2 清空前 import_batch 有 1 行", _n_before == 1, str(_n_before))

    dcv._do_clear()
    check("C3 清空成功 → 发 data_cleared 一次", got == [1], str(got))
    _after = DB.get_conn()
    _n_after = _after.execute("SELECT COUNT(*) FROM import_batch").fetchone()[0]
    _after.close()
    check("C3 表已清空", _n_after == 0, str(_n_after))
finally:
    DB.DB_PATH = _real_path
    shutil.rmtree(_tmp.parent, ignore_errors=True)

# ---------------------------------------------------------------- D) 主窗口接线
# 不真清库：直接 emit 信号，验证「收到信号 → 收拾复核页残留」
mw = MW.MainWindow()
check("D1 复核页已急切构造", mw.page_review is not None)
mw.page_review.open_pending(dict(_data), "2025-01", ["张三"], "2025.1台账.xlsx")
mw._refresh_pending_ui()
_badge = mw.sidebar._item_buttons["review"]
check("D2 入队后角标 = 1", _badge.badge() == "1", repr(_badge.badge()))
check("D2 入队后导入页提示条可见", not mw.page_import.banner_pending.isHidden())
check("D2 复核页队列 1 项", mw.page_review.pending_count() == 1,
      str(mw.page_review.pending_count()))

mw._ensure_page("data_clear")
check("D3 data_clear 页已构造并接线",
      hasattr(mw, "page_data_clear") and hasattr(mw.page_data_clear,
                                                 "data_cleared"))
mw.page_data_clear.data_cleared.emit()
check("D4 清空信号 → 复核页队列被重置",
      mw.page_review.pending_count() == 0,
      str(mw.page_review.pending_count()))
check("D4 复核页回导入后模式",
      mw.page_review.stack.currentWidget() is mw.page_review.page_post)
check("D4 角标清空", _badge.badge() == "", repr(_badge.badge()))
check("D4 导入页提示条收起", mw.page_import.banner_pending.isHidden())
check("D4 台账导入守卫放行（不再拦死导入）",
      mw.page_review.blocking_message() is None,
      str(mw.page_review.blocking_message()))

bad = [n for n, ok, _ in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
if bad:
    print("FAILED: " + " | ".join(bad))
    raise SystemExit(1)
