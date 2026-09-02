"""offscreen 冒烟：ImportReviewView（导入复核页，阶段 1）。

覆盖：
- 结构：顶部模式徽章 + 堆栈（0=导入后 ImportVerifyView 内嵌 / 1=导入前 UnifiedImportDialog）
- 默认导入后模式；徽章文案随模式切换
- open_pending → 切导入前模式、徽章带账期、load_data 生效
- confirmed（page_pre.accept()）→ commit_ledger_import 被调一次 + navigate_back + 回导入后模式
- cancelled（page_pre.reject()）→ 不写库 + navigate_back + 回导入后模式
- import_finished 信号携带 (file_name, batch_type, period, msg, ok)

commit_ledger_import / validate_ledger_before_write 打桩，QMessageBox 换空桩。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QStackedWidget, QWidget

app = QApplication.instance() or QApplication(sys.argv)

import app.ui.import_review_view as RV  # noqa: E402
from app.ui.import_review_view import ImportReviewView  # noqa: E402
from app.ui.import_verify_view import ImportVerifyView  # noqa: E402
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
_uid.QMessageBox = _MB
_imp.commit_ledger_import = _fake_commit
_imp.validate_ledger_before_write = _fake_validate
RV.commit_ledger_import = _fake_commit
RV.validate_ledger_before_write = _fake_validate

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
check("含模式徽章", hasattr(v, "lbl_mode"))
check("含内容堆栈", isinstance(v.stack, QStackedWidget))
check("堆栈 2 页（导入后/导入前）", v.stack.count() == 2, f"got={v.stack.count()}")
check("导入后页 = ImportVerifyView 内嵌（阶段1过渡）",
      isinstance(v.page_post, ImportVerifyView))
check("导入前页 = UnifiedImportDialog", isinstance(v.page_pre, UnifiedImportDialog))
check("默认导入后模式", v.stack.currentWidget() is v.page_post)
check("徽章显示已导入", "已导入" in v.lbl_mode.text(), v.lbl_mode.text())

# ---------------------------------------------------------------- 2) 导入前入口
backs = []
v.navigate_back.connect(lambda: backs.append(1))
v.open_pending(dict(_data), "2025-01", ["周立生", "陈娟"], "2025.1台账.xlsx")
check("切到导入前模式", v.stack.currentWidget() is v.page_pre)
check("徽章含账期且锁定提示", "导入前" in v.lbl_mode.text() and "2025-01" in v.lbl_mode.text(),
      v.lbl_mode.text())
check("面板已载入数据", v.page_pre._data.get("period") == "2025-01")
check("pending 已记录", v._pending == ("2025-01", "2025.1台账.xlsx"), str(v._pending))
check("尚未写库", len(_commit_calls) == 0)
check("尚未回导航", len(backs) == 0)

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

bad = [n for n, ok, _ in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
if bad:
    print("FAILED: " + ", ".join(bad))
    sys.exit(1)
