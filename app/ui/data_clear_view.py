"""数据情况页（需求 1）：需输入「我确认清空数据」验证后才可清空

- 仅清空业务数据：invoice / charge_detail / collection / refund / prepayment /
  prepayment_offset / expense_ledger / raw_invoice / import_batch / change_log。
- 保留基础/维护数据：staff（职工花名册）、expense_cat（费用类型维护）、snapshot（快照备份）。
- 清空后执行 VACUUM 回收空间。
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from app.ui.widgets import (CaptionLabel, PageHeader, PrimaryPushButton)
from app.db import get_conn

_CONFIRM_TEXT = "我确认清空数据"

_CLEAR_TABLES = [
    "raw_invoice", "charge_detail", "collection", "refund",
    "prepayment_offset", "prepayment", "expense_ledger", "invoice",
    "import_batch", "change_log",
]
_KEEP_TABLES = ["staff（职工花名册）", "expense_cat（费用类型维护）", "snapshot（快照备份）"]


class DataClearView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(14)

        lay.addWidget(PageHeader(
            "数据情况",
            "清空全部业务数据（重新导入前使用）。基础数据将保留，详见下方说明。",
        ))

        info = QPlainTextEdit()
        info.setReadOnly(True)
        info.setMaximumHeight(120)
        info.setPlainText(
            "将清空：\n  · " + "\n  · ".join(_CLEAR_TABLES) +
            "\n\n将保留：\n  · " + "\n  · ".join(_KEEP_TABLES) +
            "\n\n清空操作不可撤销，请确认已导出或备份所需数据。")
        lay.addWidget(info)

        bar = QHBoxLayout()
        bar.addWidget(CaptionLabel("请输入：「我确认清空数据」"))
        self.input = QLineEdit()
        self.input.setPlaceholderText(_CONFIRM_TEXT)
        self.input.textChanged.connect(self._on_text)
        bar.addWidget(self.input, 1)
        lay.addLayout(bar)

        self.btn_clear = PrimaryPushButton("清空数据")
        self.btn_clear.setEnabled(False)
        self.btn_clear.clicked.connect(self._do_clear)
        lay.addWidget(self.btn_clear)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(120)
        self.log.setPlaceholderText("操作结果…")
        lay.addWidget(self.log)
        lay.addStretch()

    def _on_text(self, text: str) -> None:
        self.btn_clear.setEnabled(text.strip() == _CONFIRM_TEXT)

    def _do_clear(self) -> None:
        if self.input.text().strip() != _CONFIRM_TEXT:
            QMessageBox.warning(self, "验证失败", f"请输入准确字样：「{_CONFIRM_TEXT}」")
            return
        ret = QMessageBox.question(
            self, "最终确认",
            "即将清空全部业务数据，此操作不可撤销！\n\n保留：职工花名册 / 费用类型维护 / 快照。\n确定继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        conn = get_conn()
        try:
            counts = []
            for tbl in _CLEAR_TABLES:
                n = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                conn.execute(f"DELETE FROM {tbl}")
                counts.append(f"{tbl}: {n} 行")
            conn.commit()
            conn.execute("VACUUM")
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            QMessageBox.critical(self, "清空失败", str(e))
            return
        finally:
            conn.close()
        self.log.setPlainText("已清空：\n  · " + "\n  · ".join(counts) +
                              "\n\n基础数据已保留（职工花名册 / 费用类型维护 / 快照）。\n"
                              "建议重新从「导入」页导入台账。")
        QMessageBox.information(self, "完成", "业务数据已清空。")
