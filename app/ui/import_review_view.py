"""导入复核页（合并原「导入确认」tab2 与「导入校验」独立页）

双模式（顶部徽章只读展示，非手动切换开关）：
- 导入前：发票台账源文件解析完成后由导入页进入本页，账期锁定（以文件名为准），
  复用 UnifiedImportDialog 的就地修正/确认能力，点「确认入库」才写库。
- 导入后：默认模式。顶部账期下拉可切换（同账期多批次以最新为准），
  内容为 ReviewPostView 的「raw_ledger 镜表 ↔ 业务表」融合比对单表
  （阶段 2 起取代旧 ImportVerifyView 三维度切换，spec §2/§4/§10）。

数据流（导入前）：真实 data 全程不改，修正只作用于工作副本；「确认入库」时
commit_ledger_import 一次性写库；点「取消」零副作用。
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QMessageBox, QStackedWidget, QVBoxLayout, QWidget,
)

from app.db import get_conn
from app.importer.importer import commit_ledger_import, validate_ledger_before_write
from app.ui.review_post_view import ReviewPostView
from app.ui.unified_import_dialog import UnifiedImportDialog
from app.ui.widgets import CaptionLabel, ComboBox


def _ledger_periods() -> list:
    conn = get_conn()
    try:
        return [r["period"] for r in conn.execute(
            "SELECT DISTINCT period FROM import_batch "
            "WHERE batch_type='ledger' AND status='active' ORDER BY period DESC")]
    finally:
        conn.close()


class ImportReviewView(QWidget):
    """导入复核：导入前确认/修正 + 导入后核对（双模式单页）。"""

    navigate_back = Signal()
    # file_name, batch_type, period, msg, ok
    import_finished = Signal(str, str, str, str, bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # ---- 顶部模式条：徽章 + 账期下拉（导入后可切、导入前锁定）----
        bar = QHBoxLayout()
        bar.setContentsMargins(24, 14, 24, 0)
        bar.setSpacing(8)
        self.lbl_mode = CaptionLabel("")
        bar.addWidget(self.lbl_mode)
        bar.addWidget(CaptionLabel("账期"))
        self.combo_period = ComboBox()
        self.combo_period.setMinimumWidth(120)
        self.combo_period.currentIndexChanged.connect(self._on_period_changed)
        bar.addWidget(self.combo_period)
        bar.addStretch()
        lay.addLayout(bar)

        # ---- 内容堆栈：0=导入后(默认) 1=导入前 ----
        self.stack = QStackedWidget()
        self.page_post = ReviewPostView()
        self.page_pre = UnifiedImportDialog(parent=self)
        self.page_pre.confirmed.connect(self._on_confirmed)
        self.page_pre.cancelled.connect(self._on_cancelled)
        self.stack.addWidget(self.page_post)
        self.stack.addWidget(self.page_pre)
        lay.addWidget(self.stack, 1)

        self._pending = None  # (period, path) 等待确认入库的数据
        self._set_mode("post")

    # ------------------------------------------------------------------ #
    # 模式
    # ------------------------------------------------------------------ #
    def _set_mode(self, mode: str, period: str = "") -> None:
        if mode == "pre":
            self.lbl_mode.setText(f"导入前 · 待确认　{period}")
            self.combo_period.blockSignals(True)
            self.combo_period.clear()
            self.combo_period.addItem(period, userData=period)
            self.combo_period.setEnabled(False)  # 导入前账期按文件名锁定
            self.combo_period.blockSignals(False)
            self.stack.setCurrentWidget(self.page_pre)
        else:
            self.lbl_mode.setText("已导入")
            self.combo_period.setEnabled(True)
            self._reload_periods(prefer=period)
            self.stack.setCurrentWidget(self.page_post)

    def _reload_periods(self, prefer: str = "") -> None:
        periods = _ledger_periods()
        self.combo_period.blockSignals(True)
        self.combo_period.clear()
        for p in periods:
            self.combo_period.addItem(p, userData=p)
        if prefer and prefer in periods:
            self.combo_period.setCurrentIndex(periods.index(prefer))
        cur = self.combo_period.currentData() or ""
        self.combo_period.blockSignals(False)
        # 手动驱动一次（blockSignals 期间信号被抑制）
        self.page_post.set_period(cur)

    def _on_period_changed(self, *_):
        if self.stack.currentWidget() is self.page_post:
            self.page_post.set_period(self.combo_period.currentData() or "")

    # ------------------------------------------------------------------ #
    # 导入前入口（由导入页发起）
    # ------------------------------------------------------------------ #
    def open_pending(self, data: dict, period: str, staff_names, path: str) -> None:
        """载入待确认数据并切到导入前模式（账期锁定）。"""
        self._pending = (period, path)
        validator = lambda d, _p=period: validate_ledger_before_write(d, _p)  # noqa: E731
        self.page_pre.load_data(data, period, staff_names, path, validator)
        self._set_mode("pre", period)

    # ------------------------------------------------------------------ #
    # 导入前确认 / 取消
    # ------------------------------------------------------------------ #
    def _on_confirmed(self) -> None:
        if self._pending is None:
            return
        period, path = self._pending
        self._pending = None
        fname = path.replace("\\", "/").split("/")[-1]
        try:
            r = commit_ledger_import(self.page_pre._data, period, path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "导入失败", str(e))
            self.import_finished.emit(fname, "ledger", period,
                                      f"✗ {fname}: {e}", False)
            self._set_mode("post", period)
            self.navigate_back.emit()
            return
        msg = (f"✓ 发票台账 {period}: {r['invoice_count']} 张发票, "
               f"{r['prepayment_count']} 条预收款")
        QMessageBox.information(self, "导入成功", msg)
        self.import_finished.emit(fname, "ledger", period, msg, True)
        # 确认入库后回到导入后模式（账期定位到刚导入的月，可立即核对）
        self._set_mode("post", period)
        self.navigate_back.emit()

    def _on_cancelled(self) -> None:
        self._pending = None
        self._set_mode("post")
        self.navigate_back.emit()
