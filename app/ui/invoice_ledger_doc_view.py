"""发票台账查看页（统一审计中心方案）

- 数据源：raw_ledger（发票台账文档逐 sheet 逐行 1:1 镜像，导入双写落库）。
- 只读查看：可按工作表切换、搜索（发票号/对方/金额/案号）、排序、筛选（仅红字）。
- 预留修改能力：右击「编辑此行」修改 raw_ledger 并自动写 change_log；
  右击「查看修改记录」打开统一审计中心（预设 raw_ledger + 行 id）。
- 与「销项发票」页同构（raw_invoice vs raw_ledger），概念统一。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.engine import raw_ledger as rl
from app.engine.raw_ledger import EDIT_FIELDS
from app.ui.audit_view import AuditView
from app.ui.column_state import attach_persistence, auto_fit_then_restore
from app.ui.widgets import CaptionLabel, PushButton, SubtitleLabel

_RED_BG = QColor("#FFECEC")

# 显示列定义：(表头, 取值函数)
def _date_of(r: dict) -> str:
    if r.get("kind") == "prepayment":
        return r.get("recv_date_raw") or ""
    return r.get("invoice_date_raw") or ""

_HEADERS = ["序号", "工作表", "日期", "发票号码", "对方", "金额", "经办人", "备注", "案号"]
_GETTERS = [
    lambda r: r.get("seq") or "",
    lambda r: r.get("sheet_name") or "",
    _date_of,
    lambda r: r.get("invoice_no") or "",
    lambda r: r.get("buyer") or "",
    lambda r: r.get("amount_raw") or "",
    lambda r: r.get("handler_text") or "",
    lambda r: r.get("remark") or "",
    lambda r: r.get("case_no") or "",
]
_AMOUNT_COL = 5  # 金额列（按数值排序 + 红字标红）


def _parse_total(txt) -> float:
    from app.engine.raw_ledger import _parse_total as _pt
    return _pt(txt)


class InvoiceLedgerDocView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(12)

        t = SubtitleLabel("发票台账")
        lay.addWidget(t)
        h = CaptionLabel("数据来源：发票台账文档（最新一次导入），逐 sheet 逐行镜像；只读查看，可搜索/排序/筛选；右击可编辑或查看修改记录。")
        lay.addWidget(h)

        # 筛选条
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.f_sheet = QComboBox()
        self.f_sheet.addItem("全部工作表", "")
        self.f_status = QComboBox()
        self.f_status.addItem("全部", "")
        self.f_status.addItem("仅红字", "red")
        self.f_search = QLineEdit()
        self.f_search.setPlaceholderText("搜索：发票号码 / 对方 / 金额 / 案号")
        bar.addWidget(QLabel("工作表："))
        bar.addWidget(self.f_sheet, 0)
        bar.addWidget(QLabel("状态："))
        bar.addWidget(self.f_status, 0)
        bar.addWidget(QLabel("搜索："))
        bar.addWidget(self.f_search, 1)
        lay.addLayout(bar)

        # 表格
        self.table = QTableWidget(0, len(_HEADERS))
        self.table.setHorizontalHeaderLabels(_HEADERS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setWordWrap(False)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.verticalHeader().setVisible(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(True)
        self.table.horizontalHeader().sectionClicked.connect(self._on_header)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context)
        lay.addWidget(self.table, 1)

        self.lbl_stat = CaptionLabel("")
        lay.addWidget(self.lbl_stat)

        attach_persistence(self.table, "ledger_doc", "main")

        self._all_rows: list[dict] = []
        self._meta: dict[int, int] = {}      # 显示行 -> raw_ledger.id
        self._sort_col = -1
        self._sort_order = Qt.SortOrder.AscendingOrder

        self.f_sheet.currentIndexChanged.connect(self.refresh)
        self.f_status.currentIndexChanged.connect(self.refresh)
        self.f_search.editingFinished.connect(self.refresh)
        self.f_search.returnPressed.connect(self.refresh)

    # ------------------------------------------------------------------ #
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        sheets = rl.sheet_keys()
        # 重建工作表下拉（保留当前选择）
        cur = self.f_sheet.currentData()
        self.f_sheet.blockSignals(True)
        self.f_sheet.clear()
        self.f_sheet.addItem("全部工作表", "")
        for s in sheets:
            self.f_sheet.addItem(s["sheet_name"], s["sheet_key"])
        idx = self.f_sheet.findData(cur)
        if idx >= 0:
            self.f_sheet.setCurrentIndex(idx)
        self.f_sheet.blockSignals(False)

        sheet_key = self.f_sheet.currentData() or ""
        status = self.f_status.currentData() or ""
        kw = self.f_search.text().strip()
        rows = rl.list_raw(sheet_key=sheet_key, keyword=kw, status=status)
        self._all_rows = rows
        self._apply()

    def _apply(self) -> None:
        # 排序
        if self._sort_col >= 0:
            col = self._sort_col
            rev = self._sort_order == Qt.SortOrder.DescendingOrder
            if col == 0:  # 序号
                self._all_rows.sort(key=lambda r: _safe_int(_GETTERS[0](r)), reverse=rev)
            elif col == _AMOUNT_COL:  # 金额
                self._all_rows.sort(key=lambda r: _parse_total(_GETTERS[col](r)), reverse=rev)
            else:
                self._all_rows.sort(key=lambda r: str(_GETTERS[col](r)), reverse=rev)

        self.table.setRowCount(len(self._all_rows))
        self._meta.clear()
        for r, row in enumerate(self._all_rows):
            self._meta[r] = row["id"]
            is_red = _parse_total(row.get("amount_raw")) < 0
            for c, getter in enumerate(_GETTERS):
                v = getter(row)
                item = QTableWidgetItem("" if v is None else str(v))
                item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                if c == _AMOUNT_COL:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    if is_red:
                        item.setForeground(QColor("#D44C47"))
                self.table.setItem(r, c, item)
            if is_red:
                for c in range(len(_HEADERS)):
                    self.table.item(r, c).setBackground(_RED_BG)

        used = auto_fit_then_restore(self.table, "ledger_doc", "main")
        if used:
            self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSortIndicator(
            self._sort_col, self._sort_order) if self._sort_col >= 0 else None
        self.lbl_stat.setText(f"共 {len(self._all_rows)} 行（数据来源：发票台账文档）")

    # ------------------------------------------------------------------ #
    # 排序
    # ------------------------------------------------------------------ #
    def _on_header(self, col: int) -> None:
        if self._sort_col == col:
            self._sort_order = (Qt.SortOrder.DescendingOrder
                                if self._sort_order == Qt.SortOrder.AscendingOrder
                                else Qt.SortOrder.AscendingOrder)
        else:
            self._sort_col = col
            self._sort_order = Qt.SortOrder.AscendingOrder
        self._apply()

    # ------------------------------------------------------------------ #
    # 右击菜单
    # ------------------------------------------------------------------ #
    def _on_context(self, pos) -> None:
        row = self.table.currentRow()
        if row < 0 or row not in self._meta:
            return
        rid = self._meta[row]
        menu = QMenu(self)
        act_edit = menu.addAction("编辑此行")
        act_log = menu.addAction("查看修改记录")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == act_edit:
            self._edit_row(rid)
        elif chosen == act_log:
            self._show_log(rid)

    def _show_log(self, rid: int) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(f"修改记录 · raw_ledger #{rid}")
        dlg.resize(900, 520)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)
        v.addWidget(AuditView(dlg, table_name="raw_ledger", record_id=str(rid)), 1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(dlg.reject)
        v.addWidget(box)
        dlg.exec()

    def _edit_row(self, rid: int) -> None:
        row = rl.get_row(rid)
        if row is None:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"编辑发票台账行 #{rid}")
        form = QFormLayout(dlg)
        edits = {}
        labels = {
            "seq": "序号", "invoice_date_raw": "开票日期", "invoice_no": "发票号码",
            "buyer": "对方", "amount_raw": "金额", "handler_text": "经办人",
            "remark": "备注", "case_no": "案号", "recv_date_raw": "收到日期",
        }
        for f in EDIT_FIELDS:
            le = QLineEdit(str(row.get(f) or ""))
            edits[f] = le
            form.addRow(labels.get(f, f), le)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(dlg.accept)
        box.rejected.connect(dlg.reject)
        form.addRow(box)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        data = {f: edits[f].text().strip() for f in EDIT_FIELDS}
        rl.update_row(rid, data, note="手工编辑发票台账行")
        self.refresh()
        from qfluentwidgets import InfoBar, InfoBarPosition
        InfoBar.success("已保存并记入修改记录", parent=self,
                        position=InfoBarPosition.TOP_RIGHT, duration=2500)


def _safe_int(v) -> int:
    try:
        return int(str(v))
    except (ValueError, TypeError):
        return 0
