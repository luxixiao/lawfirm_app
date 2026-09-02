"""offscreen 冒烟：ImportReviewView + ReviewPostView（导入复核页，阶段 2）。

覆盖：
- 结构：模式徽章 + 账期下拉 + 堆栈（0=导入后 ReviewPostView / 1=导入前 UnifiedImportDialog）
- 默认导入后模式（账期下拉启用）；open_pending 后切导入前（下拉锁定并显示该账期）
- confirmed → commit_ledger_import 一次 + navigate_back + 回导入后（下拉重新启用）
- cancelled → 不写库 + navigate_back
- 写库失败路径 → 不崩溃 + import_finished(ok=False)
- ReviewPostView：筛选胶囊/统计文案/空账期安全

commit_ledger_import / validate_ledger_before_write / get_conn 均打桩，QMessageBox 换空桩。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QStackedWidget, QWidget

app = QApplication.instance() or QApplication(sys.argv)

import app.ui.import_review_view as RV  # noqa: E402
import app.ui.review_post_view as RP  # noqa: E402
import app.engine.review_compare as RC  # noqa: E402
from app.ui.import_review_view import ImportReviewView  # noqa: E402
from app.ui.review_post_view import ReviewPostView  # noqa: E402
from app.ui.unified_import_dialog import UnifiedImportDialog  # noqa: E402
from app.importer import importer as _imp  # noqa: E402


# ---- 打桩 ----
class _MB:
    @staticmethod
    def warning(*a, **k):
        return None

    @staticmethod
    def information(*a, **k):
        return None


class _FakeCursor:
    def __iter__(self):
        return iter([])

    def fetchall(self):
        return []

    def fetchone(self):
        return None


class _FakeConn:
    def execute(self, *a, **k):
        return _FakeCursor()

    def commit(self):
        pass

    def close(self):
        pass


_commit_calls = []


def _fake_commit(data, period, path):
    _commit_calls.append((data, period, path))
    return {"invoice_count": 2, "prepayment_count": 1}


def _fake_validate(data, period):
    return None


import PySide6.QtWidgets as _qt  # noqa: E402
from app.ui import unified_import_dialog as _uid  # noqa: E402
_qt.QMessageBox = _MB
RV.QMessageBox = _MB
RP.QMessageBox = _MB
_uid.QMessageBox = _MB
_imp.commit_ledger_import = _fake_commit
_imp.validate_ledger_before_write = _fake_validate
RV.commit_ledger_import = _fake_commit
RV.validate_ledger_before_write = _fake_validate
RV.get_conn = lambda: _FakeConn()
RC.get_conn = lambda: _FakeConn()

results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond), extra))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {extra}", flush=True)


_data = {
    "invoices": [], "prepayments": [], "problems": [],
    "sheet_totals": {}, "sheet12_total": 0.0, "period": "2025-01",
}

# ---------------------------------------------------------------- 1) 结构
v = ImportReviewView()
check("ImportReviewView 是 QWidget", isinstance(v, QWidget))
check("含模式徽章与账期下拉", hasattr(v, "lbl_mode") and hasattr(v, "combo_period"))
check("堆栈 2 页", v.stack.count() == 2, f"got={v.stack.count()}")
check("导入后页 = ReviewPostView（阶段2融合实现）", isinstance(v.page_post, ReviewPostView))
check("导入前页 = UnifiedImportDialog", isinstance(v.page_pre, UnifiedImportDialog))
check("默认导入后模式", v.stack.currentWidget() is v.page_post)
check("徽章显示已导入", "已导入" in v.lbl_mode.text(), v.lbl_mode.text())
check("导入后账期下拉启用", v.combo_period.isEnabled())
check("ReviewPostView 有 4 个筛选胶囊", len(v.page_post.chips) == 4,
      str(list(v.page_post.chips)))
check("ReviewPostView 默认「全部」", v.page_post._grp.checkedId() == 0)
check("空账期安全（无批次统计）",
      "该账期没有已导入的发票台账批次" in v.page_post.lbl_stat.text(),
      v.page_post.lbl_stat.text())

# ---------------------------------------------------------------- 2) 导入前入口
backs = []
v.navigate_back.connect(lambda: backs.append(1))
v.open_pending(dict(_data), "2025-01", ["周立生", "陈娟"], "2025.1台账.xlsx")
check("切到导入前模式", v.stack.currentWidget() is v.page_pre)
check("徽章含账期且锁定提示", "导入前" in v.lbl_mode.text() and "2025-01" in v.lbl_mode.text(),
      v.lbl_mode.text())
check("导入前账期下拉锁定", not v.combo_period.isEnabled())
check("下拉显示锁定账期", v.combo_period.currentData() == "2025-01",
      str(v.combo_period.currentData()))
check("面板已载入数据", v.page_pre._data.get("period") == "2025-01")
check("pending 已记录", v._pending == ("2025-01", "2025.1台账.xlsx"), str(v._pending))
check("尚未写库", len(_commit_calls) == 0)

# ---------------------------------------------------------------- 3) 确认入库
fin = []
v.import_finished.connect(lambda f, b, p, m, ok: fin.append((f, b, p, m, ok)))
v.page_pre.accept()
check("确认后写库一次", len(_commit_calls) == 1, f"got={len(_commit_calls)}")
check("写库参数正确",
      _commit_calls and _commit_calls[0][1:] == ("2025-01", "2025.1台账.xlsx"),
      str(_commit_calls[:1]))
check("回导入后模式", v.stack.currentWidget() is v.page_post)
check("发 navigate_back 一次", len(backs) == 1, f"got={len(backs)}")
check("发 import_finished 且 ok=True",
      fin and fin[0][:3] == ("2025.1台账.xlsx", "ledger", "2025-01") and fin[0][4] is True,
      str(fin[:1]))
check("导入后下拉重新启用", v.combo_period.isEnabled())
check("pending 已清空", v._pending is None)

# ---------------------------------------------------------------- 4) 取消
_commit_calls.clear(); backs.clear(); fin.clear()
v.open_pending(dict(_data), "2025-02", [], "2025.2台账.xlsx")
v.page_pre.reject()
check("取消后不写库", len(_commit_calls) == 0, f"got={len(_commit_calls)}")
check("取消后回导入后模式", v.stack.currentWidget() is v.page_post)
check("取消后发 navigate_back", len(backs) == 1)
check("取消后 pending 清空", v._pending is None)

# ---------------------------------------------------------------- 5) 写库失败路径
_commit_calls.clear(); backs.clear(); fin.clear()


def _boom(data, period, path):
    raise RuntimeError("模拟写库失败")


RV.commit_ledger_import = _boom
v.open_pending(dict(_data), "2025-03", [], "2025.3台账.xlsx")
v.page_pre.accept()
check("写库失败不崩溃", True)
check("写库失败回导入后模式", v.stack.currentWidget() is v.page_post)
check("写库失败发 navigate_back", len(backs) == 1)
check("写库失败 import_finished ok=False",
      fin and fin[0][4] is False and "模拟写库失败" in fin[0][3], str(fin[:1]))

# ---------------------------------------------------------------- 6) ReviewPostView 筛选渲染（空数据安全）
pv = v.page_post
for name, chip in pv.chips.items():
    chip.setChecked(True)
    chip.clicked.emit()
check("四个筛选切换不崩溃", True)
pv.chips["全部"].setChecked(True)
pv.chips["全部"].clicked.emit()
check("切回全部", pv.chips["全部"].isChecked() and pv._grp.checkedId() == 0)
pv.set_period("")   # 空账期
check("set_period 空值安全", pv.table.rowCount() == 0)

# ---------------------------------------------------------------- 7) 编辑回写控件（阶段 3）
check("有「编辑回写」按钮且默认禁用", hasattr(pv, "btn_edit") and not pv.btn_edit.isEnabled())
fake_raw = {"id": 1, "invoice_date_raw": "2025-01-05", "invoice_no": "X1",
            "buyer": "甲公司", "amount_raw": "100", "case_no": "",
            "remark": "", "handler_text": "张三100", "kind": "invoice"}
RP.rl.get_row = lambda rid: dict(fake_raw, id=rid)
dlg = RP.WritebackDialog(pv, "2025-01", "X1", fake_raw, 1)
check("WritebackDialog 可构造", dlg is not None)
check("含修改原因输入框", hasattr(dlg, "note_edit"))
check("含收款明细子表", hasattr(dlg, "tbl") and dlg.tbl.columnCount() == 3)
check("收款明细预填 0 行（get_conn 打桩）", dlg.tbl.rowCount() == 0)
dlg._append_row("2025-12", "80.00", "张三")
receipts = dlg._receipts()
check("_receipts 收集正确", receipts == [("张三", 80.0, "2025-12")], str(receipts))
# 选中行 → 编辑按钮启用（注入一条带 raw_id 的假比对行）
pv._rows = [{
    "invoice_no": "X1", "source": "已开票已入账 · 第2行",
    "buyer_src": "甲公司", "buyer_db": "甲公司",
    "amount_src": 100.0, "amount_db": 100.0,
    "handlers_src": "张三 100.00", "handlers_db": "张三 100.00",
    "recv_src": "未收款", "recv_db": "—",
    "status": "一致", "detail": "", "confirmed_note": "",
    "raw_id": 1, "synced": False,
}]
pv._render()
pv.table.selectRow(0)
pv._on_sel()
check("选中源侧行后编辑按钮启用", pv.btn_edit.isEnabled())

bad = [n for n, ok, _ in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
if bad:
    print("FAILED: " + ", ".join(bad))
    sys.exit(1)
