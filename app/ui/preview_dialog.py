"""写前预览对话框（方案 A：发票台账导入确认）

导入写库前弹出：左侧展示解析结果（发票号/购方/经办人拆分/金额/收款认定），
并标注「已规范化」的差异行（经办人拆分、金额改写、备注收款降级），
默认只显示差异行，可切全量；双击任意行可打开原台账溯源卡片。

用户「全部确认入库」→ 返回原 data 继续写库；「取消」→ 整个导入取消。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QHBoxLayout, QLabel,
    QMessageBox, QVBoxLayout,
)

from app.importer.excel_reader import col_index
from app.ui.ledger_source import show_ledger_source
from app.ui.table_view import auto_fit_columns
from app.ui.widgets import CaptionLabel, PrimaryPushButton, PushButton, TableWidget

RED = QColor("#C0392B")     # 红字/差异强调
AMBER = QColor("#B7791F")   # 已规范化提示
GREEN = QColor("#1E8449")   # 一致
DIFF_BG = QColor("#FDF1F0")  # 差异行浅红背景


def _fmt_money(v) -> str:
    return f"{v:,.2f}" if isinstance(v, (int, float)) else str(v or "")


def _raw_cell(inv: Dict, *headers: str) -> str:
    """从 raw_row + header 提取原始单元格文本（问题行修正行无原始行时返回空）。"""
    row = inv.get("raw_row") or []
    header = inv.get("header") or []
    if not row or not header:
        return ""
    i = col_index(header, *headers)
    if 0 <= i < len(row):
        return str(row[i]).strip()
    return ""


def _norm_text(s: str) -> str:
    """比对用规范化：去空格与千分位逗号。"""
    return s.replace(" ", "").replace(",", "").replace("，", "")


def _diff_reason(inv: Dict) -> str:
    """差异判定：返回 ""（一致）或原因文本。

    - 经办人拆分/改写：多人且原始文本 ≠ 解析文本（单名全额推断不标）
    - 金额规范化：原始金额文本 ≠ 解析数值
    - sheet2/3 备注日期降级为未收（原备注有日期但解析后无收款）
    """
    if inv["is_red"]:
        return "红字发票"
    raw_amt = _raw_cell(inv, "金额", "开票金额")
    if raw_amt:
        try:
            if abs(float(raw_amt.replace(",", "").replace("，", "")) - inv["total_amount"]) > 0.005:
                return "金额已规范化"
        except ValueError:
            return "金额已规范化"
    raw_handlers = (inv.get("handler_text") or "").strip()
    if raw_handlers and len(inv["handlers"]) > 1:
        parsed_text = "、".join(f"{n}{a:g}" for n, a in inv["handlers"])
        if _norm_text(raw_handlers) != _norm_text(parsed_text):
            return "经办人已拆分"
    if inv["sheet"] in ("sheet2", "sheet3"):
        remark_raw = (inv.get("remark_raw") or "").strip()
        if remark_raw and not inv["remark"]["receipts"] and not inv["remark"]["pure_date"]:
            return "备注收款降级为未收"
    return ""


class PreviewDialog(QDialog):
    """发票台账写前预览确认对话框。

    构造参数：data = parse_ledger_file(...) 的结果 dict；period = "YYYY-MM"。
    exec() == Accepted 表示用户确认入库；结果仍为原 data（本轮不做逐条改写）。
    """

    def __init__(self, data: Dict, period: str, parent=None, path: str = "") -> None:
        super().__init__(parent)
        self._data = data
        self._path = path
        self._file_name = path.replace("\\", "/").split("/")[-1] if path else "导入文件"
        self.setWindowTitle(f"导入预览确认 — {period} 发票台账")
        self.resize(1040, 620)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(10)

        # 标题 + 说明
        t = QLabel(f"导入预览确认（{period} 发票台账）")
        t.setObjectName("pageTitle")
        root.addWidget(t)
        hint = CaptionLabel(
            "写库前逐行对照：绿色=一致，琥珀=已按规则规范化（建议确认），红色=红字发票。"
            "双击任意行可查看该行在原台账中的信息。"
        )
        root.addWidget(hint)

        # 筛选栏：只看差异 + 统计
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.chk_diff = QCheckBox("只看差异行")
        self.chk_diff.setChecked(True)
        self.chk_diff.stateChanged.connect(self._render)
        bar.addWidget(self.chk_diff)
        bar.addStretch()
        self.lbl_stat = CaptionLabel("")
        bar.addWidget(self.lbl_stat)
        root.addLayout(bar)

        # 表格
        self.table = TableWidget(self)
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(
            ["发票号", "来源", "购方", "原始经办人", "解析经办人", "金额", "收款认定", "状态"]
        )
        self.table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(TableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(False)
        # 性能：关闭单元格自动换行（避免数百行滚动时逐格重排文字造成卡顿），
        # 改用像素级滚动使大表滚动更顺滑；超长文本由双击溯源卡片查看完整内容。
        self.table.setWordWrap(False)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.cellDoubleClicked.connect(self._cell_double_clicked)
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

        self._build_rows()
        self._render()

    # ------------------------------------------------------------------ #
    # 数据
    # ------------------------------------------------------------------ #
    def _build_rows(self) -> None:
        """构造展示行：每张发票一条，附差异原因与原始/解析文本。"""
        self._rows: List[Dict] = []
        for i, inv in enumerate(self._data.get("invoices", [])):
            sheet_name = inv.get("sheet_name") or inv.get("sheet") or "—"
            row_no = inv.get("row_no") or 0
            self._rows.append({
                "invoice_no": inv.get("invoice_no", ""),
                "source": f"{sheet_name} · 第{row_no}行",
                "buyer": inv.get("buyer", ""),
                "raw_handlers": (inv.get("handler_text") or "").strip() or "—",
                "parsed_handlers": "、".join(
                    f"{n} {_fmt_money(a)}" for n, a in inv.get("handlers", [])
                ) or "—",
                "amount": inv.get("total_amount", 0.0),
                "is_red": bool(inv.get("is_red")),
                "receipt": self._receipt_text(inv),
                "reason": _diff_reason(inv),
                "_idx": i,
                "_inv": inv,
            })

    @staticmethod
    def _receipt_text(inv: Dict) -> str:
        """收款认定摘要：红字→不产生收款；纯日期→全额；多笔→逐笔。"""
        if inv.get("is_red"):
            return "红字，不产生收款"
        rem = inv["remark"]
        if rem.get("pure_date"):
            return f"{rem['pure_date']} 全额"
        if rem.get("receipts"):
            parts = []
            for ym, amt in rem["receipts"]:
                parts.append(f"{ym} " + ("全额" if amt == 0 else _fmt_money(amt)))
            return "、".join(parts)
        return "未收款"

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
                row["raw_handlers"], row["parsed_handlers"],
                row["amount"], row["receipt"], row["reason"] or "✓ 一致",
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
        auto_fit_columns(self.table, max_width=240)

        diff_n = sum(1 for r in self._rows if r["reason"])
        self.lbl_stat.setText(
            f"共 {len(self._rows)} 张发票，其中差异/需确认 {diff_n} 张"
            + ("（当前仅显示差异行）" if only_diff else "")
        )

        inv_total = sum(r["amount"] for r in self._rows)
        s12 = self._data.get("sheet12_total", 0)
        pp = self._data.get("prepayments", [])
        pp_total = sum(p.get("amount", 0.0) for p in pp)
        self.lbl_summary.setText(
            f"全量发票合计 ¥{inv_total:,.2f}（其中 sheet1+2 ¥{s12:,.2f} 用于销项校验）　"
            f"预收款 {len(pp)} 条，合计 ¥{pp_total:,.2f}"
        )

    def _make_item(self, v, c: int):
        from PySide6.QtWidgets import QTableWidgetItem
        text = _fmt_money(v) if c == 5 else str(v)
        item = QTableWidgetItem(text)
        if c == 5:
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if c == 7:
            t = str(v)
            if t.startswith("红字"):
                item.setForeground(RED)
            elif t.startswith("✓"):
                item.setForeground(GREEN)
            else:
                item.setForeground(AMBER)
        if c == 6 and isinstance(v, (int, float)) and v < 0:
            item.setForeground(RED)
        return item

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def _cell_double_clicked(self, r: int, _c: int) -> None:
        """双击行 → 打开原台账溯源卡片（复用 ledger_source）。"""
        item = self.table.item(r, 0)
        if item is None:
            return
        idx = item.data(Qt.ItemDataRole.UserRole)
        if idx is None:
            return
        inv = self._rows[idx]["_inv"]
        header = inv.get("header") or []
        raw_row = inv.get("raw_row") or []
        sheet_name = inv.get("sheet_name") or inv.get("sheet") or "—"
        row_no = inv.get("row_no") or 0
        if not raw_row:
            QMessageBox.information(
                self, "无原始行",
                "该行来自问题行修正，无对应原始台账行。",
            )
            return
        show_ledger_source(
            self, header=header, raw_row=raw_row, sheet_name=sheet_name,
            row_no=row_no, archive_path=self._path, file_name=self._file_name,
        )
