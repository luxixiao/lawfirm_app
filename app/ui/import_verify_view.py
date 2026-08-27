"""导入校验中心（方案 B + D/E：发票台账 写后对账，三维度）

维护组页：选账期 → 重读存档源文件 ↔ 与 DB 落库数据逐行比对。
三个比对维度（顶部切换）：
  1. 发票信息：金额/购方/经办人拆分（原逻辑）
  2. 经办人分摊：源经办人开票金额 ↔ DB charge_detail（缺失/多出/金额不符/合计不符）
  3. 已收认定：按批次读 received_snapshot，做「源声称收款 ↔ 本批次实际写入」对账（方案E）
维度 2（经办人分摊）提供「一键修正（按源重算）」；维度 3（已收认定）改为「逐行手动修正」对话框（按 id 行级合并，绝不整票删除）。

只读比对 + 一键修正；双击行查看原台账溯源。
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
    QLabel, QMessageBox, QPlainTextEdit, QVBoxLayout, QWidget,
)

from app.db import get_conn
from app.importer.archive_helper import resolve_archive
from app.importer.importer import (
    compute_expected_receipts,
    merge_collection_for_invoice, _refresh_snapshot_actual,
)
from app.importer.ledger_import import parse_ledger_file
from app.ui.collection_fix_dialog import CollectionFixDialog
from app.ui.ledger_source import show_source_for_invoice
from app.ui.column_layout import install_column_layout
from app.ui.widgets import CaptionLabel, ComboBox, PushButton, TableWidget

RED = QColor("#C0392B")
GREEN = QColor("#1E8449")
AMBER = QColor("#B7791F")
DIFF_BG = QColor("#FDF1F0")
CONFIRMED_BG = QColor("#EAF2FB")   # 已确认异常：淡蓝背景
CONFIRMED_FG = QColor("#1F6FB2")   # 已确认异常：蓝字

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
            "若某行不一致是手动改数据所致，选中该行点「标记已确认异常」并留备注即可（不计入差异数）。"
            "已收认定维度还可选中行点「逐行手动修正」直接改收款明细（双击任意行查看原台账信息）。"
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
        self.btn_confirm = PushButton("标记已确认异常")
        self.btn_confirm.clicked.connect(self._mark_confirmed)
        bar.addWidget(self.btn_confirm)
        self.btn_fix = PushButton("逐行手动修正")
        self.btn_fix.clicked.connect(self._on_fix_clicked)
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

        # 只看异常 + 只看已确认异常 + 统计
        bar2 = QHBoxLayout()
        self.chk_diff = QCheckBox("只看异常")
        self.chk_diff.setChecked(True)
        self.chk_diff.stateChanged.connect(self._render)
        bar2.addWidget(self.chk_diff)
        self.chk_confirmed = QCheckBox("只看已确认异常")
        self.chk_confirmed.setChecked(False)
        self.chk_confirmed.stateChanged.connect(self._render)
        bar2.addWidget(self.chk_confirmed)
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
        self.table._col = install_column_layout(self.table, "import_verify", "main")

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
        # 切换账期自动重跑对账（无需再点「加载对账」）
        if self._period:
            self.load_verify()

    def _on_dim_changed(self, *_):
        dim = self.combo_dim.currentData() or "invoice"
        # 已收认定维度：可「逐行手动修正」改收款明细；其余维度此按钮隐藏
        self.btn_fix.setVisible(dim == "received")
        self.btn_fix.setEnabled(dim == "received")
        # 标记已确认异常：经办人/已收维度均可（手动改所致差异均可确认）
        self.btn_confirm.setEnabled(dim in ("handler", "received"))
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
        self._attach_confirmed(period, dim)
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
        """发票信息对账：按发票号对库（源文件为权威清单，库中存在且经办人一致即通过）。"""
        inv_nos = [inv["invoice_no"] for inv in parsed.get("invoices", [])]
        conn = get_conn()
        try:
            if inv_nos:
                ph = ",".join("?" * len(inv_nos))
                db_invs = conn.execute(
                    f"SELECT invoice_no, invoice_date, buyer, total_amount, src_sheet, src_row "
                    f"FROM invoice WHERE source='import' AND invoice_no IN ({ph})",
                    inv_nos,
                ).fetchall()
                db_handlers: Dict[str, Dict[str, float]] = {}
                for r in conn.execute(
                    f"SELECT invoice_no, person_name, billing_amount "
                    f"FROM charge_detail WHERE source='import' AND invoice_no IN ({ph})",
                    inv_nos,
                ):
                    db_handlers.setdefault(r["invoice_no"], {})[r["person_name"]] = r["billing_amount"]
            else:
                db_invs = []
                db_handlers = {}
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
        """经办人分摊对账：源开票金额 ↔ DB charge_detail（按发票号对库）。"""
        inv_nos = [inv["invoice_no"] for inv in parsed.get("invoices", [])]
        conn = get_conn()
        try:
            db_cd: Dict[str, Dict[str, float]] = {}
            if inv_nos:
                ph = ",".join("?" * len(inv_nos))
                for r in conn.execute(
                    f"SELECT invoice_no, person_name, billing_amount "
                    f"FROM charge_detail WHERE source='import' AND invoice_no IN ({ph})",
                    inv_nos,
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
        """已收认定对账（方案E）：按批次读 received_snapshot，做「同批次 期望 vs 实际」对账。

        期望 = 导入时源文件声称的收款（快照 expected）；实际 = 本批次 import 写入的 collection
        （快照 actual）。缺失快照的批次（兼容）回退到「源重算期望 + 累计库按账期窗口过滤」。
        """
        conn = get_conn()
        try:
            snap_rows = conn.execute(
                "SELECT invoice_no, expected_json, actual_json FROM received_snapshot "
                "WHERE import_batch_id=?",
                (self._batch_id,),
            ).fetchall()
            # 兼容回退：累计库按月份窗口(<=账期)还原该批次导入时的视图
            live_act: Dict[str, List[Tuple[str, float, str]]] = {}
            for r in conn.execute(
                "SELECT invoice_no, receipt_date, amount, person_name FROM collection "
                "WHERE source='import' AND substr(receipt_date,1,7) <= ?",
                (period,),
            ):
                no = r["invoice_no"]
                ym = (r["receipt_date"] or "")[:7]
                live_act.setdefault(no, []).append((ym, r["amount"], r["person_name"] or ""))
        finally:
            conn.close()

        snap: Dict[str, Tuple[Dict, Dict]] = {}
        for r in snap_rows:
            try:
                snap[r["invoice_no"]] = (json.loads(r["expected_json"]), json.loads(r["actual_json"]))
            except Exception:  # noqa: BLE001
                continue

        rows: List[Dict] = []
        for i, inv in enumerate(parsed.get("invoices", [])):
            no = inv["invoice_no"]
            total = inv.get("total_amount") or 0.0
            is_red = inv.get("is_red")
            rem = inv.get("remark") or {}
            # 期望（源声称）
            if no in snap:
                exp = snap[no][0]
                exp_total = float(exp.get("total", 0.0) or 0.0)
                exp_items = exp.get("items", []) or []
            else:
                exp_total, exp_items = compute_expected_receipts(inv)
            exp_text = "、".join(f"{it['ym']} {it['amount']:,.2f}" for it in exp_items) \
                or ("红字无收款" if is_red else "未收款")
            # 实际（本批次落库）
            if no in snap:
                act = snap[no][1]
                act_items = act.get("items", []) or []
                act_total = float(act.get("total", 0.0) or 0.0)
            else:
                items = live_act.get(no, [])
                act_total = sum(a for _, a, _ in items)
                act_items = [{"ym": ym, "amount": a, "person": p} for ym, a, p in items]
            act_text = "、".join(f"{it['ym']} {it['amount']:,.2f}" for it in act_items) or "—"

            # 源勾稽不平提示（源自身问题，与库无关）
            src_warn = ""
            if rem.get("remaining") is not None and rem.get("receipts") and not rem.get("pure_date"):
                if abs((exp_total + rem["remaining"]) - total) > 0.01:
                    src_warn = "源勾稽不平；"

            reason = ""
            if abs(exp_total - act_total) > 0.01:
                reason = f"{src_warn}已收认定不符(差{exp_total - act_total:,.2f})"
            elif src_warn:
                reason = src_warn.rstrip("；")
            in_db = no in snap or bool(live_act.get(no))
            rows.append(self._mk(no, self._src_text(inv), inv.get("buyer", ""),
                                exp_total or None, act_total or None,
                                exp_text, act_text, reason, in_db, inv, i))
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

        show_confirmed_only = self.chk_confirmed.isChecked()
        only_diff = self.chk_diff.isChecked()
        rows = []
        for r in self._rows:
            if show_confirmed_only:
                if not r.get("confirmed_note"):
                    continue
            else:
                if only_diff and not r["reason"]:
                    continue
            rows.append(r)
        self.table.setRowCount(0)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            confirmed = bool(row.get("confirmed_note"))
            status = row["reason"] or "✓ 一致"
            if confirmed:
                note = row["confirmed_note"] or ""
                status = "✓ 已确认异常：" + (note if len(note) <= 40 else note[:39] + "…")
            vals = [
                row["invoice_no"], row["source"], row["buyer"],
                row["raw_amount"], row["db_amount"],
                row["raw_handlers"], row["db_handlers"],
                status,
            ]
            for c, v in enumerate(vals):
                item = self._make_item(v, c, confirmed=confirmed)
                if row["reason"] and not confirmed:
                    item.setBackground(DIFF_BG)
                elif confirmed:
                    item.setBackground(CONFIRMED_BG)
                self.table.setItem(r, c, item)
            self.table.setRowHeight(r, 32)
            self.table.item(r, 0).setData(Qt.ItemDataRole.UserRole, row["_idx"])
        if self._need_fit:
            self.table._col.apply()
            self._need_fit = False
        else:
            self.table._col.apply(remeasure=False)

        diff_n = sum(1 for r in self._rows if r["reason"] and not r.get("confirmed_note"))
        confirmed_n = sum(1 for r in self._rows if r.get("confirmed_note"))
        total_n = len(self._rows)
        self.lbl_stat.setText(
            f"共 {total_n} 张发票，差异 {diff_n} 张，已确认异常 {confirmed_n} 张"
            + ("（当前仅显示已确认异常）" if show_confirmed_only else
               ("（当前仅显示异常行）" if only_diff else ""))
        )
        amount = sum(r["raw_amount"] or 0 for r in self._rows if r.get("raw_amount") is not None) \
            + sum(r["db_amount"] or 0 for r in self._rows if r.get("raw_amount") is None)
        rate = f"{(total_n - diff_n) / total_n * 100:.1f}%" if total_n else "—"
        self.kpis["total"].setText(str(total_n))
        self.kpis["diff"].setText(str(diff_n))
        self.kpis["diff"].setStyleSheet("color: #C0392B;" if diff_n else "")
        self.kpis["amount"].setText(_money(amount))
        self.kpis["rate"].setText(rate)

    def _make_item(self, v, c: int, confirmed: bool = False):
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
                item.setForeground(CONFIRMED_FG if confirmed else GREEN)
            else:
                item.setForeground(RED)
        return item

    # ------------------------------------------------------------------ #
    # 一键修正
    # ------------------------------------------------------------------ #
    def _on_fix_clicked(self) -> None:
        self._fix_collection_manual()

    # 经办人分摊维度不再提供「一键修正（按源重算）」：手动改数据按手动结果来，
    # 差异通过「标记已确认异常」留备注即可（见 _mark_confirmed）。

    def _fix_collection_manual(self) -> None:
        """已收认定维度：选中行 → 弹逐行手动修正对话框（方案1优化版：行级合并）。"""
        row_idx = self.table.currentRow()
        if row_idx < 0:
            QMessageBox.information(self, "提示", "请先在表格中选中要修正的发票行。")
            return
        item = self.table.item(row_idx, 0)
        if item is None:
            return
        idx = item.data(Qt.ItemDataRole.UserRole)
        row = self._rows[idx]
        inv = row.get("src")
        if not inv:
            QMessageBox.information(self, "提示", "该行无源数据，无法手动修正。")
            return
        no = inv["invoice_no"]
        total = inv.get("total_amount") or 0.0
        conn = get_conn()
        try:
            s = conn.execute(
                "SELECT expected_json FROM received_snapshot "
                "WHERE import_batch_id=? AND invoice_no=?",
                (self._batch_id, no),
            ).fetchone()
            if s:
                snap = json.loads(s["expected_json"])
                exp_items = snap.get("items", []) or []
                exp_total = float(snap.get("total", 0.0) or 0.0)
            else:
                exp_total, exp_items = compute_expected_receipts(inv)
            cur = conn.execute(
                "SELECT id, receipt_date, amount, person_name FROM collection "
                "WHERE invoice_no=? AND source='import' ORDER BY receipt_date",
                (no,),
            ).fetchall()
        finally:
            conn.close()
        current_rows = [dict(r) for r in cur]
        dlg = CollectionFixDialog(self, no, total, exp_items, current_rows,
                                  exp_total, self._batch_id)
        if dlg.exec() == QDialog.DialogCode.Accepted:
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

    # ------------------------------------------------------------------ #
    # 已确认异常（手动改数据所致差异，留备注防误判）
    # ------------------------------------------------------------------ #
    def _attach_confirmed(self, period: str, dim: str) -> None:
        notes = self._load_confirmed(period, dim)
        for row in self._rows:
            row["confirmed_note"] = notes.get(row["invoice_no"], "")

    def _load_confirmed(self, period: str, dim: str) -> Dict[str, str]:
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT invoice_no, note FROM anomaly_note WHERE dim=? AND period=?",
                (dim, period),
            ).fetchall()
        finally:
            conn.close()
        return {r["invoice_no"]: r["note"] for r in rows}

    def _mark_confirmed(self) -> None:
        dim = self.combo_dim.currentData() or "invoice"
        if dim not in ("handler", "received"):
            QMessageBox.information(self, "提示", "当前维度不支持标记已确认异常。")
            return
        row_idx = self.table.currentRow()
        if row_idx < 0:
            QMessageBox.information(self, "提示", "请先在表格中选中要标记的行。")
            return
        item = self.table.item(row_idx, 0)
        if item is None:
            return
        idx = item.data(Qt.ItemDataRole.UserRole)
        row = self._rows[idx]
        no = row["invoice_no"]
        dlg = AnomalyConfirmDialog(
            self, no, dim, self._period, row.get("reason", ""), row.get("confirmed_note", "")
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.load_verify()


class AnomalyConfirmDialog(QDialog):
    """标记 / 更新 / 撤销「已确认异常」备注。"""

    def __init__(self, parent, invoice_no: str, dim: str, period: str,
                 reason: str, existing_note: str) -> None:
        super().__init__(parent)
        self._no = invoice_no
        self._dim = dim
        self._period = period
        self.setWindowTitle(f"标记已确认异常 — {invoice_no}")
        self.resize(460, 290)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(10)

        root.addWidget(CaptionLabel(f"维度：{dict(DIMS).get(dim, dim)}　账期：{period}"))
        if reason:
            rlab = QLabel(f"当前差异原因：{reason}")
            rlab.setWordWrap(True)
            rlab.setObjectName("anomalyReason")
            root.addWidget(rlab)
        else:
            root.addWidget(CaptionLabel("（当前无差异，仍可为该记录添加确认说明）"))

        root.addWidget(CaptionLabel("确认说明（必填，记录为什么手动修改 / 无需按源重算）："))
        self.edit = QPlainTextEdit(existing_note)
        self.edit.setPlaceholderText("例如：经办人信息已手动修正，与源台账不一致属正常")
        root.addWidget(self.edit, 1)

        box = QDialogButtonBox()
        self.btn_ok = box.addButton("确定", QDialogButtonBox.ButtonRole.AcceptRole)
        self.btn_revoke = box.addButton("撤销确认", QDialogButtonBox.ButtonRole.RejectRole)
        self.btn_cancel = box.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        self.btn_revoke.setEnabled(bool(existing_note))  # 仅已确认时可撤销
        self.btn_revoke.clicked.connect(self._revoke)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self.accept)
        root.addWidget(box)

    def accept(self) -> None:
        note = self.edit.toPlainText().strip()
        if not note:
            QMessageBox.warning(self, "需填写说明", "请填写确认说明，避免日后误判。")
            return
        conn = get_conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO anomaly_note (invoice_no, dim, period, note) VALUES (?,?,?,?)",
                (self._no, self._dim, self._period, note),
            )
            conn.commit()
        finally:
            conn.close()
        super().accept()

    def _revoke(self) -> None:
        conn = get_conn()
        try:
            conn.execute(
                "DELETE FROM anomaly_note WHERE invoice_no=? AND dim=? AND period=?",
                (self._no, self._dim, self._period),
            )
            conn.commit()
        finally:
            conn.close()
        self.done(QDialog.DialogCode.Accepted)
