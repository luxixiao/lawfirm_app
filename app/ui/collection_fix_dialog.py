"""已收认定 · 逐行手动修正对话框（方案1优化版：行级合并，绝不整票 DELETE）。

弹窗展示某发票当前 collection 收款行（可编辑），并并排显示「源声称收款」作只读参考。
确定时按 id 做行级合并（改了才 UPDATE、消失才 DELETE、新增才 INSERT），
从源头消除「一键修正删光后续月份收款」的破坏性；并同步 received_snapshot.actual。

UI 实现说明（方案 C：delegate 而非常驻 cellWidget）：
- 日期 / 金额 / 经办人 三列的数据存在 QTableWidgetItem 中，平时以文本展示；
- 金额列挂 AmountDelegate，双击进入编辑时浮出 QDoubleSpinBox（编辑器尺寸贴合单元格，
  不裁切）；日期/经办人列用默认 QLineEdit 编辑；
- 彻底弃用「setCellWidget 常驻嵌入 spinbox」——它会被行矩形裁掉底部，正是截断根因。
"""
from __future__ import annotations

import json
from typing import Dict, List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractSpinBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QHBoxLayout, QLabel, QPushButton, QStyledItemDelegate, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from app.db import get_conn
from app.importer.importer import (
    _refresh_snapshot_actual, merge_collection_for_invoice,
)


def _money(v) -> str:
    return f"{v:,.2f}" if isinstance(v, (int, float)) else str(v or "")


class AmountDelegate(QStyledItemDelegate):
    """金额列编辑代理：浮出 QDoubleSpinBox，尺寸贴合单元格、不裁切。"""

    def createEditor(self, parent, option, index):  # noqa: N802
        spin = QDoubleSpinBox(parent)
        spin.setRange(0, 9_999_999_999)
        spin.setDecimals(2)
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)  # 紧凑、不裁底
        spin.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return spin

    def setEditorData(self, editor, index):  # noqa: N802
        try:
            val = float(index.data(Qt.ItemDataRole.EditRole) or 0.0)
        except (TypeError, ValueError):
            val = 0.0
        editor.setValue(val)

    def setModelData(self, editor, model, index):  # noqa: N802
        val = editor.value()
        model.setData(index, f"{val:.2f}", Qt.ItemDataRole.EditRole)
        model.setData(index, val, Qt.ItemDataRole.UserRole)


