"""快照管理页：保存 / 恢复 / 删除"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from qfluentwidgets import (SubtitleLabel, CaptionLabel, PrimaryPushButton, PushButton)
from app.system.snapshot import delete_snapshot, list_snapshots, restore_snapshot, save_snapshot


class SnapshotView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)

        t = SubtitleLabel("快照")
        lay.addWidget(t)
        h = QLabel("保存当前完整数据为快照；恢复快照可回到该时点。每次导入前自动保存快照。")
        h.setStyleSheet("color:#8A8886;")
        lay.addWidget(h)

        btns = QHBoxLayout()
        self.btn_save = QPushButton("保存快照")
        self.btn_save.setObjectName("primary")
        self.btn_save.clicked.connect(self.save_now)
        self.btn_restore = QPushButton("恢复所选快照")
        self.btn_restore.clicked.connect(self.restore_now)
        self.btn_delete = QPushButton("删除所选快照")
        self.btn_delete.clicked.connect(self.delete_now)
        for b in (self.btn_save, self.btn_restore, self.btn_delete):
            btns.addWidget(b)
        btns.addStretch()
        lay.addLayout(btns)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["快照名", "时间", "备注"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table)

        self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802
        """切换到本页时自动刷新数据"""
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        snaps = list_snapshots()
        self.table.setRowCount(len(snaps))
        self._meta = {}
        for r, s in enumerate(snaps):
            self.table.setItem(r, 0, QTableWidgetItem(s["name"]))
            self.table.setItem(r, 1, QTableWidgetItem(s["created_at"]))
            self.table.setItem(r, 2, QTableWidgetItem(s["note"] or ""))
            self._meta[r] = s["id"]

    def save_now(self) -> None:
        name, ok = QInputDialog.getText(self, "保存快照", "快照名称：", text=f"手动快照 {self._now()}")
        if not ok:
            return
        save_snapshot(name or "手动快照", "手动保存")
        self.refresh()

    @staticmethod
    def _now() -> str:
        from datetime import datetime
        return datetime.now().strftime("%m-%d %H:%M")

    def restore_now(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选择一份快照")
            return
        snap_id = self._meta[row]
        if QMessageBox.question(self, "确认", "恢复将覆盖当前全部数据，确定继续？") != QMessageBox.StandardButton.Yes:
            return
        msg = restore_snapshot(snap_id)
        QMessageBox.information(self, "恢复", msg)
        self.refresh()

    def delete_now(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选择一份快照")
            return
        snap_id = self._meta[row]
        if QMessageBox.question(self, "确认", "确定删除该快照？") != QMessageBox.StandardButton.Yes:
            return
        delete_snapshot(snap_id)
        self.refresh()
