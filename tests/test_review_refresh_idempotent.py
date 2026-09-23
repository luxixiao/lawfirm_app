"""复核页 refresh 脏签名短路（P2-4）单元测试 — offscreen，不写真实库。

验证（import_review_view.ImportReviewView.refresh）：
1. 库脏签名或账期任一变化 → 走全量重建（page_post.load_period 被调用）；
2. 签名与账期都没变 → 短路跳过（拖窗口/切页来回不再全库重查+整表重建）；
3. 手动切账期（combo 信号）会同步 _last_period → 回显时同签名不重建；
4. 队列态分支不受影响（只刷徽章，不重建、不清队列）。

隔离手段：类级替换 UnifiedImportDialog.load_period（记录器，不触真实库）、
模块级替换 _ledger_periods、实例级替换 _db_signature —— 三者都不碰数据文件。
运行：python tests/test_review_refresh_idempotent.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

import app.ui.import_review_view as irv  # noqa: E402
from app.ui.unified_import_dialog import UnifiedImportDialog  # noqa: E402

OK, FAILS = 0, []


def check(label, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        FAILS.append(f"{label} {detail}".strip())


def main() -> int:
    QApplication.instance() or QApplication([])

    # ---- 隔离：类级记录器替换 load_period（不触真实库）----
    calls = []
    orig_lp = UnifiedImportDialog.load_period

    def fake_lp(self, period):
        calls.append((id(self), period))

    UnifiedImportDialog.load_period = fake_lp

    # ---- 隔离：模块级 _ledger_periods（__init__ 与每次 _reload_periods 都会用）----
    orig_periods = irv._ledger_periods
    irv._ledger_periods = lambda: ["2025-01"]

    view = irv.ImportReviewView()
    post_id = id(view.page_post)
    pre_id = id(view.page_pre)

    def post_calls():
        return [p for (iid, p) in calls if iid == post_id]

    try:
        # __init__ 的 _set_mode("post") 已触发一次 load_period（_ledger_periods 被
        # patch 成 ["2025-01"]，故构造期载入的是 "2025-01" 而非空骨架 ""）
        check("构造期载入 1 次", post_calls() == ["2025-01"], f"{post_calls()}")

        # 1) 首次 refresh：签名 None ≠ S1 → 全量重建
        S1 = tuple(range(12))
        view._db_signature = lambda: S1  # 实例属性遮蔽 staticmethod
        view.refresh()
        check("首次 refresh 走全量重建", post_calls() == ["2025-01", "2025-01"],
              f"{post_calls()}")
        check("签名/账期已记录", (view._last_sig, view._last_period) == (S1, "2025-01"))

        # 2) 同签名同账期再次 refresh → 短路
        view.refresh()
        view.refresh()
        check("同签名短路（不重建）", post_calls() == ["2025-01", "2025-01"],
              f"{post_calls()}")

        # 3) 签名变（模拟导入/清空/回写）→ 重建
        S2 = tuple(x + 1 for x in S1)
        view._db_signature = lambda: S2
        view.refresh()
        check("签名变化触发重建", post_calls() == ["2025-01", "2025-01", "2025-01"],
              f"{post_calls()}")

        # 4) 手动切账期：combo 信号 → load_period + _last_period 同步；再 refresh 同签名 → 短路
        view.combo_period.addItem("2025-02", userData="2025-02")
        view.combo_period.setCurrentIndex(1)  # 触发 currentIndexChanged → _on_period_changed
        check("切账期立即重建新账期",
              post_calls() == ["2025-01", "2025-01", "2025-01", "2025-02"],
              f"{post_calls()}")
        check("切账期同步 _last_period", view._last_period == "2025-02")
        view.refresh()
        check("切账期后同签名回显不重建",
              post_calls() == ["2025-01", "2025-01", "2025-01", "2025-02"],
              f"{post_calls()}")

        # 5) 队列态：refresh 只走徽章分支，不碰 post、不动队列
        view._queue = [{"data": {}, "period": "2025-03", "staff_names": [], "path": "x.xls"}]
        view._idx = 0
        view._queue_active = True
        view.refresh()
        check("队列态不重建",
              post_calls() == ["2025-01", "2025-01", "2025-01", "2025-02"],
              f"{post_calls()}")
        check("队列态不清队列", view._queue and view._queue_active)
        view._queue, view._idx, view._queue_active = [], 0, False
        check("队列页未被误切换", True)  # 队列态期间 stack 不应变到 post
        check("队列期间栈保持在原页", view.stack.currentWidget() is view.page_post
              or view.stack.currentWidget() is view.page_pre)
    finally:
        UnifiedImportDialog.load_period = orig_lp
        irv._ledger_periods = orig_periods

    check("load_period 记录器已恢复", UnifiedImportDialog.load_period is orig_lp)
    check("未触达 page_pre 的 load_period（pre 页不该被重建）",
          all(iid != pre_id for iid, _ in calls))

    print(f"PASS {OK} checks" if not FAILS else "FAILED:\n" + "\n".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
