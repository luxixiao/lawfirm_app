"""工资表查看页（按 sheet 分 Tab，每类独立表格）

- 数据源：raw_salary（工资文档逐 sheet 逐行 1:1 镜像，保留所有原始列、不归一化）。
- 账期以**文件名**为准（如 25.1 -> 2025-01），由 import_batch 带出，不取表内中文年月。
- 三个 sheet 列结构不同，**故按 sheet 分 Tab 各自成表**，每类只显示自己的列，
  避免出现「合伙人只有 4 列却占满 14 列」的大片空列与三类数据混排：
    · 聘用律师：编号/姓名/项目/每月工资/代扣个所税/代扣公积金/实发金额/养/医疗/失业
      （该 sheet 内含「分成报酬」「工资」两个数据块，实为同一列，合并为「每月工资」展示，
       用「项目」列标明来源块；数据仍按原始两块分别存储，不丢信息）
    · 合伙人：  编号/姓名/预发经营所得/实发金额
    · 后勤：    编号/姓名/工资/代扣个所税/代扣公积金/实发金额/养/医疗/失业
- 顶部年/月/搜索为全局筛选（对所有 Tab 生效）；各 Tab 独立排序、右击编辑与修改记录。
- 导入统一走「导入台账」页（文件名形如「工资25.1」= 工资表 2025 年 1 月）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMenu, QTabWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from app.engine import raw_salary as rs
from app.engine.raw_salary import EDIT_FIELDS
from app.ui.audit_view import AuditView
from app.ui.column_layout import install_column_layout
from app.ui.table_features import (
    install_accent_header, install_common_features, install_header_filter,
)
from app.ui.table_view import month_options_1_12, build_period
from app.ui.widgets import CaptionLabel, SubtitleLabel

# 编辑弹窗字段中文名
FIELD_LABELS = {
    "seq": "编号", "staff_name": "姓名",
    "share_raw": "分成报酬", "salary_raw": "工资", "partner_raw": "预发经营所得",
    "tax_raw": "代扣个所税", "fund_raw": "代扣公积金", "net_raw": "实发金额",
    "pension_raw": "养", "medical_raw": "医疗", "unemployment_raw": "失业",
    "remark": "备注",
}


def _g(field: str):
    return lambda r: r.get(field) or ""


def _monthly_wage(r: dict) -> str:
    """聘用律师 sheet 的「分成报酬」与「工资」实为同一列（都是当月工资），
    取两者中非空的那个合并展示；数据仍按原始两块分别存储，不丢信息。"""
    return (r.get("share_raw") or "").strip() or (r.get("salary_raw") or "").strip()


# 各 sheet 的列定义（列结构不同，故分开定义）
_HEADERS = {
    "lawyer": ["编号", "姓名", "项目", "每月工资",
               "代扣个所税", "代扣公积金", "实发金额", "养", "医疗", "失业"],
    "partner": ["编号", "姓名", "预发经营所得", "实发金额"],
    "logistics": ["编号", "姓名", "工资", "代扣个所税", "代扣公积金", "实发金额",
                  "养", "医疗", "失业"],
}

_GETTERS = {
    "lawyer": [_g("seq"), _g("staff_name"), _g("item_type"), _monthly_wage,
               _g("tax_raw"), _g("fund_raw"), _g("net_raw"),
               _g("pension_raw"), _g("medical_raw"), _g("unemployment_raw")],
    "partner": [_g("seq"), _g("staff_name"), _g("partner_raw"), _g("net_raw")],
    "logistics": [_g("seq"), _g("staff_name"), _g("salary_raw"), _g("tax_raw"),
                  _g("fund_raw"), _g("net_raw"), _g("pension_raw"),
                  _g("medical_raw"), _g("unemployment_raw")],
}

# 金额列（右对齐 + 按数值排序）
_AMOUNT_COLS = {
    "lawyer": {3, 4, 5, 6},
    "partner": {2, 3},
    "logistics": {2, 3, 4, 5},
}

# 各 sheet 的「应发金额」数值字段（用于底部合计）
_GROSS_FIELDS = {
    "lawyer": ("share_num", "salary_num"),
    "partner": ("partner_num",),
    "logistics": ("salary_num",),
}


def _parse_num(txt) -> float:
    from app.engine.raw_salary import _parse_num as _pn
    return _pn(txt)


def _safe_int(txt) -> int:
    try:
        return int(float(str(txt).strip()))
    except (ValueError, TypeError):
        return 0


class _SheetTab(QWidget):
    """单个 sheet 的表格页：列按该 sheet 定制，独立排序/筛选/右键菜单。"""

    def __init__(self, sheet_key: str, on_changed=None, parent=None):
        super().__init__(parent)
        self.sheet_key = sheet_key
        self._on_changed = on_changed
        self.headers = _HEADERS[sheet_key]
        self.getters = _GETTERS[sheet_key]
        self.amount_cols = _AMOUNT_COLS[sheet_key]
        self._rows: list[dict] = []
        self._meta: dict[int, int] = {}
        self._sort_col = -1
        self._sort_order = Qt.SortOrder.AscendingOrder

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.table = QTableWidget(0, len(self.headers))
        install_accent_header(self.table)
        self.table.setHorizontalHeaderLabels(self.headers)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setWordWrap(False)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.verticalHeader().setVisible(False)
        install_common_features(self.table)
        self._filter = install_header_filter(self.table)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(True)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context)
        lay.addWidget(self.table, 1)

        self.lbl_stat = CaptionLabel("")
        lay.addWidget(self.lbl_stat)

        self._col = install_column_layout(self.table, f"salary_{sheet_key}", "main")
        self._col.set_sort_callback(self._do_sort)

    # ---- 数据 ----
    def set_rows(self, rows: list[dict]) -> None:
        """只更新数据，渲染统一由 apply_keyword 触发（避免一次筛选渲染两遍）。"""
        self._rows = rows

    def apply_keyword(self, kw: str) -> None:
        self._kw = kw
        self._apply()

    def _apply(self) -> None:
        rows = self._rows
        kw = getattr(self, "_kw", "") or ""
        kw = kw.strip().lower()
        if kw:
            rows = [r for r in rows if any(
                kw in str(r.get(f) or "").lower()
                for f in ("seq", "staff_name", "item_type", "share_raw", "salary_raw",
                          "partner_raw", "tax_raw", "fund_raw", "net_raw", "remark")
            )]
        if self._sort_col >= 0:
            col = self._sort_col
            rev = self._sort_order == Qt.SortOrder.DescendingOrder
            if col == 0:  # 编号
                rows.sort(key=lambda r: _safe_int(self.getters[0](r)), reverse=rev)
            elif col in self.amount_cols:
                rows.sort(key=lambda r: _parse_num(self.getters[col](r)), reverse=rev)
            else:
                rows.sort(key=lambda r: str(self.getters[col](r)), reverse=rev)

        self.table.setRowCount(len(rows))
        self._meta.clear()
        total = 0.0
        for i, row in enumerate(rows):
            self._meta[i] = row["id"]
            total += sum(float(row.get(f) or 0) for f in _GROSS_FIELDS[self.sheet_key])
            for c, getter in enumerate(self.getters):
                v = getter(row)
                item = QTableWidgetItem("" if v is None else str(v))
                if c in self.amount_cols:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                else:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(i, c, item)

        self._col.apply()
        self._filter.apply_to_table(self.table)
        if self._sort_col >= 0:
            self.table.horizontalHeader().setSortIndicator(self._sort_col, self._sort_order)
        self.lbl_stat.setText(f"共 {len(rows)} 行；应发金额合计 {total:,.2f}")

    # ---- 排序 ----
    def _do_sort(self, logical, asc) -> None:
        self._sort_col = logical
        self._sort_order = Qt.SortOrder.AscendingOrder if asc else Qt.SortOrder.DescendingOrder
        self._col.set_sort_col(logical)
        self._apply()

    # ---- 右键 ----
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
        dlg.setWindowTitle(f"修改记录 · raw_salary #{rid}")
        dlg.resize(900, 520)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)
        v.addWidget(AuditView(dlg, table_name="raw_salary", record_id=str(rid)), 1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(dlg.reject)
        v.addWidget(box)
        dlg.exec()

    def _edit_row(self, rid: int) -> None:
        row = rs.get_row(rid)
        if row is None:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"编辑工资表行 #{rid}")
        form = QFormLayout(dlg)
        edits = {}
        for f in EDIT_FIELDS:
            le = QLineEdit(str(row.get(f) or ""))
            form.addRow(FIELD_LABELS.get(f, f), le)
            edits[f] = le
        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(dlg.accept)
        box.rejected.connect(dlg.reject)
        form.addRow(box)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        rs.update_row(rid, {f: le.text().strip() for f, le in edits.items()})
        if self._on_changed:
            self._on_changed()


class SalaryLedgerView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(12)

        lay.addWidget(SubtitleLabel("工资表"))
        lay.addWidget(CaptionLabel(
            "数据来源：工资文档（最新一次导入），逐 sheet 逐行镜像、保留所有原始列；"
            "账期以文件名为准。按 sheet 分标签展示，顶部年月/搜索对所有标签生效。"
        ))

        # 全局筛选条
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.f_year = QComboBox()
        self.f_year.currentIndexChanged.connect(self._apply)
        self.f_month = QComboBox()
        self.f_month.currentIndexChanged.connect(self._apply)
        self.f_search = QLineEdit()
        self.f_search.setPlaceholderText("搜索：编号 / 姓名 / 金额 / 备注（对所有标签生效）")
        bar.addWidget(QLabel("年份："))
        bar.addWidget(self.f_year, 0)
        bar.addWidget(QLabel("月份："))
        bar.addWidget(self.f_month, 0)
        bar.addWidget(QLabel("搜索："))
        bar.addWidget(self.f_search, 1)
        lay.addLayout(bar)

        # 分 sheet 标签
        self.tabs = QTabWidget()
        self._tabs: dict[str, _SheetTab] = {}
        for key in rs.SHEET_ORDER:
            tab = _SheetTab(key, on_changed=self.refresh)
            self._tabs[key] = tab
            self.tabs.addTab(tab, rs.SHEET_LABELS.get(key, key))
        lay.addWidget(self.tabs, 1)

        self._all_rows: list[dict] = []
        self.f_search.textChanged.connect(self._apply)

    # ------------------------------------------------------------------ #
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        self._all_rows = rs.list_raw()
        self._refresh_year_combo()
        self._refresh_month_combo()
        self._apply()

    # ---- 下拉 ----
    def _year_options(self) -> list:
        return sorted({p[:4] for r in self._all_rows if (p := (r.get("period") or ""))[:4]})

    def _refresh_year_combo(self) -> None:
        cur = self.f_year.currentData()
        self.f_year.blockSignals(True)
        self.f_year.clear()
        self.f_year.addItem("全部年份", userData="")
        for y in self._year_options():
            self.f_year.addItem(y, userData=y)
        idx = self.f_year.findData(cur)
        self.f_year.setCurrentIndex(idx if idx >= 0 else 0)
        self.f_year.blockSignals(False)

    def _refresh_month_combo(self) -> None:
        cur = self.f_month.currentData()
        self.f_month.blockSignals(True)
        self.f_month.clear()
        self.f_month.addItem("全部月份", userData="")
        for label, data in month_options_1_12():
            self.f_month.addItem(label, userData=data)
        idx = self.f_month.findData(cur)
        self.f_month.setCurrentIndex(idx if idx >= 0 else 0)
        self.f_month.blockSignals(False)

    # ---- 全局筛选（年/月/搜索）----
    def _apply(self) -> None:
        rows = self._all_rows
        period = build_period(self.f_year.currentData(), self.f_month.currentData())
        if period:
            if len(period) == 7:
                rows = [r for r in rows if (r.get("period") or "") == period]
            elif len(period) == 4:
                rows = [r for r in rows if (r.get("period") or "")[:4] == period]
            else:
                rows = [r for r in rows if (r.get("period") or "")[-2:] == period]
        kw = self.f_search.text().strip().lower()
        for key, tab in self._tabs.items():
            tab.set_rows([r for r in rows if r.get("sheet_key") == key])
            tab.apply_keyword(kw)

    # 注：导入已统一到「导入台账」页（文件名形如「工资25.1」），本页不再自带导入。