class CollectionFixDialog(QDialog):
    """逐发票收款明细手动修正。"""

    def __init__(self, parent, invoice_no: str, total_amount: float,
                 expected_items: List[Dict], current_rows: List[Dict],
                 expected_total: float, batch_id: int) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"手动修正收款 · 发票 {invoice_no}")
        self.resize(580, 460)
        self.invoice_no = invoice_no
        self.batch_id = batch_id
        self.expected_items = expected_items
        self.expected_total = expected_total
        self.expected_json = json.dumps(
            {"total": expected_total, "items": expected_items}, ensure_ascii=False)
        self.current_rows = current_rows  # [{id, receipt_date, amount, person_name}]

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)

        info = QLabel(
            f"发票号：{invoice_no}    价税合计：{_money(total_amount)}    "
            f"源声称已收合计：{_money(expected_total)}")
        info.setObjectName("dialogInfo")
        lay.addWidget(info)

        ref = QLabel("参考（源声称收款，只读）： " + (
            "、".join(f"{it.get('ym', '')} {_money(it.get('amount', 0))}" for it in expected_items)
            or "未收款 / 红字"))
        ref.setWordWrap(True)
        ref.setObjectName("dialogRef")
        lay.addWidget(ref)

        self.table = QTableWidget(self)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(
            ["收款日期 (YYYY-MM-DD)", "金额", "经办人", "操作"])
        # 方案 C：双击/选中即编辑，文本常驻展示；金额列挂 delegate
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.setItemDelegateForColumn(1, AmountDelegate(self.table))
        self.table.itemChanged.connect(lambda *_: self._update_actual())
        lay.addWidget(self.table, 1)

        bar = QHBoxLayout()
        self.btn_add = QPushButton("+ 新增收款行")
        self.btn_add.clicked.connect(self._add_row)
        self.btn_fill = QPushButton("按源填充")
        self.btn_fill.clicked.connect(self._fill_from_expected)
        self.lbl_actual = QLabel("实际合计：—")
        bar.addWidget(self.btn_add)
        bar.addWidget(self.btn_fill)
        bar.addStretch()
        bar.addWidget(self.lbl_actual)
        lay.addLayout(bar)

        self.bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.bb.accepted.connect(self._on_ok)
        self.bb.rejected.connect(self.reject)
        lay.addWidget(self.bb)

        self._load()

    # ---------------------------------------------------------------- #
    def _load(self) -> None:
        self.table.setRowCount(0)
        for r in self.current_rows:
            self._append_row(r.get("id"), r.get("receipt_date", "")[:10],
                             r.get("amount", 0.0), r.get("person_name", ""))
        self._update_actual()

    def _append_row(self, rowid, date: str, amount, person: str) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        # 日期（文本常驻，双击编辑）
        di = QTableWidgetItem(date or "")
        di.setData(Qt.ItemDataRole.UserRole, rowid)  # 该收款行在 collection 中的 id
        # 金额（delegate 编辑，UserRole 存浮点）
        ai = QTableWidgetItem(f"{float(amount or 0):.2f}")
        ai.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        ai.setData(Qt.ItemDataRole.UserRole, float(amount or 0))
        # 经办人（文本常驻，双击编辑）
        pi = QTableWidgetItem(person or "")
        self.table.setItem(r, 0, di)
        self.table.setItem(r, 1, ai)
        self.table.setItem(r, 2, pi)
        # 删除按钮：矮控件不裁切，保留为 cellWidget
        del_btn = QPushButton("删除")
        del_btn.clicked.connect(lambda _=False, b=del_btn: self._del_row_by_widget(b))
        self.table.setCellWidget(r, 3, del_btn)

    def _add_row(self) -> None:
        self._append_row(None, "", 0.0, "")

    def _del_row_by_widget(self, btn) -> None:
        for r in range(self.table.rowCount()):
            if self.table.cellWidget(r, 3) is btn:
                self.table.removeRow(r)
                self._update_actual()
                return

    def _fill_from_expected(self) -> None:
        self.table.setRowCount(0)
        for it in self.expected_items:
            ym = (it.get("ym", "") or "")[:7]
            date = (ym + "-01")[:10] if ym else ""
            self._append_row(None, date, it.get("amount", 0.0), it.get("person", ""))
        self._update_actual()

    def _update_actual(self) -> None:
        total = 0.0
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 1)
            if it is not None:
                total += float(it.data(Qt.ItemDataRole.UserRole) or 0.0)
        self.lbl_actual.setText(f"实际合计：{_money(total)}")
        self.lbl_actual.setStyleSheet(
            "color:#C0392B;" if abs(total - self.expected_total) > 0.01 else "")

    # ---------------------------------------------------------------- #
    def _collect(self) -> List[Dict]:
        target = []
        for r in range(self.table.rowCount()):
            di = self.table.item(r, 0)
            ai = self.table.item(r, 1)
            pi = self.table.item(r, 2)
            date = di.text().strip() if di else ""
            amt = float(ai.data(Qt.ItemDataRole.UserRole)) if ai else 0.0
            person = pi.text().strip() if pi else ""
            rid = di.data(Qt.ItemDataRole.UserRole) if di else None
            target.append({"id": rid, "receipt_date": date,
                           "amount": amt, "person_name": person})
        return target

    def _on_ok(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        target = self._collect()
        for t in target:
            if t["amount"] <= 0:
                QMessageBox.warning(self, "校验失败", "每张收款行金额必须大于 0。")
                return
            if len(t["receipt_date"]) < 7:
                QMessageBox.warning(
                    self, "校验失败",
                    f"收款日期格式应为 YYYY-MM-DD，当前为：{t['receipt_date']!r}")
                return
        actual = sum(t["amount"] for t in target)
        if abs(actual - self.expected_total) > 0.01:
            if QMessageBox.question(
                self, "金额不一致",
                f"修改后的实际已收合计 {_money(actual)} 与源声称 "
                f"{_money(self.expected_total)} 不一致，是否仍以手动结果覆盖？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            ) != QMessageBox.StandardButton.Yes:
                return
        conn = get_conn()
        try:
            # 行级合并（绝不整票 DELETE）+ 同步快照，整体一个事务
            merge_collection_for_invoice(conn, self.invoice_no, self.batch_id, target)
            _refresh_snapshot_actual(conn, self.invoice_no, self.batch_id, self.expected_json)
            conn.commit()
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            QMessageBox.critical(self, "修正失败", str(e))
            return
        finally:
            conn.close()
        self.accept()
