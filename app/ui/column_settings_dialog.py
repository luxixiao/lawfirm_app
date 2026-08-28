"""列设置对话框：显隐 / 顺序 / 手动宽（冻结为底层保留能力，不在对话框暴露）。"""
from __future__ import annotations

from typing import List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from app.ui.column_layout import ColumnLayoutManager, MIN_W, SPIN_MAX


def open_column_settings(parent, mgr: ColumnLayoutManager, keys: List[str],
                         state: dict, content_w: List[int], movable: bool = True) -> bool:
    """打开列设置对话框；用户确认返回 True（mgr 已保存新状态）。

    movable=False 时禁用顺序调整（用于内置冻结列的首列冻结表，避免打乱冻结顺序）。
    """
    dlg = _ColSettingsDlg(parent, mgr, keys, state, content_w, movable)
    return dlg.exec() == QDialog.DialogCode.Accepted


class _ColSettingsDlg(QDialog):
    def __init__(self, parent, mgr: ColumnLayoutManager, keys: List[str],
                 state: dict, content_w: List[int], movable: bool = True) -> None:
        super().__init__(parent)
        self._movable = movable
        self.mgr = mgr
        self.keys = keys
        self.state = state
        self.content_w = content_w
        self.setWindowTitle("列设置")
        self.resize(480, 480)

        lay = QVBoxLayout(self)
        tip = QLabel("「显示」控制显隐；上下移动调整顺序；「宽度」手动锁定列宽，"
                     "留默认(=内容宽)则仍自动填充。")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        self.tbl = QTableWidget(0, 3)
        self.tbl.setHorizontalHeaderLabels(["显示", "列名", "宽度"])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tbl.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.tbl.horizontalHeader().setStretchLastSection(False)
        self.tbl.setColumnWidth(0, 50)
        self.tbl.setColumnWidth(1, 220)
        self.tbl.setColumnWidth(2, 90)
        self._fill()
        lay.addWidget(self.tbl, 1)

        move = QHBoxLayout()
        self.up = QPushButton("↑ 上移")
        self.down = QPushButton("↓ 下移")
        self.up.clicked.connect(lambda: self._move(-1))
        self.down.clicked.connect(lambda: self._move(1))
        move.addStretch()
        move.addWidget(self.up)
        move.addWidget(self.down)
        if not self._movable:
            self.up.setEnabled(False)
            self.down.setEnabled(False)
        lay.addLayout(move)

        btns = QHBoxLayout()
        self.reset_btn = QPushButton("重置布局")
        self.reset_btn.clicked.connect(self._reset)
        btns.addWidget(self.reset_btn)
        btns.addStretch()
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self._accept)
        box.rejected.connect(self.reject)
        btns.addWidget(box)
        lay.addLayout(btns)

    def _fill(self) -> None:
        order = self.state["order"]
        cw = {k: self.content_w[i] for i, k in enumerate(self.keys)}
        self.tbl.setRowCount(len(order))
        for r, k in enumerate(order):
            vis = self.state["visible"].get(k, True)
            # 用 QCheckBox widget 替代 QTableWidgetItem，彻底避开 item 绘制出的空文本区/焦点框小方块。
            cb = QCheckBox()
            cb.setChecked(vis)
            cb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            cb.setStyleSheet("QCheckBox { margin: 0px; padding: 0px; spacing: 0px; border: none; background: transparent; }"
                             "QCheckBox::indicator { width: 16px; height: 16px; }")
            self.tbl.setCellWidget(r, 0, cb)
            ni = QTableWidgetItem(k)
            ni.setFlags(ni.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.tbl.setItem(r, 1, ni)
            sp = QSpinBox()
            sp.setRange(MIN_W, SPIN_MAX)
            sp.setSingleStep(10)
            sp.setValue(self.state["widths"].get(k) or int(round(cw.get(k, 100))))
            self.tbl.setCellWidget(r, 2, sp)

    def _move(self, d: int) -> None:
        r = self.tbl.currentRow()
        if r < 0:
            return
        nr = r + d
        if 0 <= nr < self.tbl.rowCount():
            order = self.state["order"]
            order[r], order[nr] = order[nr], order[r]
            self._fill()
            self.tbl.selectRow(nr)

    def _reset(self) -> None:
        self.mgr.reset()
        self.state = self.mgr.load(self.keys)
        self._fill()

    def _accept(self) -> None:
        order = self.state["order"]
        cw = {k: self.content_w[i] for i, k in enumerate(self.keys)}
        visible: dict = {}
        # 冻结状态沿用既有布局：对话框已移除「冻结」勾选列，不再允许在对话框内改动冻结，
        # 仅保留底层布局里已有的 frozen 标记（避免把已冻结列误解冻）。
        frozen: dict = dict(self.state.get("frozen", {}))
        widths: dict = {}
        for r in range(self.tbl.rowCount()):
            k = order[r]
            w = self.tbl.cellWidget(r, 0)
            vis = w.isChecked() if isinstance(w, QCheckBox) else False
            visible[k] = vis
            sp = self.tbl.cellWidget(r, 2)
            val = sp.value()
            if val != int(round(cw.get(k, val))):
                widths[k] = val
        new_order = list(order)
        self.state = {"order": new_order, "visible": visible, "frozen": frozen, "widths": widths}
        self.mgr.save(self.state)
        self.accept()
