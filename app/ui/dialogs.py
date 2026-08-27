"""弹窗：红冲信息 / 其他经办人金额"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QHeaderView,
)

from app.db import get_conn


def _table(columns: list, rows: list, title: str) -> QDialog:
    dlg = QDialog()
    dlg.setWindowTitle(title)
    dlg.resize(640, 400)
    lay = QVBoxLayout(dlg)
    t = QTableWidget(len(rows), len(columns))
    t.setHorizontalHeaderLabels(columns)
    t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setStretchLastSection(True)
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            item = QTableWidgetItem("" if v is None else str(v))
            if isinstance(v, float):
                item.setText(f"{v:,.2f}")
            t.setItem(r, c, item)
    lay.addWidget(t)
    return dlg


def show_red_relation(parent, invoice_no: str) -> None:
    """红冲信息弹窗：正数发票 → 其红字发票；红字发票 → 其原正数发票"""
    conn = get_conn()
    try:
        inv = conn.execute("SELECT * FROM invoice WHERE invoice_no=?", (invoice_no,)).fetchone()
        if inv is None:
            return
        if inv["total_amount"] < 0:
            # 红字发票：显示原正数发票
            rows = []
            for r in conn.execute("SELECT * FROM invoice WHERE invoice_no=?", (inv["orig_invoice_no"],)):
                rows.append([r["invoice_date"], r["invoice_no"], r["buyer"],
                             r["total_amount"], "原正数发票"])
            title = f"红字发票 {invoice_no} 对应的原正数发票"
        else:
            # 正数发票：列出其所有红字发票
            rows = []
            for r in conn.execute("SELECT * FROM invoice WHERE orig_invoice_no=?", (invoice_no,)):
                rows.append([r["invoice_date"], r["invoice_no"], r["buyer"],
                             r["total_amount"], "红字发票"])
            title = f"正数发票 {invoice_no} 的红冲记录"
        if not rows:
            rows = [["—", "无关联发票", "", "", ""]]
        dlg = _table(["开票日期", "发票号码", "购方名称", "价税合计", "类型"], rows, title)
        dlg.exec()
    finally:
        conn.close()


def show_invoice_handlers(parent, invoice_no: str, person_name: str | None = None) -> None:
    """发票所有经办人及开票金额（经办人表右击'查看其他经办人金额'）"""
    conn = get_conn()
    try:
        rows = []
        for r in conn.execute(
            "SELECT cd.person_name, cd.billing_amount FROM charge_detail cd "
            "WHERE cd.invoice_no=? ORDER BY cd.id", (invoice_no,)
        ):
            rows.append([r["person_name"], r["billing_amount"]])
        dlg = _table(["经办人", "开票金额"], rows, f"发票 {invoice_no} 的经办人金额")
        dlg.exec()
    finally:
        conn.close()


def show_invoice_info(parent, invoice_no: str) -> None:
    """查看发票信息：按「发票收款情况」同款 8 列展示该发票（待补录页右击用）"""
    from app.engine.collection import invoice_rows
    conn = get_conn()
    try:
        rows = invoice_rows(invoice_no=invoice_no)
        if not rows:
            QMessageBox.information(parent, "提示", f"未找到发票 {invoice_no} 的信息")
            return
        columns = ["开具日期", "发票号码", "购买方名称", "价税合计", "经办人",
                   "已收金额", "剩余应收", "收退款情况"]
        data = []
        for r in rows:
            data.append([
                r["invoice_date"], r["invoice_no"], r["buyer"], r["total_amount"],
                r.get("handlers_amount") or r.get("handlers", ""),
                r["collected"], r["remain"], r.get("recv_refund_str", ""),
            ])
        dlg = _table(columns, data, f"发票信息：{invoice_no}")
        dlg.resize(960, 380)
        dlg.exec()
    finally:
        conn.close()
