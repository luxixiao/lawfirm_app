"""发票台账页（方案 D）：查看所有 sheet 原始数据 + 增删改 + 同步到 invoice

- 主表展示 raw_invoice（Excel 逐行 1:1 镜像，数值列原始文本）。
- 工具栏：新增 / 编辑所选 / 删除所选 / 同步到发票 / 刷新；顶部按 sheet / 状态 / 搜索 筛选。
- 删除原始行会级联删除 invoice（及 charge_detail/collection），并写入修改记录。
- 同步：把 raw_invoice 行归一化后 upsert 进 invoice（保留经办人），置 synced=1。
- 修改记录面板仅展示发票台账（invoice / raw_invoice）的 change_log（2.5）。
"""
from __future__ import annotations

import re
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from app.ui.widgets import (CaptionLabel, PrimaryPushButton, PushButton, SubtitleLabel)
from app.ui.column_state import attach_persistence, auto_fit_then_restore
from app.db import get_conn
from app.engine import raw_invoice as ri
from app.engine.raw_invoice import RAW_FIELDS

_HEADERS = ["来源sheet", "行号", "序号", "发票号码", "种类", "开票日期", "状态", "凭证号",
            "购方名称", "价税合计", "不含税", "税率", "税额", "货物或劳务", "备注", "同步"]
_RED_RE = re.compile(r"被红冲蓝字数电票号码[:：]\s*(\d+)")


def _parse_total(txt) -> float:
    if not txt:
        return 0.0
    try:
        return float(str(txt).replace(",", ""))
    except ValueError:
        return 0.0


