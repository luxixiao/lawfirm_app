"""导入复核页（合并原「导入确认」tab2 与「导入校验」独立页）

双模式（顶部徽章只读展示，非手动切换开关）：
- 导入前：发票台账源文件解析完成后由导入页进入本页，账期锁定（以文件名为准），
  复用 UnifiedImportDialog 的就地修正/确认能力，点「确认入库」才写库。
- 导入后：默认模式，可切换账期，对已入库数据做核对与管理。
  阶段 1 暂以内嵌既有 ImportVerifyView 承载（其自带账期下拉与标题），
  阶段 2 将替换为「raw_ledger 镜表 ↔ 业务表」融合比对实现并删除旧页
  （spec: design/import_review_spec.md §2/§10）。

数据流（导入前）：真实 data 全程不改，修正只作用于工作副本；「确认入库」时
commit_ledger_import 一次性写库；点「取消」零副作用。
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QMessageBox, QStackedWidget, QVBoxLayout, QWidget

from app.importer.importer import commit_ledger_import, validate_ledger_before_write
from app.ui.import_verify_view import ImportVerifyView
from app.ui.unified_import_dialog import UnifiedImportDialog
from app.ui.widgets import CaptionLabel


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

        # ---- 顶部模式条（徽章只读展示；账期下拉随阶段 2 的融合实现一并引入）----
        bar = QHBoxLayout()
        bar.setContentsMargins(24, 14, 24, 0)
        bar.setSpacing(8)
        self.lbl_mode = CaptionLabel("")
        bar.addWidget(self.lbl_mode)
        bar.addStretch()
        lay.addLayout(bar)

        # ---- 内容堆栈：0=导入后(默认) 1=导入前 ----
        self.stack = QStackedWidget()
        self.page_post = ImportVerifyView()  # 阶段 2 替换为镜表↔业务表融合实现
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
            self.stack.setCurrentWidget(self.page_pre)
        else:
            self.lbl_mode.setText("已导入")
            self.stack.setCurrentWidget(self.page_post)

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
            self._set_mode("post")
            self.navigate_back.emit()
            return
        msg = (f"✓ 发票台账 {period}: {r['invoice_count']} 张发票, "
               f"{r['prepayment_count']} 条预收款")
        QMessageBox.information(self, "导入成功", msg)
        self.import_finished.emit(fname, "ledger", period, msg, True)
        # 确认入库后回到导入后模式（用户可立即核对刚导入的账期）
        self._set_mode("post")
        self.navigate_back.emit()

    def _on_cancelled(self) -> None:
        self._pending = None
        self._set_mode("post")
        self.navigate_back.emit()
