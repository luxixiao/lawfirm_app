"""列设置对话框：显隐 / 冻结 / 顺序 / 手动宽 四态统一管理。"""
from __future__ import annotations

from typing import List

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QHeaderView, QListWidget, QListWidgetItem,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.ui.column_layout import ColumnLayoutManager, MIN_W, SPIN_MAX


def _holder_checked(holder) -> bool:
    """从包了 QCheckBox 的 holder widget 中取出勾选状态。"""
    if holder is None:
        return False
    cb = holder.findChild(QCheckBox)
    return bool(cb.isChecked()) if cb is not None else False


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
        tip = QLabel("「显示」控制显隐；「冻结」冻结该列及其左侧所有列（灰底标记，缩窗时优先不缩）；"
                     "上下移动调整顺序；「宽度」手动锁定列宽，留默认(=内容宽)则仍自动填充。")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        self.tbl = QTableWidget(0, 4)
        self.tbl.setHorizontalHeaderLabels(["显示", "冻结", "列名", "宽度"])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        # 关掉整表选择高亮框 + 焦点框 + 网格线：消除「选中单元格出现的方框」。
        self.tbl.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.tbl.setShowGrid(False)
        self.tbl.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tbl.setStyleSheet(
            "QTableWidget::item { border: none; outline: none; }"
            "QTableWidget::item:focus { border: none; outline: none; }"
        )
        hdr = self.tbl.horizontalHeader()
        # 显示/冻结两列：列宽按各自表头标题宽度自适应（考虑「显示」「冻结」标题），固定不可拖。
        fm = QFontMetrics(hdr.font())
        for col in (0, 1):
            label = self.tbl.horizontalHeaderItem(col).text()
            w = int(fm.horizontalAdvance(label)) + 16
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self.tbl.setColumnWidth(col, max(MIN_W, min(w, 80)))
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)  # 列名：撑满剩余
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)    # 宽度：固定
        hdr.setStretchLastSection(False)
        self.tbl.setColumnWidth(3, 90)
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
            frz = self.state["frozen"].get(k, False)
            # 显示 / 冻结 两列：用 QCheckBox widget（包一层 QWidget 居中），避开 item 焦点框；
            # 原生 checkbox 自带黑色描边，取消勾选也清晰可见。
            self.tbl.setCellWidget(r, 0, self._make_checkbox(vis))
            self.tbl.setCellWidget(r, 1, self._make_checkbox(frz))
            ni = QTableWidgetItem(k)
            ni.setFlags(ni.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.tbl.setItem(r, 2, ni)
            sp = QSpinBox()
            sp.setRange(MIN_W, SPIN_MAX)
            sp.setSingleStep(10)
            sp.setValue(self.state["widths"].get(k) or int(round(cw.get(k, 100))))
            self.tbl.setCellWidget(r, 3, sp)

    @staticmethod
    def _make_checkbox(checked: bool) -> QWidget:
        cb = QCheckBox()
        cb.setChecked(checked)
        cb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        holder = QWidget()
        hb = QHBoxLayout(holder)
        hb.setContentsMargins(0, 0, 0, 0)
        hb.addStretch(1)
        hb.addWidget(cb)
        hb.addStretch(1)
        return holder

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
        frozen: dict = {}
        widths: dict = {}
        for r in range(self.tbl.rowCount()):
            k = order[r]
            visible[k] = _holder_checked(self.tbl.cellWidget(r, 0))
            frozen[k] = _holder_checked(self.tbl.cellWidget(r, 1))
            sp = self.tbl.cellWidget(r, 3)
            val = sp.value()
            if val != int(round(cw.get(k, val))):
                widths[k] = val
        new_order = list(order)
        self.state = {"order": new_order, "visible": visible, "frozen": frozen, "widths": widths}
        self.mgr.save(self.state)
        self.accept()
