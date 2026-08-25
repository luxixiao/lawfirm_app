"""导入校验中心（方案 B：发票台账 写后对账）

维护组新页：选账期 → 读该批次自动存档的源文件重新解析 ↔ 与 DB 实际落库数据
逐行比对（金额/日期/购方/经办人拆分），差异行标红，顶部 KPI（行数/差异/总额/一致率），
默认「只看异常」，可切全量；双击行打开原台账溯源卡片。

只读比对，不写库。先试点发票台账（ledger）一种。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QHBoxLayout, QLabel, QMessageBox, QVBoxLayout, QWidget,
)

from app.db import get_conn
from app.importer.archive_helper import resolve_archive
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


def _money(v) -> str:
    return f"{v:,.2f}" if isinstance(v, (int, float)) else str(v or "")


class ImportVerifyView(QWidget):
    """发票台账导入校验中心。"""

    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(12)

        t = QLabel("导入校验（发票台账）")
        t.setObjectName("pageTitle")
        lay.addWidget(t)
        hint = CaptionLabel(
            "选择已导入的账期，系统重读存档源文件并与数据库逐行对账；"
            "差异行红色标注，双击任意行查看原台账信息。"
        )
        lay.addWidget(hint)

        # 筛选栏
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(CaptionLabel("账期"))
        self.combo_period = ComboBox()
        self.combo_period.currentIndexChanged.connect(self._on_period_changed)
        bar.addWidget(self.combo_period)
        self.btn_load = PrimaryPushButton("加载对账")
        self.btn_load.clicked.connect(self.load_verify)
        bar.addWidget(self.btn_load)
        bar.addStretch()
        lay.addLayout(bar)

        # KPI
        kpi = QHBoxLayout()
        kpi.setSpacing(12)
        self.kpis: Dict[str, QLabel] = {}
        for key, name in [("total", "总行数"), ("diff", "差异数"),
                          ("amount", "发票总额"), ("rate", "一致率")]:
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
        # 性能：关闭单元格自动换行 + 像素级滚动，避免大表滚动卡顿。
        self.table.setWordWrap(False)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        # 横向滚动同样用像素级，宽表左右滑动更顺滑（此前只设了竖向）。
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.cellDoubleClicked.connect(self._cell_double_clicked)
        lay.addWidget(self.table, 1)
        # 列宽持久化：用户手动调整后的列宽记入 QSettings，跨页面/重开保留。
        attach_persistence(self.table, "import_verify", "main")

        # 底部说明
        foot = CaptionLabel("双击任意行可查看该记录在原台账中的信息（含所属 sheet 与行号）。")
        lay.addWidget(foot)

        self._rows: List[Dict] = []
        self._period = ""
        self._need_fit = True  # 数据集合变化时才自适应列宽（切「只看异常」不重算）
        self._archive = ""  # 当前对账批次存档绝对路径（溯源卡片打开原文件用）
        self._load_periods()

    # ------------------------------------------------------------------ #
    # 账期
    # ------------------------------------------------------------------ #
    def _load_periods(self) -> None:
        """账期下拉：已导入过发票台账的账期，默认选最新。"""
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

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # 重新进入页面时刷新账期下拉（可能有新导入）
        cur = self._period
        self._load_periods()
        idx = self.combo_period.findData(cur)
        if idx >= 0:
            self.combo_period.setCurrentIndex(idx)
        if self.combo_period.count():
            self.load_verify()

    # ------------------------------------------------------------------ #
    # 对账
    # ------------------------------------------------------------------ #
    def load_verify(self) -> None:
        period = self._period
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

        self._rows = self._compare(batch["id"], parsed, period, str(path))
        self._archive = str(path)
        self._need_fit = True
        self._render()

    def _compare(self, batch_id: int, parsed: Dict, period: str, archive: str) -> List[Dict]:
        """解析结果 ↔ DB 落库逐行比对。返回展示行列表。

        口径说明：invoice 为跨批次 upsert（首次创建即归属该批次，重导只补差），
        因此"该批次导入后应有数据"= 该账期发票 ∪ 该批次新建发票（含期外），
        而不是按 import_batch_id 过滤 invoice。
        """
        conn = get_conn()
        try:
            db_invs = conn.execute(
                "SELECT invoice_no, invoice_date, buyer, total_amount, src_sheet, src_row "
                "FROM invoice WHERE source='import' AND "
                "(strftime('%Y-%m', invoice_date)=? OR import_batch_id=?)",
                (period, batch_id),
            ).fetchall()
            db_handlers: Dict[str, Dict[str, float]] = {}
            for r in conn.execute(
                "SELECT cd.invoice_no, cd.person_name, cd.billing_amount "
                "FROM charge_detail cd JOIN invoice i ON cd.invoice_no=i.invoice_no "
                "WHERE cd.source='import' AND "
                "(strftime('%Y-%m', i.invoice_date)=? OR i.import_batch_id=?)",
                (period, batch_id),
            ):
                db_handlers.setdefault(r["invoice_no"], {})[r["person_name"]] = r["billing_amount"]
        finally:
            conn.close()

        db_map: Dict[str, Dict] = {}
        for r in db_invs:
            db_map[r["invoice_no"]] = {
                "invoice_date": r["invoice_date"] or "",
                "buyer": r["buyer"] or "",
                "total_amount": r["total_amount"] or 0.0,
                "handlers": db_handlers.get(r["invoice_no"], {}),
                "src_sheet": r["src_sheet"] or "",
                "src_row": r["src_row"] or 0,
            }

        parsed_map: Dict[str, Dict] = {}
        for inv in parsed.get("invoices", []):
            no = inv["invoice_no"]
            parsed_map[no] = {
                "invoice_date": inv.get("invoice_date") or "",
                "buyer": inv.get("buyer") or "",
                "total_amount": inv.get("total_amount") or 0.0,
                "handlers": {n: a for n, a in inv.get("handlers", [])},
                "src_sheet": inv.get("sheet_name") or inv.get("sheet") or "",
                "src_row": inv.get("row_no") or 0,
                # 溯源用：重读存档得到的原始行（双击直接展示，不依赖 DB src）
                "header": inv.get("header") or [],
                "raw_row": inv.get("raw_row") or [],
                "archive": archive,
                "file_name": archive.replace("\\", "/").split("/")[-1] if archive else "",
            }

        rows: List[Dict] = []
        for i, no in enumerate(sorted(set(parsed_map) | set(db_map))):
            p = parsed_map.get(no)
            d = db_map.get(no)
            if p is None:
                rows.append({
                    "invoice_no": no,
                    "source": "—",
                    "buyer": d["buyer"],
                    "raw_amount": None, "db_amount": d["total_amount"],
                    "raw_handlers": "—", "db_handlers": self._handlers_text(d["handlers"]),
                    "reason": "解析中缺失", "in_db": True, "src": None,
                    "_idx": i,
                })
                continue
            if d is None:
                rows.append({
                    "invoice_no": no,
                    "source": self._src_text(p),
                    "buyer": p["buyer"],
                    "raw_amount": p["total_amount"], "db_amount": None,
                    "raw_handlers": self._handlers_text(p["handlers"]),
                    "db_handlers": "—",
                    "reason": "库中缺失", "in_db": False, "src": p,
                    "_idx": i,
                })
                continue
            reason = ""
            if abs(p["total_amount"] - d["total_amount"]) > 0.005:
                reason = "金额不一致"
            elif self._handlers_text(p["handlers"]) != self._handlers_text(d["handlers"]):
                reason = "经办人拆分不一致"
            elif (p["invoice_date"] or "") != (d["invoice_date"] or ""):
                reason = "日期不一致"
            rows.append({
                "invoice_no": no,
                "source": self._src_text(p),
                "buyer": p["buyer"] or d["buyer"],
                "raw_amount": p["total_amount"], "db_amount": d["total_amount"],
                "raw_handlers": self._handlers_text(p["handlers"]),
                "db_handlers": self._handlers_text(d["handlers"]),
                "reason": reason, "in_db": True, "src": p,
                "_idx": i,
            })
        return rows

    @staticmethod
    def _handlers_text(handlers: Dict[str, float]) -> str:
        if not handlers:
            return "—"
        return "、".join(f"{n} {_money(a)}" for n, a in sorted(handlers.items()))

    @staticmethod
    def _src_text(src: Dict) -> str:
        return f"{src['src_sheet']} · 第{src['src_row']}行"

    # ------------------------------------------------------------------ #
    # 渲染
    # ------------------------------------------------------------------ #
    def _render(self) -> None:
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
            # 存全量行索引（self._rows 中的位置），而非筛选后的显示行号，
            # 避免「只看差异行」开启时双击取到错误的发票溯源。
            self.table.item(r, 0).setData(Qt.ItemDataRole.UserRole, row["_idx"])
        # 列宽：仅在数据集合变化时自适应（auto-fit 重算全部列宽较耗时），
        # 切换「只看异常」等纯过滤不动列宽、复用上次结果，避免卡顿（C 方案）。
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

        # KPI
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
            # 解析侧已重读存档 → 直接用原行展示（不依赖 DB src，历史批次也可溯源）
            from app.ui.ledger_source import show_ledger_source
            show_ledger_source(
                self, header=src.get("header") or [], raw_row=src["raw_row"],
                sheet_name=src.get("src_sheet") or "—", row_no=src.get("src_row") or 0,
                archive_path=src.get("archive") or "", file_name=src.get("file_name") or "",
            )
            return
        if not row.get("in_db"):
            QMessageBox.information(
                self, "无库记录", f"发票 {row['invoice_no']} 在数据库中不存在，无法溯源。"
            )
            return
        # 兜底：走 DB 溯源（仅新代码导入的批次有 src 记录）
        from app.ui.ledger_source import show_source_for_invoice
        show_source_for_invoice(self, row["invoice_no"])
