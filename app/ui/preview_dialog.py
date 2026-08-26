"""写前预览对话框（方案 D：发票台账导入确认 — 置信度分级 + 逐人已收可编辑）

导入写库前弹出：
- 对每个发票评估「收款认定」「经办人分摊」两个维度的置信度；
- 高置信（系统规则已可靠判定）→ 折叠隐藏，自动过、不打扰；
- 低置信（无法识别/有疑问）→ 列在「待确认」清单，每张可看系统预填的
  每经办人已收金额，并可直接改成正确的已收金额（选项 2：先按系统规则预填）；
- 「全部确认入库」→ 高置信用系统值、疑点用确认值，写库时回写
  charge_detail.received_override。

未改动（疑点也）一律确认时，行为与历史一致（received_override 为空→系统推导）。
"""
from __future__ import annotations

from typing import Dict, List

from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtGui import QColor, QCursor
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractSpinBox, QCheckBox, QDoubleSpinBox, QDialog,
    QHBoxLayout, QLabel, QMessageBox, QSizePolicy, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from app.db import get_conn
from app.engine.import_confidence import evaluate
from app.ui.ledger_source import show_ledger_source
from app.ui.table_view import auto_fit_columns
from app.ui.widgets import CaptionLabel, PrimaryPushButton, PushButton, TableWidget

RED = QColor("#C0392B")     # 差异/疑问强调
AMBER = QColor("#B7791F")   # 疑问提示
GREEN = QColor("#1E8449")   # 高置信一致
DIFF_BG = QColor("#FDF1F0")  # 疑点行浅红背景


def _fmt_money(v) -> str:
    return f"{v:,.2f}" if isinstance(v, (int, float)) else str(v or "")


class HandlerReceivedEditor(QWidget):
    """每经办人已收编辑器：经办人标签 + 可编辑金额框，预填系统推导值。

    编辑结果实时写入 overrides[invoice_no][name]，供确认时回写。
    """

    def __init__(self, ev: Dict, overrides: Dict[str, Dict[str, float]]) -> None:
        super().__init__()
        self._ev = ev
        self._overrides = overrides
        no = ev["invoice_no"]
        self._overrides.setdefault(no, {})
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        sys_recv = ev["system_received"]
        for name, billing in ev["handlers"]:
            lab = QLabel(name)
            lab.setToolTip(f"开票金额 {billing:,.2f}")
            spin = QDoubleSpinBox()
            spin.setRange(0, 9_999_999_999)
            spin.setDecimals(2)
            spin.setSingleStep(100)
            spin.setMaximumWidth(96)
            # 去掉金额增减按钮；输入框上下顶满单元格
            spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            spin.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
            cur = self._overrides[no].get(name, sys_recv.get(name, 0.0))
            spin.blockSignals(True)
            spin.setValue(cur)
            spin.blockSignals(False)
            spin.valueChanged.connect(lambda v, n=name: self._on_change(n, v))
            lay.addWidget(lab)
            lay.addWidget(spin)
        lay.addStretch()

    def _on_change(self, name: str, value: float) -> None:
        self._overrides[self._ev["invoice_no"]][name] = value


