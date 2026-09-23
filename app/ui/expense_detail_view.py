"""账面情况 → 费用台账详情页（T3 只读骨架 + T4 Excel 式编辑 / 保存校验）。

整页跳转（非内嵌面板、非抽屉）：由 BookBalanceView 内部 QStackedWidget 承载，
占满账面情况内容区。本页展示组成某透视单元格的全部 expense_ledger 行，支持：
- 只读展示（默认）；
- 「编辑」进入 Excel 式编辑态（双击单元格进入编辑，金额列用 QDoubleSpinBox、
  身份列用下拉、其余文本列直接编辑；改费用金额/税额实时重算账面费用金额）；
- 「保存」触发 validate_expense_edit 校验（账面=费用−税、科目在册、经办人参与结算），
  通过则同一事务内 UPDATE + 写 change_log + 自动算 book_amount；不通过则红字标出并拦截；
- 「查看修改记录」内嵌统一审计中心 AuditView。

皮肤契约（硬性）：
- 颜色一律经 style.qcolor（如 text_mute / neg_fg / text），禁止十六进制字面量与 QColor(r,g,b)；
- 固定尺寸一律 scale.px(基准值)，禁止裸数字。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QSplitter, QStyledItemDelegate, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.db import get_conn
from app.engine import book_balance as bb
from app.engine.expense_edit import (
    EXPENSE_EDITABLE_KEYS as _EDITABLE_KEYS, apply_expense_edits,
)
from app.engine.expense_validation import validate_expense_edit
from app.ui import scale, style
from app.ui.audit_view import AuditView
from app.ui.expense_ledger_view import (
    _HEADERS as _LEDGER_HEADERS, _KEYS as _LEDGER_KEYS, _PERSON_TYPES,
)
from app.ui.widgets import CaptionLabel, DialogTitleLabel

# 复用费用台账页的列定义（顺序 = 源文件列序），尾部追加系统字段 id / import_batch_id
_DETAIL_HEADERS = list(_LEDGER_HEADERS) + ["ID", "批次ID"]
_DETAIL_KEYS = list(_LEDGER_KEYS) + ["id", "import_batch_id"]

# 列索引（_DETAIL_KEYS 的前 16 项与 _LEDGER_KEYS 完全一致，可直接复用）
_EXP_COL = _LEDGER_KEYS.index("expense_amount")
_TAX_COL = _LEDGER_KEYS.index("tax_amount")
_BOOK_COL = _LEDGER_KEYS.index("book_amount")
_PTYPE_COL = _LEDGER_KEYS.index("person_type")

# 金额列（展示时千分位格式化）
_MONEY_KEYS = {"expense_amount", "tax_amount", "book_amount"}
# 只读列（id/period/seq/source/import_batch_id 系统字段；book_amount 自动算且只读）
_READONLY_KEYS = {"id", "period", "seq", "source", "import_batch_id", "book_amount"}


def _cell_text(v, is_money: bool) -> str:
    if v is None:
        return ""
    if is_money and isinstance(v, (int, float)) and not isinstance(v, bool):
        return f"{v:,.2f}"
    return str(v)


class _MoneySpinDelegate(QStyledItemDelegate):
    """金额列编辑器：QDoubleSpinBox，EditRole 存原始浮点、DisplayRole 存千分位文本。"""

    def createEditor(self, parent, option, index):  # noqa: N802
        sb = QDoubleSpinBox(parent)
        sb.setRange(-1e9, 1e9)
        sb.setDecimals(2)
        return sb

    def setEditorData(self, editor, index):  # noqa: N802
        v = index.data(Qt.ItemDataRole.EditRole)
        try:
            editor.setValue(float(v) if v is not None else 0.0)
        except (TypeError, ValueError):
            editor.setValue(0.0)

    def setModelData(self, editor, model, index):  # noqa: N802
        val = round(editor.value(), 2)
        model.setData(index, val, Qt.ItemDataRole.EditRole)
        model.setData(index, f"{val:,.2f}", Qt.ItemDataRole.DisplayRole)


class _PersonTypeDelegate(QStyledItemDelegate):
    """身份列编辑器：下拉（与费用台账编辑页一致的可选集合）。"""

    def createEditor(self, parent, option, index):  # noqa: N802
        cb = QComboBox(parent)
        cb.addItems(_PERSON_TYPES)
        return cb

    def setEditorData(self, editor, index):  # noqa: N802
        cur = index.data(Qt.ItemDataRole.DisplayRole) or ""
        editor.setCurrentText(cur if cur in _PERSON_TYPES else _PERSON_TYPES[-1])

    def setModelData(self, editor, model, index):  # noqa: N802
        model.setData(index, editor.currentText(), Qt.ItemDataRole.DisplayRole)


class ExpenseDetailView(QWidget):
    """账面情况下钻到的「费用台账明细」整页（T3 只读 + T4 编辑/保存）。"""

    def __init__(self, on_back, parent=None) -> None:
        super().__init__(parent)
        self._on_back = on_back
        self._rows: list[dict] = []
        self._orig_by_rid: dict = {}
        self._row_ids: dict = {}        # row -> rid
        self._rid_to_row: dict = {}      # rid -> row
        self._dirty: set = set()
        self._editing = False
        self._loading = False
        self._totals_row = -1        # 底部合计行索引（<0 表示尚未建）
        self._s1 = self._s2 = self._kind = self._year = ""
        self._months = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(scale.px(12))

        # 顶部条：返回 + 标题 + 编辑 / 保存 / 查看修改记录
        bar = QHBoxLayout()
        bar.setSpacing(scale.px(8))
        self.btn_back = QPushButton("← 返回账面情况")
        self.btn_back.setFixedHeight(scale.px(36))
        self.btn_back.clicked.connect(self._on_back)
        bar.addWidget(self.btn_back)

        self.title = DialogTitleLabel("")
        bar.addWidget(self.title, 1)

        self.btn_edit = QPushButton("编辑")
        self.btn_edit.setFixedHeight(scale.px(36))
        self.btn_edit.clicked.connect(self._toggle_edit)
        bar.addWidget(self.btn_edit)

        self.btn_save = QPushButton("保存")
        self.btn_save.setFixedHeight(scale.px(36))
        self.btn_save.setEnabled(False)
        self.btn_save.clicked.connect(self._save)
        bar.addWidget(self.btn_save)

        self.btn_log = QPushButton("查看修改记录")
        self.btn_log.setFixedHeight(scale.px(36))
        self.btn_log.setCheckable(True)
        self.btn_log.toggled.connect(self._toggle_log)
        bar.addWidget(self.btn_log)
        lay.addLayout(bar)

        # 明细表
        self.table = QTableWidget(0, len(_DETAIL_HEADERS))
        self.table.setHorizontalHeaderLabels(_DETAIL_HEADERS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.cellChanged.connect(self._on_cell_changed)
        self.table.setItemDelegateForColumn(_EXP_COL, _MoneySpinDelegate())
        self.table.setItemDelegateForColumn(_TAX_COL, _MoneySpinDelegate())
        self.table.setItemDelegateForColumn(_PTYPE_COL, _PersonTypeDelegate())

        # 内嵌审计中心（默认隐藏，点「查看修改记录」显示）；record_id=None = 展示该表全部修改记录
        self._audit = AuditView(self, table_name="expense_ledger", record_id=None, embedded=True)
        self._audit.setVisible(False)

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.table)
        split.addWidget(self._audit)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        lay.addWidget(split, 1)

        self.hint = CaptionLabel("")
        lay.addWidget(self.hint)

    # ------------------------------------------------------------------ #
    def load(self, s1: str, s2: str, kind: str, months, year) -> None:
        self._s1, self._s2, self._kind = s1, s2, kind
        self._months, self._year = list(months), str(year)
        rows = bb.expense_rows_for_cell(self._year, months, s1, s2, kind)
        self._rows = rows
        self._orig_by_rid = {r["id"]: r for r in rows}
        self._dirty = set()

        self._loading = True
        self.table.setRowCount(len(rows))
        self._row_ids, self._rid_to_row = {}, {}
        for r, row in enumerate(rows):
            rid = row.get("id")
            self._row_ids[r] = rid
            self._rid_to_row[rid] = r
            for c, key in enumerate(_DETAIL_KEYS):
                v = row.get(key)
                if key in _MONEY_KEYS:
                    item = QTableWidgetItem(
                        f"{float(v or 0):,.2f}" if v is not None else "")
                    item.setData(Qt.ItemDataRole.EditRole,
                                 float(v) if v is not None else 0.0)
                else:
                    item = QTableWidgetItem(_cell_text(v, False))
                if key in _MONEY_KEYS:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                else:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                if key in _READONLY_KEYS:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    item.setForeground(style.qcolor("text_mute"))
                self.table.setItem(r, c, item)
        self._loading = False
        self._update_totals()

        # 标题：按 kind 拼可读标签
        if kind == "grand":
            label = "全部费用"
        elif kind == "uncat":
            label = f"{s1}（未分类）"
        elif kind == "l2":
            label = f"{s1} / {s2}"
        else:  # l1
            label = s1 or "全部费用"
        self.title.setText(f"{self._year} {label} 明细，共 {len(rows)} 行")
        self.hint.setText(
            "双击单元格可编辑（金额列用微调框、身份列用下拉）；改费用金额/税额会实时重算账面费用金额。"
            "编辑后点「保存」校验并写库。")

    def _reload(self) -> None:
        self.load(self._s1, self._s2, self._kind, self._months, self._year)

    # -- 编辑态 ---------------------------------------------------------
    def _toggle_edit(self) -> None:
        if not self._editing:
            self._editing = True
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
            self.btn_edit.setText("取消编辑")
            self.btn_save.setEnabled(True)
        else:
            # 取消：丢弃改动，重新载入（恢复原始值并清空 dirty）
            self._editing = False
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.btn_edit.setText("编辑")
            self.btn_save.setEnabled(False)
            self._reload()

    def _money_raw(self, r: int, col: int) -> float:
        item = self.table.item(r, col)
        v = item.data(Qt.ItemDataRole.EditRole) if item else None
        try:
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    def _cell_value(self, r: int, key: str):
        c = _DETAIL_KEYS.index(key)
        item = self.table.item(r, c)
        if item is None:
            return ""
        if key in ("expense_amount", "tax_amount"):
            return self._money_raw(r, c)
        return (item.text() or "").strip()

    def _recompute_book(self, r: int) -> None:
        amt = self._money_raw(r, _EXP_COL)
        tax = self._money_raw(r, _TAX_COL)
        book = round(amt - tax, 2)
        item = self.table.item(r, _BOOK_COL)
        item.setData(Qt.ItemDataRole.EditRole, book)
        item.setText(f"{book:,.2f}")

    def _update_totals(self) -> None:
        """底部合计行：对 费用金额/税额/账面费用金额 求和（只读、加粗、row_sum_bg 底）。

        合计行置于数据行之后，不占 _row_ids（_on_cell_changed / _save 自然跳过）；
        改费用金额/税额或重新载入时都会重算。
        """
        n = len(self._rows)
        if n == 0:
            return
        exp = tax = book = 0.0
        for r in range(n):
            exp += self._money_raw(r, _EXP_COL)
            tax += self._money_raw(r, _TAX_COL)
            book += self._money_raw(r, _BOOK_COL)
        row = n  # 合计行位于数据行之后
        if self.table.rowCount() < row + 1:
            self.table.setRowCount(row + 1)
        self._totals_row = row
        was = self._loading
        self._loading = True  # 写合计行不触发 _on_cell_changed 的编辑逻辑
        try:
            for c, key in enumerate(_DETAIL_KEYS):
                item = QTableWidgetItem()
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if c == 0:
                    item.setText("合计")
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                elif key in _MONEY_KEYS:
                    val = exp if c == _EXP_COL else tax if c == _TAX_COL else book
                    item.setText(f"{val:,.2f}")
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                else:
                    item.setText("")
                fnt = item.font()
                fnt.setBold(True)
                item.setFont(fnt)
                item.setForeground(style.qcolor("text"))
                item.setBackground(style.qcolor("row_sum_bg"))
                self.table.setItem(row, c, item)
        finally:
            self._loading = was

    def _on_cell_changed(self, r: int, c: int) -> None:
        if self._loading:
            return
        key = _DETAIL_KEYS[c]
        if key not in _EDITABLE_KEYS:
            return  # 只读列（含 book_amount）改动不标记、不重算
        rid = self._row_ids.get(r)
        if rid is None:
            return
        self._dirty.add(rid)
        if key in ("expense_amount", "tax_amount"):
            self._recompute_book(r)
        self._update_totals()

    # -- 保存 -----------------------------------------------------------
    def _save(self) -> None:
        if not self._editing:
            return
        self._clear_highlights()

        edited = []  # (rid, orig, new)
        for r, rid in self._row_ids.items():
            if rid not in self._dirty:
                continue
            orig = self._orig_by_rid.get(rid)
            if orig is None:
                continue
            new = {k: self._cell_value(r, k) for k in _EDITABLE_KEYS}
            amt = float(new["expense_amount"] or 0)
            tax = float(new["tax_amount"] or 0)
            new["book_amount"] = round(amt - tax, 2)
            edited.append((rid, orig, new))

        if not edited:
            self._exit_edit(reload=False)
            return

        val_rows = [{"id": rid, **new} for rid, _o, new in edited]
        viol = validate_expense_edit(val_rows)
        if viol:
            self._show_violations(viol)
            return

        conn = get_conn()
        try:
            apply_expense_edits(conn, edited)
            conn.commit()
        except Exception:  # noqa: BLE001
            conn.rollback()
            raise
        finally:
            conn.close()

        self._exit_edit(reload=True)

    def _exit_edit(self, reload: bool) -> None:
        self._editing = False
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.btn_edit.setText("编辑")
        self.btn_save.setEnabled(False)
        self._clear_highlights()
        if reload:
            self._reload()

    # -- 校验失败高亮 ---------------------------------------------------
    def _clear_highlights(self) -> None:
        for r in range(self.table.rowCount()):
            if r == self._totals_row:
                continue
            for c, key in enumerate(_DETAIL_KEYS):
                item = self.table.item(r, c)
                if item is None:
                    continue
                item.setForeground(
                    style.qcolor("text_mute") if key in _READONLY_KEYS
                    else style.qcolor("text"))

    def _show_violations(self, viol) -> None:
        for v in viol:
            r = self._rid_to_row.get(v.row_id)
            if r is None or v.field not in _DETAIL_KEYS:
                continue
            c = _DETAIL_KEYS.index(v.field)
            item = self.table.item(r, c)
            if item is not None:
                item.setForeground(style.qcolor("neg_fg"))
        QMessageBox.warning(
            self, "无法保存",
            "以下校验未通过，请修正后再保存：\n"
            + "\n".join(f"行#{v.row_id} [{v.field}] {v.message}" for v in viol))

    # -- 审计 -----------------------------------------------------------
    def _toggle_log(self, on: bool) -> None:
        self._audit.setVisible(on)
        if on:
            self._audit.load()