class InvoiceLedgerView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(12)

        t = SubtitleLabel("发票台账")
        lay.addWidget(t)
        h = CaptionLabel("查看发票台账所有 sheet 的原始数据（逐行 1:1 镜像），可增删改；"
                          "「同步到发票」把改动归一化进计算表（保留经办人）。所有修改均记录。"
                          "红字发票整行浅红标注。")
        lay.addWidget(h)

        # ---- 筛选条 ----
        fbar = QHBoxLayout()
        fbar.addWidget(CaptionLabel("来源sheet"))
        self.f_sheet = QComboBox()
        self.f_sheet.addItem("全部", userData="")
        fbar.addWidget(self.f_sheet)
        fbar.addWidget(CaptionLabel("状态"))
        self.f_status = QComboBox()
        for label, val in [("全部", ""), ("仅红字", "red"), ("仅待同步", "pending")]:
            self.f_status.addItem(label, userData=val)
        fbar.addWidget(self.f_status)
        fbar.addWidget(CaptionLabel("搜索"))
        self.f_search = QLineEdit()
        self.f_search.setPlaceholderText("发票号码 / 购方 / 备注")
        fbar.addWidget(self.f_search, 1)
        self.btn_refresh = PushButton("刷新")
        self.btn_refresh.clicked.connect(self.refresh)
        fbar.addWidget(self.btn_refresh)
        lay.addLayout(fbar)

        # ---- 工具栏 ----
        tbar = QHBoxLayout()
        self.btn_add = PrimaryPushButton("+ 新增")
        self.btn_add.clicked.connect(self.add_row)
        self.btn_edit = PushButton("编辑所选")
        self.btn_edit.clicked.connect(self.edit_row)
        self.btn_del = PushButton("删除所选")
        self.btn_del.clicked.connect(self.delete_rows)
        self.btn_sync = PushButton("⇄ 同步到发票")
        self.btn_sync.clicked.connect(self.sync)
        tbar.addWidget(self.btn_add)
        tbar.addWidget(self.btn_edit)
        tbar.addWidget(self.btn_del)
        tbar.addWidget(self.btn_sync)
        tbar.addStretch()
        self.lbl_stat = CaptionLabel("")
        tbar.addWidget(self.lbl_stat)
        lay.addLayout(tbar)

        # ---- 主表 ----
        self.table = QTableWidget(0, len(_HEADERS))
        self.table.setHorizontalHeaderLabels(_HEADERS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setHorizontalScrollMode(QTableWidget.ScrollMode.ScrollPerPixel)
        self.table.doubleClicked.connect(lambda *_: self.edit_row())
        attach_persistence(self.table, "invoice_ledger", "main")
        lay.addWidget(self.table, 1)

        # ---- 修改记录面板（仅发票台账）----
        log_title = CaptionLabel("修改记录（仅发票台账）")
        lay.addWidget(log_title)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(110)
        self.log.setPlaceholderText("发票台账的修改记录…")
        lay.addWidget(self.log)

        self._meta: dict[int, int] = {}
        self.refresh()

    # ------------------------------------------------------------------ #
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    def _refresh_sheet_combo(self) -> None:
        cur = self.f_sheet.currentData()
        self.f_sheet.blockSignals(True)
        self.f_sheet.clear()
        self.f_sheet.addItem("全部", userData="")
        for s in ri.distinct_sheets():
            self.f_sheet.addItem(s, userData=s)
        idx = self.f_sheet.findData(cur)
        if idx >= 0:
            self.f_sheet.setCurrentIndex(idx)
        self.f_sheet.blockSignals(False)

    def refresh(self) -> None:
        self._refresh_sheet_combo()
        sheet = self.f_sheet.currentData() or ""
        status = self.f_status.currentData() or ""
        keyword = self.f_search.text().strip()
        rows = ri.list_raw(sheet=sheet, status=status, keyword=keyword)
        self._fill(rows)
        pending = ri.count_pending()
        self.lbl_stat.setText(f"共 {len(rows)} 行 · 待同步 {pending} 行")
        self._fill_log()

    def _fill(self, rows) -> None:
        self.table.setRowCount(len(rows))
        self._meta = {}
        for r, row in enumerate(rows):
            total = _parse_total(row.get("total_amount_raw"))
            is_red = total < 0
            synced = bool(row.get("synced"))
            vals = [
                row.get("sheet_name") or "",
                str(row.get("row_no") or ""),
                row.get("seq") or "",
                row.get("invoice_no") or "",
                row.get("kind") or "",
                row.get("invoice_date_raw") or "",
                row.get("status") or "",
                row.get("voucher_no") or "",
                row.get("buyer") or "",
                row.get("total_amount_raw") or "",
                row.get("net_amount_raw") or "",
                row.get("tax_rate_raw") or "",
                row.get("tax_raw") or "",
                row.get("goods") or "",
                row.get("remark") or "",
                "已同步" if synced else "待同步",
            ]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                if c in (9, 10, 12):  # 金额类右对齐
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if c == 15:  # 同步状态着色
                    item.setForeground(Qt.GlobalColor.darkGreen if synced else Qt.GlobalColor.darkYellow)
                if is_red and c in (3, 9):  # 红字发票号与金额标红
                    item.setForeground(Qt.GlobalColor.red)
                self.table.setItem(r, c, item)
            if is_red:
                for c in range(self.table.columnCount()):
                    it = self.table.item(r, c)
                    if it is not None:
                        it.setBackground(Qt.GlobalColor(0xFFECEC))
            self._meta[r] = row["id"]
        used = auto_fit_then_restore(self.table, "invoice_ledger", "main")
        if used:
            self.table.horizontalHeader().setStretchLastSection(False)

    def _fill_log(self) -> None:
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT created_at, table_name, record_id, field, old_value, new_value, note "
                "FROM change_log WHERE table_name IN ('invoice','raw_invoice') "
                "ORDER BY id DESC LIMIT 300").fetchall()
        finally:
            conn.close()
        lines = []
        for r in rows:
            note = f" 〔{r['note']}〕" if r["note"] else ""
            lines.append(f"{r['created_at']}  {r['table_name']}#{r['record_id']} "
                         f"{r['field']}: {r['old_value']} → {r['new_value']}{note}")
        self.log.setPlainText("\n".join(lines))

    # ------------------------------------------------------------------ #
    # 增 / 改 / 删
    # ------------------------------------------------------------------ #
    def _edit_dialog(self, data: dict | None = None) -> dict | None:
        dlg = QDialog(self)
        dlg.setWindowTitle("新增发票台账行" if data is None else "编辑发票台账行")
        dlg.resize(520, 560)
        form = QFormLayout(dlg)
        edits = {}
        for f in RAW_FIELDS:
            le = QLineEdit("" if data is None else (data.get(f) or ""))
            edits[f] = le
            form.addRow(f, le)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return {f: edits[f].text().strip() for f in RAW_FIELDS}

    def add_row(self) -> None:
        data = self._edit_dialog(None)
        if data is None:
            return
        ri.insert_row(data, note="手动新增")
        self.refresh()

    def edit_row(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选中一行（或双击）")
            return
        rid = self._meta.get(row)
        if rid is None:
            return
        old = ri.get_row(rid)
        if old is None:
            return
        note, ok = _ask_note(self, "修改备注（记录在修改记录中，可空）")
        if not ok:
            return
        data = self._edit_dialog(old)
        if data is None:
            return
        ri.update_row(rid, data, note=note)
        self.refresh()

    def delete_rows(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "提示", "请先选中要删除的行（可多选）")
            return
        ids = [self._meta[r.row()] for r in rows if self._meta.get(r.row()) is not None]
        if not ids:
            return
        ret = QMessageBox.question(
            self, "确认删除",
            f"将删除 {len(ids)} 条发票台账原始行。"
            "已同步的发票会一并从计算表删除（含经办人/收款）。此操作记录到修改记录。确定？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        n = ri.delete_rows(ids, note="手动删除")
        QMessageBox.information(self, "已删除", f"已删除 {n} 行")
        self.refresh()

    def sync(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        ids = [self._meta[r.row()] for r in rows if self._meta.get(r.row()) is not None]
        # 有选中则同步选中，否则同步全部
        res = ri.sync_to_invoice(ids if ids else None)
        msg = f"已同步 {res['synced']} 行" + (f"，跳过 {res['skipped']} 行（无发票号码）" if res["skipped"] else "")
        self._info(msg)
        self.refresh()

    def _info(self, msg: str) -> None:
        w = self.window()
        if hasattr(w, "show_info"):
            w.show_info(msg, success=True)
        else:
            QMessageBox.information(self, "完成", msg)


def _ask_note(parent, label: str):
    from PySide6.QtWidgets import QInputDialog
    return QInputDialog.getText(parent, "修改备注", label)
