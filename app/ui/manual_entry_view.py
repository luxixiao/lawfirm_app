"""补录（期初/历史应收）发票：待补录发票 / 已补录发票 两页

- 待补录 = 两个来源合并（按票号去重，「来源」列区分）：
  · 红字引用 —— 被 invoice/refund 的 orig_invoice_no 引用但原票缺失；
  · 应收账款 —— 发票台账 sheet3 中「开票月份 < 账期月份」且未入库的历史应收
    （方案 A：导入时不再自动建票，只写镜表）。
- 统一补录弹窗：原始发票信息 8 项，其中 2.5~2.8（经办人/开票金额/已收金额/收款日期）
  做成可增删的明细表；两个来源都按已有信息自动预填（应收账款可预填开票日期/对方/
  价税合计/经办人开票额/已收金额与收款日期；红字引用只能预填购方/金额/经办人）。
- 写入 invoice(source='manual') + charge_detail + collection，与现有手工补录完全一致，
  不影响其他页面（收款表/结算/退款判定）。
- 保存前校验：至少一名经办人 + 须在花名册（与导入写前校验同一口径，
  见 app/engine/backfill.missing_handlers）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QMessageBox,
    QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.db import get_conn
from app.ui import scale
from app.ui.backfill_dialog import BackfillDialog, backfill_validator
from app.ui.widgets import PrimaryPushButton, PushButton, tab_help_corner
from app.ui.column_layout import install_column_layout
from app.engine.backfill_module import (
    list_pending_backfill, list_backfilled, load_invoice_detail,
    prefill_red_original, save_backfill, delete_backfill,
)


class ManualEntryView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)

        self.tabs = QTabWidget()
        self.tabs.tabBar().setObjectName("pageTitleBar")
        self.tab_pending = self._make_table(
            ["来源", "开票日期", "发票号码", "对方", "价税合计", "状态",
             "收款金额", "对应红字发票", "操作"], "pending")
        self.tab_done = self._make_table(
            ["开票日期", "发票号码", "对方", "价税合计", "状态", "收款金额", "补录时间", "操作"], "done")
        self.tabs.addTab(self.tab_pending, "待补录发票")
        self.tabs.addTab(self.tab_done, "已补录发票")
        self.tabs.setCornerWidget(tab_help_corner(
            "补录期初/历史应收的原始发票。待补录来自两处：①被红字发票引用但原票缺失；"
            "②发票台账「应收账款」中开票月份早于导入账期的历史票（导入时不自动建票）。"
            "两个来源都会按已有信息预填，请核对后再保存。"
        ), Qt.Corner.TopRightCorner)
        self.tabs.currentChanged.connect(self._sync_tab_actions)
        lay.addWidget(self.tabs, 1)

        # 已补录页操作按钮：①新增补录发票（全页通用）；②编辑所选 / 删除所选
        # 只对「已补录发票」列表生效，切到「待补录发票」时隐藏（待补录每行有各自的
        # 「补录」按钮，不存在"所选一行"的概念）。
        done_btns = QHBoxLayout()
        self.btn_new = PrimaryPushButton("新增补录发票")
        self.btn_new.clicked.connect(lambda: self.open_backfill())
        self.btn_edit = PushButton("编辑所选")
        self.btn_edit.clicked.connect(self.edit_selected)
        self.btn_del = PushButton("删除所选")
        self.btn_del.clicked.connect(self.delete_selected)
        done_btns.addWidget(self.btn_new)
        done_btns.addWidget(self.btn_edit)
        done_btns.addWidget(self.btn_del)
        done_btns.addStretch()
        lay.addLayout(done_btns)

        self._pending_meta: dict = {}
        self._done_meta: dict = {}
        self.refresh()
        self._sync_tab_actions()

    # ------------------------------------------------------------------ #
    def _sync_tab_actions(self, *_args) -> None:
        """「编辑所选 / 删除所选」仅对已补录（第 2 个 tab）有效，其余 tab 隐藏。"""
        on_done = self.tabs.currentWidget() is self.tab_done
        self.btn_edit.setVisible(on_done)
        self.btn_del.setVisible(on_done)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    def _make_table(self, headers, name: str) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        t.verticalHeader().setVisible(False)
        # 行高随字号档位缩放（Qt 默认行高固定 30px，不随字号变），并把内嵌的
        # 「补录/编辑」按钮安全容纳在内，任何档位下都不被裁。
        t.verticalHeader().setDefaultSectionSize(scale.px(34))
        t.horizontalHeader().setStretchLastSection(False)
        t.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        t.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        from app.ui.table_features import install_common_features, install_header_filter
        install_common_features(t)
        install_header_filter(t)
        t._col = install_column_layout(t, "manual", name)
        return t

    def refresh(self) -> None:
        self._load_pending()
        self._load_done()

    # ---- 待补录 ----
    def _load_pending(self) -> None:
        rows = list_pending_backfill()
        tbl = self.tab_pending
        tbl.setRowCount(len(rows))
        self._pending_meta = {}
        for r, row in enumerate(rows):
            red_text = "、".join(row["red_invoices"]) if row["red_invoices"] else "—"
            amount = row.get("total_amount")
            vals = [
                row.get("source") or "—",
                row.get("invoice_date") or "—",
                row["invoice_no"],
                row.get("buyer") or "—",
                f"{amount:,.2f}" if isinstance(amount, (int, float)) else "—",
                row["status"],
                f"{row['collected']:,.2f}",
                red_text,
                "",
            ]
            for c, v in enumerate(vals):
                tbl.setItem(r, c, QTableWidgetItem(str(v)))
            btn = PushButton("补录")
            btn.setObjectName("rowBtn")          # 行内按钮统一紧凑样式（不裁字）
            btn.clicked.connect(lambda _checked=False, rw=row: self.open_backfill_pending(rw))
            tbl.setCellWidget(r, 8, btn)
            self._pending_meta[r] = row
        tbl._col.apply()

    # ---- 已补录 ----
    def _load_done(self) -> None:
        rows = list_backfilled()
        tbl = self.tab_done
        tbl.setRowCount(len(rows))
        self._done_meta = {}
        for r, row in enumerate(rows):
            vals = [row["invoice_date"], row["invoice_no"], row["buyer"],
                    f"{row['total_amount']:,.2f}", row["status"],
                    f"{row['collected']:,.2f}", row["created_at"], ""]
            for c, v in enumerate(vals):
                tbl.setItem(r, c, QTableWidgetItem(str(v)))
            btn = PushButton("编辑")
            btn.setObjectName("rowBtn")          # 行内按钮统一紧凑样式（不裁字）
            btn.clicked.connect(lambda _checked=False, no=row["invoice_no"]: self.open_backfill(no, edit=True))
            tbl.setCellWidget(r, 7, btn)
            self._done_meta[r] = row["invoice_no"]
        tbl._col.apply()

    # ------------------------------------------------------------------ #
    # 打开补录弹窗（待补录点击 → 预填红字发票信息；已补录编辑 → 预填已存数据）
    # ------------------------------------------------------------------ #
    def open_backfill_pending(self, row: dict) -> None:
        """待补录点击「补录」：按来源预填。

        · 应收账款：开票日期/对方/价税合计/经办人开票额/已收金额与收款日期全预填；
        · 红字引用：只能预填购方/金额/经办人（原票日期不可知，留空手填）；
        · 若该票已入库（例如刚被补录过）→ 直接载入已存明细进入编辑。
        """
        no = row["invoice_no"]
        source = row.get("source") or ""
        prefill = None
        conn = get_conn()
        try:
            exists = conn.execute("SELECT 1 FROM invoice WHERE invoice_no=?", (no,)).fetchone()
            if not exists:
                if source == "应收账款":
                    prefill = {
                        "invoice_no": no,
                        "invoice_date": row.get("invoice_date") or "",
                        "buyer": row.get("buyer") or "",
                        "total_amount": row.get("total_amount") or 0.0,
                        "handlers": row.get("handlers") or [],
                    }
                else:
                    prefill = prefill_red_original(conn, no)
        finally:
            conn.close()
        if prefill is None:
            prefill = load_invoice_detail(no)
        self._open_dialog(prefill, edit=False, locked_no=False)

    # ------------------------------------------------------------------ #
    # 打开补录弹窗（已补录编辑 → 预填已存数据；新增 → 空白）
    # ------------------------------------------------------------------ #
    def open_backfill(self, invoice_no: str | None = None, edit: bool = False) -> None:
        prefill = load_invoice_detail(invoice_no) if invoice_no else None
        self._open_dialog(prefill, edit=edit, locked_no=bool(invoice_no and edit))

    def _open_dialog(self, prefill: dict | None, edit: bool, locked_no: bool) -> None:
        """打开补录弹窗（阶段 3 B2f：表单已抽到 app.ui.backfill_dialog.BackfillDialog，
        与导入复核页的行内「补录原票」共用同一实现）。

        本页（「发票补录」）保持**立即写库**不变：弹窗确定后直接 save_backfill()，
        不属任何导入批次（B2i / B2k）。复核页则把结果收集进 data["backfills"]，
        随发票台账同一事务入库。
        """
        dlg = BackfillDialog(
            prefill,
            title=("编辑补录发票" if edit else "补录原始发票"),
            locked_no=locked_no,
            validator=backfill_validator,
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            save_backfill(dlg.data(), editing=edit)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "保存失败", str(e))
            return
        self.refresh()

    # ---- 已补录：编辑 / 删除 ----
    def edit_selected(self) -> None:
        row = self.tab_done.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先在「已补录发票」列表中选择一行")
            return
        no = self._done_meta.get(row)
        if not no:
            return
        self.open_backfill(no, edit=True)

    def delete_selected(self) -> None:
        row = self.tab_done.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先在「已补录发票」列表中选择一行")
            return
        no = self._done_meta.get(row)
        if not no:
            return
        if QMessageBox.question(self, "确认删除",
                                f"确定删除补录发票 {no} 吗？\n（若仍被红字发票引用，删除后会重新进入待补录）",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                                ) != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_backfill(no)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "删除失败", str(e))
            return
        self.refresh()
