"""导入后回写对话框（批 3-3 从 review_post_view.py 原样搬出）。

为什么搬：导入复核页统一后，`UnifiedImportDialog(mode="post")` 要复用本对话框
（编辑回写 `review_writeback.apply_edit`），而旧「导入后」页（`review_post_view`）
将在批 4 整体删除 —— 对话框先落到中立模块，两页共用，删除旧页时无牵连。

交互与字段与旧页**逐字一致**：编辑镜表原始文本字段（EDIT_FIELDS 子集）+
收款明细表（留空=按备注自动推导；填写=显式覆盖）+ 修改原因（必填，审计留痕）。
"""
from __future__ import annotations

from typing import Dict

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from app.db import get_conn
from app.engine.review_writeback import apply_edit
from app.ui.widgets import CaptionLabel


def _money(v) -> str:
    return f"{v:,.2f}" if isinstance(v, (int, float)) else "—"


class WritebackDialog(QDialog):
    """导入后回写对话框：编辑镜表原始文本字段 + 收款明细，修改原因必填。"""

    _EDIT_FIELDS = [
        ("invoice_date_raw", "开票日期"),
        ("invoice_no", "发票号码"),
        ("buyer", "对方"),
        ("amount_raw", "金额"),
        ("case_no", "案号"),
        ("remark", "备注"),
        ("handler_text", "经办人"),
    ]

    def __init__(self, parent, period: str, invoice_no: str, raw: Dict,
                 raw_id: int) -> None:
        super().__init__(parent)
        self._period = period
        self._raw_id = raw_id
        self.setWindowTitle(f"编辑回写 — {invoice_no}")
        self.resize(620, 560)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(10)

        root.addWidget(CaptionLabel(
            f"账期 {period}　·　修改将写入镜表并同步业务表（invoice/经办人分摊/收款），"
            "全程留痕。改后该行标记「已手工修订」。"))

        form = QFormLayout()
        form.setSpacing(8)
        self.edits: Dict[str, QLineEdit] = {}
        for key, label in self._EDIT_FIELDS:
            le = QLineEdit(str(raw.get(key) or ""))
            self.edits[key] = le
            form.addRow(label, le)
        root.addLayout(form)

        root.addWidget(CaptionLabel("收款明细（留空=按备注自动推导；填写=显式覆盖）："))
        self.tbl = QTableWidget(0, 3)
        self.tbl.setHorizontalHeaderLabels(["收款年月(YYYY-MM)", "金额", "经办人"])
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl.verticalHeader().setVisible(False)
        root.addWidget(self.tbl, 1)
        btns = QHBoxLayout()
        btn_add = QPushButton("添加收款行")
        btn_add.clicked.connect(self._add_row)
        btn_del = QPushButton("删除所选行")
        btn_del.clicked.connect(self._del_row)
        btns.addWidget(btn_add)
        btns.addWidget(btn_del)
        btns.addStretch()
        root.addLayout(btns)
        self._load_receipts(raw.get("invoice_no") or "")

        root.addWidget(CaptionLabel("修改原因（必填）："))
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("例如：经核对实际收款日期为 2025-12-10，备注修正")
        root.addWidget(self.note_edit)

        box = QDialogButtonBox()
        self.btn_ok = box.addButton("确定回写", QDialogButtonBox.ButtonRole.AcceptRole)
        btn_cancel = box.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        btn_cancel.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self._on_ok)
        root.addWidget(box)

    def _load_receipts(self, invoice_no: str) -> None:
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT receipt_date, amount, person_name FROM collection "
                "WHERE invoice_no=? AND source='import' ORDER BY id",
                (invoice_no,),
            ).fetchall()
        finally:
            conn.close()
        self.tbl.setRowCount(0)
        for r in rows:
            self._append_row((r["receipt_date"] or "")[:7],
                             _money(r["amount"]), r["person_name"] or "")

    def _append_row(self, ym: str, amount: str, person: str) -> None:
        r = self.tbl.rowCount()
        self.tbl.insertRow(r)
        for c, v in enumerate([ym, amount, person]):
            it = QTableWidgetItem(v)
            if c == 1:
                it.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                    | Qt.AlignmentFlag.AlignVCenter)
            self.tbl.setItem(r, c, it)

    def _add_row(self) -> None:
        self._append_row("", "", "")

    def _del_row(self) -> None:
        r = self.tbl.currentRow()
        if r >= 0:
            self.tbl.removeRow(r)

    def _receipts(self) -> list:
        """收集收款明细 → [(person_name, amount, ym)]；无行返回 []。"""
        out = []
        for r in range(self.tbl.rowCount()):
            def _txt(c):
                it = self.tbl.item(r, c)
                return (it.text() if it else "").strip()
            ym, amt, person = _txt(0), _txt(1), _txt(2)
            if not ym and not amt and not person:
                continue
            try:
                amt_f = float(amt.replace(",", "")) if amt else 0.0
            except ValueError:
                raise ValueError(f"第 {r + 1} 行收款金额无法解析：{amt!r}")
            if amt_f < 0:
                # P3-2：collection 表不允许负数（红冲=负字发票、退款=独立退款台账，
                # 都不写 collection）。负数流入结算引擎会虚增其后未归因收款的分摊。
                raise ValueError(f"第 {r + 1} 行收款金额不允许为负数：{amt!r}"
                                 "（红冲/退款请在「退款台账」处理）")
            if not ym:
                raise ValueError(f"第 {r + 1} 行请填写收款年月（YYYY-MM）")
            out.append((person, amt_f, ym[:7]))
        return out

    def _on_ok(self) -> None:
        note = self.note_edit.text().strip()
        if not note:
            QMessageBox.warning(self, "需填写修改原因", "修改原因必填，便于日后审计追溯。")
            return
        patch = {}
        for key, _ in self._EDIT_FIELDS:
            patch[key] = self.edits[key].text().strip()
        try:
            patch["receipts"] = self._receipts()
        except ValueError as e:
            QMessageBox.warning(self, "收款明细有误", str(e))
            return
        try:
            self.result_data = apply_edit(self._period, self._raw_id, patch, note)
        except ValueError as e:
            QMessageBox.warning(self, "无法回写", str(e))
            return
        super().accept()
