"""已收认定 · 逐行手动修正对话框（方案1优化版：行级合并，绝不整票 DELETE）。

弹窗展示某发票当前 collection 收款行（可编辑），并并排显示「源声称收款」作只读参考。
确定时按 rowid 做行级合并（改了才 UPDATE、消失才 DELETE、新增才 INSERT），
从源头消除「一键修正删光后续月份收款」的破坏性；并同步 received_snapshot.actual。
"""
from __future__ import annotations

import json
from typing import Dict, List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QDoubleSpinBox, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from app.db import get_conn
from app.importer.importer import (
    _refresh_snapshot_actual, merge_collection_for_invoice,
)


def _money(v) -> str:
    return f"{v:,.2f}" if isinstance(v, (int, float)) else str(v or "")


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
        self.current_rows = current_rows  # [{rowid, receipt_date, amount, person_name}]

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
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
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
        date_edit = QLineEdit(date or "")
        date_edit.setPlaceholderText("YYYY-MM-DD")
        amt_edit = QDoubleSpinBox()
        amt_edit.setRange(0, 9_999_999_999)
        amt_edit.setDecimals(2)
        amt_edit.setValue(float(amount or 0.0))
        person_edit = QLineEdit(person or "")
        del_btn = QPushButton("删除")
        del_btn.clicked.connect(lambda _checked=False, row=r: self._del_row(row))
        self.table.setCellWidget(r, 0, date_edit)
        self.table.setCellWidget(r, 1, amt_edit)
        self.table.setCellWidget(r, 2, person_edit)
        self.table.setCellWidget(r, 3, del_btn)
        date_edit.setProperty("rowid", rowid)
        self._update_actual()

    def _add_row(self) -> None:
        self._append_row(None, "", 0.0, "")

    def _del_row(self, row: int) -> None:
        if 0 <= row < self.table.rowCount():
            self.table.removeRow(row)
            self._update_actual()

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
            w = self.table.cellWidget(r, 1)
            if isinstance(w, QDoubleSpinBox):
                total += w.value()
        self.lbl_actual.setText(f"实际合计：{_money(total)}")
        self.lbl_actual.setStyleSheet(
            "color:#C0392B;" if abs(total - self.expected_total) > 0.01 else "")

    # ---------------------------------------------------------------- #
    def _collect(self) -> List[Dict]:
        target = []
        for r in range(self.table.rowCount()):
            date_w = self.table.cellWidget(r, 0)
            amt_w = self.table.cellWidget(r, 1)
            person_w = self.table.cellWidget(r, 2)
            date = date_w.text().strip() if isinstance(date_w, QLineEdit) else ""
            amt = amt_w.value() if isinstance(amt_w, QDoubleSpinBox) else 0.0
            person = person_w.text().strip() if isinstance(person_w, QLineEdit) else ""
            rid = date_w.property("rowid") if isinstance(date_w, QLineEdit) else None
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
