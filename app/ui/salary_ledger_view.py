"""工资表查看页

- 数据源：raw_salary（工资文档逐 sheet 逐行 1:1 镜像，保留所有原始列、不归一化）。
- 账期以**文件名**为准（如 25.1 -> 2025-01），由 import_batch 带出，不取表内中文年月。
- 三个 sheet 列结构不同（聘用律师 9 列 / 合伙人 4 列 / 后勤 9 列），故取并集展示：
  每行只填自己 sheet 对应的金额列（分成报酬 / 工资 / 预发经营所得），其余留空。
- 支持：按年/月/类别/关键字筛选、排序、右击编辑（写 change_log）、查看修改记录、
  以及页面内「导入工资表」。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QMenu, QMessageBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.engine import raw_salary as rs
from app.engine.raw_salary import EDIT_FIELDS, sheet_label
from app.importer.importer import import_salary_file
from app.ui.audit_view import AuditView
from app.ui.column_layout import install_column_layout
from app.ui.import_view import guess_period
from app.ui.table_features import (
    install_accent_header, install_common_features, install_header_filter,
)
from app.ui.table_view import month_options_1_12, build_period
from app.ui.widgets import CaptionLabel, PushButton, SubtitleLabel

# 编辑弹窗字段中文名
FIELD_LABELS = {
    "seq": "编号", "staff_name": "姓名",
    "share_raw": "分成报酬", "salary_raw": "工资", "partner_raw": "预发经营所得",
    "tax_raw": "代扣个所税", "fund_raw": "代扣公积金", "net_raw": "实发金额",
    "pension_raw": "养", "medical_raw": "医疗", "unemployment_raw": "失业",
    "remark": "备注",
}

_HEADERS = ["账期", "类别", "编号", "姓名", "项目",
            "分成报酬", "工资", "预发经营所得",
            "代扣个所税", "代扣公积金", "实发金额",
            "养", "医疗", "失业"]

_GETTERS = [
    lambda r: r.get("period") or "",
    lambda r: sheet_label(r.get("sheet_key")),
    lambda r: r.get("seq") or "",
    lambda r: r.get("staff_name") or "",
    lambda r: r.get("item_type") or "",
    lambda r: r.get("share_raw") or "",
    lambda r: r.get("salary_raw") or "",
    lambda r: r.get("partner_raw") or "",
    lambda r: r.get("tax_raw") or "",
    lambda r: r.get("fund_raw") or "",
    lambda r: r.get("net_raw") or "",
    lambda r: r.get("pension_raw") or "",
    lambda r: r.get("medical_raw") or "",
    lambda r: r.get("unemployment_raw") or "",
]

# 金额列（按数值排序 + 右对齐）
_AMOUNT_COLS = {5, 6, 7, 8, 9, 10}


def _parse_num(txt) -> float:
    from app.engine.raw_salary import _parse_num as _pn
    return _pn(txt)


def _safe_int(txt) -> int:
    try:
        return int(float(str(txt).strip()))
    except (ValueError, TypeError):
        return 0


class SalaryLedgerView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(12)

        t = SubtitleLabel("工资表")
        lay.addWidget(t)
        h = CaptionLabel(
            "数据来源：工资文档（最新一次导入），逐 sheet 逐行镜像、保留所有原始列；"
            "账期以文件名为准。可搜索/排序/筛选，右击可编辑或查看修改记录。"
        )
        lay.addWidget(h)

        # 筛选条
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.f_year = QComboBox()
        self.f_year.currentIndexChanged.connect(self._apply)
        self.f_month = QComboBox()
        self.f_month.currentIndexChanged.connect(self._apply)
        self.f_sheet = QComboBox()
        self.f_sheet.addItem("全部类别", "")
        self.f_sheet.currentIndexChanged.connect(self._apply)
        self.f_search = QLineEdit()
        self.f_search.setPlaceholderText("搜索：编号 / 姓名 / 金额 / 备注")
        bar.addWidget(QLabel("年份："))
        bar.addWidget(self.f_year, 0)
        bar.addWidget(QLabel("月份："))
        bar.addWidget(self.f_month, 0)
        bar.addWidget(QLabel("类别："))
        bar.addWidget(self.f_sheet, 0)
        bar.addWidget(QLabel("搜索："))
        bar.addWidget(self.f_search, 1)
        lay.addLayout(bar)

        # 操作条
        ops = QHBoxLayout()
        ops.setSpacing(8)
        self.btn_import = PushButton("导入工资表")
        self.btn_import.clicked.connect(self._on_import)
        ops.addWidget(self.btn_import)
        ops.addStretch(1)
        lay.addLayout(ops)

        # 表格
        self.table = QTableWidget(0, len(_HEADERS))
        install_accent_header(self.table)
        self.table.setHorizontalHeaderLabels(_HEADERS)
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

        self._col = install_column_layout(self.table, "salary_ledger", "main")
        self._col.set_sort_callback(self._do_sort)

        self._all_rows: list[dict] = []
        self._meta: dict[int, int] = {}      # 显示行 -> raw_salary.id
        self._sort_col = -1
        self._sort_order = Qt.SortOrder.AscendingOrder

        self.f_search.textChanged.connect(self._apply)

    # ------------------------------------------------------------------ #
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        self._all_rows = rs.list_raw()
        self._refresh_year_combo()
        self._refresh_month_combo()
        self._refresh_sheet_combo()
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

    def _refresh_sheet_combo(self) -> None:
        cur = self.f_sheet.currentData()
        self.f_sheet.blockSignals(True)
        self.f_sheet.clear()
        self.f_sheet.addItem("全部类别", "")
        for s in rs.sheet_keys():
            self.f_sheet.addItem(s["sheet_name"], s["sheet_key"])
        idx = self.f_sheet.findData(cur)
        self.f_sheet.setCurrentIndex(idx if idx >= 0 else 0)
        self.f_sheet.blockSignals(False)

    # ---- 筛选 ----
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

        sheet_key = self.f_sheet.currentData() or ""
        if sheet_key:
            rows = [r for r in rows if r.get("sheet_key") == sheet_key]

        kw = self.f_search.text().strip().lower()
        if kw:
            rows = [r for r in rows if any(
                kw in str(r.get(f) or "").lower()
                for f in ("seq", "staff_name", "share_raw", "salary_raw",
                          "partner_raw", "tax_raw", "fund_raw", "net_raw", "remark")
            )]

        if self._sort_col >= 0:
            col = self._sort_col
            rev = self._sort_order == Qt.SortOrder.DescendingOrder
            if col == 2:  # 编号
                rows.sort(key=lambda r: _safe_int(_GETTERS[2](r)), reverse=rev)
            elif col in _AMOUNT_COLS:
                rows.sort(key=lambda r: _parse_num(_GETTERS[col](r)), reverse=rev)
            else:
                rows.sort(key=lambda r: str(_GETTERS[col](r)), reverse=rev)

        self.table.setRowCount(len(rows))
        self._meta.clear()
        total = 0.0
        for r, row in enumerate(rows):
            self._meta[r] = row["id"]
            total += (row.get("share_num") or 0) + (row.get("salary_num") or 0) \
                     + (row.get("partner_num") or 0)
            for c, getter in enumerate(_GETTERS):
                v = getter(row)
                item = QTableWidgetItem("" if v is None else str(v))
                if c in _AMOUNT_COLS:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                else:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(r, c, item)

        self._col.apply()
        self._filter.apply_to_table(self.table)
        if self._sort_col >= 0:
            self.table.horizontalHeader().setSortIndicator(self._sort_col, self._sort_order)
        self.lbl_stat.setText(
            f"共 {len(rows)} 行（数据来源：工资文档）；应发金额合计 {total:,.2f}"
        )

    # ------------------------------------------------------------------ #
    # 排序
    # ------------------------------------------------------------------ #
    def _do_sort(self, logical, asc) -> None:
        self._sort_col = logical
        self._sort_order = Qt.SortOrder.AscendingOrder if asc else Qt.SortOrder.DescendingOrder
        self._col.set_sort_col(logical)
        self._apply()

    # ------------------------------------------------------------------ #
    # 导入
    # ------------------------------------------------------------------ #
    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择工资表文件", "",
            "Excel 文件 (*.xls *.xlsx *.xlsm);;所有文件 (*.*)")
        if not path:
            return
        fname = path.replace("\\", "/").split("/")[-1]
        period = guess_period(fname)
        if not period:
            QMessageBox.warning(
                self, "无法识别账期",
                f"文件名「{fname}」无法识别账期。\n\n"
                "工资表以**文件名**为准记账期，请命名为如：25.1（2025年1月）或 2025.1。")
            return
        # 允许人工确认/纠正账期（默认取文件名解析结果）
        text, ok = QInputDialog.getText(
            self, "确认账期", "账期（YYYY-MM）：", text=period)
        if not ok or not text.strip():
            return
        period = text.strip()
        ret = QMessageBox.question(
            self, "确认导入",
            f"账期：{period}\n文件：{fname}\n\n同账期重导会覆盖旧数据，确定导入？")
        if ret != QMessageBox.StandardButton.Yes:
            return
        try:
            r = import_salary_file(path, period)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "导入失败", str(e))
            return
        QMessageBox.information(
            self, "导入成功",
            f"账期 {period}：共 {r['count']} 行，{r['sheet_count']} 个工作表")
        self.refresh()

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
        data = {f: le.text().strip() for f, le in edits.items()}
        rs.update_row(rid, data)
        self.refresh()
