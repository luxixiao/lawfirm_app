"""经办人发票收款总表（一票多经办人拆行）"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QLabel, QMenu, QStyledItemDelegate,
)

from app.db import get_conn
from app.engine.collection import handler_rows
from app.ui.dialogs import show_invoice_handlers, show_red_relation
from app.ui.table_view import BaseTableView, make_filter_widgets


def _to_months(receipt_dates: str) -> str:
    """收款日期串 → 收款月（去重、月份补零），未收款显示「未收款」。

    兼容非零填充日期（2025-1-5 → 2025-01）。
    """
    months = set()
    for d in receipt_dates.split("、"):
        d = d.strip()
        if not d:
            continue
        parts = d.split("-")
        if len(parts) >= 2:
            try:
                mm = int(parts[1])
                months.add(f"{parts[0]}-{mm:02d}")
            except ValueError:
                months.add(parts[1])
        else:
            months.add(d)
    return "、".join(sorted(months)) if months else "未收款"


class _TypeDelegate(QStyledItemDelegate):
    """身份列（索引5）编辑委托：双击/F2 弹下拉，选择后直接写库。

    替代旧的「每行常驻 QComboBox」方案——行数上千时逐行建 widget 极慢，
    改为按列委托后仅在进入编辑时创建一个下拉，渲染零额外开销。
    """

    def __init__(self, view: "HandlerCollectView") -> None:
        super().__init__(view)
        self._view = view

    def createEditor(self, parent, option, index):  # noqa: N802
        combo = QComboBox(parent)
        for t in ["未标", "合伙", "聘用", "兼职"]:
            combo.addItem(t, userData=t)
        return combo

    def setEditorData(self, editor, index):  # noqa: N802
        cur = index.data(Qt.ItemDataRole.DisplayRole) or "未标"
        editor.setCurrentText(cur)

    def setModelData(self, editor, model, index):  # noqa: N802
        val = editor.currentData()
        if val is None:
            return
        model.setData(index, val, Qt.ItemDataRole.EditRole)
        meta = index.data(Qt.ItemDataRole.UserRole)
        if meta and meta.get("invoice_no") and meta.get("person_name"):
            self._view._set_handler_type(meta["invoice_no"], meta["person_name"], editor)


class HandlerCollectView(BaseTableView):
    def __init__(self) -> None:
        super().__init__(
            "经办人发票收款情况",
            ["开具日期", "发票号码", "购买方名称", "开票总额", "经办人",
             "身份", "开票金额", "已收金额", "剩余应收", "收款月", "备注"],
            "经办人维度收款情况；搜索框可检索全部字段；左键点表头排序，右键点表头按列筛选；身份列双击可修改经办人身份；右击查看台账信息 / 红冲信息 / 其他经办人金额。",
        )
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._ctx_menu)
        self._enable_col_filter()
        # 身份列：双击/F2 弹下拉修改（其他列保持只读）
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.table.setItemDelegateForColumn(5, _TypeDelegate(self))

    def _total_cols(self) -> set:
        """开票总额(3) / 开票金额(6) / 已收金额(7) / 剩余应收(8)"""
        return {3, 6, 7, 8}

    def _build_filters(self) -> None:
        self.year, self.month, self.src, self.keyword, _ = make_filter_widgets(
            self, self.filters, lambda *_: self.refresh(),
            search_label="发票号码/购方名/金额/经办人"
        )
        # 经办人下拉
        lbl = QLabel("经办人")
        self.person = QComboBox()
        self.person.addItem("全部经办人", userData="")
        conn = get_conn()
        try:
            for r in conn.execute("SELECT DISTINCT person_name FROM charge_detail ORDER BY person_name"):
                self.person.addItem(r["person_name"], userData=r["person_name"])
        finally:
            conn.close()
        self.person.currentIndexChanged.connect(lambda *_: self.refresh())
        self.filters.insertWidget(0, lbl)
        self.filters.insertWidget(1, self.person)

    def load_data(self) -> None:
        rows = handler_rows(
            person=self.person.currentData() or None,
            period=self.month.currentData() or self.year.currentData() or None,
            keyword=self.keyword.text().strip() or None,
            source=self.src.currentData() or None,
        )
        self._rows = [
            [r["invoice_date"], r["invoice_no"], r["buyer"], r["total_amount"],
             r["person_name"], r["person_type"] or "未标",
             r["billing_amount"], r["collected"], r["remain"],
             _to_months(r["receipt_dates"]), r.get("remark", "")]
            for r in rows
        ]
        self._meta = {i: r for i, r in enumerate(rows)}

    def _green_cols(self) -> set:
        return {6}

    def _make_item(self, val, r: int, c: int):
        item = super()._make_item(val, r, c)
        # 仅身份列（索引5）可编辑（双击弹下拉），其余列保持只读
        if c != 5:
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return item

    def _set_handler_type(self, invoice_no: str, person_name: str, combo) -> None:
        """按 发票+经办人 精确设置身份"""
        from app.db import get_conn
        from app.engine.change_log import log_change, build_friendly_table
        new_pt = combo.currentData()
        if new_pt is None:
            return
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT person_type FROM charge_detail WHERE invoice_no=? AND person_name=? LIMIT 1",
                (invoice_no, person_name)).fetchone()
            old_pt = row["person_type"] or "未标" if row else "未标"
            if old_pt == new_pt:
                return
            conn.execute(
                "UPDATE charge_detail SET person_type=? WHERE invoice_no=? AND person_name=?",
                (new_pt, invoice_no, person_name))
            # 取发票对方/金额用于修改前快照展示
            inv = conn.execute(
                "SELECT buyer, total_amount FROM invoice WHERE invoice_no=?",
                (invoice_no,)).fetchone()
            log_change(conn, "charge_detail", f"{invoice_no}/{person_name}", "person_type",
                       old_pt, new_pt, "经办人总表身份修改",
                       friendly_table=build_friendly_table("charge_detail"),
                       invoice_no=invoice_no,
                       buyer=(inv["buyer"] if inv else ""),
                       amount=(f"{inv['total_amount']:g}" if inv and inv["total_amount"] is not None else ""),
                       handlers=person_name)
            conn.commit()
        except Exception:  # noqa: BLE001
            conn.rollback()
        finally:
            conn.close()

    def _ctx_menu(self, pos) -> None:
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        meta = self._meta_at(row)
        if not meta:
            return
        menu = QMenu(self)
        a3 = menu.addAction("查看台账信息")
        menu.addSeparator()
        a1 = menu.addAction("查看红冲信息")
        a2 = menu.addAction("查看其他经办人金额")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen == a3:
            from app.ui.ledger_source import show_source_for_invoice
            show_source_for_invoice(self, meta["invoice_no"])
        elif chosen == a1:
            show_red_relation(self, meta["invoice_no"])
        elif chosen == a2:
            show_invoice_handlers(self, meta["invoice_no"], meta["person_name"])
