"""预收款管理：待核销 / 已核销 两个页面（QTabWidget）。

核销规则（方案1）：核销只写 `prepayment_offset`，**不再写 `collection`**。
原因：预收款与「已开票已入账」发票的已收是同一笔现金，发票台账导入时
已把已收记进 `collection`；若核销再插一条 collection 会双计。方案1 让核销
只"消耗"预收款余额，不动发票/收款/结算数据。
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

from app.ui.widgets import SubtitleLabel, PrimaryPushButton, PushButton
from app.ui.column_layout import install_column_layout
from app.ui.table_features import install_accent_header
from app.db import get_conn
from app.engine.collection import invoice_rows


def _num(v):
    """把任意金额文本/数字安全转 float（用于排序键）。"""
    if v is None or v == "":
        return 0.0
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return 0.0


class _OffsetDialog(QDialog):
    """可搜索的核销对话框：按 对方/金额/经办人/案号 过滤发票，并展示发票全部信息。"""

    # 列 -> invoice_rows 字典键（用于右键排序）
    _KEYS = ["invoice_date", "invoice_no", "buyer", "total_amount",
             "handlers_amount", "case_no", "collected", "remain"]
    _NUMERIC = {3, 6, 7}

    def __init__(self, parent, prepay_remain: float) -> None:
        super().__init__(parent)
        self.prepay_remain = prepay_remain
        self._matched = []
        self.setWindowTitle("核销预收款（选择发票）")
        self.resize(900, 560)
        lay = QVBoxLayout(self)

        h = QHBoxLayout()
        h.addWidget(QLabel("搜索"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("对方 / 金额 / 经办人 / 案号（留空显示全部待核销发票）")
        self.search.textChanged.connect(self._apply_filter)
        h.addWidget(self.search, 1)
        lay.addLayout(h)

        cols = ["开票日期", "发票号码", "对方", "价税合计", "经办人", "案号", "已收", "剩余应收"]
        self.table = QTableWidget(0, len(cols))
        install_accent_header(self.table)
        self.table.setHorizontalHeaderLabels(cols)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.itemSelectionChanged.connect(self._on_sel)
        self.table.doubleClicked.connect(self.accept)
        from app.ui.table_features import install_common_features, install_header_filter
        install_common_features(self.table)
        self._filter = install_header_filter(self.table)
        self._col = install_column_layout(self.table, "prepayment_offset", "main")
        self._col.set_sort_callback(self._do_sort)
        lay.addWidget(self.table, 1)

        f = QFormLayout()
        self.amt = QDoubleSpinBox()
        self.amt.setRange(0.01, 99999999)
        self.amt.setDecimals(2)
        self.amt.setValue(prepay_remain)
        f.addRow("核销金额（消耗预收款，≤ 剩余余额）", self.amt)
        lay.addLayout(f)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

        # 候选发票：所有正数且仍有剩余应收的发票（来自 invoice_rows，含经办人/已收/剩余）
        conn = get_conn()
        try:
            rows = invoice_rows(conn=conn)
        finally:
            conn.close()
        self._all = [r for r in rows if r["total_amount"] > 0 and r["remain"] > 0.005]
        self._sort_col = -1
        self._sort_asc = True
        self._apply_filter()

    def _apply_filter(self) -> None:
        q = self.search.text().strip().lower()
        if q:
            matched = []
            for r in self._all:
                hay = " ".join(str(r.get(k, "")) for k in
                              ("buyer", "total_amount", "handlers_amount",
                               "case_no", "invoice_no"))
                if q in hay.lower():
                    matched.append(r)
        else:
            matched = self._all
        if self._sort_col >= 0:
            col = self._sort_col
            num = col in self._NUMERIC
            matched.sort(key=lambda r, c=col, n=num: _num(r.get(self._KEYS[c])) if n
                         else str(r.get(self._KEYS[c]) or ""), reverse=not self._sort_asc)
        self._matched = matched
        self.table.setRowCount(len(matched))
        for ri, r in enumerate(matched):
            vals = [r["invoice_date"], r["invoice_no"], r["buyer"], r["total_amount"],
                    r["handlers_amount"], r["case_no"] or "", r["collected"], r["remain"]]
            for ci, v in enumerate(vals):
                item = QTableWidgetItem("" if v is None else
                                        (f"{v:,.2f}" if isinstance(v, float) else str(v)))
                if ci in (3, 6, 7):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(ri, ci, item)
        self._col.apply()
        # 重新叠加右键「按列筛选」（与搜索 AND 组合）：_apply_filter 重建表格会清除 setRowHidden 状态
        self._filter.apply_to_table(self.table)
        if self._sort_col >= 0:
            hdr = self.table.horizontalHeader()
            hdr.setSortIndicator(self._sort_col,
                                 Qt.SortOrder.DescendingOrder if not self._sort_asc else Qt.SortOrder.AscendingOrder)
        if matched:
            self.table.selectRow(0)

    def _on_sel(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._matched):
            return
        default = min(self.prepay_remain, self._matched[row]["remain"])
        self.amt.setValue(default)

    def selected(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._matched):
            return None, 0.0
        return self._matched[row]["invoice_no"], self.amt.value()

    def _do_sort(self, logical, asc) -> None:
        self._sort_col = logical
        self._sort_asc = asc
        self._col.set_sort_col(logical)
        self._apply_filter()


class PrepaymentView(QTabWidget):
    _PEND_KEYS = ["received_date", "buyer", "amount", "person_text", "case_no", "remark", "offset_total", "remain"]
    _PEND_NUMERIC = {2, 6, 7}
    _DONE_KEYS = ["p_buyer", "received_date", "p_amount", "invoice_no", "offset_amount", "offset_date", "remain"]
    _DONE_NUMERIC = {2, 4, 6}

    def __init__(self) -> None:
        super().__init__()
        self.setDocumentMode(True)

        self.tab_pending = QWidget()
        self._build_pending()
        self.addTab(self.tab_pending, "待核销预收款")

        self.tab_done = QWidget()
        self._build_done()
        self.addTab(self.tab_done, "已核销预收款")

        self._meta = {}
        self._pend_rows: list = []
        self._done_rows: list = []
        self._pend_sort_col = -1
        self._pend_sort_asc = True
        self._done_sort_col = -1
        self._done_sort_asc = True

    # ---------- 待核销 ----------
    def _build_pending(self) -> None:
        lay = QVBoxLayout(self.tab_pending)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)
        lay.addWidget(SubtitleLabel("待核销预收款"))
        lay.addWidget(QLabel(
            "已入账未开票（sheet4）。选择预收款 → 核销到发票（可搜索对方/金额/经办人/案号）。"
            "核销只消耗预收款余额，不再重复记收款，不影响发票/收款/结算数据。"))

        btns = QHBoxLayout()
        self.btn_offset = PrimaryPushButton("核销到发票")
        self.btn_offset.clicked.connect(self.offset)
        btns.addWidget(self.btn_offset)
        btns.addStretch()
        lay.addLayout(btns)

        self.table = QTableWidget(0, 8)
        install_accent_header(self.table)
        self.table.setHorizontalHeaderLabels(
            ["收到日期", "对方", "金额", "经办人", "案号", "备注", "已核销", "剩余余额"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        from app.ui.table_features import install_common_features, install_header_filter
        install_common_features(self.table)
        install_header_filter(self.table)
        self._col = install_column_layout(self.table, "prepayment", "main")
        self._col.set_sort_callback(self._do_sort_pending)
        lay.addWidget(self.table)

    # ---------- 已核销 ----------
    def _build_done(self) -> None:
        lay = QVBoxLayout(self.tab_done)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)
        lay.addWidget(SubtitleLabel("已核销预收款"))
        lay.addWidget(QLabel("已做过核销的预收款及其冲抵明细。核销金额消耗预收款余额，不影响其它表。"))

        self.table_done = QTableWidget(0, 7)
        install_accent_header(self.table_done)
        self.table_done.setHorizontalHeaderLabels(
            ["预收款方", "收到日期", "预收款金额", "核销到发票", "核销金额", "核销日期", "预收款剩余"])
        self.table_done.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table_done.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_done.verticalHeader().setVisible(False)
        self.table_done.horizontalHeader().setStretchLastSection(True)
        from app.ui.table_features import install_common_features, install_header_filter
        install_common_features(self.table_done)
        self._filter_done = install_header_filter(self.table_done)
        self._col_done = install_column_layout(self.table_done, "prepayment_done", "main")
        self._col_done.set_sort_callback(self._do_sort_done)
        lay.addWidget(self.table_done)

    # ---------- 刷新 ----------
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        conn = get_conn()
        try:
            rows = conn.execute(
                """SELECT p.*, COALESCE(o.offset_total,0) AS offset_total
                   FROM prepayment p
                   LEFT JOIN (SELECT prepayment_id, SUM(offset_amount) AS offset_total
                              FROM prepayment_offset GROUP BY prepayment_id) o
                   ON o.prepayment_id = p.id
                   ORDER BY p.received_date"""
            ).fetchall()
        finally:
            conn.close()
        rows = [dict(r) for r in rows]

        # 待核销：剩余余额 > 0
        pend = [r for r in rows if (r["amount"] - (r["offset_total"] or 0.0)) > 0.005]
        for r in pend:
            r["offset"] = r["offset_total"] or 0.0
            r["remain"] = r["amount"] - r["offset"]
        self._pend_rows = pend
        self._sort_rows_inplace(self._pend_rows, self._pend_sort_col, self._pend_sort_asc,
                                self._PEND_KEYS, self._PEND_NUMERIC)
        self._render_pending(self._pend_rows)

        # 已核销：核销明细
        conn = get_conn()
        try:
            done = conn.execute(
                """SELECT p.buyer AS p_buyer, p.received_date, p.amount AS p_amount,
                          o.invoice_no, o.offset_amount, o.offset_date,
                          COALESCE(off.off_total,0) AS off_total
                   FROM prepayment_offset o
                   JOIN prepayment p ON o.prepayment_id = p.id
                   LEFT JOIN (SELECT prepayment_id, SUM(offset_amount) AS off_total
                              FROM prepayment_offset GROUP BY prepayment_id) off
                     ON off.prepayment_id = p.id
                   ORDER BY o.offset_date DESC, p.received_date"""
            ).fetchall()
        finally:
            conn.close()
        done = [dict(r) for r in done]
        for r in done:
            r["remain"] = r["p_amount"] - (r["off_total"] or 0.0)
        self._done_rows = done
        self._sort_rows_inplace(self._done_rows, self._done_sort_col, self._done_sort_asc,
                                self._DONE_KEYS, self._DONE_NUMERIC)
        self._render_done(self._done_rows)
        # 重新叠加右键「按列筛选」（与下拉/刷新 AND 组合）
        self._filter_done.apply_to_table(self.table_done)

    def _render_pending(self, rows) -> None:
        self._meta = {}
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            offset = row["offset_total"] or 0.0
            remain = row["amount"] - offset
            vals = [row["received_date"], row["buyer"], row["amount"], row["person_text"],
                    row["case_no"], row["remark"], offset, remain]
            for c, v in enumerate(vals):
                item = QTableWidgetItem("" if v is None else
                                        (f"{v:,.2f}" if isinstance(v, float) else str(v)))
                self.table.setItem(r, c, item)
            self._meta[r] = {"id": row["id"], "buyer": row["buyer"], "amount": row["amount"],
                             "case_no": row["case_no"], "remain": remain}
        self._col.apply()

    def _render_done(self, rows) -> None:
        self.table_done.setRowCount(len(rows))
        for r, row in enumerate(rows):
            remain = row["remain"]
            vals = [row["p_buyer"], row["received_date"], row["p_amount"],
                    row["invoice_no"], row["offset_amount"], row["offset_date"] or "", remain]
            for c, v in enumerate(vals):
                item = QTableWidgetItem("" if v is None else
                                        (f"{v:,.2f}" if isinstance(v, float) else str(v)))
                self.table_done.setItem(r, c, item)
        self._col_done.apply()

    def _do_sort_pending(self, logical, asc) -> None:
        self._pend_sort_col = logical
        self._pend_sort_asc = asc
        self._col.set_sort_col(logical)
        self._sort_rows_inplace(self._pend_rows, logical, asc, self._PEND_KEYS, self._PEND_NUMERIC)
        self._render_pending(self._pend_rows)
        self.table.horizontalHeader().setSortIndicator(
            logical, Qt.SortOrder.DescendingOrder if not asc else Qt.SortOrder.AscendingOrder)

    def _do_sort_done(self, logical, asc) -> None:
        self._done_sort_col = logical
        self._done_sort_asc = asc
        self._col_done.set_sort_col(logical)
        self._sort_rows_inplace(self._done_rows, logical, asc, self._DONE_KEYS, self._DONE_NUMERIC)
        self._render_done(self._done_rows)
        self._filter_done.apply_to_table(self.table_done)
        self.table_done.horizontalHeader().setSortIndicator(
            logical, Qt.SortOrder.DescendingOrder if not asc else Qt.SortOrder.AscendingOrder)

    @staticmethod
    def _sort_rows_inplace(rows, col, asc, keys, numeric) -> None:
        """原地按列排序（keys 为列->字典键映射，numeric 为数值列集合）。"""
        if col < 0 or not rows:
            return

        def keyf(r):
            v = r.get(keys[col])
            if col in numeric:
                return _num(v)
            return str(v or "")
        rows.sort(key=keyf, reverse=not asc)

    # ---------- 核销（方案1） ----------
    def offset(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选择一条待核销预收款")
            return
        m = self._meta.get(row)
        if not m:
            return
        dlg = _OffsetDialog(self, m["remain"])
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        inv_no, amount = dlg.selected()
        if not inv_no or amount <= 0:
            QMessageBox.information(self, "提示", "请选择一张发票并输入核销金额")
            return
        if amount > m["remain"] + 0.01:
            QMessageBox.warning(self, "提示", "核销金额超出预收款剩余余额")
            return
        try:
            self._do_offset(m["id"], inv_no, amount)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "核销失败", str(e))
            return
        self.refresh()

    def _do_offset(self, prepay_id: int, invoice_no: str, amount: float) -> None:
        """方案1：仅记 prepayment_offset，不写 collection（避免与发票台账已收双计）。"""
        ym = datetime.now().strftime("%Y-%m")  # 自动记当前月，作核销台账日期（不再手动输入）
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO prepayment_offset (prepayment_id, invoice_no, offset_amount, offset_date) "
                "VALUES (?,?,?,?)",
                (prepay_id, invoice_no, amount, ym),
            )
            conn.commit()
        finally:
            conn.close()