class PreviewDialog(QDialog):
    """发票台账写前预览确认对话框（方案 D）。

    构造参数：data = parse_ledger_file(...) 结果；period = "YYYY-MM"。
    exec() == Accepted 表示确认入库；结果回写进 data["invoices"] 的
    received_overrides 字段（原地修改，importer 写库时读取）。
    """

    def __init__(self, data: Dict, period: str, parent=None, path: str = "") -> None:
        super().__init__(parent)
        self._data = data
        self._path = path
        self._file_name = path.replace("\\", "/").split("/")[-1] if path else "导入文件"
        self._overrides: Dict[str, Dict[str, float]] = {}
        self.setWindowTitle(f"导入预览确认 — {period} 发票台账")
        self.resize(1120, 640)

        # 置信度评估
        staff = set()
        conn = get_conn()
        try:
            staff = {r["name"] for r in conn.execute("SELECT name FROM staff")}
        finally:
            conn.close()
        self._rows: List[Dict] = evaluate(data, staff)
        for i, ev in enumerate(self._rows):
            ev["_idx_global"] = i
        self._low = sum(1 for r in self._rows if r["conf"] == "low")
        self._high = len(self._rows) - self._low

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(10)

        t = QLabel(f"导入预览确认（{period} 发票台账）")
        t.setObjectName("pageTitle")
        root.addWidget(t)
        hint = CaptionLabel(
            "系统已按规则对每张发票做置信度判定：高置信将自动入库（不弹出）；"
            "以下仅列出「待确认（有疑问）」的发票——请核对每经办人已收金额（已按系统规则预填，"
            "可直接改为正确值），确认后回写。勾选「显示高置信全部」可查看全部。"
        )
        root.addWidget(hint)

        # 筛选栏
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.chk_diff = QCheckBox("只看待确认（有疑问）")
        self.chk_diff.setChecked(True)
        self.chk_diff.stateChanged.connect(self._render)
        bar.addWidget(self.chk_diff)
        self.lbl_stat = CaptionLabel("")
        bar.addWidget(self.lbl_stat)
        bar.addStretch()
        root.addLayout(bar)

        # 表格
        self.table = TableWidget(self)
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(
            ["发票号", "来源", "购方", "金额", "经办人分摊(开票金额)",
             "各经办人已收(可改)", "收款认定", "状态"]
        )
        self.table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(TableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setWordWrap(False)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.cellDoubleClicked.connect(self._cell_double_clicked)
        self._init_cell_tooltip()
        root.addWidget(self.table, 1)

        # 汇总
        self.lbl_summary = CaptionLabel("")
        root.addWidget(self.lbl_summary)

        # 底部按钮
        btns = QHBoxLayout()
        self.btn_confirm = PrimaryPushButton("全部确认入库")
        self.btn_confirm.clicked.connect(self.accept)
        btn_cancel = PushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btns.addStretch()
        btns.addWidget(btn_cancel)
        btns.addWidget(self.btn_confirm)
        root.addLayout(btns)

        self._render()

    # ------------------------------------------------------------------ #
    # 渲染
    # ------------------------------------------------------------------ #
    def _render(self) -> None:
        only_diff = self.chk_diff.isChecked()
        rows = [r for r in self._rows if not only_diff or r["conf"] == "low"]
        self.table.setRowCount(0)
        self.table.setRowCount(len(rows))
        for r, ev in enumerate(rows):
            vals = [
                ev["invoice_no"],
                f"{ev['sheet_label']} · 第{ev['row_no']}行",
                ev["buyer"],
                ev["total_amount"],
                "、".join(f"{n} {_fmt_money(b)}" for n, b in ev["handlers"]) or "—",
                "",  # 第 5 列放可编辑控件
                ev["receipt_text"],
                "、".join(ev["reasons"]) if ev["reasons"] else "✓ 高置信自动过",
            ]
            for c, v in enumerate(vals):
                if c == 5:
                    continue
                item = self._make_item(v, c, ev)
                if ev["conf"] == "low":
                    item.setBackground(DIFF_BG)
                self.table.setItem(r, c, item)
            # 第 5 列：每经办人已收编辑器
            editor = HandlerReceivedEditor(ev, self._overrides)
            self.table.setCellWidget(r, 5, editor)
            self.table.setRowHeight(r, 34)
            self.table.item(r, 0).setData(Qt.ItemDataRole.UserRole, ev["_idx_global"])
        auto_fit_columns(self.table, max_width=220)
        self.table.setColumnWidth(4, 220)
        self.table.setColumnWidth(5, 380)

        # 单元格文字被列宽裁剪时，悬停显示全文（列 5 是控件，无 item）
        fm = self.table.fontMetrics()
        pad = 14
        for r in range(self.table.rowCount()):
            for c in range(self.table.columnCount()):
                if c == 5:
                    continue
                item = self.table.item(r, c)
                if item is None:
                    continue
                text = item.text()
                if text and fm.horizontalAdvance(text) > self.table.columnWidth(c) - pad:
                    item.setData(self._TIP_ROLE, text)

        self.lbl_stat.setText(
            f"共 {len(self._rows)} 张：高置信自动过 {self._high} 张，"
            f"待确认（有疑问）{self._low} 张"
            + ("（当前仅显示待确认）" if only_diff else "（全部显示）")
        )
        inv_total = sum(ev["total_amount"] for ev in self._rows)
        pp = self._data.get("prepayments", [])
        pp_total = sum(p.get("amount", 0.0) for p in pp)
        self.lbl_summary.setText(
            f"全量发票合计 ¥{inv_total:,.2f}　预收款 {len(pp)} 条，合计 ¥{pp_total:,.2f}"
        )

    # ---- 自定义单元格 tooltip（Notion 浅灰卡片，仅当内容被列宽裁剪时悬停显示全文） ----
    _TIP_ROLE = Qt.ItemDataRole.UserRole + 1

    def _init_cell_tooltip(self) -> None:
        self._tip = QLabel(self)
        self._tip.setObjectName("cellTip")
        self._tip.setWindowFlag(Qt.WindowType.ToolTip, True)
        self._tip.hide()
        self._tip_timer = QTimer(self)
        self._tip_timer.setSingleShot(True)
        self._tip_timer.timeout.connect(self._pop_tip)
        self.table.setMouseTracking(True)
        self.table.cellEntered.connect(self._on_cell_entered)
        self.table.viewportEntered.connect(self._hide_tip)

    def _on_cell_entered(self, row: int, col: int) -> None:
        self._tip_timer.stop()
        item = self.table.item(row, col)
        if item and item.data(self._TIP_ROLE):
            self._tip_row = row
            self._tip_col = col
            self._tip_timer.start(120)
        else:
            self._hide_tip()

    def _pop_tip(self) -> None:
        row = getattr(self, "_tip_row", -1)
        col = getattr(self, "_tip_col", -1)
        item = self.table.item(row, col)
        text = item.data(self._TIP_ROLE) if item else None
        if not text:
            return
        self._tip.setText(text)
        self._tip.adjustSize()
        self._tip.move(QCursor.pos() + QPoint(14, 18))
        self._tip.show()

    def _hide_tip(self) -> None:
        self._tip_timer.stop()
        self._tip.hide()

    def _make_item(self, v, c: int, ev: Dict):
        from PySide6.QtWidgets import QTableWidgetItem
        if c == 3:
            item = QTableWidgetItem(_fmt_money(v))
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        else:
            item = QTableWidgetItem(str(v))
        if c == 7:
            t = str(v)
            if t.startswith("✓"):
                item.setForeground(GREEN)
            else:
                item.setForeground(AMBER)
        if c == 3 and isinstance(v, (int, float)) and v < 0:
            item.setForeground(RED)
        return item

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
        ev = self._rows[idx]
        inv = ev["_inv"]
        header = inv.get("header") or []
        raw_row = inv.get("raw_row") or []
        sheet_name = inv.get("sheet_name") or inv.get("sheet") or "—"
        row_no = inv.get("row_no") or 0
        if not raw_row:
            QMessageBox.information(self, "无原始行", "该行来自问题行修正，无对应原始台账行。")
            return
        show_ledger_source(
            self, header=header, raw_row=raw_row, sheet_name=sheet_name,
            row_no=row_no, archive_path=self._path, file_name=self._file_name,
        )

    def accept(self) -> None:
        """确认入库：把编辑后的每经办人已收回写为 received_overrides。"""
        for ev in self._rows:
            no = ev["invoice_no"]
            ov = self._overrides.get(no, {})
            # 仅保留与系统预填不同的（其余由系统推导，保持 received_override 为空）
            final = {
                name: v for name, v in ov.items()
                if abs(v - ev["system_received"].get(name, 0.0)) > 0.005
            }
            ev["_inv"]["received_overrides"] = final
        super().accept()
