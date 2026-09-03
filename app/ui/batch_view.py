"""导入记录页：批次列表 + 撤销 / 重新导入"""
from __future__ import annotations

from app.ui.column_layout import install_column_layout
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.ui.widgets import (page_header, PrimaryPushButton, PushButton)
from app.db import get_conn
from app.importer.importer import rollback_batch

TYPE_TEXT = {"invoice": "销项", "ledger": "发票台账", "expense": "费用台账", "staff": "职工清单"}


class BatchView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)

        lay.addWidget(page_header("导入记录", "撤销导入只删除该批次导入的数据，手动补录数据不受影响。重新导入同月同类型文件会自动覆盖。"))

        btns = QHBoxLayout()
        self.btn_rollback = QPushButton("撤销所选批次")
        self.btn_rollback.setObjectName("primary")
        self.btn_rollback.clicked.connect(self.rollback_now)
        btns.addWidget(self.btn_rollback)
        btns.addStretch()
        lay.addLayout(btns)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["类型", "账期", "文件名", "导入时间", "状态", "存档路径"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        from app.ui.table_features import install_common_features, install_header_filter
        install_common_features(self.table)
        install_header_filter(self.table)
        self._col = install_column_layout(self.table, "batch", "main")
        lay.addWidget(self.table)

        self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802
        """切换到本页时自动刷新数据"""
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM import_batch ORDER BY id DESC"
            ).fetchall()
        finally:
            conn.close()
        self.table.setRowCount(len(rows))
        self._meta = {}
        for r, row in enumerate(rows):
            vals = [TYPE_TEXT.get(row["batch_type"], row["batch_type"]), row["period"],
                    row["file_name"], row["imported_at"], "有效" if row["status"] == "active" else "已撤销",
                    row["archive_path"] or ""]
            for c, v in enumerate(vals):
                item = QTableWidgetItem("" if v is None else str(v))
                if row["status"] != "active" and c == 4:
                    item.setForeground(__import__("PySide6.QtGui", fromlist=["QColor"]).QColor("#787774"))
                self.table.setItem(r, c, item)
            self._meta[r] = row["id"]

        self._col.apply()

    def rollback_now(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选择一条导入记录")
            return
        batch_id = self._meta[row]
        if QMessageBox.question(self, "确认", "确定撤销该批次导入？\n（手动补录数据不受影响）") != QMessageBox.StandardButton.Yes:
            return
        conn = get_conn()
        try:
            rollback_batch(conn, batch_id)
            conn.commit()
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            QMessageBox.warning(self, "撤销失败", str(e))
            return
        finally:
            conn.close()
        QMessageBox.information(self, "已撤销", "该批次数据已删除")
        self.refresh()
