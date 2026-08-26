"""导入校验中心（方案 B + D/E：发票台账 写后对账，三维度）

维护组页：选账期 → 重读存档源文件 ↔ 与 DB 落库数据逐行比对。
三个比对维度（顶部切换）：
  1. 发票信息：金额/购方/经办人拆分（原逻辑）
  2. 经办人分摊：源经办人开票金额 ↔ DB charge_detail（缺失/多出/金额不符/合计不符）
  3. 已收认定：源备注收款 ↔ DB collection（已收认定不符/勾稽不平）
维度 2、3 提供「一键修正（按源重算）」，把 DB 重新对齐到源文件。

只读比对 + 一键修正；双击行查看原台账溯源。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QLabel, QMessageBox,
    QVBoxLayout, QWidget,
)

from app.db import get_conn
from app.importer.archive_helper import resolve_archive
from app.importer.importer import (
    _regen_charge_detail, _write_collection_for_invoice,
)
from app.importer.ledger_import import parse_ledger_file
from app.ui.ledger_source import show_source_for_invoice
from app.ui.column_state import (
    attach_persistence, auto_fit_then_restore, restore_col_widths,
)
from app.ui.widgets import CaptionLabel, ComboBox, PrimaryPushButton, PushButton, TableWidget

RED = QColor("#C0392B")
GREEN = QColor("#1E8449")
AMBER = QColor("#B7791F")
DIFF_BG = QColor("#FDF1F0")

DIMS = [
    ("invoice", "发票信息"),
    ("handler", "经办人分摊"),
    ("received", "已收认定"),
]


def _money(v) -> str:
    return f"{v:,.2f}" if isinstance(v, (int, float)) else str(v or "")


class ImportVerifyView(QWidget):
    """发票台账导入校验中心（三维度对账）。"""

    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(12)

        t = QLabel("导入校验（发票台账）")
        t.setObjectName("pageTitle")
        lay.addWidget(t)
        hint = CaptionLabel(
            "选择已导入的账期，系统重读存档源文件并与数据库逐维度对账；"
            "维度 2/3 提供「一键修正（按源重算）」把数据库重新对齐到源文件。双击任意行查看原台账信息。"
        )
        lay.addWidget(hint)

        # 筛选栏
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(CaptionLabel("账期"))
        self.combo_period = ComboBox()
        self.combo_period.currentIndexChanged.connect(self._on_period_changed)
        bar.addWidget(self.combo_period)
        bar.addWidget(CaptionLabel("比对维度"))
        self.combo_dim = ComboBox()
        for key, name in DIMS:
            self.combo_dim.addItem(name, userData=key)
        self.combo_dim.setCurrentIndex(0)
        self.combo_dim.currentIndexChanged.connect(self._on_dim_changed)
        bar.addWidget(self.combo_dim)
        self.btn_load = PrimaryPushButton("加载对账")
        self.btn_load.clicked.connect(self.load_verify)
        bar.addWidget(self.btn_load)
        self.btn_fix = PushButton("一键修正（按源重算）")
        self.btn_fix.clicked.connect(self._fix)
        bar.addWidget(self.btn_fix)
        bar.addStretch()
        lay.addLayout(bar)

        # KPI
        kpi = QHBoxLayout()
        kpi.setSpacing(12)
        self.kpis: Dict[str, QLabel] = {}
        for key, name in [("total", "总行数"), ("diff", "差异数"),
                          ("amount", "金额合计"), ("rate", "一致率")]:
            box = QWidget()
            box.setObjectName("kpiCard")
            bx = QVBoxLayout(box)
            bx.setContentsMargins(14, 10, 14, 10)
            bx.setSpacing(2)
            lbl_v = QLabel("—")
            lbl_v.setObjectName("kpiValue")
            lbl_n = CaptionLabel(name)
            bx.addWidget(lbl_v)
            bx.addWidget(lbl_n)
            self.kpis[key] = lbl_v
            kpi.addWidget(box)
        kpi.addStretch()
        lay.addLayout(kpi)

        # 只看异常 + 统计
        bar2 = QHBoxLayout()
        self.chk_diff = QCheckBox("只看异常")
        self.chk_diff.setChecked(True)
        self.chk_diff.stateChanged.connect(self._render)
        bar2.addWidget(self.chk_diff)
        bar2.addStretch()
        self.lbl_stat = CaptionLabel("")
        bar2.addWidget(self.lbl_stat)
        lay.addLayout(bar2)

        # 表格
        self.table = TableWidget(self)
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(
            ["发票号", "来源", "购方", "原金额", "库金额", "原经办人", "库经办人", "状态"]
        )
        self.table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(TableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.cellDoubleClicked.connect(self._cell_double_clicked)
        lay.addWidget(self.table, 1)
        attach_persistence(self.table, "import_verify", "main")

        foot = CaptionLabel("双击任意行可查看该记录在原台账中的信息（含所属 sheet 与行号）。")
        lay.addWidget(foot)

        self._rows: List[Dict] = []
        self._period = ""
        self._need_fit = True
        self._archive = ""
        self._parsed: Optional[Dict] = None
        self._batch_id = 0
        self._load_periods()

    # ------------------------------------------------------------------ #
    # 账期 / 维度
    # ------------------------------------------------------------------ #
    def _load_periods(self) -> None:
        conn = get_conn()
        try:
            periods = [r["period"] for r in conn.execute(
                "SELECT DISTINCT period FROM import_batch "
                "WHERE batch_type='ledger' AND status='active' ORDER BY period DESC"
            )]
        finally:
            conn.close()
        self.combo_period.blockSignals(True)
        self.combo_period.clear()
        for p in periods:
            self.combo_period.addItem(p, userData=p)
        self.combo_period.blockSignals(False)
        if periods:
            self.combo_period.setCurrentIndex(0)
            self._period = periods[0]

    def _on_period_changed(self, *_):
        self._period = self.combo_period.currentData() or ""

    def _on_dim_changed(self, *_):
        dim = self.combo_dim.currentData() or "invoice"
        self.btn_fix.setEnabled(dim in ("handler", "received"))
        self._need_fit = True
        if self._period:
            self.load_verify()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        cur = self._period
        self._load_periods()
        idx = self.combo_period.findData(cur)
        if idx >= 0:
            self.combo_period.setCurrentIndex(idx)
        if self.combo_period.count():
            self.load_verify()

    # ------------------------------------------------------------------ #
    # 加载 + 比对
    # ------------------------------------------------------------------ #
    def load_verify(self) -> None:
        period = self._period
        dim = self.combo_dim.currentData() or "invoice"
        if not period:
            self._rows = []
            self._need_fit = True
            self._render()
            self.lbl_stat.setText("该月没有已导入的发票台账批次")
            return

        conn = get_conn()
        try:
            batch = conn.execute(
                "SELECT id, file_name, archive_path FROM import_batch "
                "WHERE batch_type='ledger' AND period=? AND status='active' "
                "ORDER BY imported_at DESC LIMIT 1",
                (period,),
            ).fetchone()
        finally:
            conn.close()
        if batch is None:
            QMessageBox.information(self, "提示", f"账期 {period} 没有已导入的发票台账批次。")
            self._rows = []
            self._need_fit = True
            self._render()
            return
        archive = (batch["archive_path"] or "").strip()
        if not archive:
            QMessageBox.information(self, "无法对账", f"该批次没有存档文件（早期版本导入），无法重读源文件对账。")
            self._rows = []
            self._need_fit = True
            self._render()
            return
        path = resolve_archive(archive)
        if not path.exists():
            QMessageBox.information(self, "无法对账", f"存档文件不存在：\n{path}")
            self._rows = []
            self._need_fit = True
            self._render()
            return

        try:
            parsed = parse_ledger_file(str(path), period)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "重读源文件失败", str(e))
            self._rows = []
            self._need_fit = True
            self._render()
            return

        self._parsed = parsed
        self._batch_id = batch["id"]
        self._archive = str(path)
        self._rows = self._compare(dim, parsed, period, str(path))
        self._need_fit = True
        self._render()

    # ------------------------------------------------------------------ #
    # 三维度比对
    # ------------------------------------------------------------------ #
    def _compare(self, dim: str, parsed: Dict, period: str, archive: str) -> List[Dict]:
        if dim == "handler":
            return self._compare_handler(parsed, period, archive)
        if dim == "received":
            return self._compare_received(parsed, period, archive)
        return self._compare_invoice(parsed, period, archive)

    def _compare_invoice(self, parsed: Dict, period: str, archive: str) -> List[Dict]:
        """发票信息对账（原逻辑）。"""
        conn = get_conn()
        try:
            db_invs = conn.execute(
                "SELECT invoice_no, invoice_date, buyer, total_amount, src_sheet, src_row "
                "FROM invoice WHERE source='import' AND "
                "(strftime('%Y-%m', invoice_date)=? OR import_batch_id=?)",
                (period, self._batch_id),
            ).fetchall()
            db_handlers: Dict[str, Dict[str, float]] = {}
            for r in conn.execute(
                "SELECT cd.invoice_no, cd.person_name, cd.billing_amount "
                "FROM charge_detail cd JOIN invoice i ON cd.invoice_no=i.invoice_no "
                "WHERE cd.source='import' AND "
                "(strftime('%Y-%m', i.invoice_date)=? OR i.import_batch_id=?)",
                (period, self._batch_id),
            ):
                db_handlers.setdefault(r["invoice_no"], {})[r["person_name"]] = r["billing_amount"]
        finally:
            conn.close()

        db_map: Dict[str, Dict] = {}
        for r in db_invs:
            db_map[r["invoice_no"]] = {
                "buyer": r["buyer"] or "",
                "total_amount": r["total_amount"] or 0.0,
                "handlers": db_handlers.get(r["invoice_no"], {}),
            }
        parsed_map: Dict[str, Dict] = {}
        for inv in parsed.get("invoices", []):
            no = inv["invoice_no"]
            parsed_map[no] = {
                "buyer": inv.get("buyer") or "",
                "total_amount": inv.get("total_amount") or 0.0,
                "handlers": {n: a for n, a in inv.get("handlers", [])},
                "src": inv,
            }

        rows: List[Dict] = []
        for i, no in enumerate(sorted(set(parsed_map) | set(db_map))):
            p = parsed_map.get(no)
            d = db_map.get(no)
            if p is None:
                rows.append(self._mk(no, "—", d["buyer"], None, d["total_amount"],
                                    "—", self._ht(d["handlers"]), "解析中缺失", True, None, i))
                continue
            if d is None:
                rows.append(self._mk(no, self._src_text(p["src"]), p["buyer"], p["total_amount"],
                                    None, self._ht(p["handlers"]), "—", "库中缺失", False, p["src"], i))
                continue
            reason = ""
            if abs(p["total_amount"] - d["total_amount"]) > 0.005:
                reason = "金额不一致"
            elif self._ht(p["handlers"]) != self._ht(d["handlers"]):
                reason = "经办人拆分不一致"
            rows.append(self._mk(no, self._src_text(p["src"]), p["buyer"] or d["buyer"],
                                p["total_amount"], d["total_amount"],
                                self._ht(p["handlers"]), self._ht(d["handlers"]),
                                reason, True, p["src"], i))
        return rows

    def _compare_handler(self, parsed: Dict, period: str, archive: str) -> List[Dict]:
        """经办人分摊对账：源开票金额 ↔ DB charge_detail。"""
        conn = get_conn()
        try:
            db_cd: Dict[str, Dict[str, float]] = {}
            for r in conn.execute(
                "SELECT cd.invoice_no, cd.person_name, cd.billing_amount "
                "FROM charge_detail cd JOIN invoice i ON cd.invoice_no=i.invoice_no "
                "WHERE cd.source='import' AND "
                "(strftime('%Y-%m', i.invoice_date)=? OR i.import_batch_id=?)",
                (period, self._batch_id),
            ):
                db_cd.setdefault(r["invoice_no"], {})[r["person_name"]] = r["billing_amount"]
        finally:
            conn.close()

        rows: List[Dict] = []
        for i, inv in enumerate(parsed.get("invoices", [])):
            no = inv["invoice_no"]
            p_handlers = {n: a for n, a in inv.get("handlers", [])}
            d_handlers = db_cd.get(no, {})
            reason = ""
            if set(p_handlers) != set(d_handlers):
                miss = set(p_handlers) - set(d_handlers)
                extra = set(d_handlers) - set(p_handlers)
                parts = []
                if miss:
                    parts.append("经办人缺失:" + "、".join(sorted(miss)))
                if extra:
                    parts.append("经办人多出:" + "、".join(sorted(extra)))
                reason = "；".join(parts)
            else:
                diff = [n for n in p_handlers if abs(p_handlers[n] - d_handlers.get(n, 0.0)) > 0.005]
                if diff:
                    reason = "分摊金额不符:" + "、".join(sorted(diff))
                elif abs(sum(p_handlers.values()) - sum(d_handlers.values())) > 0.01:
                    reason = "分摊合计≠发票总额"
            rows.append(self._mk(no, self._src_text(inv), inv.get("buyer", ""),
                                sum(p_handlers.values()) or None,
                                sum(d_handlers.values()) or None,
                                self._ht(p_handlers), self._ht(d_handlers),
                                reason, no in db_cd, inv, i))
        return rows

    def _compare_received(self, parsed: Dict, period: str, archive: str) -> List[Dict]:
        """已收认定对账：源备注收款 ↔ DB collection。"""
        conn = get_conn()
        try:
            db_coll: Dict[str, Dict[str, float]] = {}
            for r in conn.execute(
                "SELECT invoice_no, receipt_date, amount FROM collection "
                "WHERE source='import'",
            ):
                no = r["invoice_no"]
                ym = (r["receipt_date"] or "")[:7]
                db_coll.setdefault(no, {})[ym] = db_coll.setdefault(no, {}).get(ym, 0.0) + r["amount"]
        finally:
            conn.close()

        rows: List[Dict] = []
        for i, inv in enumerate(parsed.get("invoices", [])):
            no = inv["invoice_no"]
            total = inv.get("total_amount") or 0.0
            is_red = inv.get("is_red")
            rem = inv.get("remark") or {}
            # 源期望已收
            if is_red:
                exp_total = 0.0
                exp_text = "红字无收款"
            elif rem.get("pure_date"):
                exp_total = total
                exp_text = f"{rem['pure_date']} {total:,.2f}"
            elif rem.get("receipts"):
                exp_total = 0.0
                parts = []
                for ym, amt in rem["receipts"]:
                    a = amt if amt > 0 else total
                    exp_total += a
                    parts.append(f"{ym} {a:,.2f}")
                exp_text = "、".join(parts)
            else:
                exp_total = 0.0
                exp_text = "未收款"
            # 源勾稽不平提示
            src_warn = ""
            if rem.get("remaining") is not None and rem.get("receipts") and not rem.get("pure_date"):
                if abs((exp_total + rem["remaining"]) - total) > 0.01:
                    src_warn = "源勾稽不平；"
            # DB 实际
            d_map = db_coll.get(no, {})
            act_total = sum(d_map.values())
            act_text = "、".join(f"{ym} {amt:,.2f}" for ym, amt in sorted(d_map.items())) or "—"

            reason = ""
            if abs(exp_total - act_total) > 0.01:
                reason = f"{src_warn}已收认定不符(差{exp_total - act_total:,.2f})"
            elif src_warn:
                reason = src_warn.rstrip("；")
            rows.append(self._mk(no, self._src_text(inv), inv.get("buyer", ""),
                                exp_total or None, act_total or None,
                                exp_text, act_text, reason, no in db_coll, inv, i))
        return rows

    @staticmethod
    def _mk(invoice_no, source, buyer, raw_amount, db_amount, raw_handlers,
            db_handlers, reason, in_db, src, idx) -> Dict:
        return {
            "invoice_no": invoice_no, "source": source, "buyer": buyer,
            "raw_amount": raw_amount, "db_amount": db_amount,
            "raw_handlers": raw_handlers, "db_handlers": db_handlers,
            "reason": reason, "in_db": in_db, "src": src, "_idx": idx,
        }

    @staticmethod
    def _ht(handlers: Dict[str, float]) -> str:
        if not handlers:
            return "—"
        return "、".join(f"{n} {_money(a)}" for n, a in sorted(handlers.items()))

    @staticmethod
    def _src_text(src: Dict) -> str:
        return f"{src.get('sheet_name') or src.get('sheet') or '—'} · 第{src.get('row_no') or 0}行"

    # ------------------------------------------------------------------ #
    # 渲染
    # ------------------------------------------------------------------ #
    def _render(self) -> None:
        dim = self.combo_dim.currentData() or "invoice"
        headers = {
            "invoice": ["发票号", "来源", "购方", "原金额", "库金额", "原经办人", "库经办人", "状态"],
            "handler": ["发票号", "来源", "购方", "原分摊合计", "库分摊合计", "原经办人", "库经办人", "状态"],
            "received": ["发票号", "来源", "购方", "原已收合计", "库已收合计", "原收款明细", "库收款明细", "状态"],
        }[dim]
        self.table.setHorizontalHeaderLabels(headers)

        only_diff = self.chk_diff.isChecked()
        rows = [r for r in self._rows if not only_diff or r["reason"]]
        self.table.setRowCount(0)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            vals = [
                row["invoice_no"], row["source"], row["buyer"],
                row["raw_amount"], row["db_amount"],
                row["raw_handlers"], row["db_handlers"],
                row["reason"] or "✓ 一致",
            ]
            for c, v in enumerate(vals):
                item = self._make_item(v, c)
                if row["reason"]:
                    item.setBackground(DIFF_BG)
                self.table.setItem(r, c, item)
            self.table.setRowHeight(r, 32)
            self.table.item(r, 0).setData(Qt.ItemDataRole.UserRole, row["_idx"])
        if self._need_fit:
            auto_fit_then_restore(self.table, "import_verify", "main", max_width=200)
            self._need_fit = False
        else:
            restore_col_widths(self.table, "import_verify", "main")

        diff_n = sum(1 for r in self._rows if r["reason"])
        total_n = len(self._rows)
        self.lbl_stat.setText(
            f"共 {total_n} 张发票，差异 {diff_n} 张"
            + ("（当前仅显示异常行）" if only_diff else "")
        )
        amount = sum(r["raw_amount"] or 0 for r in self._rows if r["raw_amount"] is not None) \
            + sum(r["db_amount"] or 0 for r in self._rows if r["raw_amount"] is None)
        rate = f"{(total_n - diff_n) / total_n * 100:.1f}%" if total_n else "—"
        self.kpis["total"].setText(str(total_n))
        self.kpis["diff"].setText(str(diff_n))
        self.kpis["diff"].setStyleSheet("color: #C0392B;" if diff_n else "")
        self.kpis["amount"].setText(_money(amount))
        self.kpis["rate"].setText(rate)

    def _make_item(self, v, c: int):
        from PySide6.QtWidgets import QTableWidgetItem
        if c in (3, 4):
            text = _money(v) if v is not None else "—"
            item = QTableWidgetItem(text)
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        else:
            item = QTableWidgetItem(str(v))
        if c == 7:
            t = str(v)
            if t.startswith("✓"):
                item.setForeground(GREEN)
            else:
                item.setForeground(RED)
        return item

    # ------------------------------------------------------------------ #
    # 一键修正
    # ------------------------------------------------------------------ #
    def _fix(self) -> None:
        dim = self.combo_dim.currentData() or "invoice"
        if dim not in ("handler", "received") or not self._parsed:
            return
        if not self._rows:
            QMessageBox.information(self, "提示", "当前维度没有可对账的数据。")
            return
        diff_n = sum(1 for r in self._rows if r["reason"])
        if diff_n == 0:
            QMessageBox.information(self, "无需修正", "当前维度无差异，无需修正。")
            return
        if QMessageBox.question(
            self, "确认修正", f"将按源文件重算「{dict(DIMS)[dim]}」维度下全部 {len(self._rows)} 张发票，"
            f"覆盖数据库中对应记录（已收维度重算 collection，分摊维度重算 charge_detail）。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        conn = get_conn()
        try:
            for inv in self._parsed.get("invoices", []):
                if dim == "handler":
                    _regen_charge_detail(conn, inv, self._batch_id)
                else:
                    _write_collection_for_invoice(conn, inv, self._batch_id)
            conn.commit()
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            QMessageBox.critical(self, "修正失败", str(e))
            return
        finally:
            conn.close()
        QMessageBox.information(self, "修正完成", f"已按源重算「{dict(DIMS)[dim]}」维度。")
        self.load_verify()

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def _cell_double_clicked(self, r: int, _c: int) -> None:
        item = self.table.item(r, 0)
        if item is None:
            return
        idx = item.data(Qt.ItemDataRole.UserRole)
        if idx is None:
            return
        row = self._rows[idx]
        src = row.get("src")
        if src and src.get("raw_row"):
            from app.ui.ledger_source import show_ledger_source
            show_ledger_source(
                self, header=src.get("header") or [], raw_row=src["raw_row"],
                sheet_name=src.get("src_sheet") or "—", row_no=src.get("src_row") or 0,
                archive_path=src.get("archive") or self._archive, file_name=src.get("file_name") or "",
            )
            return
        if not row.get("in_db"):
            QMessageBox.information(self, "无库记录", f"发票 {row['invoice_no']} 在数据库中不存在，无法溯源。")
            return
        from app.ui.ledger_source import show_source_for_invoice
        show_source_for_invoice(self, row["invoice_no"])
